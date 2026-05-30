from __future__ import annotations

import shutil
import sys

from config import get_settings
from database import data_files, ensure_dirs, stats


def status(name: str, ok: bool, detail: str) -> bool:
    marker = "OK" if ok else "FAIL"
    print(f"[{marker}] {name}: {detail}")
    return ok


def main() -> int:
    settings = get_settings()
    ensure_dirs(settings.data_dir, settings.db_path, settings.tmp_dir)

    checks: list[bool] = []
    checks.append(status("BOT_TOKEN", bool(settings.bot_token), "set" if settings.bot_token else "missing"))
    checks.append(
        status(
            "ADMIN_IDS",
            bool(settings.admin_ids),
            ",".join(str(x) for x in sorted(settings.admin_ids)) if settings.admin_ids else "missing",
        )
    )

    files = data_files(settings.data_dir)
    checks.append(status("DATA_FILES", bool(files), f"{len(files)} supported files"))

    indexed_lines, indexed_files = stats(settings.db_path)
    checks.append(status("INDEX", indexed_lines > 0, f"{indexed_lines:,} lines, {indexed_files:,} files"))

    disk = shutil.disk_usage(settings.db_path.parent)
    free_gb = disk.free / (1024**3)
    checks.append(status("DISK_FREE", free_gb >= 2, f"{free_gb:.2f} GB free"))

    print()
    print(f"SEARCH_CONCURRENCY={settings.search_concurrency}")
    print(f"SEARCH_QUEUE_LIMIT={settings.search_queue_limit}")
    print(f"USER_COOLDOWN_SECONDS={settings.user_cooldown_seconds}")
    print(f"MAX_RESULT_FILE_MB={settings.max_result_file_mb}")
    print(f"MIN_PREFIX_LENGTH={settings.min_prefix_length}")

    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
