from __future__ import annotations

import sqlite3
import tempfile
from hashlib import sha256
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path


TEXT_EXTENSIONS = {".txt", ".csv", ".log", ".dat"}


@dataclass(frozen=True)
class SearchResult:
    count: int
    truncated_by_results: bool = False
    truncated_by_size: bool = False


@dataclass(frozen=True)
class FileFingerprint:
    path: Path
    size: int
    sha256: str


@dataclass(frozen=True)
class RebuildResult:
    indexed_lines: int
    indexed_files: int
    skipped_duplicate_files: int


def ensure_dirs(data_dir: Path, db_path: Path, tmp_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)


def data_files(data_dir: Path) -> list[Path]:
    if not data_dir.exists():
        return []
    return sorted(
        path
        for path in data_dir.iterdir()
        if path.is_file() and path.suffix.lower() in TEXT_EXTENSIONS
    )


def fingerprint_file(path: Path) -> FileFingerprint:
    digest = sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return FileFingerprint(path=path, size=size, sha256=digest.hexdigest())


def find_duplicate_file(data_dir: Path, candidate: Path) -> Path | None:
    if not candidate.exists():
        return None

    candidate_fp = fingerprint_file(candidate)
    for path in data_files(data_dir):
        if path.resolve() == candidate.resolve():
            continue
        if path.stat().st_size != candidate_fp.size:
            continue
        if fingerprint_file(path).sha256 == candidate_fp.sha256:
            return path
    return None


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=FILE")
    conn.execute("PRAGMA cache_size=-200000")
    return conn


def init_db(db_path: Path) -> None:
    with closing(_connect(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY,
                line_key TEXT NOT NULL,
                line TEXT NOT NULL,
                source_file TEXT NOT NULL,
                line_no INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_records_line_key ON records(line_key)"
        )
        conn.commit()


def _extract_key(line: str) -> str:
    return line.split("|", 1)[0].strip()


def _iter_file_records(path: Path) -> Iterable[tuple[str, str, str, int]]:
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            line = raw_line.rstrip("\r\n")
            if not line:
                continue
            key = _extract_key(line)
            if key:
                yield key, line, path.name, line_no


def rebuild_index(data_dir: Path, db_path: Path, batch_size: int = 10_000) -> RebuildResult:
    ensure_dirs(data_dir, db_path, db_path.parent)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    temp_file = tempfile.NamedTemporaryFile(
        prefix=f"{db_path.stem}.", suffix=".rebuild", dir=db_path.parent
    )
    temp_path = Path(temp_file.name)
    temp_file.close()

    count = 0
    indexed_files = 0
    skipped_duplicate_files = 0
    seen_hashes: set[tuple[int, str]] = set()
    try:
        with closing(_connect(temp_path)) as conn:
            conn.execute("DROP TABLE IF EXISTS records")
            conn.execute(
                """
                CREATE TABLE records (
                    id INTEGER PRIMARY KEY,
                    line_key TEXT NOT NULL,
                    line TEXT NOT NULL,
                    source_file TEXT NOT NULL,
                    line_no INTEGER NOT NULL
                )
                """
            )

            batch: list[tuple[str, str, str, int]] = []
            for path in data_files(data_dir):
                fingerprint = fingerprint_file(path)
                file_identity = (fingerprint.size, fingerprint.sha256)
                if file_identity in seen_hashes:
                    skipped_duplicate_files += 1
                    continue
                seen_hashes.add(file_identity)
                indexed_files += 1

                for record in _iter_file_records(path):
                    batch.append(record)
                    if len(batch) >= batch_size:
                        conn.executemany(
                            """
                            INSERT INTO records
                            (line_key, line, source_file, line_no)
                            VALUES (?, ?, ?, ?)
                            """,
                            batch,
                        )
                        count += len(batch)
                        batch.clear()

            if batch:
                conn.executemany(
                    """
                    INSERT INTO records (line_key, line, source_file, line_no)
                    VALUES (?, ?, ?, ?)
                    """,
                    batch,
                )
                count += len(batch)

            conn.execute("CREATE INDEX idx_records_line_key ON records(line_key)")
            conn.execute("ANALYZE")
            conn.commit()

        db_path.unlink(missing_ok=True)
        temp_path.replace(db_path)
        return RebuildResult(count, indexed_files, skipped_duplicate_files)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def next_prefix(prefix: str) -> str | None:
    if prefix == "":
        return None
    chars = list(prefix)
    for index in range(len(chars) - 1, -1, -1):
        codepoint = ord(chars[index])
        if codepoint < 0x10FFFF:
            chars[index] = chr(codepoint + 1)
            return "".join(chars[: index + 1])
    return None


def search_to_file(
    db_path: Path,
    prefix: str,
    output_path: Path,
    max_results: int = 0,
    max_bytes: int = 0,
    header: str = "",
) -> SearchResult:
    upper = next_prefix(prefix)
    count = 0
    bytes_written = 0
    truncated_by_results = False
    truncated_by_size = False

    with closing(_connect(db_path)) as conn, output_path.open(
        "w", encoding="utf-8", newline="\n"
    ) as output:
        if header:
            output.write(header.rstrip("\n"))
            output.write("\n\n")

        if upper is None:
            cursor = conn.execute(
                """
                SELECT line FROM records
                WHERE line_key >= ?
                ORDER BY line_key, id
                """,
                (prefix,),
            )
        else:
            cursor = conn.execute(
                """
                SELECT line FROM records
                WHERE line_key >= ? AND line_key < ?
                ORDER BY line_key, id
                """,
                (prefix, upper),
            )

        for (line,) in cursor:
            line_bytes = len(line.encode("utf-8")) + 1
            if max_bytes and count > 0 and bytes_written + line_bytes > max_bytes:
                truncated_by_size = True
                break
            output.write(line)
            output.write("\n")
            count += 1
            bytes_written += line_bytes
            if max_results and count >= max_results:
                truncated_by_results = True
                break

    return SearchResult(count, truncated_by_results, truncated_by_size)


def stats(db_path: Path) -> tuple[int, int]:
    if not db_path.exists():
        return 0, 0
    with closing(_connect(db_path)) as conn:
        rows = conn.execute("SELECT COUNT(*), COUNT(DISTINCT source_file) FROM records")
        total, files = rows.fetchone()
        return int(total), int(files)
