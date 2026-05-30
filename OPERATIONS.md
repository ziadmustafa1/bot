# Operations Runbook

## Install

```powershell
cd E:\bot
.\install.ps1
notepad .env
```

Set:

```env
BOT_TOKEN=your_token
ADMIN_IDS=your_telegram_id
```

## Build or Refresh Data

Copy files into:

```text
E:\bot\data
```

Then run one of:

```powershell
.\build_index.ps1
```

or from Telegram admin chat:

```text
/sync
```

## Run

```powershell
.\run_bot.ps1
```

## Health Check

```powershell
.\health_check.ps1
```

The command checks:

- bot token exists
- admin IDs are configured
- supported data files exist
- index has rows
- free disk space

## Recommended Windows 24/7 Setup

Use Task Scheduler:

- Program: `powershell.exe`
- Arguments:

```powershell
-ExecutionPolicy Bypass -File "E:\bot\run_bot.ps1"
```

- Start in:

```text
E:\bot
```

Enable:

- Run whether user is logged on or not.
- Restart on failure.
- Start at system startup.

## Load Tuning

For small VPS/RDP:

```env
SEARCH_CONCURRENCY=2
SEARCH_QUEUE_LIMIT=10
USER_COOLDOWN_SECONDS=5
```

For stronger VPS:

```env
SEARCH_CONCURRENCY=6
SEARCH_QUEUE_LIMIT=50
USER_COOLDOWN_SECONDS=2
```

## Disk Checklist

Before `/sync`, make sure free disk is at least:

```text
current index size + new expected index size + tmp result space
```

Rebuild creates a new database first, then replaces the old one after success.

## Failure Handling

- If bot does not start: check `BOT_TOKEN` and Python dependencies.
- If admin commands say no admin: set `ADMIN_IDS` in `.env` and restart.
- If search says no index: run `.\build_index.ps1` or `/sync`.
- If result is truncated: increase `MAX_RESULT_FILE_MB` only if Telegram and disk can handle it.
- If server is busy: increase server resources or tune `SEARCH_CONCURRENCY` and `SEARCH_QUEUE_LIMIT`.
