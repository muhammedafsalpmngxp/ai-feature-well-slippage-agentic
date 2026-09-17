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

    # ── LLM ─────────────────────────────────────────────────────────────────────
    openai_api_key: str = field(default_factory=lambda: _get("OPENAI_API_KEY"))
    openai_model: str = field(default_factory=lambda: _get("OPENAI_MODEL", "gpt-5-mini"))
    # Optional cheaper tier for the planner. Blank = use the main model, so this is opt-in.
    openai_fast_model: str = field(default_factory=lambda: _get("OPENAI_FAST_MODEL"))
    llm_timeout: int = field(default_factory=lambda: _get_int("LLM_TIMEOUT", 180))
    llm_max_retries: int = field(default_factory=lambda: _get_int("LLM_MAX_RETRIES", 2))
    # Reasoning models often accept only their default temperature. When true, main-tier calls
    # omit it up front rather than paying a rejected call plus a silent retry every time.
    reasoning_ignores_temperature: bool = field(
        default_factory=lambda: _get("OPENAI_IGNORES_TEMPERATURE", "true").lower()
        in ("1", "true", "yes")
    )

    # ── Introspection scope ─────────────────────────────────────────────────────
    # The slippage domain spans the well tables, the two activity lookups in dbo, and the
    # project/ref lookups. Narrower than "every schema" on purpose: the schema block goes into
    # every SQL-writing and verifying prompt, so each extra table is paid for on every call.
    allowed_schemas: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            s.strip().lower()
            for s in _get("ALLOWED_SCHEMAS", "well,dbo,project,ref,core").split(",")
            if s.strip()
        )
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

    log_level: str = field(default_factory=lambda: _get("LOG_LEVEL", "INFO"))


settings = Settings()
