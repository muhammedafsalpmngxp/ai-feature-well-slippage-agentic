"""OpenAI chat client shared by every agent.

One entry point (`chat`) so the model tier, timeout and retry policy live in a single place.
`fast=True` selects the optional cheap tier (OPENAI_FAST_MODEL); when that is blank it falls
back to the main model, so enabling it is purely opt-in and changes nothing by default.
"""
from __future__ import annotations

from functools import lru_cache

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import settings
from app.observability import get_logger

log = get_logger()


@lru_cache(maxsize=8)
def _client(model: str, temperature: float | None) -> ChatOpenAI:
    """Cached per (model, temperature).

    Keyed on both because a single cached client would hand the second caller whatever
    temperature the first one asked for.
    """
    kwargs: dict = {
        "model": model,
        "api_key": settings.openai_api_key,
        "timeout": settings.llm_timeout,
        "max_retries": settings.llm_max_retries,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    return ChatOpenAI(**kwargs)


def chat(system: str, user: str, temperature: float = 0.0, fast: bool = False) -> str:
    """Send one system+user exchange and return the reply text.

    Reasoning-style models reject a non-default temperature. Rather than discover that on every
    call and pay a rejected request plus a retry, OPENAI_IGNORES_TEMPERATURE drops it up front;
    the except branch stays as the safety net for a model that is not covered by that flag.
    """
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set in .env — no agent can run.")

    model = (settings.openai_fast_model or settings.openai_model) if fast else settings.openai_model
    effective = None if settings.reasoning_ignores_temperature else temperature
    messages = [SystemMessage(content=system), HumanMessage(content=user)]

    try:
        response = _client(model, effective).invoke(messages)
    except Exception as exc:  # noqa: BLE001 - one targeted retry, then give up
        if "temperature" in str(exc).lower() and effective is not None:
            log.info("llm: %s rejected temperature — retrying without it", model)
            response = _client(model, None).invoke(messages)
        else:
            raise

    return str(getattr(response, "content", "") or "")
