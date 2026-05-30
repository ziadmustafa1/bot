# Production Architecture

## Goals

- Search all supported files inside `data` without depending on file names.
- Keep Telegram handlers lightweight and move heavy work away from the event loop.
- Serve multiple users with bounded concurrency and queue limits.
- Keep access control separate from the data index.
- Make future migration to PostgreSQL, ClickHouse, or sharded files possible without changing the bot commands.

## Current Components

```text
Telegram User
    |
    v
Telegram Bot API
    |
    v
bot.py
    |-- access_control.py -> index/auth.sqlite3
    |-- database.py       -> index/data.sqlite3
    |-- data/             -> raw imported files
    |-- tmp/              -> temporary result files
```

## Request Flow

1. User sends BIN.
2. Bot checks access:
   - admins in `ADMIN_IDS`, or
   - active redeemed code in `auth.sqlite3`.
3. Bot validates BIN length and format.
4. Search request enters a bounded queue.
5. A worker queries `data.sqlite3` using an indexed range:
   - `line_key >= BIN`
   - `line_key < next_BIN`
6. Results stream to a temporary TXT file.
7. Bot sends the TXT file.
8. Temporary file is deleted.

## Indexing Flow

1. Admin puts files in `data`.
2. Admin runs `/sync` or `.\build_index.ps1`.
3. The indexer scans every supported file:
   - `.txt`
   - `.csv`
   - `.log`
   - `.dat`
4. Data is read line by line in batches.
5. File fingerprints are calculated with SHA256.
6. Duplicate files are skipped even if their names are different.
7. A new SQLite database is built in a temporary file.
8. The old index is atomically replaced only after success.

This keeps searches using the previous index while a rebuild is prepared.

## Pressure Controls

Configured in `.env`:

```env
SEARCH_CONCURRENCY=4
SEARCH_QUEUE_LIMIT=20
USER_COOLDOWN_SECONDS=3
MAX_RESULT_FILE_MB=45
MIN_PREFIX_LENGTH=6
```

- `SEARCH_CONCURRENCY`: active searches at the same time.
- `SEARCH_QUEUE_LIMIT`: waiting searches before rejecting new requests.
- `USER_COOLDOWN_SECONDS`: protects against repeated requests from one user.
- `MAX_RESULT_FILE_MB`: avoids creating files Telegram cannot send reliably.
- `MIN_PREFIX_LENGTH`: avoids accidental broad scans.

## Scaling Path

### Stage 1: Current RDP/VPS

- SQLite index on SSD.
- Good for millions to tens of millions of rows if result sizes are controlled.
- Keep `SEARCH_CONCURRENCY` near CPU core count or lower.

### Stage 2: Larger VPS

- Put `data`, `index`, and `tmp` on SSD/NVMe.
- Use Task Scheduler or NSSM to run 24/7.
- Add daily backup for `index/auth.sqlite3`.

### Stage 3: Very Large Data

When the dataset grows beyond comfortable SQLite limits:

- Option A: ClickHouse for high-volume indexed reads.
- Option B: PostgreSQL with partitioning and `text_pattern_ops`.
- Option C: sharded prefix files if searches are only BIN-prefix based.

The bot layer should keep the same command surface. Only the search backend changes.

## Operational Rules

- Never store secrets in `.env.example`.
- Never commit `.env`, `data`, `index`, or `tmp`.
- Rebuild the index after adding files.
- Use `/sync` from admin chat after copying files to `data`.
- Watch disk usage before large rebuilds; rebuilding needs free space for a second index copy.

## Required Next Refactor

The current implementation is functional but `bot.py` is too large for long-term maintenance. The next code-architecture step should split it into:

```text
app/
  main.py
  handlers/
    admin.py
    access.py
    search.py
  services/
    access_service.py
    search_service.py
    index_service.py
  infra/
    sqlite_access.py
    sqlite_search.py
```

That split keeps Telegram-specific code separate from business logic and storage.
