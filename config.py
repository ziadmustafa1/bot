from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    load_dotenv = None


if load_dotenv is not None:
    load_dotenv()


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _admin_ids() -> set[int]:
    raw = os.getenv("ADMIN_IDS", "").strip()
    if not raw:
        return set()
    return {int(part.strip()) for part in raw.split(",") if part.strip()}


@dataclass(frozen=True)
class Settings:
    bot_token: str
    support_username: str
    local_bot_api_url: str
    bin_lookup_url: str
    data_dir: Path
    db_path: Path
    auth_db_path: Path
    tmp_dir: Path
    admin_ids: set[int]
    max_results_per_query: int
    min_prefix_length: int
    max_result_file_mb: int
    search_concurrency: int
    search_queue_limit: int
    user_cooldown_seconds: int
    max_telegram_download_mb: int
    redact_card_fields: bool


def get_settings() -> Settings:
    return Settings(
        bot_token=os.getenv("BOT_TOKEN", "").strip(),
        support_username=os.getenv("SUPPORT_USERNAME", "@I_INW").strip(),
        local_bot_api_url=os.getenv("LOCAL_BOT_API_URL", "").strip().rstrip("/"),
        bin_lookup_url=os.getenv("BIN_LOOKUP_URL", "http://bins.antipublic.cc/bins").strip(),
        data_dir=Path(os.getenv("DATA_DIR", "data")),
        db_path=Path(os.getenv("DB_PATH", "index/data.sqlite3")),
        auth_db_path=Path(os.getenv("AUTH_DB_PATH", "index/auth.sqlite3")),
        tmp_dir=Path(os.getenv("TMP_DIR", "tmp")),
        admin_ids=_admin_ids(),
        max_results_per_query=_int_env("MAX_RESULTS_PER_QUERY", 0),
        min_prefix_length=max(1, _int_env("MIN_PREFIX_LENGTH", 6)),
        max_result_file_mb=max(1, _int_env("MAX_RESULT_FILE_MB", 45)),
        search_concurrency=max(1, _int_env("SEARCH_CONCURRENCY", 4)),
        search_queue_limit=max(0, _int_env("SEARCH_QUEUE_LIMIT", 20)),
        user_cooldown_seconds=max(0, _int_env("USER_COOLDOWN_SECONDS", 3)),
        max_telegram_download_mb=max(1, _int_env("MAX_TELEGRAM_DOWNLOAD_MB", 20)),
        redact_card_fields=os.getenv("REDACT_CARD_FIELDS", "1").strip().lower()
        not in {"0", "false", "no", "off"},
    )
