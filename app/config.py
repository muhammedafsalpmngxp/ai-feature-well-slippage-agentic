"""Central configuration, loaded once from .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


def _csv(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(v.strip().lower() for v in _get(name, default).split(",") if v.strip())


def _first(*names: str, default: str = "") -> str:
    """First non-empty of several env names.

    The .env in this folder spells the trust flag DB_TRUST_SERVER_CERTIFICATE, while the sibling
    project uses DB_TRUST_CERT. Accepting both means neither file has to be edited to run this.
    """
    for name in names:
        value = _get(name)
        if value:
            return value
    return default


@dataclass(frozen=True)
class Settings:
    # ── Database ────────────────────────────────────────────────────────────────
    db_server: str = field(default_factory=lambda: _get("DB_SERVER"))
    db_port: int = field(default_factory=lambda: _get_int("DB_PORT", 1433))
    db_name: str = field(default_factory=lambda: _get("DB_NAME"))
    db_user: str = field(default_factory=lambda: _get("DB_USER"))
    db_password: str = field(default_factory=lambda: _get("DB_PASSWORD"))
    db_driver: str = field(default_factory=lambda: _get("DB_DRIVER"))
    db_encrypt: str = field(default_factory=lambda: _get("DB_ENCRYPT", "yes"))
    db_trust_cert: str = field(
        default_factory=lambda: _first("DB_TRUST_SERVER_CERTIFICATE", "DB_TRUST_CERT", default="yes")
    )
    query_timeout: int = field(default_factory=lambda: _get_int("QUERY_TIMEOUT", 120))
    # Budget for the ADVISORY catalogue read behind /api/status's drift check. Much shorter than
    # query_timeout on purpose: that 120s is sized for a real analytical query, and applying it
    # to a decorative check let one slow catalogue read hold the whole dashboard behind a
    # skeleton for two minutes. A drift check that cannot answer in a few seconds should report
    # "unknown" and get out of the way - the figures on the page do not depend on it.
    status_fingerprint_timeout: int = field(
        default_factory=lambda: _get_int("STATUS_FINGERPRINT_TIMEOUT", 8)
    )

    # ── LLM ─────────────────────────────────────────────────────────────────────
    openai_api_key: str = field(default_factory=lambda: _get("OPENAI_API_KEY"))
    openai_model: str = field(default_factory=lambda: _get("OPENAI_MODEL", "gpt-5-mini"))
    # Optional cheaper tier for the planner. Blank = use the main model, so this is opt-in.
    openai_fast_model: str = field(default_factory=lambda: _get("OPENAI_FAST_MODEL"))
    llm_timeout: int = field(default_factory=lambda: _get_int("LLM_TIMEOUT", 180))
    llm_max_retries: int = field(default_factory=lambda: _get_int("LLM_MAX_RETRIES", 2))
    # Reasoning models often accept only their default temperature. When true, calls omit it up
    # front rather than paying a rejected call plus a silent retry every time. Default false so
    # the per-agent temperatures in llm.py actually take effect; llm.chat() drops it and retries
    # once if the model turns out to reject it, so a wrong setting costs one call, not a run.
    reasoning_ignores_temperature: bool = field(
        default_factory=lambda: _get("OPENAI_IGNORES_TEMPERATURE", "false").lower()
        in ("1", "true", "yes")
    )
    # GLOBAL overrides for the per-agent reasoning settings in llm.py. Blank means "use each
    # agent's own value", the same convention as a blank API key disabling a feature: setting
    # nothing changes nothing. Set one of these to force every agent to the same value, which is
    # useful for measuring what the tuning is actually buying.
    reasoning_effort: str = field(default_factory=lambda: _get("OPENAI_REASONING_EFFORT"))
    reasoning_verbosity: str = field(default_factory=lambda: _get("OPENAI_VERBOSITY"))

    # ── Introspection scope ─────────────────────────────────────────────────────
    # The slippage domain spans the well tables, the two activity lookups in dbo, and the
    # project/ref lookups. Narrower than "every schema" on purpose: the schema block goes into
    # every SQL-writing and verifying prompt, so each extra table is paid for on every call.
    # Which schemas introspection reads, straight from .env.
    #
    # ⚠ THIS IS APPLIED BEFORE INCLUDED_TABLES. The schema filter runs inside the catalogue
    # queries; the table allowlist is applied to what comes back. So a table named in
    # INCLUDED_TABLES whose schema is missing here is dropped before the allowlist ever sees it,
    # and reports as "matched no table". When you point .env at a database that keeps its tables
    # in a different schema, set BOTH.
    allowed_schemas: tuple[str, ...] = field(
        default_factory=lambda: _csv("ALLOWED_SCHEMAS", "well,dbo,project,ref,core")
    )
    # An ALLOWLIST. When set, ONLY these tables are introspected and nothing else reaches any
    # prompt. Each entry is "schema.table" (exact, preferred) or a bare "table" name, which
    # matches that name in any allowed schema.
    #
    # This is the strongest lever on cost and on accuracy. The schema block goes into the SQL
    # Author's and the Verifier's prompts on EVERY call, so each unused table is paid for on
    # every attempt - and every near-miss name in it is one more thing for an author to reach
    # for by mistake. Blank means "every table in the allowed schemas".
    included_tables: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            t.strip().lower() for t in _get("INCLUDED_TABLES").split(",") if t.strip()
        )
    )
    # Subtracted AFTER the allowlist, so an entry here still wins.
    excluded_tables: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            t.strip().lower() for t in _get("EXCLUDED_TABLES").split(",") if t.strip()
        )
    )
    excluded_columns: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            c.strip().lower() for c in _get("EXCLUDED_COLUMNS").split(",") if c.strip()
        )
    )

    # ── Guardrails ──────────────────────────────────────────────────────────────
    max_rows: int = field(default_factory=lambda: _get_int("MAX_ROWS", 200))
    # Rewrites for a syntax/safety/execution failure.
    max_sql_retries: int = field(default_factory=lambda: _get_int("MAX_SQL_RETRIES", 2))
    # Rewrites the Verifier may demand. SEPARATE from max_sql_retries deliberately: sharing one
    # counter lets unrelated syntax errors earlier in the run leave the Verifier with zero
    # rewrites, so it rejects and is overruled immediately.
    verify_retries: int = field(default_factory=lambda: _get_int("VERIFY_RETRIES", 1))

    # The well the per-well query is EXECUTED against when it is generated and verified.
    # It is only a sample: the query filters on a bound `?` parameter, so it does not care
    # which well - it just needs one to return rows the Verifier can judge. At serve time the
    # API binds whatever well the user asks for.
    #
    # Making this a fixed setting is what removes the last dependency between the queries and
    # lets all three generate concurrently. Prefer a well that HAS late tasks: verifying against
    # an empty result only checks the SQL text, never its output.
    sample_well_id: str = field(default_factory=lambda: _get("SAMPLE_WELL_ID", "33151"))

    log_level: str = field(default_factory=lambda: _get("LOG_LEVEL", "INFO"))
    # Shared log file, so a run can be followed from a terminal other than the one that started
    # it - which is the only way to watch a run triggered from the dashboard, since that one is
    # a subprocess of the API. Set LOG_FILE= (empty) to disable and log to the console only.
    log_file: str = field(default_factory=lambda: _get("LOG_FILE", "logs/pipeline.log"))


settings = Settings()
