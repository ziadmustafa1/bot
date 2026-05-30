# Telegram BIN Search Bot

Fast Telegram bot for searching huge text data by BIN.

## Quick Start on Windows RDP

```powershell
cd E:\bot
.\install.ps1
notepad .env
```

Set:

```env
BOT_TOKEN=your_token
SUPPORT_USERNAME=@I_INW
LOCAL_BOT_API_URL=
BIN_LOOKUP_URL=http://bins.antipublic.cc/bins
ADMIN_IDS=your_telegram_id
```

Put data files in:

```text
E:\bot\data
```

Build the index:

```powershell
.\build_index.ps1
```

Run the bot:

```powershell
.\run_bot.ps1
```

On Linux or hosting platforms that expect `main.py`:

```bash
python main.py
```

Check health:

```powershell
.\health_check.ps1
```

## Important Setup

1. Start the bot once.
2. Send `/id` to get your Telegram ID.
3. Put that ID in `.env` as `ADMIN_IDS`.
4. Restart the bot.

Without `ADMIN_IDS`, admin commands are disabled.

## User Commands

- `/start` - basic instructions.
- `/help` - show commands.
- Send BIN - search data.
- `/redeem CODE` - activate an access code.
- `/myaccess` - show remaining access time.
- `/id` - show your Telegram ID.

## Admin Commands

- `/admin` - admin panel.
- `/code 2h` - create an access code for two hours.
- `/code 30m` - create an access code for 30 minutes.
- `/code 1d` - create an access code for one day.
- `/grant USER_ID 2h` - grant access directly.
- `/expire USER_ID` - expire a user's access.
- `/codes` - list recent codes.
- `/revoke CODE` - revoke an unused code.
- `/rebuild` - rebuild the search index from `data`.
- `/sync` - scan the `data` folder and rebuild the search index.
- `/stats` - show index stats.

## Limits

These values are in `.env`:

```env
MIN_PREFIX_LENGTH=6
MAX_RESULT_FILE_MB=45
MAX_RESULTS_PER_QUERY=0
SEARCH_CONCURRENCY=4
SEARCH_QUEUE_LIMIT=20
USER_COOLDOWN_SECONDS=3
MAX_TELEGRAM_DOWNLOAD_MB=2000
REDACT_CARD_FIELDS=1
```

- `MIN_PREFIX_LENGTH` prevents huge accidental searches with very short BINs.
- `MAX_RESULT_FILE_MB` keeps result files under Telegram upload limits.
- `MAX_RESULTS_PER_QUERY=0` means no count limit.
- `SEARCH_CONCURRENCY` limits simultaneous searches.
- `SEARCH_QUEUE_LIMIT` limits how many searches can wait when all workers are busy.
- `USER_COOLDOWN_SECONDS` slows repeated searches from the same user.
- `MAX_TELEGRAM_DOWNLOAD_MB` rejects oversized Telegram uploads before download when not using a local Bot API server.
- `REDACT_CARD_FIELDS=1` removes card number, CVV, and all extra personal fields from result files.

## Large Telegram Uploads

The public Telegram Bot API can download files up to 20MB. To receive larger files through the bot, run a local Bot API server and set:

```env
LOCAL_BOT_API_URL=http://127.0.0.1:8081
MAX_TELEGRAM_DOWNLOAD_MB=2000
```

## Notes

- Do not put huge data files in Git.
- Do not upload huge data through Telegram Bot API; the normal Bot API has small download limits.
- For very large datasets, keep the bot on a VPS/RDP with SSD storage or move the index to a real database service.
- The bot scans all supported files inside `data`; it is not tied to any specific file name.
- Duplicate files are detected by SHA256 hash and skipped during indexing.
- If the same file is uploaded through Telegram again, the new copy is removed.
- Files with the same name are saved with a unique suffix instead of overwriting older files.
- BIN metadata is fetched from `BIN_LOOKUP_URL` and added to the top of result files.

## Production Docs

- See `ARCHITECTURE.md` for system design and scaling path.
- See `OPERATIONS.md` for deployment and failure handling.
