"""OpenAI chat client shared by every agent, with per-agent tuning.

One entry point (`chat`) so the model tier, timeout, retry policy and reasoning settings live in
a single place. Each caller passes its agent name; the settings for that agent are looked up in
_AGENT_TUNING below, so no node, prompt or graph code carries tuning of its own.

WHY TUNE PER AGENT. The four agents do genuinely different work, and the cost of being wrong is
not the same for each:

  * The SQL Author and the Verifier are where thinking pays for itself. An Author mistake costs a
    full rewrite cycle - roughly 14k input tokens plus a database round trip - and a Verifier miss
    is worse, because it is silent: a wrong query gets approved and the brief reports wrong
    numbers with nothing to indicate it. Both were run at high effort for that reason; they
    are currently set to medium to cut latency - see the note on _AGENT_TUNING.
  * The Planner is a bounded matching task over a small schema block. It has to be careful, not
    inventive: medium effort is enough, and its failure mode (an unresolved binding) is REPORTED
    rather than silent.
  * The Synthesizer derives nothing. Every count it states is computed in Python and handed to it
    (see nodes/synthesize.py), so it is formatting known figures. Low effort, and it is the one
    agent that needs room to WRITE - hence the only non-low verbosity.

VERBOSITY follows the output shape, not the difficulty: three of the four emit a single JSON
object or one SQL block, where extra prose is waste at best and something to parse around at
worst. Only the brief is meant to be long.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import settings
from app.observability import get_logger

log = get_logger()


@dataclass(frozen=True)
class Tuning:
    """What one agent asks of the model."""

    temperature: float | None
    effort: str      # minimal | low | medium | high
    verbosity: str   # low | medium | high
    why: str


_AGENT_TUNING: dict[str, Tuning] = {
    "planner": Tuning(
        temperature=0.0,
        effort="medium",
        verbosity="low",
        why="bounded matching over a small schema; must not guess, but need not explore",
    ),
    # ⚠ BOTH SET TO medium ON REQUEST, TO CUT LATENCY. Measured on well_slippage, this is a
    # net LOSS: high wrote correct SQL in 50s on ONE attempt (82s to verified), medium wrote it
    # in 19s but took THREE attempts and a verifier rejection (116s to verified) - and the
    # rejection was the §2 missing-data guard, the one milestone_rules calls "the most
    # dangerous wrong answer this system can produce".
    #
    # Revert by changing these two strings back to "high". Watch the rework rate in the log:
    # if a query needs more than one attempt more often than not, medium is costing time, not
    # saving it.
    "sql_author": Tuning(
        temperature=0.0,
        effort="medium",
        verbosity="low",
        why="the hardest task here; every avoided rewrite saves a full cycle",
    ),
    "verifier": Tuning(
        temperature=0.0,
        effort="medium",
        verbosity="low",
        why="the quality gate - its misses are silent, so thinking is worth more than speed",
    ),
    "synthesize": Tuning(
        temperature=0.2,
        effort="low",
        verbosity="medium",
        why="derives nothing; the counts are precomputed, so it is formatting, not reasoning",
    ),
}

_FALLBACK = Tuning(temperature=0.0, effort="medium", verbosity="low", why="unknown agent")


def tuning_for(agent: str) -> Tuning:
    """The agent's tuning, with the .env overrides applied.

    A blank override leaves the per-agent value alone, the same convention as a blank API key
    disabling a feature: setting nothing changes nothing.
    """
    base = _AGENT_TUNING.get(agent, _FALLBACK)
    effort = settings.reasoning_effort or base.effort
    verbosity = settings.reasoning_verbosity or base.verbosity
    temperature = None if settings.reasoning_ignores_temperature else base.temperature
    return Tuning(temperature, effort, verbosity, base.why)


@lru_cache(maxsize=16)
def _client(model: str, temperature: float | None, effort: str, verbosity: str) -> ChatOpenAI:
    """Cached per distinct configuration.

    Keyed on every tuning field, not just the model: a single cached client would hand the
    second caller whatever the first one asked for, so the Synthesizer would silently inherit
    the Verifier's high effort.
    """
    kwargs: dict = {
        "model": model,
        "api_key": settings.openai_api_key,
        "timeout": settings.llm_timeout,
        "max_retries": settings.llm_max_retries,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    if effort:
        kwargs["reasoning_effort"] = effort
    if verbosity:
        kwargs["verbosity"] = verbosity
    return ChatOpenAI(**kwargs)


def chat(system: str, user: str, agent: str = "", temperature: float | None = None,
         fast: bool = False) -> str:
    """Send one system+user exchange and return the reply text.

    `agent` selects the tuning. `temperature` overrides that agent's value when passed
    explicitly, which keeps existing call sites working unchanged.
    """
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set in .env - no agent can run.")

    model = (settings.openai_fast_model or settings.openai_model) if fast else settings.openai_model
    tune = tuning_for(agent)
    effective_temp = tune.temperature if temperature is None else temperature
    if settings.reasoning_ignores_temperature:
        effective_temp = None

    messages = [SystemMessage(content=system), HumanMessage(content=user)]
    # Logged at INFO, not debug: these four calls ARE the run, and 85% of its wall-clock. A log
    # that shows which agent is working, on what model, at what effort, and how long it took is
    # the only way to see where the time actually goes.
    log.info(
        "llm  %-11s model=%s effort=%-6s verbosity=%-6s temp=%-4s system=%.1fk user=%.1fk chars",
        agent or "?", model, tune.effort, tune.verbosity, str(effective_temp),
        len(system) / 1000, len(user) / 1000,
    )
    started = time.perf_counter()

    try:
        response = _client(model, effective_temp, tune.effort, tune.verbosity).invoke(messages)
    except Exception as exc:  # noqa: BLE001
        text = str(exc).lower()
        # Some models reject a non-default temperature, and some reject one or both of the
        # reasoning parameters. Drop whichever the error names and retry ONCE, rather than
        # failing a whole run over a parameter the caller does not actually need.
        if "temperature" in text and effective_temp is not None:
            log.info("llm[%s]: %s rejected temperature - retrying without it", agent, model)
            response = _client(model, None, tune.effort, tune.verbosity).invoke(messages)
        elif "verbosity" in text:
            log.info("llm[%s]: %s rejected verbosity - retrying without it", agent, model)
            response = _client(model, effective_temp, tune.effort, "").invoke(messages)
        elif "reasoning" in text or "effort" in text:
            log.info("llm[%s]: %s rejected reasoning_effort - retrying without it", agent, model)
            response = _client(model, effective_temp, "", tune.verbosity).invoke(messages)
        else:
            raise

    text = str(getattr(response, "content", "") or "")
    log.info(
        "llm  %-11s done in %5.1fs  -> %d chars",
        agent or "?", time.perf_counter() - started, len(text),
    )
    return text
