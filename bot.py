from __future__ import annotations

import argparse
import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Any

from access_control import (
    create_code,
    expire_access,
    get_access_expiry,
    grant_access,
    has_active_access,
    init_access_db,
    list_recent_codes,
    redeem_code,
    revoke_code,
)
from config import Settings, get_settings
from database import ensure_dirs, find_duplicate_file, rebuild_index, search_to_file, stats


logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

SAFE_PREFIX = re.compile(r"^[^\s]{1,128}$")
UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9_.-]+")
DURATION = re.compile(r"^(\d+)(m|h|d)$", re.IGNORECASE)


def is_admin(settings: Settings, user_id: int | None) -> bool:
    return user_id is not None and user_id in settings.admin_ids


def clean_prefix(text: str) -> str:
    text = text.strip()
    if text.startswith("/search"):
        text = text.removeprefix("/search").strip()
    return text


def result_filename(prefix: str) -> str:
    safe = UNSAFE_FILENAME.sub("_", prefix).strip("._")[:80]
    return f"bin_results_{safe or 'bin'}.txt"


def parse_duration(value: str) -> int | None:
    match = DURATION.match(value.strip())
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    seconds = amount * {"m": 60, "h": 3600, "d": 86400}[unit]
    if seconds <= 0 or seconds > 31 * 86400:
        return None
    return seconds


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, _ = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


def user_can_search(settings: Settings, user_id: int | None) -> bool:
    if user_id is None:
        return False
    return is_admin(settings, user_id) or has_active_access(settings.auth_db_path, user_id)


async def acquire_search_slot(context: Any, settings: Settings) -> tuple[bool, int]:
    semaphore: asyncio.Semaphore = context.application.bot_data["search_semaphore"]
    queue_lock: asyncio.Lock = context.application.bot_data["search_queue_lock"]

    async with queue_lock:
        waiting = context.application.bot_data["search_waiting"]
        if semaphore.locked() and waiting >= settings.search_queue_limit:
            return False, waiting
        if semaphore.locked():
            context.application.bot_data["search_waiting"] = waiting + 1
            position = waiting + 1
        else:
            position = 0

    await semaphore.acquire()

    async with queue_lock:
        if position:
            context.application.bot_data["search_waiting"] = max(
                0,
                context.application.bot_data["search_waiting"] - 1,
            )

    return True, position


def release_search_slot(context: Any) -> None:
    semaphore: asyncio.Semaphore = context.application.bot_data["search_semaphore"]
    semaphore.release()


async def check_user_cooldown(update: Any, context: Any, settings: Settings) -> bool:
    user_id = update.effective_user.id if update.effective_user else None
    if user_id is None or is_admin(settings, user_id) or settings.user_cooldown_seconds <= 0:
        return True

    now = time.monotonic()
    cooldowns: dict[int, float] = context.application.bot_data["user_cooldowns"]
    last = cooldowns.get(user_id, 0)
    remaining = settings.user_cooldown_seconds - (now - last)
    if remaining > 0:
        await update.message.reply_text(f"Wait {remaining:.1f}s before another search.")
        return False

    cooldowns[user_id] = now
    return True


def admin_denied_message(settings: Settings) -> str:
    if not settings.admin_ids:
        return (
            "No admin is configured yet.\n"
            "Send /id, copy your Telegram ID, then put it in ADMIN_IDS inside .env and restart the bot."
        )
    return "Admin only."


def support_line(settings: Settings) -> str:
    if not settings.support_username:
        return ""
    return f"\nSupport: {settings.support_username}"


async def start(update: Any, context: Any) -> None:
    settings: Settings = context.application.bot_data["settings"]
    await update.message.reply_text(
        "BIN Search Bot\n\n"
        "Send BIN\n\n"
        "Access:\n"
        "/redeem CODE\n"
        "/myaccess\n\n"
        "Use /help for commands."
        f"{support_line(settings)}"
    )


async def show_id(update: Any, context: Any) -> None:
    user_id = update.effective_user.id if update.effective_user else None
    await update.message.reply_text(f"Your Telegram ID: {user_id}")


async def help_command(update: Any, context: Any) -> None:
    settings: Settings = context.application.bot_data["settings"]
    user_id = update.effective_user.id if update.effective_user else None
    lines = [
        "Commands:",
        "Send BIN - search data",
        "/redeem CODE - activate access",
        "/myaccess - show remaining time",
        "/id - show your Telegram ID",
        "/stats - show index stats",
    ]
    if settings.support_username:
        lines.append(f"Support: {settings.support_username}")
    if is_admin(settings, user_id):
        lines.extend(
            [
                "",
                "Admin:",
                "/code 2h - create an access code",
                "/grant USER_ID 2h - grant access directly",
                "/expire USER_ID - remove user access",
                "/codes - list recent codes",
                "/revoke CODE - revoke unused code",
                "/rebuild - rebuild data index",
                "/sync - scan data folder and rebuild index",
            ]
        )
    await update.message.reply_text("\n".join(lines))


async def admin_panel(update: Any, context: Any) -> None:
    settings: Settings = context.application.bot_data["settings"]
    user_id = update.effective_user.id if update.effective_user else None
    if not is_admin(settings, user_id):
        await update.message.reply_text(admin_denied_message(settings))
        return

    total, files = await asyncio.to_thread(stats, settings.db_path)
    await update.message.reply_text(
        "Admin panel\n"
        f"Indexed lines: {total:,}\n"
        f"Files: {files:,}\n"
        f"Min BIN length: {settings.min_prefix_length}\n"
        f"Max result file: {settings.max_result_file_mb}MB\n"
        f"Search workers: {settings.search_concurrency}\n"
        f"Queue limit: {settings.search_queue_limit}\n\n"
        "/code 2h\n/grant USER_ID 2h\n/expire USER_ID\n/codes\n/sync"
    )


async def show_stats(update: Any, context: Any) -> None:
    settings: Settings = context.application.bot_data["settings"]
    total, files = await asyncio.to_thread(stats, settings.db_path)
    await update.message.reply_text(f"Indexed lines: {total:,}\nFiles: {files:,}")


async def create_access_code(update: Any, context: Any) -> None:
    settings: Settings = context.application.bot_data["settings"]
    user_id = update.effective_user.id if update.effective_user else None
    if not is_admin(settings, user_id):
        await update.message.reply_text(admin_denied_message(settings))
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /code 2h\nExamples: /code 30m, /code 1h, /code 2h, /code 1d"
        )
        return

    seconds = parse_duration(context.args[0])
    if seconds is None:
        await update.message.reply_text("Invalid duration. Use m, h, or d. Example: /code 2h")
        return

    code = await asyncio.to_thread(create_code, settings.auth_db_path, seconds, user_id)
    await update.message.reply_text(
        f"Access code:\n{code}\n\n"
        f"Duration: {format_duration(seconds)}\n"
        "User activates it with:\n"
        f"/redeem {code}"
    )


async def redeem_access_code(update: Any, context: Any) -> None:
    settings: Settings = context.application.bot_data["settings"]
    user_id = update.effective_user.id if update.effective_user else None
    if user_id is None:
        await update.message.reply_text("Could not identify user.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /redeem CODE")
        return

    ok, reason, expires_at = await asyncio.to_thread(
        redeem_code,
        settings.auth_db_path,
        context.args[0],
        user_id,
    )
    if not ok:
        messages = {
            "code_not_found": "Code not found.",
            "code_revoked": "This code was revoked.",
            "code_used": "This code was already used.",
        }
        await update.message.reply_text(messages.get(reason, "Could not redeem code."))
        return

    remaining = int(expires_at - time.time()) if expires_at else 0
    await update.message.reply_text(f"Access activated. Remaining: {format_duration(remaining)}")


async def grant_user_access(update: Any, context: Any) -> None:
    settings: Settings = context.application.bot_data["settings"]
    admin_id = update.effective_user.id if update.effective_user else None
    if not is_admin(settings, admin_id):
        await update.message.reply_text(admin_denied_message(settings))
        return
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /grant USER_ID 2h")
        return

    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("USER_ID must be a number.")
        return

    seconds = parse_duration(context.args[1])
    if seconds is None:
        await update.message.reply_text("Invalid duration. Example: /grant 123456789 2h")
        return

    expires_at = await asyncio.to_thread(grant_access, settings.auth_db_path, user_id, seconds)
    await update.message.reply_text(
        f"Granted access to {user_id}.\nRemaining: {format_duration(expires_at - int(time.time()))}"
    )


async def expire_user_access(update: Any, context: Any) -> None:
    settings: Settings = context.application.bot_data["settings"]
    admin_id = update.effective_user.id if update.effective_user else None
    if not is_admin(settings, admin_id):
        await update.message.reply_text(admin_denied_message(settings))
        return
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /expire USER_ID")
        return

    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("USER_ID must be a number.")
        return

    changed = await asyncio.to_thread(expire_access, settings.auth_db_path, user_id)
    await update.message.reply_text("Access expired." if changed else "User has no access record.")


async def configure_bot_commands(application: Any) -> None:
    from telegram import BotCommand

    await application.bot.set_my_commands(
        [
            BotCommand("start", "Start the bot"),
            BotCommand("help", "Show commands"),
            BotCommand("search", "Search by BIN"),
            BotCommand("redeem", "Activate access code"),
            BotCommand("myaccess", "Show remaining access"),
            BotCommand("id", "Show your Telegram ID"),
            BotCommand("stats", "Show index stats"),
            BotCommand("admin", "Admin panel"),
            BotCommand("sync", "Scan data folder"),
        ]
    )


async def my_access(update: Any, context: Any) -> None:
    settings: Settings = context.application.bot_data["settings"]
    user_id = update.effective_user.id if update.effective_user else None
    if user_id is None:
        await update.message.reply_text("Could not identify user.")
        return
    if is_admin(settings, user_id):
        await update.message.reply_text("You are admin. Access is always active.")
        return

    expires_at = await asyncio.to_thread(get_access_expiry, settings.auth_db_path, user_id)
    if not expires_at or expires_at <= int(time.time()):
        await update.message.reply_text("No active access. Send /redeem CODE")
        return
    await update.message.reply_text(f"Remaining: {format_duration(expires_at - int(time.time()))}")


async def list_codes(update: Any, context: Any) -> None:
    settings: Settings = context.application.bot_data["settings"]
    user_id = update.effective_user.id if update.effective_user else None
    if not is_admin(settings, user_id):
        await update.message.reply_text(admin_denied_message(settings))
        return

    rows = await asyncio.to_thread(list_recent_codes, settings.auth_db_path, 10)
    if not rows:
        await update.message.reply_text("No codes yet.")
        return

    lines = []
    for code, duration, created_at, used_by, revoked_at in rows:
        if revoked_at:
            status = "revoked"
        elif used_by:
            status = f"used by {used_by}"
        else:
            status = "new"
        lines.append(f"{code} - {format_duration(duration)} - {status}")
    await update.message.reply_text("\n".join(lines))


async def revoke_access_code(update: Any, context: Any) -> None:
    settings: Settings = context.application.bot_data["settings"]
    user_id = update.effective_user.id if update.effective_user else None
    if not is_admin(settings, user_id):
        await update.message.reply_text(admin_denied_message(settings))
        return
    if not context.args:
        await update.message.reply_text("Usage: /revoke CODE")
        return

    revoked = await asyncio.to_thread(revoke_code, settings.auth_db_path, context.args[0])
    await update.message.reply_text(
        "Code revoked." if revoked else "Code not found, already used, or already revoked."
    )


async def rebuild(update: Any, context: Any) -> None:
    settings: Settings = context.application.bot_data["settings"]
    user_id = update.effective_user.id if update.effective_user else None
    if not is_admin(settings, user_id):
        await update.message.reply_text(admin_denied_message(settings))
        return

    lock: asyncio.Lock = context.application.bot_data["rebuild_lock"]
    if lock.locked():
        await update.message.reply_text("Index rebuild is already running.")
        return

    async with lock:
        message = await update.message.reply_text("Scanning data folder and rebuilding index...")
        started = time.monotonic()
        try:
            result = await asyncio.to_thread(rebuild_index, settings.data_dir, settings.db_path)
        except Exception:
            logger.exception("Index rebuild failed")
            await message.edit_text("Index rebuild failed. Check server logs.")
            return

        elapsed = time.monotonic() - started
        await message.edit_text(
            "Index rebuilt successfully.\n"
            f"Lines: {result.indexed_lines:,}\n"
            f"Files: {result.indexed_files:,}\n"
            f"Duplicate files skipped: {result.skipped_duplicate_files:,}\n"
            f"Time: {elapsed:.1f}s"
        )


async def handle_document(update: Any, context: Any) -> None:
    from telegram.constants import ChatAction

    settings: Settings = context.application.bot_data["settings"]
    user_id = update.effective_user.id if update.effective_user else None
    if not is_admin(settings, user_id):
        await update.message.reply_text(admin_denied_message(settings))
        return

    document = update.message.document
    if document is None or not document.file_name:
        await update.message.reply_text("Send a valid text file.")
        return
    if (
        not settings.local_bot_api_url
        and document.file_size
        and document.file_size > settings.max_telegram_download_mb * 1024 * 1024
    ):
        await update.message.reply_text(
            f"File is too big for Telegram Bot API download.\n"
            f"Max upload through bot: {settings.max_telegram_download_mb}MB\n"
            "Use a local Bot API server or upload it directly to the server data folder, then use /sync."
        )
        return

    suffix = Path(document.file_name).suffix.lower()
    if suffix not in {".txt", ".csv", ".log", ".dat"}:
        await update.message.reply_text("Supported files: txt, csv, log, dat")
        return

    safe_name = Path(document.file_name).name
    target = settings.data_dir / safe_name
    try:
        telegram_file = await document.get_file()
        await update.message.chat.send_action(ChatAction.UPLOAD_DOCUMENT)
        await telegram_file.download_to_drive(custom_path=target)
    except Exception as exc:
        logger.exception("Document download failed")
        hint = "Check server logs."
        if not settings.local_bot_api_url:
            hint = "If it is larger than 20MB, configure LOCAL_BOT_API_URL or upload it directly to data and use /sync."
        await update.message.reply_text(f"Could not download this file from Telegram.\n{hint}")
        return
    duplicate = await asyncio.to_thread(find_duplicate_file, settings.data_dir, target)
    if duplicate is not None:
        target.unlink(missing_ok=True)
        await update.message.reply_text(
            f"Duplicate file skipped.\nAlready exists as: {duplicate.name}"
        )
        return
    await update.message.reply_text(f"Saved file: {safe_name}\nRebuilding index now.")
    await rebuild(update, context)


async def search(update: Any, context: Any) -> None:
    from telegram.constants import ChatAction

    settings: Settings = context.application.bot_data["settings"]
    user_id = update.effective_user.id if update.effective_user else None

    if not await asyncio.to_thread(user_can_search, settings, user_id):
        await update.message.reply_text(
            "No active access. Send /redeem CODE"
            f"{support_line(settings)}"
        )
        return
    if not await check_user_cooldown(update, context, settings):
        return

    text = update.message.text or ""
    prefix = " ".join(context.args).strip() if context.args else clean_prefix(text)
    if not prefix or not SAFE_PREFIX.match(prefix):
        await update.message.reply_text("Send BIN")
        return
    if len(prefix) < settings.min_prefix_length:
        await update.message.reply_text(
            f"BIN is too short. Minimum length is {settings.min_prefix_length}."
        )
        return

    if not settings.db_path.exists():
        await update.message.reply_text("No index yet. Add data files and use /rebuild.")
        return

    accepted, position = await acquire_search_slot(context, settings)
    if not accepted:
        await update.message.reply_text("Server is busy. Try again in a moment.")
        return

    if position:
        await update.message.reply_text(f"Request queued. Position: {position}")

    try:
        await update.message.chat.send_action(ChatAction.TYPING)
        output_path = settings.tmp_dir / f"result_{update.effective_chat.id}_{int(time.time() * 1000)}.txt"
        started = time.monotonic()
        try:
            count = await asyncio.to_thread(
                search_to_file,
                settings.db_path,
                prefix,
                output_path,
                settings.max_results_per_query,
                settings.max_result_file_mb * 1024 * 1024,
            )

            if count.count == 0:
                output_path.unlink(missing_ok=True)
                await update.message.reply_text("No matching results.")
                return

            elapsed = time.monotonic() - started
            caption = f"Results: {count.count:,}\nTime: {elapsed:.2f}s"
            if count.truncated_by_results:
                caption += f"\nReached limit: {settings.max_results_per_query:,}"
            if count.truncated_by_size:
                caption += f"\nFile stopped at {settings.max_result_file_mb}MB limit."

            await update.message.chat.send_action(ChatAction.UPLOAD_DOCUMENT)
            with output_path.open("rb") as handle:
                await update.message.reply_document(
                    document=handle,
                    filename=result_filename(prefix),
                    caption=caption,
                )
        except Exception:
            logger.exception("Search failed")
            await update.message.reply_text("Search failed. Check server logs.")
        finally:
            output_path.unlink(missing_ok=True)
    finally:
        release_search_slot(context)


def create_application(settings: Settings) -> Any:
    from telegram.ext import Application, CommandHandler, MessageHandler, filters

    ensure_dirs(settings.data_dir, settings.db_path, settings.tmp_dir)
    init_access_db(settings.auth_db_path)
    if not settings.admin_ids:
        logger.warning("ADMIN_IDS is empty. Admin commands are disabled until you set it in .env.")
    builder = (
        Application.builder()
        .token(settings.bot_token)
        .concurrent_updates(True)
        .post_init(configure_bot_commands)
    )
    if settings.local_bot_api_url:
        builder = (
            builder.base_url(f"{settings.local_bot_api_url}/bot")
            .base_file_url(f"{settings.local_bot_api_url}/file/bot")
            .local_mode(True)
        )
    app = builder.build()
    app.bot_data["settings"] = settings
    app.bot_data["search_semaphore"] = asyncio.Semaphore(settings.search_concurrency)
    app.bot_data["search_queue_lock"] = asyncio.Lock()
    app.bot_data["search_waiting"] = 0
    app.bot_data["user_cooldowns"] = {}
    app.bot_data["rebuild_lock"] = asyncio.Lock()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("id", show_id))
    app.add_handler(CommandHandler("stats", show_stats))
    app.add_handler(CommandHandler("rebuild", rebuild))
    app.add_handler(CommandHandler("sync", rebuild))
    app.add_handler(CommandHandler("search", search))
    app.add_handler(CommandHandler("code", create_access_code))
    app.add_handler(CommandHandler("grant", grant_user_access))
    app.add_handler(CommandHandler("expire", expire_user_access))
    app.add_handler(CommandHandler("redeem", redeem_access_code))
    app.add_handler(CommandHandler("myaccess", my_access))
    app.add_handler(CommandHandler("codes", list_codes))
    app.add_handler(CommandHandler("revoke", revoke_access_code))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search))
    return app


def run_bot(settings: Settings) -> None:
    from telegram import Update

    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN is missing. Copy .env.example to .env and set BOT_TOKEN.")
    asyncio.set_event_loop(asyncio.new_event_loop())
    app = create_application(settings)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


def main() -> None:
    parser = argparse.ArgumentParser(description="Telegram BIN search bot")
    parser.add_argument("command", choices=["run", "build-index", "stats"])
    args = parser.parse_args()

    settings = get_settings()
    ensure_dirs(settings.data_dir, settings.db_path, settings.tmp_dir)

    if args.command == "run":
        run_bot(settings)
    elif args.command == "build-index":
        result = rebuild_index(settings.data_dir, settings.db_path)
        print(f"Indexed {result.indexed_lines:,} lines")
        print(f"Files: {result.indexed_files:,}")
        print(f"Duplicate files skipped: {result.skipped_duplicate_files:,}")
    elif args.command == "stats":
        total, files = stats(settings.db_path)
        print(f"Indexed lines: {total:,}")
        print(f"Files: {files:,}")


if __name__ == "__main__":
    main()
