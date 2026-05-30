from __future__ import annotations

import secrets
import sqlite3
import string
import time
from contextlib import closing
from pathlib import Path


ALPHABET = string.ascii_uppercase + string.digits


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_access_db(db_path: Path) -> None:
    with closing(_connect(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS access_codes (
                code TEXT PRIMARY KEY,
                duration_seconds INTEGER NOT NULL,
                created_by INTEGER,
                created_at INTEGER NOT NULL,
                used_by INTEGER,
                used_at INTEGER,
                revoked_at INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_access (
                user_id INTEGER PRIMARY KEY,
                expires_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        conn.commit()


def make_code(length: int = 12) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def create_code(db_path: Path, duration_seconds: int, created_by: int | None) -> str:
    init_access_db(db_path)
    now = int(time.time())
    with closing(_connect(db_path)) as conn:
        for _ in range(20):
            code = make_code()
            try:
                conn.execute(
                    """
                    INSERT INTO access_codes
                    (code, duration_seconds, created_by, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (code, duration_seconds, created_by, now),
                )
                conn.commit()
                return code
            except sqlite3.IntegrityError:
                continue
    raise RuntimeError("Could not create a unique code")


def redeem_code(db_path: Path, code: str, user_id: int) -> tuple[bool, str, int | None]:
    init_access_db(db_path)
    code = code.strip().upper()
    now = int(time.time())

    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT duration_seconds, used_by, revoked_at
            FROM access_codes
            WHERE code = ?
            """,
            (code,),
        ).fetchone()

        if row is None:
            return False, "code_not_found", None

        duration_seconds, used_by, revoked_at = row
        if revoked_at is not None:
            return False, "code_revoked", None
        if used_by is not None:
            return False, "code_used", None

        current = conn.execute(
            "SELECT expires_at FROM user_access WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        base = max(now, int(current[0])) if current else now
        expires_at = base + int(duration_seconds)

        conn.execute(
            """
            INSERT INTO user_access (user_id, expires_at, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                expires_at = excluded.expires_at,
                updated_at = excluded.updated_at
            """,
            (user_id, expires_at, now),
        )
        conn.execute(
            """
            UPDATE access_codes
            SET used_by = ?, used_at = ?
            WHERE code = ?
            """,
            (user_id, now, code),
        )
        conn.commit()
        return True, "ok", expires_at


def grant_access(db_path: Path, user_id: int, duration_seconds: int) -> int:
    init_access_db(db_path)
    now = int(time.time())
    with closing(_connect(db_path)) as conn:
        current = conn.execute(
            "SELECT expires_at FROM user_access WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        base = max(now, int(current[0])) if current else now
        expires_at = base + int(duration_seconds)
        conn.execute(
            """
            INSERT INTO user_access (user_id, expires_at, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                expires_at = excluded.expires_at,
                updated_at = excluded.updated_at
            """,
            (user_id, expires_at, now),
        )
        conn.commit()
        return expires_at


def expire_access(db_path: Path, user_id: int) -> bool:
    init_access_db(db_path)
    now = int(time.time())
    with closing(_connect(db_path)) as conn:
        cur = conn.execute(
            """
            UPDATE user_access
            SET expires_at = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (now, now, user_id),
        )
        conn.commit()
        return cur.rowcount > 0


def get_access_expiry(db_path: Path, user_id: int) -> int | None:
    init_access_db(db_path)
    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT expires_at FROM user_access WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return int(row[0]) if row else None


def has_active_access(db_path: Path, user_id: int) -> bool:
    expires_at = get_access_expiry(db_path, user_id)
    return expires_at is not None and expires_at > int(time.time())


def revoke_code(db_path: Path, code: str) -> bool:
    init_access_db(db_path)
    now = int(time.time())
    with closing(_connect(db_path)) as conn:
        cur = conn.execute(
            """
            UPDATE access_codes
            SET revoked_at = ?
            WHERE code = ? AND used_by IS NULL AND revoked_at IS NULL
            """,
            (now, code.strip().upper()),
        )
        conn.commit()
        return cur.rowcount > 0


def list_recent_codes(db_path: Path, limit: int = 10) -> list[tuple[str, int, int, int | None, int | None]]:
    init_access_db(db_path)
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT code, duration_seconds, created_at, used_by, revoked_at
            FROM access_codes
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [(str(a), int(b), int(c), d, e) for a, b, c, d, e in rows]
