#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot.py - Multi-user Python / Node.js script hosting and management bot for Telegram.

Single-file, production-oriented rewrite. Nothing is hardcoded: every secret,
identifier and channel is read from the environment.

REQUIRED ENVIRONMENT VARIABLES
    BOT_TOKEN                Telegram bot token from @BotFather.
    OWNER_ID                 Numeric Telegram user id of the owner.

OPTIONAL ENVIRONMENT VARIABLES
    ADMIN_IDS                Extra admins, comma/space separated numeric ids.
    REQUIRED_CHANNELS        Force-join channels, e.g. "@channel1,@channel2".
                             Empty value disables mandatory channel membership.
    OWNER_USERNAME           Public username for the "Contact Owner" button (no @ needed).
    UPDATES_CHANNEL_URL      Link used by the "Updates Channel" button.
    DATA_DIR                 Root for data (default: directory of this file).
    FREE_USER_LIMIT          Files per free user (default 2).
    PREMIUM_USER_LIMIT       Files per subscribed user (default 15).
    ADMIN_FILE_LIMIT         Files per admin (default 99). Owner is unlimited.
    MAX_UPLOAD_MB            Max single upload size in MB (default 20).
    USER_DISK_QUOTA_MB       Max disk per user folder in MB (default 200).
    MAX_ZIP_UNCOMPRESSED_MB  Max uncompressed zip payload (default 60).
    SCRIPT_MEMORY_MB         Address-space cap per hosted script (default 512, POSIX).
    ALLOW_AUTO_PACKAGE_INSTALL  "1" (default) to auto-install known missing modules.
    EXTRA_ALLOWED_PACKAGES   Extra pip package names admins may install.
    VERIFY_TTL_HOURS         Re-check channel membership after N hours (default 12).
    SAMBA_API_KEY            SambaNova API key. AI features are disabled if unset.
    SAMBA_URL                Override the SambaNova chat-completions endpoint.
    AI_DEFAULT_MODEL         One of the keys in AVAILABLE_MODELS (default "llama").
    HEALTH_PORT / PORT       If set, expose a plain-text health endpoint.
    LOG_LEVEL                DEBUG / INFO / WARNING / ERROR (default INFO).

RUN
    pip install pyTelegramBotAPI psutil requests
    export BOT_TOKEN="123:ABC" OWNER_ID="123456789"
    python bot.py

SECURITY NOTES (deliberate, documented defaults)
  * Hosted scripts are arbitrary user code. They are admin-approved before they
    ever run, started in their own process session/group, capped in memory and
    process count on POSIX, and launched with a sanitised environment so the bot
    token and API keys are never visible to them. Run this bot as an
    unprivileged user, ideally inside a container.
  * Installing packages mutates the bot's own interpreter, so package
    installation is admin/owner only. Regular users get a request flow that
    notifies admins instead.
  * Only plain package names (optionally with a pinned version) are accepted.
    URLs, VCS specs, local paths and pip flags are rejected.
  * npm dependencies are installed with --ignore-scripts to block lifecycle
    script execution.
  * Zip archives are checked for path traversal, absolute paths, symlinks,
    member count and uncompressed size before extraction.
  * TLS verification is always on and warnings are never suppressed.
  * Users never receive stack traces, API payloads or server paths.
"""

from __future__ import annotations

import atexit
import contextlib
import io
import json
import logging
import math
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple

import psutil
import requests
import telebot
from telebot import types
from telebot.apihelper import ApiTelegramException

try:  # POSIX only; used to cap resources of hosted scripts.
    import resource as _resource
except ImportError:  # pragma: no cover - Windows
    _resource = None


# =============================================================================
# 1. CONFIGURATION
# =============================================================================

def _env_str(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else default


def _env_int(name: str, default: int) -> int:
    raw = _env_str(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(f"Configuration error: {name} must be an integer (got {raw!r}).")


def _env_bool(name: str, default: bool) -> bool:
    raw = _env_str(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on", "y")


def _env_id_set(name: str) -> set:
    ids = set()
    for part in re.split(r"[,\s;]+", _env_str(name)):
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            raise SystemExit(f"Configuration error: {name} must contain numeric ids only (got {part!r}).")
    return ids


def _env_channel_list(name: str) -> List[str]:
    channels: List[str] = []
    for part in re.split(r"[,\s;]+", _env_str(name)):
        if not part:
            continue
        if part.startswith("https://t.me/"):
            part = "@" + part.rsplit("/", 1)[-1]
        if not part.startswith("@") and not part.startswith("-100"):
            part = "@" + part
        channels.append(part)
    return channels


BOT_TOKEN = _env_str("BOT_TOKEN") or _env_str("TOKEN")
if not BOT_TOKEN:
    raise SystemExit("Configuration error: BOT_TOKEN is not set. Export it before starting the bot.")

OWNER_ID = _env_int("OWNER_ID", 0)
if OWNER_ID <= 0:
    raise SystemExit("Configuration error: OWNER_ID is not set to a valid numeric Telegram user id.")

BOOTSTRAP_ADMIN_IDS = _env_id_set("ADMIN_IDS") | {OWNER_ID}
REQUIRED_CHANNELS = _env_channel_list("REQUIRED_CHANNELS")

OWNER_USERNAME = _env_str("OWNER_USERNAME").lstrip("@")
OWNER_CONTACT_URL = f"https://t.me/{OWNER_USERNAME}" if OWNER_USERNAME else ""
UPDATES_CHANNEL_URL = _env_str("UPDATES_CHANNEL_URL")
if not UPDATES_CHANNEL_URL and REQUIRED_CHANNELS:
    _first = REQUIRED_CHANNELS[0]
    if _first.startswith("@"):
        UPDATES_CHANNEL_URL = f"https://t.me/{_first.lstrip('@')}"

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.abspath(_env_str("DATA_DIR", BASE_DIR))
UPLOAD_BOTS_DIR = os.path.join(DATA_DIR, "upload_bots")
DB_DIR = os.path.join(DATA_DIR, "inf")
DATABASE_PATH = os.path.join(DB_DIR, "bot_data.db")

FREE_USER_LIMIT = _env_int("FREE_USER_LIMIT", 2)
PREMIUM_USER_LIMIT = _env_int("PREMIUM_USER_LIMIT", 15)
ADMIN_FILE_LIMIT = _env_int("ADMIN_FILE_LIMIT", 99)
OWNER_FILE_LIMIT = math.inf

MAX_UPLOAD_BYTES = _env_int("MAX_UPLOAD_MB", 20) * 1024 * 1024
USER_DISK_QUOTA_BYTES = _env_int("USER_DISK_QUOTA_MB", 200) * 1024 * 1024
MAX_ZIP_UNCOMPRESSED_BYTES = _env_int("MAX_ZIP_UNCOMPRESSED_MB", 60) * 1024 * 1024
MAX_ZIP_MEMBERS = _env_int("MAX_ZIP_MEMBERS", 3000)
SCRIPT_MEMORY_BYTES = _env_int("SCRIPT_MEMORY_MB", 512) * 1024 * 1024
SCRIPT_MAX_PROCESSES = _env_int("SCRIPT_MAX_PROCESSES", 64)
LOG_MAX_BYTES = _env_int("SCRIPT_LOG_MAX_KB", 512) * 1024
LOG_PREVIEW_CHARS = 3500

ALLOW_AUTO_PACKAGE_INSTALL = _env_bool("ALLOW_AUTO_PACKAGE_INSTALL", True)
PIP_TIMEOUT_SECONDS = _env_int("PIP_TIMEOUT_SECONDS", 300)
NPM_TIMEOUT_SECONDS = _env_int("NPM_TIMEOUT_SECONDS", 300)
VERIFY_TTL_HOURS = _env_int("VERIFY_TTL_HOURS", 12)
ALLOWED_UPLOAD_EXTENSIONS = (".py", ".js", ".zip")

SAMBA_API_KEY = _env_str("SAMBA_API_KEY")
SAMBA_URL = _env_str("SAMBA_URL", "https://api.sambanova.ai/v1/chat/completions")
AI_ENABLED = bool(SAMBA_API_KEY)
AVAILABLE_MODELS: Dict[str, str] = {
    "llama": _env_str("AI_MODEL_LLAMA", "Meta-Llama-3.3-70B-Instruct"),
    "deepseek": _env_str("AI_MODEL_DEEPSEEK", "DeepSeek-V3"),
    "gpt-oss": _env_str("AI_MODEL_GPT_OSS", "gpt-oss-120b"),
}
AI_DEFAULT_MODEL = _env_str("AI_DEFAULT_MODEL", "llama")
if AI_DEFAULT_MODEL not in AVAILABLE_MODELS:
    AI_DEFAULT_MODEL = next(iter(AVAILABLE_MODELS))
AI_MAX_TOKENS = _env_int("AI_MAX_TOKENS", 700)
AI_TIMEOUT_SECONDS = _env_int("AI_TIMEOUT_SECONDS", 45)

BRAND_NAME = _env_str("BRAND_NAME", "Script Hosting")

os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
os.makedirs(DB_DIR, exist_ok=True)


# =============================================================================
# 2. LOGGING
# =============================================================================

class _RedactingFilter(logging.Filter):
    """Keeps secrets out of the log stream even if they reach a log call."""

    def __init__(self, secrets: List[str]) -> None:
        super().__init__()
        self._secrets = [s for s in secrets if s and len(s) > 6]

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = message
        for secret in self._secrets:
            redacted = redacted.replace(secret, "***REDACTED***")
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


logging.basicConfig(
    level=getattr(logging, _env_str("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)
logger = logging.getLogger("hosting-bot")
for _handler in logging.getLogger().handlers:
    _handler.addFilter(_RedactingFilter([BOT_TOKEN, SAMBA_API_KEY]))


# =============================================================================
# 3. USER-FACING TEXT (single place, ready for localization)
# =============================================================================

MSG: Dict[str, str] = {
    # Access / gating
    "banned": "🚫 You are banned from using this bot.",
    "bot_locked": "⚠️ The bot is temporarily locked by an administrator. Please try again later.",
    "admin_only": "⚠️ This action is available to administrators only.",
    "owner_only": "⚠️ This action is available to the owner only.",
    "permission_denied": "⚠️ You can only manage your own files.",
    "private_only": "ℹ️ Please use this bot in a private chat.",
    "unknown_action": "This action is no longer available.",
    "generic_error": "❌ Something went wrong. Please try again, or contact an administrator.",
    "cooldown": "⏳ Please wait {seconds}s before using this again.",
    "cancelled": "✅ Cancelled.",
    "nothing_to_cancel": "ℹ️ There is nothing to cancel.",
    # Channel verification
    "join_prompt": (
        "🔐 Join all required channels to unlock the bot.\n"
        "📢 Complete every join, then tap Verify.\n"
        "⚡ Access is granted immediately after a successful check."
    ),
    "verify_not_yours": "This verification button is not for you.",
    "verify_already": "✅ You are already verified.",
    "verify_ok": "✅ Verification successful. Send /start to begin.",
    "verify_missing": "❌ You have not joined all required channels yet. Please join them and tap Verify again.",
    "verify_unavailable": "⚠️ Membership check is temporarily unavailable. Please try again in a minute.",
    # Welcome / menu
    "welcome": (
        "👤 Welcome, {name}!\n"
        "🆔 User ID: {user_id}\n"
        "🎖 Access level: {level}\n"
        "📁 Files: {files}/{limit}\n\n"
        "⚡ Features:\n"
        "• Python, Node.js and ZIP hosting\n"
        "• Auto-recovery after a crash\n"
        "• Live logs and process control\n\n"
        "Use the menu below to navigate."
    ),
    "menu_hint": "ℹ️ Use the menu buttons or /help to see everything I can do.",
    "channels_title": "📢 Our channels:",
    "channels_none": "ℹ️ No channel link is configured.",
    "contact_title": "📞 Contact the owner:",
    "contact_none": "ℹ️ No owner contact is configured.",
    # Uploads
    "upload_prompt": "📤 Send your .py, .js or .zip file. It will be forwarded to the administrators for approval.",
    "upload_no_name": "⚠️ That file has no name. Please rename it and send it again.",
    "upload_bad_ext": "⚠️ Only .py, .js and .zip files are accepted.",
    "upload_bad_name": "⚠️ That file name is not allowed. Use letters, digits, dots, dashes and underscores only.",
    "upload_too_large": "⚠️ That file is too large. The limit is {limit_mb} MB.",
    "upload_limit_reached": "⚠️ You reached your file limit ({current}/{limit}). Delete a file first.",
    "upload_quota": "⚠️ Your storage quota is full ({used_mb} MB of {quota_mb} MB). Delete some files first.",
    "upload_duplicate": "⚠️ You already have a file named {file_name}. Delete it first or rename the new one.",
    "upload_pending_exists": "ℹ️ You already have {count} upload(s) waiting for approval.",
    "upload_submitted": "✅ {file_name} was submitted for approval. You will be notified once it is reviewed.",
    "upload_approved_user": "✅ Your file {file_name} was approved and is now running.",
    "upload_rejected_user": "❌ Your file {file_name} was not approved.\n\nPlease review your code and the bot rules, then submit again.",
    "upload_gone": "⚠️ This upload request no longer exists.",
    "approve_working": "⏳ Approving and starting…",
    "approve_done": "✅ Approved and started: {file_name}",
    "approve_failed": "❌ Could not process this upload. Details are in the server log.",
    "reject_done": "❌ Upload rejected.",
    "admin_new_upload": (
        "📥 New upload awaiting approval\n"
        "👤 User: {name} (@{username})\n"
        "🆔 User ID: {user_id}\n"
        "📄 File: {file_name}\n"
        "📏 Size: {size_kb} KB\n"
        "🆔 Upload ID: {upload_id}{extra}"
    ),
    "zip_no_script": "❌ No .py or .js entry point was found in that archive.",
    "zip_unsafe": "❌ That archive was rejected by the safety check.",
    "zip_too_big": "❌ The archive contents exceed the allowed size.",
    "deps_python_ok": "✅ Python dependencies installed.",
    "deps_python_fail": "❌ Python dependencies could not be installed.",
    "deps_node_ok": "✅ Node.js dependencies installed.",
    "deps_node_fail": "❌ Node.js dependencies could not be installed.",
    # Files / process control
    "files_empty": "📂 You have not uploaded any files yet.",
    "files_title": "📂 Your files:",
    "file_controls": "⚙️ {file_name} ({file_type})\nOwner: {owner_id}\nStatus: {status}",
    "file_missing": "⚠️ That file is missing on disk. Please upload it again.",
    "file_not_found": "⚠️ File not found.",
    "already_running": "ℹ️ That script is already running.",
    "not_running": "ℹ️ That script is not running.",
    "starting": "🟢 Starting {file_name}…",
    "started": "✅ {file_name} started.",
    "stopped": "⏹ {file_name} stopped.",
    "restarting": "🔄 Restarting {file_name}…",
    "deleted": "🗑️ {file_name} deleted.",
    "delete_confirm": "⚠️ Delete {file_name} and its logs permanently?",
    "logs_empty": "ℹ️ No logs yet for {file_name}.",
    "logs_title": "📜 Last log lines for {file_name}:",
    "logs_as_file": "📜 Full log for {file_name}.",
    "stop_all_mine": "⏹ Stopped {count} of your script(s).",
    "restart_all_mine": "🔄 Restarted {started} of your script(s).",
    "no_scripts_running": "ℹ️ No scripts are currently running.",
    "stop_all_admin": "✅ Stopped {count} running script(s).",
    "run_all_started": "✅ Attempted to start {count} script(s).",
    "run_all_working": "⏳ Starting all user scripts…",
    "crash_notice": "⚠️ {file_name} stopped unexpectedly. Auto-recovery is restarting it (attempt {attempt}).",
    "crash_giveup": "🛑 {file_name} keeps crashing, so auto-recovery stopped retrying. Check the logs and use Fix Modules.",
    # Packages
    "pkg_menu": (
        "📦 Package manager\n\n"
        "Installing packages changes the hosting environment, so it is limited to administrators.\n"
        "Send a package name (optionally pinned, e.g. requests==2.32.3) or tap a button below."
    ),
    "pkg_request_menu": (
        "📦 Package request\n\n"
        "Send the name of the package your script needs and the administrators will review it.\n"
        "Example: beautifulsoup4"
    ),
    "pkg_requested": "✅ Your request for {package} was sent to the administrators.",
    "pkg_request_admin": "📦 Package request\n👤 User: {user_id}\n📦 Package: {package}",
    "pkg_invalid": "❌ {package} is not an accepted package name. Plain names and pinned versions only.",
    "pkg_blocked": "❌ {package} cannot be installed from here.",
    "pkg_installing": "⏳ Installing {package}…",
    "pkg_installed": "✅ {package} installed.",
    "pkg_failed": "❌ {package} could not be installed.",
    "pkg_batch_done": "✅ Finished.\n✅ Installed: {ok}\n❌ Failed: {failed}",
    "pkg_batch_start": "🚀 Installing {count} package(s). This can take a while…",
    "modules_none": "✅ No missing Python modules were found in the log of {file_name}.",
    "modules_found": "🔍 Missing modules detected: {modules}\n⏳ Installing…",
    "modules_summary": "🔧 Fix report for {file_name}:\n{results}\n\n✅ Installed: {ok}\n❌ Failed: {failed}\n\n💡 Restart the script to apply the changes.",
    "modules_not_allowed": "⚠️ These modules are not on the allow-list, so an administrator has to install them: {modules}",
    # GitHub
    "gh_prompt": "📦 Send the GitHub repository URL.\nExample: https://github.com/user/repo\n\nSend /cancel to abort.",
    "gh_bad_url": "❌ That does not look like a valid GitHub repository URL.",
    "gh_private_q": "Is this a private repository?",
    "gh_token_prompt": "🔑 Send a GitHub personal access token with read access to that repository.\nThe message is deleted immediately and the token is never stored.\n\nSend /cancel to abort.",
    "gh_downloading": "📥 Downloading the repository…",
    "gh_downloaded": "✅ Repository downloaded. Submitting it for approval…",
    "gh_download_failed": "❌ The repository could not be downloaded: {reason}",
    "gh_session_expired": "⚠️ That session expired. Start again from the menu.",
    "gh_submitted": "✅ Repository submitted for approval. You will be notified once it is reviewed.",
    # Admin
    "admin_panel": "🛠️ Admin panel",
    "admin_list": "👑 Administrators:\n{admins}",
    "admin_add_prompt": "👑 Send the numeric user id to promote to administrator.\n/cancel to abort.",
    "admin_remove_prompt": "👑 Send the numeric user id to remove from administrators.\n/cancel to abort.",
    "admin_added": "✅ User {user_id} is now an administrator.",
    "admin_removed": "✅ Administrator {user_id} removed.",
    "admin_not_admin": "ℹ️ That user is not an administrator.",
    "admin_is_owner": "ℹ️ The owner always has administrator rights.",
    "admin_cannot_remove_owner": "❌ The owner cannot be removed.",
    "invalid_user_id": "❌ Invalid user id. Send digits only.",
    "limit_prompt": "🔧 Send the user id and the new file limit.\nFormat: 123456789 50\nSend 123456789 default to clear a custom limit.\n/cancel to abort.",
    "limit_set": "✅ User {user_id} now has a custom file limit of {limit}.",
    "limit_cleared": "✅ Custom limit for user {user_id} removed.",
    "limit_invalid": "❌ Invalid format. Use: user_id limit",
    "ban_usage": "Usage: /ban <user_id>",
    "unban_usage": "Usage: /unban <user_id>",
    "ban_done": "✅ User {user_id} banned.",
    "ban_failed": "❌ User {user_id} could not be banned.",
    "ban_protected": "❌ Administrators and the owner cannot be banned.",
    "ban_notice": "🚫 You have been banned from using this bot.",
    "unban_done": "✅ User {user_id} unbanned.",
    "unban_missing": "ℹ️ User {user_id} was not banned.",
    "unban_notice": "✅ You have been unbanned. You can use the bot again.",
    "lock_state": "🔒 The bot is now {state}.",
    "new_user_owner": "🎉 New user\n👤 {name}\n✳️ @{username}\n🆔 {user_id}",
    # Subscriptions
    "subs_panel": "💳 Subscription management",
    "subs_add_prompt": "💳 Send the user id and the number of days.\nFormat: 123456789 30\n/cancel to abort.",
    "subs_remove_prompt": "💳 Send the user id whose subscription should be removed.\n/cancel to abort.",
    "subs_check_prompt": "💳 Send the user id to check.\n/cancel to abort.",
    "subs_added": "✅ Subscription for {user_id} is active until {expiry}.",
    "subs_invalid": "❌ Invalid format. Use: user_id days",
    "subs_removed": "✅ Subscription for {user_id} removed.",
    "subs_none": "ℹ️ User {user_id} has no subscription.",
    "subs_active": "✅ User {user_id} is subscribed until {expiry} ({days} day(s) left).",
    "subs_expired": "⚠️ The subscription of user {user_id} expired on {expiry}.",
    "subs_user_notice": "🎉 Your premium access is active until {expiry}.",
    # Broadcast
    "broadcast_prompt": "📢 Send the message to broadcast (text or media).\n/cancel to abort.",
    "broadcast_confirm": "⚠️ Send this message to {count} user(s)?",
    "broadcast_running": "📢 Broadcasting…",
    "broadcast_done": "📢 Broadcast finished.\n✅ Sent: {sent}\n❌ Failed: {failed}",
    "broadcast_expired": "⚠️ That broadcast draft expired. Start again.",
    # Status / speed
    "speed_testing": "🏃 Measuring…",
    "speed_result": (
        "⚡ Response time: {latency} ms\n"
        "⚙️ CPU: {cpu}\n"
        "💾 RAM: {ram_used} / {ram_total} GB\n"
        "💿 Disk free: {disk_free} GB\n"
        "🚦 Bot: {state}\n"
        "🎖 Your level: {level}"
    ),
    "stats_user": "📊 Status\n👥 Users: {users}\n📂 Files: {files}\n🟢 Running: {running}\n⏱️ Uptime: {uptime}",
    "stats_admin": (
        "📊 Status\n👥 Users: {users}\n📂 Files: {files}\n🟢 Running: {running}\n"
        "⏳ Pending approvals: {pending}\n🚫 Banned: {banned}\n👑 Admins: {admins}\n"
        "⭐ Premium: {premium}\n⏱️ Uptime: {uptime}\n🔒 Locked: {locked}"
    ),
    # AI
    "ai_disabled": "ℹ️ The AI assistant is not configured on this instance.",
    "ai_welcome": (
        "🤖 AI assistant\n\n"
        "⚡ Model: {model}\n\n"
        "• Send a Python error and I will detect and install missing modules.\n"
        "• Ask any coding question.\n"
        "• Type help for the full bot guide.\n\n"
        "Send /cancel to leave AI mode."
    ),
    "ai_thinking": "🤔 Thinking…",
    "ai_left": "✅ AI mode closed.",
    "ai_unavailable": "⚠️ The AI service did not respond. Please try again later.",
    "ai_model_current": "🧠 Current AI model: {model}",
    "ai_model_prompt": "Select the AI model:",
    "ai_model_changed": "✅ AI model changed to {model}.",
    "ai_model_invalid": "❌ Unknown model.",
}


def t(key: str, **kwargs: Any) -> str:
    template = MSG.get(key, key)
    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except Exception:  # never break a reply over a formatting mistake
        logger.warning("Message template %s could not be formatted", key)
        return template


_STYLISH_MAP = {
    "a": "ᴀ", "b": "ʙ", "c": "ᴄ", "d": "ᴅ", "e": "ᴇ", "f": "ꜰ", "g": "ɢ",
    "h": "ʜ", "i": "ɪ", "j": "ᴊ", "k": "ᴋ", "l": "ʟ", "m": "ᴍ", "n": "ɴ",
    "o": "ᴏ", "p": "ᴘ", "q": "ǫ", "r": "ʀ", "s": "ꜱ", "t": "ᴛ", "u": "ᴜ",
    "v": "ᴠ", "w": "ᴡ", "x": "x", "y": "ʏ", "z": "ᴢ",
}


def stylish_text(text: str) -> str:
    """Small-caps styling for short UI strings. Never used on logs or user data."""
    if not text:
        return ""
    return "".join(_STYLISH_MAP.get(ch, ch) for ch in text)


# =============================================================================
# 4. DATABASE
# =============================================================================

class Database:
    """One SQLite connection guarded by a re-entrant lock (safe for telebot threads)."""

    def __init__(self, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=10000")
            self._conn.commit()

    @contextlib.contextmanager
    def read(self):
        with self._lock:
            cursor = self._conn.cursor()
            try:
                yield cursor
            finally:
                cursor.close()

    @contextlib.contextmanager
    def write(self):
        with self._lock:
            cursor = self._conn.cursor()
            try:
                yield cursor
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cursor.close()

    def close(self) -> None:
        with self._lock:
            with contextlib.suppress(Exception):
                self._conn.close()


db = Database(DATABASE_PATH)


def _table_columns(cursor, table: str) -> List[str]:
    return [row[1] for row in cursor.execute(f"PRAGMA table_info({table})").fetchall()]


def init_schema() -> None:
    """Creates the schema and migrates older layouts in place."""
    with db.write() as c:
        c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        c.execute("CREATE TABLE IF NOT EXISTS subscriptions (user_id INTEGER PRIMARY KEY, expiry TEXT NOT NULL)")
        c.execute("CREATE TABLE IF NOT EXISTS active_users (user_id INTEGER PRIMARY KEY, joined_at TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)")
        c.execute("CREATE TABLE IF NOT EXISTS verified_users (user_id INTEGER PRIMARY KEY, verified_at TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS banned_users (user_id INTEGER PRIMARY KEY, banned_at TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS user_limits (user_id INTEGER PRIMARY KEY, custom_limit INTEGER NOT NULL)")
        c.execute(
            """CREATE TABLE IF NOT EXISTS pending_uploads (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   user_id INTEGER NOT NULL,
                   file_id TEXT NOT NULL,
                   file_name TEXT NOT NULL,
                   file_type TEXT NOT NULL,
                   file_size INTEGER,
                   user_name TEXT,
                   user_username TEXT,
                   timestamp TEXT,
                   extra_info TEXT
               )"""
        )
        if "extra_info" not in _table_columns(c, "pending_uploads"):
            c.execute("ALTER TABLE pending_uploads ADD COLUMN extra_info TEXT")

        # user_files gains a stable numeric id so callback data stays inside
        # Telegram's 64-byte limit regardless of file name length.
        columns = _table_columns(c, "user_files")
        if not columns:
            c.execute(
                """CREATE TABLE user_files (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       user_id INTEGER NOT NULL,
                       file_name TEXT NOT NULL,
                       file_type TEXT NOT NULL,
                       created_at TEXT,
                       UNIQUE (user_id, file_name)
                   )"""
            )
        elif "id" not in columns:
            logger.info("Migrating user_files to the id-based schema")
            c.execute(
                """CREATE TABLE user_files_new (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       user_id INTEGER NOT NULL,
                       file_name TEXT NOT NULL,
                       file_type TEXT NOT NULL,
                       created_at TEXT,
                       UNIQUE (user_id, file_name)
                   )"""
            )
            c.execute(
                "INSERT OR IGNORE INTO user_files_new (user_id, file_name, file_type, created_at) "
                "SELECT user_id, file_name, file_type, NULL FROM user_files"
            )
            c.execute("DROP TABLE user_files")
            c.execute("ALTER TABLE user_files_new RENAME TO user_files")
        elif "created_at" not in columns:
            c.execute("ALTER TABLE user_files ADD COLUMN created_at TEXT")

        c.execute("CREATE INDEX IF NOT EXISTS idx_user_files_user ON user_files (user_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_pending_user ON pending_uploads (user_id)")
        for admin_id in sorted(BOOTSTRAP_ADMIN_IDS):
            c.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (admin_id,))


# ---------------------------------------------------------------- settings ---

_SETTINGS_LOCK = threading.RLock()
_SETTINGS_CACHE: Dict[str, str] = {}


def load_settings() -> None:
    with db.read() as c:
        rows = c.execute("SELECT key, value FROM settings").fetchall()
    with _SETTINGS_LOCK:
        _SETTINGS_CACHE.clear()
        _SETTINGS_CACHE.update({row["key"]: row["value"] for row in rows})


def get_setting(key: str, default: str = "") -> str:
    with _SETTINGS_LOCK:
        return _SETTINGS_CACHE.get(key, default)


def set_setting(key: str, value: str) -> None:
    with db.write() as c:
        c.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
    with _SETTINGS_LOCK:
        _SETTINGS_CACHE[key] = value


# ------------------------------------------------------------------- users ---

_ADMIN_LOCK = threading.RLock()
_admin_ids: set = set()
_banned_ids: set = set()


def load_users() -> None:
    global _admin_ids, _banned_ids
    with db.read() as c:
        admins = {row["user_id"] for row in c.execute("SELECT user_id FROM admins").fetchall()}
        banned = {row["user_id"] for row in c.execute("SELECT user_id FROM banned_users").fetchall()}
    with _ADMIN_LOCK:
        _admin_ids = admins | BOOTSTRAP_ADMIN_IDS
        _banned_ids = banned - {OWNER_ID}


def admin_ids() -> set:
    with _ADMIN_LOCK:
        return set(_admin_ids)


def is_admin(user_id: int) -> bool:
    with _ADMIN_LOCK:
        return user_id == OWNER_ID or user_id in _admin_ids


def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


def add_admin(user_id: int) -> bool:
    if is_admin(user_id):
        return False
    with db.write() as c:
        c.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (user_id,))
    with _ADMIN_LOCK:
        _admin_ids.add(user_id)
    return True


def remove_admin(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return False
    with db.write() as c:
        c.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        removed = c.rowcount > 0
    with _ADMIN_LOCK:
        _admin_ids.discard(user_id)
    return removed


def is_banned(user_id: int) -> bool:
    with _ADMIN_LOCK:
        return user_id in _banned_ids


def ban_user(user_id: int) -> bool:
    if is_admin(user_id):
        return False
    with db.write() as c:
        c.execute(
            "INSERT OR IGNORE INTO banned_users (user_id, banned_at) VALUES (?, ?)",
            (user_id, datetime.now().isoformat(timespec="seconds")),
        )
    with _ADMIN_LOCK:
        _banned_ids.add(user_id)
    return True


def unban_user(user_id: int) -> bool:
    with db.write() as c:
        c.execute("DELETE FROM banned_users WHERE user_id = ?", (user_id,))
        removed = c.rowcount > 0
    with _ADMIN_LOCK:
        _banned_ids.discard(user_id)
    return removed


def banned_count() -> int:
    with _ADMIN_LOCK:
        return len(_banned_ids)


def register_active_user(user_id: int) -> bool:
    """Returns True the first time a user is seen."""
    with db.write() as c:
        c.execute(
            "INSERT OR IGNORE INTO active_users (user_id, joined_at) VALUES (?, ?)",
            (user_id, datetime.now().isoformat(timespec="seconds")),
        )
        return c.rowcount > 0


def active_user_ids() -> List[int]:
    with db.read() as c:
        return [row["user_id"] for row in c.execute("SELECT user_id FROM active_users").fetchall()]


def active_user_count() -> int:
    with db.read() as c:
        return int(c.execute("SELECT COUNT(*) FROM active_users").fetchone()[0])


# ----------------------------------------------------------- verification ---

def verified_at(user_id: int) -> Optional[datetime]:
    with db.read() as c:
        row = c.execute("SELECT verified_at FROM verified_users WHERE user_id = ?", (user_id,)).fetchone()
    if not row or not row["verified_at"]:
        return None
    try:
        return datetime.fromisoformat(row["verified_at"])
    except ValueError:
        return None


def mark_verified(user_id: int) -> None:
    with db.write() as c:
        c.execute(
            "INSERT INTO verified_users (user_id, verified_at) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET verified_at = excluded.verified_at",
            (user_id, datetime.now().isoformat(timespec="seconds")),
        )


def clear_verification(user_id: int) -> None:
    with db.write() as c:
        c.execute("DELETE FROM verified_users WHERE user_id = ?", (user_id,))


# ----------------------------------------------------------- subscriptions ---

def save_subscription(user_id: int, expiry: datetime) -> None:
    with db.write() as c:
        c.execute(
            "INSERT INTO subscriptions (user_id, expiry) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET expiry = excluded.expiry",
            (user_id, expiry.isoformat(timespec="seconds")),
        )


def remove_subscription(user_id: int) -> bool:
    with db.write() as c:
        c.execute("DELETE FROM subscriptions WHERE user_id = ?", (user_id,))
        return c.rowcount > 0


def subscription_expiry(user_id: int) -> Optional[datetime]:
    with db.read() as c:
        row = c.execute("SELECT expiry FROM subscriptions WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        return None
    try:
        return datetime.fromisoformat(row["expiry"])
    except ValueError:
        return None


def is_premium(user_id: int) -> bool:
    expiry = subscription_expiry(user_id)
    return bool(expiry and expiry > datetime.now())


def premium_count() -> int:
    now = datetime.now().isoformat(timespec="seconds")
    with db.read() as c:
        return int(c.execute("SELECT COUNT(*) FROM subscriptions WHERE expiry > ?", (now,)).fetchone()[0])


# ------------------------------------------------------------ file records ---

def add_file_record(user_id: int, file_name: str, file_type: str) -> int:
    with db.write() as c:
        c.execute(
            "INSERT INTO user_files (user_id, file_name, file_type, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, file_name) DO UPDATE SET file_type = excluded.file_type",
            (user_id, file_name, file_type, datetime.now().isoformat(timespec="seconds")),
        )
        row = c.execute(
            "SELECT id FROM user_files WHERE user_id = ? AND file_name = ?", (user_id, file_name)
        ).fetchone()
    return int(row["id"]) if row else 0


def remove_file_record(user_id: int, file_name: str) -> None:
    with db.write() as c:
        c.execute("DELETE FROM user_files WHERE user_id = ? AND file_name = ?", (user_id, file_name))


def list_user_files(user_id: int) -> List[sqlite3.Row]:
    with db.read() as c:
        return c.execute(
            "SELECT id, user_id, file_name, file_type FROM user_files WHERE user_id = ? ORDER BY file_name",
            (user_id,),
        ).fetchall()


def get_file_record(file_row_id: int) -> Optional[sqlite3.Row]:
    with db.read() as c:
        return c.execute(
            "SELECT id, user_id, file_name, file_type FROM user_files WHERE id = ?", (file_row_id,)
        ).fetchone()


def find_file_record(user_id: int, file_name: str) -> Optional[sqlite3.Row]:
    with db.read() as c:
        return c.execute(
            "SELECT id, user_id, file_name, file_type FROM user_files WHERE user_id = ? AND file_name = ?",
            (user_id, file_name),
        ).fetchone()


def all_file_records() -> List[sqlite3.Row]:
    with db.read() as c:
        return c.execute("SELECT id, user_id, file_name, file_type FROM user_files").fetchall()


def user_file_count(user_id: int) -> int:
    with db.read() as c:
        return int(c.execute("SELECT COUNT(*) FROM user_files WHERE user_id = ?", (user_id,)).fetchone()[0])


def total_file_count() -> int:
    with db.read() as c:
        return int(c.execute("SELECT COUNT(*) FROM user_files").fetchone()[0])


# --------------------------------------------------------- pending uploads ---

def add_pending_upload(user_id: int, file_id: str, file_name: str, file_type: str,
                       file_size: int, user_name: str, user_username: str,
                       extra_info: str = "") -> Optional[int]:
    try:
        with db.write() as c:
            c.execute(
                """INSERT INTO pending_uploads
                   (user_id, file_id, file_name, file_type, file_size, user_name, user_username, timestamp, extra_info)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, file_id, file_name, file_type, file_size, user_name, user_username,
                 datetime.now().isoformat(timespec="seconds"), extra_info),
            )
            return int(c.lastrowid)
    except sqlite3.Error as exc:
        logger.error("Could not store pending upload for %s: %s", user_id, exc)
        return None


def get_pending_upload(upload_id: int) -> Optional[sqlite3.Row]:
    with db.read() as c:
        return c.execute("SELECT * FROM pending_uploads WHERE id = ?", (upload_id,)).fetchone()


def delete_pending_upload(upload_id: int) -> None:
    with db.write() as c:
        c.execute("DELETE FROM pending_uploads WHERE id = ?", (upload_id,))


def pending_upload_count(user_id: Optional[int] = None) -> int:
    with db.read() as c:
        if user_id is None:
            return int(c.execute("SELECT COUNT(*) FROM pending_uploads").fetchone()[0])
        return int(c.execute("SELECT COUNT(*) FROM pending_uploads WHERE user_id = ?", (user_id,)).fetchone()[0])


# --------------------------------------------------------------- user limits ---

def set_custom_limit(user_id: int, limit: int) -> None:
    with db.write() as c:
        c.execute(
            "INSERT INTO user_limits (user_id, custom_limit) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET custom_limit = excluded.custom_limit",
            (user_id, limit),
        )


def clear_custom_limit(user_id: int) -> bool:
    with db.write() as c:
        c.execute("DELETE FROM user_limits WHERE user_id = ?", (user_id,))
        return c.rowcount > 0


def custom_limit(user_id: int) -> Optional[int]:
    with db.read() as c:
        row = c.execute("SELECT custom_limit FROM user_limits WHERE user_id = ?", (user_id,)).fetchone()
    return int(row["custom_limit"]) if row else None


init_schema()
load_settings()
load_users()


# =============================================================================
# 5. BOT INSTANCE AND SAFE API HELPERS
# =============================================================================

telebot.apihelper.CONNECT_TIMEOUT = 20
telebot.apihelper.READ_TIMEOUT = 40
bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None, threaded=True, num_threads=6)

SHUTTING_DOWN = threading.Event()


def api_call(method, *args, **kwargs):
    """Calls a Telegram API method. Handles flood control, never raises."""
    for attempt in range(3):
        try:
            return method(*args, **kwargs)
        except ApiTelegramException as exc:
            if exc.error_code == 429:
                retry_after = 3
                try:
                    retry_after = int(exc.result_json["parameters"]["retry_after"])
                except (KeyError, TypeError, ValueError):
                    pass
                time.sleep(min(retry_after + 1, 30))
                continue
            logger.info("Telegram API rejected %s: %s %s", getattr(method, "__name__", method),
                        exc.error_code, exc.description)
            return None
        except requests.RequestException as exc:
            logger.warning("Network error calling %s: %s", getattr(method, "__name__", method), exc)
            time.sleep(1 + attempt)
        except Exception:
            logger.exception("Unexpected error calling %s", getattr(method, "__name__", method))
            return None
    return None


@dataclass(frozen=True)
class Ctx:
    """Everything a feature needs to answer, regardless of message or callback origin."""

    user_id: int
    chat_id: int
    message_id: Optional[int] = None
    first_name: str = ""
    username: str = ""
    call_id: Optional[str] = None


def ctx_from_message(message: types.Message) -> Ctx:
    return Ctx(
        user_id=message.from_user.id,
        chat_id=message.chat.id,
        message_id=message.message_id,
        first_name=message.from_user.first_name or "",
        username=message.from_user.username or "",
    )


def ctx_from_call(call: types.CallbackQuery) -> Ctx:
    return Ctx(
        user_id=call.from_user.id,
        chat_id=call.message.chat.id if call.message else call.from_user.id,
        message_id=None,
        first_name=call.from_user.first_name or "",
        username=call.from_user.username or "",
        call_id=call.id,
    )


def say(ctx: Ctx, text: str, styled: bool = True, **kwargs):
    """Sends a reply, falling back to a plain message if the original is gone."""
    body = stylish_text(text) if styled else text
    kwargs.setdefault("disable_web_page_preview", True)
    if ctx.message_id:
        sent = api_call(bot.send_message, ctx.chat_id, body, reply_to_message_id=ctx.message_id, **kwargs)
        if sent:
            return sent
    return api_call(bot.send_message, ctx.chat_id, body, **kwargs)


def send_text(chat_id: int, text: str, styled: bool = True, **kwargs):
    body = stylish_text(text) if styled else text
    kwargs.setdefault("disable_web_page_preview", True)
    return api_call(bot.send_message, chat_id, body, **kwargs)


def edit_text(chat_id: int, message_id: int, text: str, styled: bool = True, **kwargs):
    body = stylish_text(text) if styled else text
    kwargs.setdefault("disable_web_page_preview", True)
    return api_call(bot.edit_message_text, body, chat_id, message_id, **kwargs)


def answer_call(call_id: Optional[str], text: str = "", alert: bool = False) -> None:
    if not call_id:
        return
    api_call(bot.answer_callback_query, call_id, stylish_text(text)[:200] if text else None, show_alert=alert)


def notify_admins(text: str, skip: Optional[int] = None) -> None:
    for admin_id in sorted(admin_ids()):
        if skip is not None and admin_id == skip:
            continue
        send_text(admin_id, text)


# --------------------------------------------------------------- cooldowns ---

_COOLDOWN_LOCK = threading.Lock()
_COOLDOWNS: Dict[Tuple[int, str], float] = {}


def cooldown_left(user_id: int, action: str, seconds: int) -> int:
    """Returns 0 when the action is allowed, otherwise the remaining seconds."""
    now = time.monotonic()
    key = (user_id, action)
    with _COOLDOWN_LOCK:
        until = _COOLDOWNS.get(key, 0.0)
        if until > now:
            return int(until - now) + 1
        _COOLDOWNS[key] = now + seconds
        return 0


# =============================================================================
# 6. ACCESS CONTROL
# =============================================================================

LEVEL_OWNER = "👑 Owner"
LEVEL_ADMIN = "🛡️ Admin"
LEVEL_PREMIUM = "⭐ Premium"
LEVEL_FREE = "🆓 Free"


def access_level(user_id: int) -> str:
    if is_owner(user_id):
        return LEVEL_OWNER
    if is_admin(user_id):
        return LEVEL_ADMIN
    if is_premium(user_id):
        return LEVEL_PREMIUM
    return LEVEL_FREE


def file_limit(user_id: int) -> float:
    explicit = custom_limit(user_id)
    if explicit is not None:
        return float(explicit)
    if is_owner(user_id):
        return OWNER_FILE_LIMIT
    if is_admin(user_id):
        return float(ADMIN_FILE_LIMIT)
    if is_premium(user_id):
        return float(PREMIUM_USER_LIMIT)
    return float(FREE_USER_LIMIT)


def limit_display(limit: float) -> str:
    return "Unlimited" if limit == math.inf else str(int(limit))


def bot_is_locked() -> bool:
    return get_setting("bot_locked", "0") == "1"


def set_bot_locked(locked: bool) -> None:
    set_setting("bot_locked", "1" if locked else "0")


def current_model_key() -> str:
    key = get_setting("ai_model", AI_DEFAULT_MODEL)
    return key if key in AVAILABLE_MODELS else AI_DEFAULT_MODEL


def set_current_model(key: str) -> None:
    set_setting("ai_model", key)


def bot_start_time() -> datetime:
    raw = get_setting("start_time")
    if raw:
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
    now = datetime.now()
    set_setting("start_time", now.isoformat(timespec="seconds"))
    return now


# ---------------------------------------------------- channel verification ---

def channel_membership(user_id: int) -> Tuple[List[str], List[str]]:
    """Returns (channels not joined, channels that could not be checked)."""
    missing: List[str] = []
    unchecked: List[str] = []
    for channel in REQUIRED_CHANNELS:
        try:
            member = bot.get_chat_member(channel, user_id)
            if member.status not in ("member", "administrator", "creator"):
                missing.append(channel)
        except ApiTelegramException as exc:
            description = (exc.description or "").lower()
            if "user not found" in description or "participant" in description:
                missing.append(channel)
            else:
                # Bot is not an admin of the channel, channel renamed, etc.
                logger.warning("Membership check failed for %s: %s", channel, exc.description)
                unchecked.append(channel)
        except Exception:
            logger.exception("Membership check crashed for %s", channel)
            unchecked.append(channel)
    return missing, unchecked


def send_join_prompt(chat_id: int, user_id: int) -> None:
    markup = types.InlineKeyboardMarkup(row_width=1)
    for channel in REQUIRED_CHANNELS:
        if channel.startswith("@"):
            markup.add(types.InlineKeyboardButton(f"📢 Join {channel}", url=f"https://t.me/{channel.lstrip('@')}"))
    markup.add(types.InlineKeyboardButton("✅ Verify", callback_data=f"vfy:{user_id}"))
    send_text(chat_id, t("join_prompt"), reply_markup=markup)


def ensure_access(ctx: Ctx, silent: bool = False) -> bool:
    """Single gate for every feature: ban check, lock check, channel verification."""
    if is_banned(ctx.user_id):
        if not silent:
            if ctx.call_id:
                answer_call(ctx.call_id, t("banned"), alert=True)
            else:
                say(ctx, t("banned"))
        return False

    if is_admin(ctx.user_id):
        return True

    if not REQUIRED_CHANNELS:
        return True

    seen = verified_at(ctx.user_id)
    if seen and (datetime.now() - seen) < timedelta(hours=VERIFY_TTL_HOURS):
        return True

    missing, unchecked = channel_membership(ctx.user_id)
    if not missing and not unchecked:
        mark_verified(ctx.user_id)
        return True
    if not missing and unchecked:
        # Fail open on transient API problems, but do not cache a pass.
        logger.warning("Letting user %s through: channels unreachable %s", ctx.user_id, unchecked)
        return True

    clear_verification(ctx.user_id)
    if not silent:
        if ctx.call_id:
            answer_call(ctx.call_id, t("verify_missing"), alert=True)
        send_join_prompt(ctx.chat_id, ctx.user_id)
    return False


def ensure_unlocked(ctx: Ctx) -> bool:
    if not bot_is_locked() or is_admin(ctx.user_id):
        return True
    if ctx.call_id:
        answer_call(ctx.call_id, t("bot_locked"), alert=True)
    else:
        say(ctx, t("bot_locked"))
    return False


def require_admin(ctx: Ctx) -> bool:
    if is_admin(ctx.user_id):
        return True
    if ctx.call_id:
        answer_call(ctx.call_id, t("admin_only"), alert=True)
    else:
        say(ctx, t("admin_only"))
    return False


def require_owner(ctx: Ctx) -> bool:
    if is_owner(ctx.user_id):
        return True
    if ctx.call_id:
        answer_call(ctx.call_id, t("owner_only"), alert=True)
    else:
        say(ctx, t("owner_only"))
    return False


# =============================================================================
# 7. FILESYSTEM HELPERS
# =============================================================================

SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def is_safe_file_name(name: str) -> bool:
    if not name or name != os.path.basename(name):
        return False
    if ".." in name or name.startswith("."):
        return False
    return bool(SAFE_NAME_RE.match(name))


def sanitize_file_name(name: str) -> str:
    """Best-effort conversion of an arbitrary upload name into a safe one."""
    name = os.path.basename(name or "").strip().replace(" ", "_")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    name = re.sub(r"_{2,}", "_", name).lstrip("._-")
    if len(name) > 80:
        stem, ext = os.path.splitext(name)
        name = stem[: 79 - len(ext)] + ext
    return name


def user_folder(user_id: int) -> str:
    folder = os.path.join(UPLOAD_BOTS_DIR, str(int(user_id)))
    os.makedirs(folder, exist_ok=True)
    return folder


def inside(base: str, path: str) -> bool:
    base_real = os.path.realpath(base)
    target = os.path.realpath(path)
    return target == base_real or target.startswith(base_real + os.sep)


def directory_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda _e: None):
        for name in files:
            with contextlib.suppress(OSError):
                total += os.path.getsize(os.path.join(root, name))
    return total


def log_path_for(folder: str, file_name: str) -> str:
    return os.path.join(folder, f"{os.path.splitext(file_name)[0]}.log")


def rotate_log(path: str) -> None:
    with contextlib.suppress(OSError):
        if os.path.exists(path) and os.path.getsize(path) > LOG_MAX_BYTES:
            shutil.move(path, path + ".1")


def read_log_tail(path: str, max_chars: int) -> str:
    if not os.path.exists(path):
        return ""
    try:
        size = os.path.getsize(path)
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            if size > max_chars:
                handle.seek(size - max_chars)
                handle.readline()
            return handle.read()
    except OSError as exc:
        logger.warning("Could not read log %s: %s", path, exc)
        return ""


def delete_file_assets(folder: str, file_name: str) -> None:
    for path in (os.path.join(folder, file_name), log_path_for(folder, file_name),
                 log_path_for(folder, file_name) + ".1"):
        if inside(folder, path):
            with contextlib.suppress(OSError):
                if os.path.isfile(path):
                    os.remove(path)


# =============================================================================
# 8. PROCESS MANAGER
# =============================================================================

PYTHON_BIN = sys.executable or "python3"
NODE_BIN = shutil.which("node") or "node"
NODE_AVAILABLE = shutil.which("node") is not None
STARTUP_WATCH_SECONDS = _env_int("STARTUP_WATCH_SECONDS", 6)
RECOVERY_INTERVAL_SECONDS = _env_int("RECOVERY_INTERVAL_SECONDS", 30)
RECOVERY_MAX_ATTEMPTS = _env_int("RECOVERY_MAX_ATTEMPTS", 5)
RECOVERY_WINDOW_SECONDS = _env_int("RECOVERY_WINDOW_SECONDS", 900)

SENSITIVE_ENV_RE = re.compile(r"(TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|CREDENTIAL|PRIVATE_KEY|SESSION)", re.IGNORECASE)
BLOCKED_ENV_KEYS = {
    "BOT_TOKEN", "TOKEN", "OWNER_ID", "ADMIN_IDS", "REQUIRED_CHANNELS", "DATA_DIR",
    "SAMBA_API_KEY", "SAMBA_URL", "OWNER_USERNAME", "UPDATES_CHANNEL_URL", "LOG_LEVEL",
}


def child_environment(folder: str) -> Dict[str, str]:
    """Environment for hosted scripts: no bot secrets, isolated HOME and TMPDIR."""
    env: Dict[str, str] = {}
    for key, value in os.environ.items():
        if key in BLOCKED_ENV_KEYS or SENSITIVE_ENV_RE.search(key):
            continue
        env[key] = value
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["HOME"] = folder
    tmp_dir = os.path.join(folder, ".tmp")
    with contextlib.suppress(OSError):
        os.makedirs(tmp_dir, exist_ok=True)
    env["TMPDIR"] = tmp_dir
    env.setdefault("PATH", os.defpath)
    return env


def _make_preexec(apply_memory_limit: bool):
    """POSIX only: own session (so the whole tree can be signalled) plus rlimits."""

    def _preexec() -> None:
        with contextlib.suppress(OSError):
            os.setsid()
        if _resource is None:
            return
        with contextlib.suppress(ValueError, OSError):
            _resource.setrlimit(_resource.RLIMIT_CORE, (0, 0))
        with contextlib.suppress(ValueError, OSError):
            _resource.setrlimit(_resource.RLIMIT_NPROC, (SCRIPT_MAX_PROCESSES, SCRIPT_MAX_PROCESSES))
        if apply_memory_limit:
            # Node.js reserves a very large virtual address space, so the
            # address-space cap is only applied to Python scripts.
            with contextlib.suppress(ValueError, OSError):
                _resource.setrlimit(_resource.RLIMIT_AS, (SCRIPT_MEMORY_BYTES, SCRIPT_MEMORY_BYTES))

    return _preexec


@dataclass
class ScriptProcess:
    key: str
    owner_id: int
    file_name: str
    file_type: str
    folder: str
    log_path: str
    process: subprocess.Popen
    log_handle: Any
    chat_id: Optional[int]
    started_at: datetime = field(default_factory=datetime.now)
    recovery_attempts: int = 0
    recovery_window_start: float = field(default_factory=time.monotonic)
    autofix_done: bool = False


_PROC_LOCK = threading.RLock()
_processes: Dict[str, ScriptProcess] = {}


def script_key(owner_id: int, file_name: str) -> str:
    return f"{owner_id}::{file_name}"


def _close_handle(record: ScriptProcess) -> None:
    handle = record.log_handle
    if handle is not None and not getattr(handle, "closed", True):
        with contextlib.suppress(OSError):
            handle.close()


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        else:
            process.terminate()
    except (OSError, ProcessLookupError):
        pass
    try:
        process.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        else:
            with contextlib.suppress(psutil.Error):
                for child in psutil.Process(process.pid).children(recursive=True):
                    with contextlib.suppress(psutil.Error):
                        child.kill()
            process.kill()
    except (OSError, ProcessLookupError):
        pass
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=5)


def process_alive(record: ScriptProcess) -> bool:
    return record.process.poll() is None


def is_running(owner_id: int, file_name: str) -> bool:
    key = script_key(owner_id, file_name)
    with _PROC_LOCK:
        record = _processes.get(key)
        if record is None:
            return False
        if process_alive(record):
            return True
        _close_handle(record)
        _processes.pop(key, None)
        return False


def running_count() -> int:
    with _PROC_LOCK:
        keys = list(_processes.keys())
    alive = 0
    for key in keys:
        with _PROC_LOCK:
            record = _processes.get(key)
        if record and process_alive(record):
            alive += 1
    return alive


def stop_script(owner_id: int, file_name: str) -> bool:
    key = script_key(owner_id, file_name)
    with _PROC_LOCK:
        record = _processes.pop(key, None)
    if record is None:
        return False
    _terminate(record.process)
    _close_handle(record)
    logger.info("Stopped %s", key)
    return True


def stop_all_scripts() -> int:
    with _PROC_LOCK:
        records = list(_processes.values())
        _processes.clear()
    for record in records:
        _terminate(record.process)
        _close_handle(record)
    return len(records)


def stop_user_scripts(owner_id: int) -> int:
    with _PROC_LOCK:
        keys = [key for key, record in _processes.items() if record.owner_id == owner_id]
    stopped = 0
    for key in keys:
        with _PROC_LOCK:
            record = _processes.pop(key, None)
        if record:
            _terminate(record.process)
            _close_handle(record)
            stopped += 1
    return stopped


def start_script(owner_id: int, file_name: str, file_type: str,
                 chat_id: Optional[int] = None) -> Tuple[bool, str]:
    """Starts one hosted script. Returns (started, user-facing message)."""
    if not is_safe_file_name(file_name):
        return False, t("upload_bad_name")

    folder = user_folder(owner_id)
    script_path = os.path.join(folder, file_name)
    if not inside(folder, script_path) or not os.path.isfile(script_path):
        return False, t("file_missing")

    if file_type == "js" and not NODE_AVAILABLE:
        return False, "❌ Node.js is not installed on this host."

    if is_running(owner_id, file_name):
        return False, t("already_running")

    key = script_key(owner_id, file_name)
    log_path = log_path_for(folder, file_name)
    rotate_log(log_path)

    try:
        log_handle = open(log_path, "a", encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.error("Cannot open log for %s: %s", key, exc)
        return False, t("generic_error")

    command = [PYTHON_BIN, script_path] if file_type == "py" else [NODE_BIN, script_path]
    popen_kwargs: Dict[str, Any] = {
        "cwd": folder,
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "env": child_environment(folder),
        "close_fds": True,
    }
    if os.name == "posix":
        popen_kwargs["preexec_fn"] = _make_preexec(file_type == "py")
    else:  # pragma: no cover - Windows
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    log_handle.write(f"\n===== started {datetime.now().isoformat(timespec='seconds')} =====\n")
    log_handle.flush()

    try:
        process = subprocess.Popen(command, **popen_kwargs)
    except (OSError, ValueError) as exc:
        _close_handle_raw(log_handle)
        logger.error("Failed to launch %s: %s", key, exc)
        return False, t("generic_error")

    record = ScriptProcess(
        key=key,
        owner_id=owner_id,
        file_name=file_name,
        file_type=file_type,
        folder=folder,
        log_path=log_path,
        process=process,
        log_handle=log_handle,
        chat_id=chat_id,
    )
    with _PROC_LOCK:
        _processes[key] = record

    logger.info("Started %s (pid %s)", key, process.pid)
    threading.Thread(target=_watch_startup, args=(key,), name=f"watch-{key}", daemon=True).start()
    return True, t("started", file_name=file_name)


def _close_handle_raw(handle) -> None:
    if handle is not None and not getattr(handle, "closed", True):
        with contextlib.suppress(OSError):
            handle.close()


def _watch_startup(key: str) -> None:
    """Detects an immediate crash and repairs missing dependencies once."""
    time.sleep(STARTUP_WATCH_SECONDS)
    with _PROC_LOCK:
        record = _processes.get(key)
    if record is None or process_alive(record):
        return

    exit_code = record.process.returncode
    tail = read_log_tail(record.log_path, 6000)
    with _PROC_LOCK:
        _processes.pop(key, None)
    _close_handle(record)

    chat_id = record.chat_id
    if record.file_type == "py":
        modules = extract_missing_python_modules(tail)
    else:
        modules = extract_missing_node_modules(tail)

    if modules and not record.autofix_done:
        installable, blocked = split_installable_modules(modules)
        if chat_id:
            send_text(chat_id, t("modules_found", modules=", ".join(sorted(modules))))
        installed = 0
        for module in sorted(installable):
            if record.file_type == "py":
                ok, _detail = install_pip_package(module_to_package(module))
            else:
                ok, _detail = install_npm_package(module, record.folder)
            installed += 1 if ok else 0
        if blocked and chat_id:
            send_text(chat_id, t("modules_not_allowed", modules=", ".join(sorted(blocked))))
        if installed:
            started, message = start_script(record.owner_id, record.file_name, record.file_type, chat_id)
            with _PROC_LOCK:
                new_record = _processes.get(key)
                if new_record:
                    new_record.autofix_done = True
            if chat_id:
                send_text(chat_id, message if started else t("pkg_failed", package=", ".join(sorted(modules))))
            return

    if chat_id:
        send_text(chat_id, f"⚠️ {record.file_name} exited immediately (code {exit_code}). Open Logs to see why.")


# =============================================================================
# 9. PACKAGE MANAGEMENT
# =============================================================================

PACKAGE_SPEC_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}(==[A-Za-z0-9][A-Za-z0-9.\-_+]{0,31})?$")
PACKAGE_BLOCKLIST = {"pip", "setuptools", "wheel", "python", "pip3", "distutils", "virtualenv"}

STDLIB_MODULES = {
    "abc", "argparse", "asyncio", "base64", "collections", "contextlib", "copy", "csv", "ctypes",
    "dataclasses", "datetime", "decimal", "enum", "functools", "glob", "hashlib", "hmac", "html",
    "http", "importlib", "inspect", "io", "itertools", "json", "logging", "math", "mimetypes",
    "multiprocessing", "os", "pathlib", "pickle", "platform", "queue", "random", "re", "secrets",
    "shutil", "signal", "socket", "sqlite3", "ssl", "string", "struct", "subprocess", "sys",
    "tempfile", "textwrap", "threading", "time", "traceback", "types", "typing", "unittest",
    "urllib", "uuid", "warnings", "weakref", "xml", "zipfile", "zlib",
}

# Import name -> pip distribution name. Only verified, real packages.
MODULE_PACKAGE_MAP = {
    "telebot": "pyTelegramBotAPI",
    "telegram": "python-telegram-bot",
    "aiogram": "aiogram",
    "pyrogram": "pyrogram",
    "telethon": "telethon",
    "tgcrypto": "tgcrypto",
    "bs4": "beautifulsoup4",
    "cv2": "opencv-python-headless",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "PIL": "Pillow",
    "yaml": "PyYAML",
    "sklearn": "scikit-learn",
    "serial": "pyserial",
    "jwt": "PyJWT",
    "Crypto": "pycryptodome",
    "google": "google-api-python-client",
    "psycopg2": "psycopg2-binary",
    "OpenSSL": "pyOpenSSL",
    "attr": "attrs",
    "fitz": "PyMuPDF",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "magic": "python-magic",
    "redis": "redis",
    "requests": "requests",
    "httpx": "httpx",
    "aiohttp": "aiohttp",
    "flask": "Flask",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "django": "Django",
    "sqlalchemy": "SQLAlchemy",
    "pandas": "pandas",
    "numpy": "numpy",
    "scipy": "scipy",
    "matplotlib": "matplotlib",
    "lxml": "lxml",
    "psutil": "psutil",
    "pytz": "pytz",
    "colorama": "colorama",
    "rich": "rich",
    "tqdm": "tqdm",
    "websockets": "websockets",
    "socketio": "python-socketio",
    "pymongo": "pymongo",
    "motor": "motor",
    "openpyxl": "openpyxl",
    "faker": "Faker",
    "qrcode": "qrcode",
    "pydantic": "pydantic",
    "yt_dlp": "yt-dlp",
    "youtube_dl": "youtube-dl",
    "moviepy": "moviepy",
    "pydub": "pydub",
    "speech_recognition": "SpeechRecognition",
    "gtts": "gTTS",
    "googletrans": "googletrans",
    "instaloader": "instaloader",
    "selenium": "selenium",
    "playwright": "playwright",
    "cloudscraper": "cloudscraper",
    "fake_useragent": "fake-useragent",
    "emoji": "emoji",
    "schedule": "schedule",
    "apscheduler": "APScheduler",
    "cryptography": "cryptography",
    "nacl": "PyNaCl",
    "bcrypt": "bcrypt",
    "ujson": "ujson",
    "orjson": "orjson",
    "uvloop": "uvloop",
    "async_timeout": "async-timeout",
}

RECOMMENDED_PACKAGES = [
    "requests", "httpx", "aiohttp", "python-dotenv", "beautifulsoup4", "lxml", "Pillow",
    "numpy", "pandas", "pytz", "python-dateutil", "psutil", "rich", "tqdm",
    "pyTelegramBotAPI", "aiogram", "pyrogram", "tgcrypto", "telethon", "Flask",
]

ALLOWED_PACKAGES = (
    set(MODULE_PACKAGE_MAP.values())
    | set(RECOMMENDED_PACKAGES)
    | {p for p in re.split(r"[,\s;]+", _env_str("EXTRA_ALLOWED_PACKAGES")) if p}
)

_INSTALL_LOCK = threading.Lock()


def module_to_package(module_name: str) -> str:
    root = (module_name or "").split(".")[0]
    return MODULE_PACKAGE_MAP.get(root, root)


def is_valid_package_spec(spec: str) -> bool:
    if not spec or not PACKAGE_SPEC_RE.match(spec):
        return False
    return spec.split("==")[0].lower() not in PACKAGE_BLOCKLIST


def is_allowlisted_package(spec: str) -> bool:
    name = spec.split("==")[0]
    lowered = {p.lower() for p in ALLOWED_PACKAGES}
    return name.lower() in lowered


def split_installable_modules(modules: set) -> Tuple[set, set]:
    """Splits detected modules into auto-installable and admin-only sets."""
    installable, blocked = set(), set()
    if not ALLOW_AUTO_PACKAGE_INSTALL:
        return set(), set(modules)
    for module in modules:
        package = module_to_package(module)
        if is_valid_package_spec(package) and is_allowlisted_package(package):
            installable.add(module)
        else:
            blocked.add(module)
    return installable, blocked


def install_pip_package(spec: str) -> Tuple[bool, str]:
    if not is_valid_package_spec(spec):
        return False, t("pkg_invalid", package=spec)
    with _INSTALL_LOCK:
        try:
            result = subprocess.run(
                [PYTHON_BIN, "-m", "pip", "install", "--no-input",
                 "--disable-pip-version-check", "--no-color", spec],
                capture_output=True, text=True, timeout=PIP_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            logger.error("pip install %s timed out", spec)
            return False, t("pkg_failed", package=spec)
        except OSError as exc:
            logger.error("pip is unavailable: %s", exc)
            return False, t("pkg_failed", package=spec)
    if result.returncode == 0:
        logger.info("Installed pip package %s", spec)
        return True, t("pkg_installed", package=spec)
    logger.warning("pip install %s failed: %s", spec, (result.stderr or "")[-500:])
    return False, t("pkg_failed", package=spec)


def install_npm_package(name: str, folder: str) -> Tuple[bool, str]:
    if not is_valid_package_spec(name.replace("@", "").replace("/", "-")):
        return False, t("pkg_invalid", package=name)
    if not NODE_AVAILABLE:
        return False, "❌ Node.js is not installed on this host."
    try:
        result = subprocess.run(
            ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund", name],
            cwd=folder, capture_output=True, text=True, timeout=NPM_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        logger.error("npm install %s timed out", name)
        return False, t("pkg_failed", package=name)
    except OSError as exc:
        logger.error("npm is unavailable: %s", exc)
        return False, t("pkg_failed", package=name)
    if result.returncode == 0:
        return True, t("pkg_installed", package=name)
    logger.warning("npm install %s failed: %s", name, (result.stderr or "")[-500:])
    return False, t("pkg_failed", package=name)


def extract_missing_python_modules(text: str) -> set:
    modules = set()
    for pattern in (r"ModuleNotFoundError: No module named '([^']+)'",
                    r"ImportError: No module named '?([A-Za-z0-9_.]+)'?"):
        for match in re.findall(pattern, text or ""):
            root = match.split(".")[0].strip()
            if root and root not in STDLIB_MODULES and not root.startswith("_"):
                modules.add(root)
    return modules


def extract_missing_node_modules(text: str) -> set:
    modules = set()
    for match in re.findall(r"Cannot find module '([^']+)'", text or ""):
        if match.startswith(".") or match.startswith("/"):
            continue
        modules.add(match.strip())
    return modules


def install_requirements_file(path: str) -> Tuple[bool, str]:
    """Validates every requirement line before letting pip touch it."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            raw_lines = handle.readlines()
    except OSError:
        return False, t("deps_python_fail")

    specs: List[str] = []
    for line in raw_lines:
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-") or "://" in line or os.sep in line:
            logger.warning("Rejected requirement line: %s", line[:120])
            return False, t("deps_python_fail")
        normalised = re.sub(r"\s+", "", line)
        normalised = re.sub(r"[><~!]=?.*$", "", normalised)
        if not normalised:
            continue
        if not is_valid_package_spec(normalised):
            logger.warning("Rejected requirement spec: %s", line[:120])
            return False, t("deps_python_fail")
        specs.append(normalised)

    if not specs:
        return True, t("deps_python_ok")
    failures = [spec for spec in specs if not install_pip_package(spec)[0]]
    if failures:
        return False, t("deps_python_fail")
    return True, t("deps_python_ok")


def install_package_json(folder: str) -> Tuple[bool, str]:
    if not NODE_AVAILABLE:
        return False, t("deps_node_fail")
    try:
        result = subprocess.run(
            ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"],
            cwd=folder, capture_output=True, text=True, timeout=NPM_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.error("npm install in %s failed: %s", folder, exc)
        return False, t("deps_node_fail")
    if result.returncode == 0:
        return True, t("deps_node_ok")
    logger.warning("npm install failed in %s: %s", folder, (result.stderr or "")[-500:])
    return False, t("deps_node_fail")


# =============================================================================
# 10. ARCHIVE HANDLING
# =============================================================================

PY_ENTRY_CANDIDATES = ("main.py", "bot.py", "app.py", "run.py", "index.py", "start.py")
JS_ENTRY_CANDIDATES = ("index.js", "main.js", "bot.js", "app.js", "server.js")


def extract_zip_safely(zip_path: str, destination: str) -> Tuple[bool, str]:
    """Extracts an archive after checking traversal, symlinks, count and size."""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            members = archive.infolist()
            if len(members) > MAX_ZIP_MEMBERS:
                return False, t("zip_too_big")
            total = 0
            for member in members:
                name = member.filename
                if not name or name.startswith("/") or name.startswith("\\") or ".." in name.replace("\\", "/").split("/"):
                    logger.warning("Blocked archive member %r", name[:120])
                    return False, t("zip_unsafe")
                if re.search(r"[\x00-\x1f]", name):
                    return False, t("zip_unsafe")
                mode = member.external_attr >> 16
                if mode and (mode & 0o170000) == 0o120000:  # symlink
                    logger.warning("Blocked symlink in archive: %r", name[:120])
                    return False, t("zip_unsafe")
                target = os.path.join(destination, name)
                if not inside(destination, target):
                    return False, t("zip_unsafe")
                total += member.file_size
                if total > MAX_ZIP_UNCOMPRESSED_BYTES:
                    return False, t("zip_too_big")
            archive.extractall(destination)
    except zipfile.BadZipFile:
        return False, t("zip_unsafe")
    except OSError as exc:
        logger.error("Archive extraction failed: %s", exc)
        return False, t("generic_error")
    return True, ""


def flatten_single_root(directory: str) -> str:
    """GitHub archives wrap everything in one folder; use it as the project root."""
    entries = [e for e in os.listdir(directory) if e not in (".", "..")]
    if len(entries) == 1:
        candidate = os.path.join(directory, entries[0])
        if os.path.isdir(candidate):
            return candidate
    return directory


def find_entry_point(root: str) -> Tuple[Optional[str], Optional[str]]:
    """Returns (relative entry script, 'py'|'js') or (None, None)."""
    top_level = sorted(os.listdir(root)) if os.path.isdir(root) else []
    for candidate in PY_ENTRY_CANDIDATES:
        if candidate in top_level:
            return candidate, "py"
    for candidate in JS_ENTRY_CANDIDATES:
        if candidate in top_level:
            return candidate, "js"
    py_files = [name for name in top_level if name.endswith(".py") and os.path.isfile(os.path.join(root, name))]
    if py_files:
        return py_files[0], "py"
    js_files = [name for name in top_level if name.endswith(".js") and os.path.isfile(os.path.join(root, name))]
    if js_files:
        return js_files[0], "js"
    return None, None


def move_tree_into(source_root: str, destination: str) -> None:
    for entry in os.listdir(source_root):
        src = os.path.join(source_root, entry)
        dst = os.path.join(destination, entry)
        if not inside(destination, dst):
            continue
        if os.path.isdir(dst):
            shutil.rmtree(dst, ignore_errors=True)
        elif os.path.exists(dst):
            with contextlib.suppress(OSError):
                os.remove(dst)
        shutil.move(src, dst)


# =============================================================================
# 11. UPLOAD AND APPROVAL FLOW
# =============================================================================

def upload_capacity_error(user_id: int) -> Optional[str]:
    limit = file_limit(user_id)
    current = user_file_count(user_id)
    if current >= limit:
        return t("upload_limit_reached", current=current, limit=limit_display(limit))
    used = directory_size(user_folder(user_id))
    if not is_owner(user_id) and used >= USER_DISK_QUOTA_BYTES:
        return t("upload_quota", used_mb=round(used / (1024 * 1024), 1),
                 quota_mb=round(USER_DISK_QUOTA_BYTES / (1024 * 1024)))
    return None


def submit_for_approval(ctx: Ctx, file_id: str, file_name: str, file_type: str,
                        file_size: int, extra_info: str = "") -> bool:
    upload_id = add_pending_upload(
        user_id=ctx.user_id,
        file_id=file_id,
        file_name=file_name,
        file_type=file_type,
        file_size=file_size or 0,
        user_name=ctx.first_name or str(ctx.user_id),
        user_username=ctx.username or "no_username",
        extra_info=extra_info,
    )
    if not upload_id:
        say(ctx, t("generic_error"))
        return False

    say(ctx, t("upload_submitted", file_name=file_name))

    caption = t(
        "admin_new_upload",
        name=ctx.first_name or "-",
        username=ctx.username or "no_username",
        user_id=ctx.user_id,
        file_name=file_name,
        size_kb=max(1, (file_size or 0) // 1024),
        upload_id=upload_id,
        extra=f"\nℹ️ {extra_info}" if extra_info else "",
    )
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ Approve", callback_data=f"up:ok:{upload_id}"),
        types.InlineKeyboardButton("❌ Reject", callback_data=f"up:no:{upload_id}"),
    )
    for admin_id in sorted(admin_ids()):
        sent = api_call(bot.send_document, admin_id, file_id,
                        caption=stylish_text(caption), reply_markup=markup)
        if sent is None:
            send_text(admin_id, caption, reply_markup=markup)
    return True


def process_approved_upload(upload_id: int, admin_chat_id: int) -> Tuple[bool, Optional[int], Optional[str]]:
    """Materialises an approved upload on disk and starts it.

    Returns (success, owner_id, file_name).
    """
    pending = get_pending_upload(upload_id)
    if not pending:
        send_text(admin_chat_id, t("upload_gone"))
        return False, None, None

    owner_id = int(pending["user_id"])
    file_name = sanitize_file_name(pending["file_name"])
    file_type = (pending["file_type"] or "").lower()
    temp_dir: Optional[str] = None

    try:
        if not is_safe_file_name(file_name):
            send_text(admin_chat_id, t("upload_bad_name"))
            return False, owner_id, file_name

        limit = file_limit(owner_id)
        if user_file_count(owner_id) >= limit:
            send_text(admin_chat_id, t("upload_limit_reached",
                                       current=user_file_count(owner_id), limit=limit_display(limit)))
            return False, owner_id, file_name

        file_info = api_call(bot.get_file, pending["file_id"])
        if file_info is None:
            send_text(admin_chat_id, t("approve_failed"))
            return False, owner_id, file_name
        payload = api_call(bot.download_file, file_info.file_path)
        if not payload:
            send_text(admin_chat_id, t("approve_failed"))
            return False, owner_id, file_name
        if len(payload) > MAX_UPLOAD_BYTES:
            send_text(admin_chat_id, t("upload_too_large", limit_mb=MAX_UPLOAD_BYTES // (1024 * 1024)))
            return False, owner_id, file_name

        folder = user_folder(owner_id)

        if file_name.lower().endswith(".zip"):
            temp_dir = tempfile.mkdtemp(prefix=f"upload_{owner_id}_")
            archive_path = os.path.join(temp_dir, "payload.zip")
            with open(archive_path, "wb") as handle:
                handle.write(payload)
            ok, error = extract_zip_safely(archive_path, temp_dir)
            with contextlib.suppress(OSError):
                os.remove(archive_path)
            if not ok:
                send_text(admin_chat_id, error)
                return False, owner_id, file_name

            root = flatten_single_root(temp_dir)
            entry, entry_type = find_entry_point(root)
            if not entry:
                send_text(admin_chat_id, t("zip_no_script"))
                return False, owner_id, file_name

            requirements = os.path.join(root, "requirements.txt")
            if os.path.isfile(requirements):
                ok, message = install_requirements_file(requirements)
                send_text(admin_chat_id, message)
                if not ok:
                    return False, owner_id, file_name

            if os.path.isfile(os.path.join(root, "package.json")):
                ok, message = install_package_json(root)
                send_text(admin_chat_id, message)
                if not ok:
                    return False, owner_id, file_name

            payload_size = directory_size(root)
            if not is_owner(owner_id) and directory_size(folder) + payload_size > USER_DISK_QUOTA_BYTES:
                send_text(admin_chat_id, t("upload_quota",
                                           used_mb=round(directory_size(folder) / (1024 * 1024), 1),
                                           quota_mb=round(USER_DISK_QUOTA_BYTES / (1024 * 1024))))
                return False, owner_id, file_name

            move_tree_into(root, folder)
            entry_name = sanitize_file_name(entry)
            if entry_name != entry:
                with contextlib.suppress(OSError):
                    os.replace(os.path.join(folder, entry), os.path.join(folder, entry_name))
            file_name = entry_name
            file_type = entry_type or "py"
        else:
            file_type = "js" if file_name.lower().endswith(".js") else "py"
            target = os.path.join(folder, file_name)
            if not inside(folder, target):
                send_text(admin_chat_id, t("upload_bad_name"))
                return False, owner_id, file_name
            if not is_owner(owner_id) and directory_size(folder) + len(payload) > USER_DISK_QUOTA_BYTES:
                send_text(admin_chat_id, t("upload_quota",
                                           used_mb=round(directory_size(folder) / (1024 * 1024), 1),
                                           quota_mb=round(USER_DISK_QUOTA_BYTES / (1024 * 1024))))
                return False, owner_id, file_name
            with open(target, "wb") as handle:
                handle.write(payload)

        add_file_record(owner_id, file_name, file_type)
        started, message = start_script(owner_id, file_name, file_type, chat_id=owner_id)
        send_text(admin_chat_id, t("approve_done", file_name=file_name) if started else message)
        return True, owner_id, file_name

    except (OSError, sqlite3.Error) as exc:
        logger.error("Approval of upload %s failed: %s", upload_id, exc, exc_info=True)
        send_text(admin_chat_id, t("approve_failed"))
        return False, owner_id, file_name
    except Exception:
        logger.exception("Unexpected failure approving upload %s", upload_id)
        send_text(admin_chat_id, t("approve_failed"))
        return False, owner_id, file_name
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        delete_pending_upload(upload_id)


# =============================================================================
# 12. GITHUB DEPLOY
# =============================================================================

GITHUB_URL_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/"
    r"(?P<repo>[A-Za-z0-9._-]{1,100}?)(?:\.git)?(?:/tree/(?P<branch>[A-Za-z0-9._/-]{1,100}))?/?$"
)


def parse_github_url(url: str) -> Optional[Tuple[str, str, Optional[str]]]:
    match = GITHUB_URL_RE.match((url or "").strip())
    if not match:
        return None
    return match.group("owner"), match.group("repo"), match.group("branch")


def download_github_archive(owner: str, repo: str, branch: Optional[str],
                            token: Optional[str]) -> Tuple[Optional[bytes], str]:
    """Streams a repository archive with a hard size cap. TLS is always verified."""
    branches = [branch] if branch else ["main", "master"]
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "script-hosting-bot"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    last_reason = "not found"
    for candidate in branches:
        url = f"https://api.github.com/repos/{owner}/{repo}/zipball/{candidate}"
        try:
            with requests.get(url, headers=headers, stream=True, timeout=60, allow_redirects=True) as response:
                if response.status_code == 404:
                    last_reason = "repository or branch not found"
                    continue
                if response.status_code in (401, 403):
                    return None, "access denied - check the token and its permissions"
                if response.status_code != 200:
                    last_reason = f"GitHub returned status {response.status_code}"
                    continue
                declared = response.headers.get("content-length")
                if declared and int(declared) > MAX_UPLOAD_BYTES:
                    return None, "the archive is larger than the allowed size"
                buffer = io.BytesIO()
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    buffer.write(chunk)
                    if buffer.tell() > MAX_UPLOAD_BYTES:
                        return None, "the archive is larger than the allowed size"
                return buffer.getvalue(), ""
        except requests.Timeout:
            last_reason = "the download timed out"
        except requests.RequestException as exc:
            logger.warning("GitHub download error: %s", exc)
            last_reason = "the download failed"
    return None, last_reason


@dataclass
class GithubSession:
    step: str = "url"
    owner: str = ""
    repo: str = ""
    branch: Optional[str] = None
    url: str = ""
    created: float = field(default_factory=time.monotonic)


_GH_LOCK = threading.Lock()
_gh_sessions: Dict[int, GithubSession] = {}
GH_SESSION_TTL = 600


def gh_session(user_id: int) -> Optional[GithubSession]:
    with _GH_LOCK:
        session = _gh_sessions.get(user_id)
        if session and time.monotonic() - session.created > GH_SESSION_TTL:
            _gh_sessions.pop(user_id, None)
            return None
        return session


def gh_set(user_id: int, session: Optional[GithubSession]) -> None:
    with _GH_LOCK:
        if session is None:
            _gh_sessions.pop(user_id, None)
        else:
            _gh_sessions[user_id] = session


def github_fetch_and_submit(ctx: Ctx, token: Optional[str]) -> None:
    session = gh_session(ctx.user_id)
    if not session:
        say(ctx, t("gh_session_expired"))
        return
    status = send_text(ctx.chat_id, t("gh_downloading"))
    payload, reason = download_github_archive(session.owner, session.repo, session.branch, token)
    token = None
    if payload is None:
        if status:
            edit_text(ctx.chat_id, status.message_id, t("gh_download_failed", reason=reason))
        else:
            say(ctx, t("gh_download_failed", reason=reason))
        gh_set(ctx.user_id, None)
        return

    if status:
        edit_text(ctx.chat_id, status.message_id, t("gh_downloaded"))

    file_name = sanitize_file_name(f"{session.repo}_{session.branch or 'default'}.zip")
    stream = io.BytesIO(payload)
    stream.name = file_name
    sent = api_call(bot.send_document, ctx.chat_id, stream, visible_file_name=file_name,
                    caption=stylish_text(t("gh_submitted")))
    if sent is None or not sent.document:
        say(ctx, t("generic_error"))
        gh_set(ctx.user_id, None)
        return

    extra = f"GitHub: {session.url}" + (" (private, token used and discarded)" if token is not None else "")
    submit_for_approval(ctx, sent.document.file_id, file_name, "zip",
                        sent.document.file_size or len(payload), extra_info=extra)
    gh_set(ctx.user_id, None)


# =============================================================================
# 13. AI ASSISTANT
# =============================================================================

_AI_LOCK = threading.Lock()
_ai_sessions: Dict[int, float] = {}
AI_SESSION_TTL = 1800


def ai_session_active(chat_id: int) -> bool:
    with _AI_LOCK:
        started = _ai_sessions.get(chat_id)
        if started is None:
            return False
        if time.monotonic() - started > AI_SESSION_TTL:
            _ai_sessions.pop(chat_id, None)
            return False
        return True


def ai_session_set(chat_id: int, active: bool) -> None:
    with _AI_LOCK:
        if active:
            _ai_sessions[chat_id] = time.monotonic()
        else:
            _ai_sessions.pop(chat_id, None)


def ask_sambanova(prompt: str) -> Optional[str]:
    if not AI_ENABLED:
        return None
    payload = {
        "model": AVAILABLE_MODELS[current_model_key()],
        "messages": [
            {"role": "system", "content": "You are a concise assistant for a Python and Node.js script hosting bot. "
                                          "Answer with practical, correct code and short explanations."},
            {"role": "user", "content": prompt[:6000]},
        ],
        "temperature": 0.6,
        "max_tokens": AI_MAX_TOKENS,
        "top_p": 0.95,
    }
    headers = {"Authorization": f"Bearer {SAMBA_API_KEY}", "Content-Type": "application/json"}

    for attempt in range(3):
        try:
            response = requests.post(SAMBA_URL, headers=headers, json=payload, timeout=AI_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            logger.warning("AI request failed (attempt %s): %s", attempt + 1, exc)
            time.sleep(2 ** attempt)
            continue
        if response.status_code == 429:
            time.sleep(2 ** attempt + 1)
            continue
        if response.status_code != 200:
            logger.warning("AI provider returned %s", response.status_code)
            return None
        try:
            data = response.json()
            return str(data["choices"][0]["message"]["content"])
        except (ValueError, KeyError, IndexError, TypeError):
            logger.warning("AI provider returned an unexpected payload shape")
            return None
    return None


def bot_help_text() -> str:
    lines = [
        "🤖 Bot guide",
        "",
        "Commands",
        "/start - main menu",
        "/help - this guide",
        "/uploadfile - upload .py, .js or .zip",
        "/checkfiles - list your files and control them",
        "/restart - restart your scripts",
        "/stop - stop your scripts",
        "/botspeed - host and latency info",
        "/statistics - bot statistics",
        "/cancel - leave any input mode",
        "/model - show the current AI model",
        "",
        "Files",
        "• Upload a file, an administrator approves it, then it starts automatically.",
        "• Per file you can Start, Stop, Restart, Delete, view Logs and Fix Modules.",
        "• ZIP archives are extracted and the entry point is detected automatically.",
        "",
        "Access levels",
        f"• Free: {FREE_USER_LIMIT} files · Premium: {PREMIUM_USER_LIMIT} · Admin: {ADMIN_FILE_LIMIT} · Owner: unlimited",
        "",
        "Admin commands",
        "/adminpanel, /broadcast, /lockbot, /runningallcode, /stopall, /ban, /unban, /setmodel",
    ]
    return "\n".join(lines)


def handle_ai_message(ctx: Ctx, text: str) -> None:
    lowered = text.lower().strip()
    if lowered in ("help", "how to use", "commands", "guide", "features"):
        say(ctx, bot_help_text())
        return

    modules = extract_missing_python_modules(text)
    if modules:
        installable, blocked = split_installable_modules(modules)
        say(ctx, t("modules_found", modules=", ".join(sorted(modules))))
        results = []
        installed = failed = 0
        for module in sorted(installable):
            ok, _detail = install_pip_package(module_to_package(module))
            results.append(("✅ " if ok else "❌ ") + module)
            installed += 1 if ok else 0
            failed += 0 if ok else 1
        if blocked:
            results.append("⚠️ needs an administrator: " + ", ".join(sorted(blocked)))
        say(ctx, t("modules_summary", file_name="your message", results="\n".join(results) or "-",
                   ok=installed, failed=failed))
        return

    if not AI_ENABLED:
        say(ctx, t("ai_disabled"))
        return

    remaining = cooldown_left(ctx.user_id, "ai", 5)
    if remaining:
        say(ctx, t("cooldown", seconds=remaining))
        return

    api_call(bot.send_chat_action, ctx.chat_id, "typing")
    thinking = send_text(ctx.chat_id, t("ai_thinking"))
    answer = ask_sambanova(text)
    body = answer.strip() if answer else t("ai_unavailable")
    if len(body) > 3900:
        body = body[:3900] + "\n… (truncated)"
    if thinking:
        if edit_text(ctx.chat_id, thinking.message_id, body, styled=False) is None:
            send_text(ctx.chat_id, body, styled=False)
    else:
        send_text(ctx.chat_id, body, styled=False)


def fix_modules_for_file(ctx: Ctx, record: sqlite3.Row) -> None:
    folder = user_folder(int(record["user_id"]))
    log_text = read_log_tail(log_path_for(folder, record["file_name"]), 12000)
    if record["file_type"] == "js":
        modules = extract_missing_node_modules(log_text)
    else:
        modules = extract_missing_python_modules(log_text)
    if not modules:
        send_text(ctx.chat_id, t("modules_none", file_name=record["file_name"]))
        return

    installable, blocked = split_installable_modules(modules)
    send_text(ctx.chat_id, t("modules_found", modules=", ".join(sorted(modules))))
    results, installed, failed = [], 0, 0
    for module in sorted(installable):
        if record["file_type"] == "js":
            ok, _detail = install_npm_package(module, folder)
        else:
            ok, _detail = install_pip_package(module_to_package(module))
        results.append(("✅ " if ok else "❌ ") + module)
        installed += 1 if ok else 0
        failed += 0 if ok else 1
    if blocked:
        results.append("⚠️ needs an administrator: " + ", ".join(sorted(blocked)))
        notify_admins(t("pkg_request_admin", user_id=ctx.user_id, package=", ".join(sorted(blocked))))
    send_text(ctx.chat_id, t("modules_summary", file_name=record["file_name"],
                             results="\n".join(results) or "-", ok=installed, failed=failed))


# =============================================================================
# 14. KEYBOARDS
# =============================================================================

BTN_UPDATES = "📢 𝐔𝐩𝐝𝐚𝐭𝐞𝐬 𝐂𝐡𝐚𝐧𝐧𝐞𝐥"
BTN_UPLOAD = "🌏 Upload"
BTN_FILES = "📁 𝐌𝐲 𝐅𝐢𝐥𝐞𝐬"
BTN_SPEED = "⚡ 𝐁𝐨𝐭 𝐒𝐩𝐞𝐞𝐝"
BTN_STATUS = "🚀 𝐒𝐭𝐚𝐭𝐮𝐬"
BTN_RESTART = "🔄 𝐑𝐞𝐬𝐭𝐚𝐫𝐭"
BTN_STOP = "⏹ 𝐒𝐭𝐨𝐩"
BTN_PACKAGES = "⚙️ Recommended Install"
BTN_GITHUB = "🌐 𝐆𝐈𝐓𝐇𝐔𝐁"
BTN_CONTACT = "📞 𝐂𝐨𝐧𝐭𝐚𝐜𝐭 𝐎𝐰𝐧𝐞𝐫"
BTN_AGENT = "🤖 𝐀𝐆𝐄𝐍𝐓"
BTN_SUBS = "💳 𝐒𝐮𝐛𝐬𝐜𝐫𝐢𝐩𝐭𝐢𝐨𝐧𝐬"
BTN_BROADCAST = "📢 𝐁𝐫𝐨𝐚𝐝𝐜𝐚𝐬𝐭"
BTN_LOCK = "🔒 𝐋𝐨𝐜𝐤 𝐁𝐨𝐭"
BTN_RUN_ALL = "🟢 𝐑𝐮𝐧𝐧𝐢𝐧𝐠 𝐀𝐥𝐥 𝐂𝐨𝐝𝐞"
BTN_ADMIN = "🛠️ 𝐀𝐝𝐦𝐢𝐧 𝐏𝐚𝐧𝐞𝐥"

USER_KEYBOARD_LAYOUT = [
    [BTN_UPDATES],
    [BTN_UPLOAD, BTN_FILES],
    [BTN_SPEED, BTN_STATUS],
    [BTN_RESTART, BTN_STOP],
    [BTN_PACKAGES, BTN_AGENT],
    [BTN_GITHUB, BTN_CONTACT],
]

ADMIN_KEYBOARD_LAYOUT = [
    [BTN_UPDATES],
    [BTN_UPLOAD, BTN_FILES],
    [BTN_SPEED, BTN_STATUS],
    [BTN_RESTART, BTN_STOP],
    [BTN_SUBS, BTN_BROADCAST],
    [BTN_LOCK, BTN_RUN_ALL],
    [BTN_ADMIN, BTN_PACKAGES],
    [BTN_AGENT, BTN_GITHUB],
    [BTN_CONTACT],
]


def main_reply_keyboard(user_id: int) -> types.ReplyKeyboardMarkup:
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    layout = ADMIN_KEYBOARD_LAYOUT if is_admin(user_id) else USER_KEYBOARD_LAYOUT
    for row in layout:
        markup.add(*[types.KeyboardButton(label) for label in row])
    return markup


def main_inline_menu(user_id: int) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=2)
    if UPDATES_CHANNEL_URL:
        markup.add(types.InlineKeyboardButton(BTN_UPDATES, url=UPDATES_CHANNEL_URL))
    markup.add(
        types.InlineKeyboardButton(BTN_UPLOAD, callback_data="nav:upload"),
        types.InlineKeyboardButton(BTN_FILES, callback_data="nav:files"),
    )
    markup.add(
        types.InlineKeyboardButton(BTN_SPEED, callback_data="nav:speed"),
        types.InlineKeyboardButton(BTN_STATUS, callback_data="nav:stats"),
    )
    markup.add(
        types.InlineKeyboardButton("📦 Packages", callback_data="nav:packages"),
        types.InlineKeyboardButton("🤖 AI Assistant", callback_data="nav:ai"),
    )
    markup.add(types.InlineKeyboardButton(BTN_GITHUB, callback_data="nav:github"))
    if is_admin(user_id):
        markup.add(
            types.InlineKeyboardButton(BTN_SUBS, callback_data="nav:subs"),
            types.InlineKeyboardButton(BTN_BROADCAST, callback_data="nav:broadcast"),
        )
        markup.add(
            types.InlineKeyboardButton("🔓 Unlock Bot" if bot_is_locked() else BTN_LOCK,
                                       callback_data="adm:unlock" if bot_is_locked() else "adm:lock"),
            types.InlineKeyboardButton("🟢 Run All Scripts", callback_data="adm:runall"),
        )
        markup.add(types.InlineKeyboardButton(BTN_ADMIN, callback_data="nav:admin"))
    if OWNER_CONTACT_URL:
        markup.add(types.InlineKeyboardButton(BTN_CONTACT, url=OWNER_CONTACT_URL))
    return markup


def file_controls_markup(record: sqlite3.Row, running: bool) -> types.InlineKeyboardMarkup:
    fid = int(record["id"])
    markup = types.InlineKeyboardMarkup(row_width=2)
    if running:
        markup.row(
            types.InlineKeyboardButton("🔴 Stop", callback_data=f"sp:{fid}"),
            types.InlineKeyboardButton("🔄 Restart", callback_data=f"rs:{fid}"),
        )
    else:
        markup.row(
            types.InlineKeyboardButton("🟢 Start", callback_data=f"st:{fid}"),
            types.InlineKeyboardButton("🔄 Restart", callback_data=f"rs:{fid}"),
        )
    markup.row(
        types.InlineKeyboardButton("📜 Logs", callback_data=f"lg:{fid}"),
        types.InlineKeyboardButton("🔧 Fix Modules", callback_data=f"fx:{fid}"),
    )
    markup.row(
        types.InlineKeyboardButton("🗑️ Delete", callback_data=f"dl:{fid}"),
        types.InlineKeyboardButton("🔙 Back", callback_data="nav:files"),
    )
    return markup


def admin_panel_markup() -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton("➕ Add Admin", callback_data="adm:addadmin"),
        types.InlineKeyboardButton("➖ Remove Admin", callback_data="adm:deladmin"),
    )
    markup.row(types.InlineKeyboardButton("📋 List Admins", callback_data="adm:listadmins"))
    markup.row(types.InlineKeyboardButton("🔧 Set User Limit", callback_data="adm:setlimit"))
    markup.row(types.InlineKeyboardButton("🤖 Change AI Model", callback_data="adm:model"))
    markup.row(types.InlineKeyboardButton("🔙 Back to Main", callback_data="nav:main"))
    return markup


def subscription_markup() -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton("➕ Add", callback_data="sub:add"),
        types.InlineKeyboardButton("➖ Remove", callback_data="sub:del"),
    )
    markup.row(types.InlineKeyboardButton("🔍 Check", callback_data="sub:check"))
    markup.row(types.InlineKeyboardButton("🔙 Back to Main", callback_data="nav:main"))
    return markup


def model_markup() -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=1)
    for key, model in AVAILABLE_MODELS.items():
        prefix = "✅ " if key == current_model_key() else ""
        markup.add(types.InlineKeyboardButton(f"{prefix}{key} - {model}", callback_data=f"mdl:{key}"))
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="nav:admin"))
    return markup


def package_markup(user_id: int) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=1)
    if is_admin(user_id):
        markup.add(types.InlineKeyboardButton("✅ Install recommended set", callback_data="pkg:rec"))
    markup.add(types.InlineKeyboardButton("❌ Close", callback_data="pkg:close"))
    return markup


# =============================================================================
# 15. PENDING INPUT STATE (replaces register_next_step_handler)
# =============================================================================

PENDING_TTL = 600


@dataclass
class PendingInput:
    action: str
    created: float = field(default_factory=time.monotonic)


_PENDING_LOCK = threading.Lock()
_pending: Dict[Tuple[int, int], PendingInput] = {}


def pending_key(ctx: Ctx) -> Tuple[int, int]:
    return (ctx.chat_id, ctx.user_id)


def set_pending(ctx: Ctx, action: str) -> None:
    with _PENDING_LOCK:
        _pending[pending_key(ctx)] = PendingInput(action=action)


def get_pending(chat_id: int, user_id: int) -> Optional[str]:
    with _PENDING_LOCK:
        entry = _pending.get((chat_id, user_id))
        if not entry:
            return None
        if time.monotonic() - entry.created > PENDING_TTL:
            _pending.pop((chat_id, user_id), None)
            return None
        return entry.action


def clear_pending(chat_id: int, user_id: int) -> bool:
    with _PENDING_LOCK:
        return _pending.pop((chat_id, user_id), None) is not None


def ask_for(ctx: Ctx, action: str, prompt: str) -> None:
    set_pending(ctx, action)
    say(ctx, prompt)


# ------------------------------------------------------------- broadcast ---

@dataclass
class BroadcastDraft:
    admin_id: int
    from_chat_id: int
    message_id: int
    created: float = field(default_factory=time.monotonic)


_BC_LOCK = threading.Lock()
_broadcasts: Dict[str, BroadcastDraft] = {}
BROADCAST_TTL = 900
_bc_counter = 0


def store_broadcast(draft: BroadcastDraft) -> str:
    global _bc_counter
    with _BC_LOCK:
        _bc_counter += 1
        token = str(_bc_counter)
        _broadcasts[token] = draft
        for key, value in list(_broadcasts.items()):
            if time.monotonic() - value.created > BROADCAST_TTL:
                _broadcasts.pop(key, None)
        return token


def take_broadcast(token: str) -> Optional[BroadcastDraft]:
    with _BC_LOCK:
        draft = _broadcasts.pop(token, None)
    if draft and time.monotonic() - draft.created > BROADCAST_TTL:
        return None
    return draft


# =============================================================================
# 16. FEATURE LOGIC
# =============================================================================

def logic_welcome(ctx: Ctx) -> None:
    is_new = register_active_user(ctx.user_id)
    if is_new:
        send_text(OWNER_ID, t("new_user_owner", name=ctx.first_name or "-",
                              username=ctx.username or "no_username", user_id=ctx.user_id))

    limit = file_limit(ctx.user_id)
    header = f"┏━━━━━━━━━━━━━━━━━━┓\n   {BRAND_NAME}\n┗━━━━━━━━━━━━━━━━━━┛\n\n"
    body = header + t(
        "welcome",
        name=ctx.first_name or str(ctx.user_id),
        user_id=ctx.user_id,
        level=access_level(ctx.user_id),
        files=user_file_count(ctx.user_id),
        limit=limit_display(limit),
    )
    api_call(bot.send_message, ctx.chat_id, stylish_text(body),
             reply_markup=main_reply_keyboard(ctx.user_id), disable_web_page_preview=True)
    send_text(ctx.chat_id, t("menu_hint"), reply_markup=main_inline_menu(ctx.user_id))


def logic_updates(ctx: Ctx) -> None:
    if not UPDATES_CHANNEL_URL and not REQUIRED_CHANNELS:
        say(ctx, t("channels_none"))
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    if UPDATES_CHANNEL_URL:
        markup.add(types.InlineKeyboardButton("📢 Updates", url=UPDATES_CHANNEL_URL))
    for channel in REQUIRED_CHANNELS:
        if channel.startswith("@"):
            markup.add(types.InlineKeyboardButton(channel, url=f"https://t.me/{channel.lstrip('@')}"))
    say(ctx, t("channels_title"), reply_markup=markup)


def logic_contact(ctx: Ctx) -> None:
    if not OWNER_CONTACT_URL:
        say(ctx, t("contact_none"))
        return
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(BTN_CONTACT, url=OWNER_CONTACT_URL))
    say(ctx, t("contact_title"), reply_markup=markup)


def logic_upload(ctx: Ctx) -> None:
    error = upload_capacity_error(ctx.user_id)
    if error:
        say(ctx, error)
        return
    pending = pending_upload_count(ctx.user_id)
    if pending:
        say(ctx, t("upload_pending_exists", count=pending))
    say(ctx, t("upload_prompt"))


def logic_files(ctx: Ctx, edit_message_id: Optional[int] = None) -> None:
    records = list_user_files(ctx.user_id)
    if not records:
        if edit_message_id:
            edit_text(ctx.chat_id, edit_message_id, t("files_empty"))
        else:
            say(ctx, t("files_empty"))
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for record in records:
        running = is_running(ctx.user_id, record["file_name"])
        status = "🟢" if running else "🔴"
        label = f"{status} {record['file_name']} ({record['file_type']})"
        markup.add(types.InlineKeyboardButton(label[:64], callback_data=f"f:{int(record['id'])}"))
    if edit_message_id:
        edit_text(ctx.chat_id, edit_message_id, t("files_title"), reply_markup=markup)
    else:
        say(ctx, t("files_title"), reply_markup=markup)


def file_status_text(record: sqlite3.Row) -> str:
    running = is_running(int(record["user_id"]), record["file_name"])
    return t("file_controls", file_name=record["file_name"], file_type=record["file_type"],
             owner_id=int(record["user_id"]), status="🟢 Running" if running else "🔴 Stopped")


def show_file_controls(ctx: Ctx, record: sqlite3.Row, edit_message_id: Optional[int] = None) -> None:
    running = is_running(int(record["user_id"]), record["file_name"])
    markup = file_controls_markup(record, running)
    text = file_status_text(record)
    if edit_message_id:
        if edit_text(ctx.chat_id, edit_message_id, text, reply_markup=markup) is not None:
            return
    say(ctx, text, reply_markup=markup)


def can_manage(ctx: Ctx, record: sqlite3.Row) -> bool:
    return ctx.user_id == int(record["user_id"]) or is_admin(ctx.user_id)


def logic_stop_mine(ctx: Ctx) -> None:
    stopped = stop_user_scripts(ctx.user_id)
    say(ctx, t("stop_all_mine", count=stopped))


def logic_restart_mine(ctx: Ctx) -> None:
    records = list_user_files(ctx.user_id)
    if not records:
        say(ctx, t("files_empty"))
        return
    stop_user_scripts(ctx.user_id)
    time.sleep(1)
    started = 0
    for record in records:
        ok, _message = start_script(int(record["user_id"]), record["file_name"],
                                   record["file_type"], chat_id=ctx.chat_id)
        started += 1 if ok else 0
        time.sleep(0.3)
    say(ctx, t("restart_all_mine", started=started))


def logic_run_all(ctx: Ctx) -> None:
    say(ctx, t("run_all_working"))
    started = 0
    for record in all_file_records():
        owner_id = int(record["user_id"])
        if is_banned(owner_id) or is_running(owner_id, record["file_name"]):
            continue
        ok, _message = start_script(owner_id, record["file_name"], record["file_type"], chat_id=owner_id)
        started += 1 if ok else 0
        time.sleep(0.3)
    send_text(ctx.chat_id, t("run_all_started", count=started))


def logic_speed(ctx: Ctx) -> None:
    started = time.monotonic()
    placeholder = say(ctx, t("speed_testing"))
    api_call(bot.send_chat_action, ctx.chat_id, "typing")
    latency = round((time.monotonic() - started) * 1000, 1)

    cpu_label = "n/a"
    with contextlib.suppress(Exception):
        frequency = psutil.cpu_freq()
        cores = psutil.cpu_count(logical=True) or 1
        if frequency and frequency.current:
            cpu_label = f"{round(frequency.current / 1000, 2)} GHz x {cores}"
        else:
            cpu_label = f"{cores} core(s)"

    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(DATA_DIR)
    text = t(
        "speed_result",
        latency=latency,
        cpu=cpu_label,
        ram_used=round(memory.used / (1024 ** 3), 2),
        ram_total=round(memory.total / (1024 ** 3), 2),
        disk_free=round(disk.free / (1024 ** 3), 2),
        state="🔒 Locked" if bot_is_locked() else "🔓 Unlocked",
        level=access_level(ctx.user_id),
    )
    if placeholder:
        if edit_text(ctx.chat_id, placeholder.message_id, text) is None:
            send_text(ctx.chat_id, text)
    else:
        send_text(ctx.chat_id, text)


def uptime_text() -> str:
    delta = datetime.now() - bot_start_time()
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{delta.days}d {hours}h {minutes}m {seconds}s"


def logic_stats(ctx: Ctx) -> None:
    common = {
        "users": active_user_count(),
        "files": total_file_count(),
        "running": running_count(),
        "uptime": uptime_text(),
    }
    if is_admin(ctx.user_id):
        say(ctx, t("stats_admin", pending=pending_upload_count(), banned=banned_count(),
                   admins=len(admin_ids()), premium=premium_count(),
                   locked="yes" if bot_is_locked() else "no", **common))
    else:
        say(ctx, t("stats_user", **common))


def logic_admin_panel(ctx: Ctx, edit_message_id: Optional[int] = None) -> None:
    if edit_message_id and edit_text(ctx.chat_id, edit_message_id, t("admin_panel"),
                                     reply_markup=admin_panel_markup()) is not None:
        return
    say(ctx, t("admin_panel"), reply_markup=admin_panel_markup())


def logic_subs_panel(ctx: Ctx, edit_message_id: Optional[int] = None) -> None:
    if edit_message_id and edit_text(ctx.chat_id, edit_message_id, t("subs_panel"),
                                     reply_markup=subscription_markup()) is not None:
        return
    say(ctx, t("subs_panel"), reply_markup=subscription_markup())


def logic_lock_toggle(ctx: Ctx) -> None:
    new_state = not bot_is_locked()
    set_bot_locked(new_state)
    say(ctx, t("lock_state", state="locked" if new_state else "unlocked"))


def logic_packages(ctx: Ctx) -> None:
    if is_admin(ctx.user_id):
        set_pending(ctx, "pkg_install")
        say(ctx, t("pkg_menu"), reply_markup=package_markup(ctx.user_id))
    else:
        set_pending(ctx, "pkg_request")
        say(ctx, t("pkg_request_menu"))


def logic_ai(ctx: Ctx) -> None:
    if not AI_ENABLED:
        say(ctx, t("ai_disabled"))
        say(ctx, bot_help_text())
        return
    ai_session_set(ctx.chat_id, True)
    say(ctx, t("ai_welcome", model=f"{current_model_key()} ({AVAILABLE_MODELS[current_model_key()]})"))


def logic_github(ctx: Ctx) -> None:
    error = upload_capacity_error(ctx.user_id)
    if error:
        say(ctx, error)
        return
    gh_set(ctx.user_id, GithubSession(step="url"))
    ask_for(ctx, "gh_url", t("gh_prompt"))


def logic_broadcast(ctx: Ctx) -> None:
    ask_for(ctx, "broadcast", t("broadcast_prompt"))


def execute_broadcast(draft: BroadcastDraft, admin_chat_id: int) -> None:
    sent = failed = 0
    for user_id in active_user_ids():
        if is_banned(user_id):
            continue
        result = api_call(bot.copy_message, user_id, draft.from_chat_id, draft.message_id)
        if result is None:
            failed += 1
        else:
            sent += 1
        time.sleep(0.06)
    send_text(admin_chat_id, t("broadcast_done", sent=sent, failed=failed))


# ------------------------------------------------------- pending processors ---

def process_admin_add(ctx: Ctx, message: types.Message) -> None:
    try:
        target = int((message.text or "").strip())
    except ValueError:
        say(ctx, t("invalid_user_id"))
        return
    if target == OWNER_ID:
        say(ctx, t("admin_is_owner"))
        return
    if add_admin(target):
        say(ctx, t("admin_added", user_id=target))
        send_text(target, "👑 You have been granted administrator access.")
    else:
        say(ctx, t("admin_is_owner") if is_owner(target) else t("admin_added", user_id=target))


def process_admin_remove(ctx: Ctx, message: types.Message) -> None:
    try:
        target = int((message.text or "").strip())
    except ValueError:
        say(ctx, t("invalid_user_id"))
        return
    if target == OWNER_ID:
        say(ctx, t("admin_cannot_remove_owner"))
        return
    say(ctx, t("admin_removed", user_id=target) if remove_admin(target) else t("admin_not_admin"))


def process_set_limit(ctx: Ctx, message: types.Message) -> None:
    parts = (message.text or "").split()
    if len(parts) != 2:
        say(ctx, t("limit_invalid"))
        return
    try:
        target = int(parts[0])
    except ValueError:
        say(ctx, t("invalid_user_id"))
        return
    if parts[1].lower() in ("default", "reset", "clear", "none"):
        clear_custom_limit(target)
        say(ctx, t("limit_cleared", user_id=target))
        return
    try:
        limit = int(parts[1])
    except ValueError:
        say(ctx, t("limit_invalid"))
        return
    if limit < 0 or limit > 10000:
        say(ctx, t("limit_invalid"))
        return
    set_custom_limit(target, limit)
    say(ctx, t("limit_set", user_id=target, limit=limit))


def process_sub_add(ctx: Ctx, message: types.Message) -> None:
    parts = (message.text or "").split()
    if len(parts) != 2:
        say(ctx, t("subs_invalid"))
        return
    try:
        target, days = int(parts[0]), int(parts[1])
    except ValueError:
        say(ctx, t("subs_invalid"))
        return
    if days <= 0 or days > 3650:
        say(ctx, t("subs_invalid"))
        return
    current = subscription_expiry(target)
    base = current if current and current > datetime.now() else datetime.now()
    expiry = base + timedelta(days=days)
    save_subscription(target, expiry)
    stamp = expiry.strftime("%Y-%m-%d")
    say(ctx, t("subs_added", user_id=target, expiry=stamp))
    send_text(target, t("subs_user_notice", expiry=stamp))


def process_sub_remove(ctx: Ctx, message: types.Message) -> None:
    try:
        target = int((message.text or "").strip())
    except ValueError:
        say(ctx, t("invalid_user_id"))
        return
    say(ctx, t("subs_removed", user_id=target) if remove_subscription(target) else t("subs_none", user_id=target))


def process_sub_check(ctx: Ctx, message: types.Message) -> None:
    try:
        target = int((message.text or "").strip())
    except ValueError:
        say(ctx, t("invalid_user_id"))
        return
    expiry = subscription_expiry(target)
    if not expiry:
        say(ctx, t("subs_none", user_id=target))
    elif expiry > datetime.now():
        say(ctx, t("subs_active", user_id=target, expiry=expiry.strftime("%Y-%m-%d"),
                   days=(expiry - datetime.now()).days))
    else:
        say(ctx, t("subs_expired", user_id=target, expiry=expiry.strftime("%Y-%m-%d")))


def process_pkg_install(ctx: Ctx, message: types.Message) -> None:
    spec = (message.text or "").strip()
    if not is_valid_package_spec(spec):
        say(ctx, t("pkg_invalid", package=spec[:60]))
        return
    remaining = cooldown_left(ctx.user_id, "install", 10)
    if remaining:
        say(ctx, t("cooldown", seconds=remaining))
        return
    say(ctx, t("pkg_installing", package=spec))
    ok, detail = install_pip_package(spec)
    send_text(ctx.chat_id, detail)
    logger.info("Admin %s installed %s: %s", ctx.user_id, spec, ok)


def process_pkg_request(ctx: Ctx, message: types.Message) -> None:
    spec = (message.text or "").strip()
    if not is_valid_package_spec(spec):
        say(ctx, t("pkg_invalid", package=spec[:60]))
        return
    remaining = cooldown_left(ctx.user_id, "pkg_request", 60)
    if remaining:
        say(ctx, t("cooldown", seconds=remaining))
        return
    if is_allowlisted_package(spec) and ALLOW_AUTO_PACKAGE_INSTALL:
        say(ctx, t("pkg_installing", package=spec))
        _ok, detail = install_pip_package(spec)
        send_text(ctx.chat_id, detail)
        return
    notify_admins(t("pkg_request_admin", user_id=ctx.user_id, package=spec))
    say(ctx, t("pkg_requested", package=spec))


def process_broadcast(ctx: Ctx, message: types.Message) -> None:
    token = store_broadcast(BroadcastDraft(admin_id=ctx.user_id, from_chat_id=message.chat.id,
                                           message_id=message.message_id))
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ Confirm", callback_data=f"bc:ok:{token}"),
        types.InlineKeyboardButton("❌ Cancel", callback_data=f"bc:no:{token}"),
    )
    say(ctx, t("broadcast_confirm", count=active_user_count()), reply_markup=markup)


def process_gh_url(ctx: Ctx, message: types.Message) -> None:
    parsed = parse_github_url(message.text or "")
    if not parsed:
        say(ctx, t("gh_bad_url"))
        set_pending(ctx, "gh_url")
        return
    owner, repo, branch = parsed
    gh_set(ctx.user_id, GithubSession(step="type", owner=owner, repo=repo, branch=branch,
                                      url=(message.text or "").strip()))
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("🌐 Public", callback_data="gh:public"),
        types.InlineKeyboardButton("🔒 Private", callback_data="gh:private"),
    )
    say(ctx, t("gh_private_q"), reply_markup=markup)


def process_gh_token(ctx: Ctx, message: types.Message) -> None:
    token = (message.text or "").strip()
    api_call(bot.delete_message, message.chat.id, message.message_id)
    if not token or len(token) < 20 or " " in token:
        say(ctx, "❌ That does not look like a GitHub token. Start again from the menu.")
        gh_set(ctx.user_id, None)
        return
    threading.Thread(target=github_fetch_and_submit, args=(ctx, token),
                     name="gh-download", daemon=True).start()


PENDING_PROCESSORS = {
    "admin_add": (process_admin_add, "owner"),
    "admin_remove": (process_admin_remove, "owner"),
    "set_limit": (process_set_limit, "admin"),
    "sub_add": (process_sub_add, "admin"),
    "sub_remove": (process_sub_remove, "admin"),
    "sub_check": (process_sub_check, "admin"),
    "pkg_install": (process_pkg_install, "admin"),
    "pkg_request": (process_pkg_request, "user"),
    "broadcast": (process_broadcast, "admin"),
    "gh_url": (process_gh_url, "user"),
    "gh_token": (process_gh_token, "user"),
}


# =============================================================================
# 17. BUTTON ROUTING
# =============================================================================

BUTTON_TEXT_TO_LOGIC = {
    BTN_UPDATES: logic_updates,
    BTN_UPLOAD: logic_upload,
    BTN_FILES: logic_files,
    BTN_SPEED: logic_speed,
    BTN_STATUS: logic_stats,
    BTN_RESTART: logic_restart_mine,
    BTN_STOP: logic_stop_mine,
    BTN_PACKAGES: logic_packages,
    BTN_GITHUB: logic_github,
    BTN_CONTACT: logic_contact,
    BTN_AGENT: logic_ai,
    BTN_SUBS: logic_subs_panel,
    BTN_BROADCAST: logic_broadcast,
    BTN_LOCK: logic_lock_toggle,
    BTN_RUN_ALL: logic_run_all,
    BTN_ADMIN: logic_admin_panel,
}

ADMIN_ONLY_BUTTONS = {BTN_SUBS, BTN_BROADCAST, BTN_LOCK, BTN_RUN_ALL, BTN_ADMIN}


def is_menu_text(text: Optional[str]) -> bool:
    return bool(text) and text in BUTTON_TEXT_TO_LOGIC


def is_command(text: Optional[str]) -> bool:
    return bool(text) and text.startswith("/")


def gate(ctx: Ctx, require_unlocked: bool = True) -> bool:
    """Ban check, channel verification and (optionally) the global lock."""
    if not ensure_access(ctx):
        return False
    if require_unlocked and not ensure_unlocked(ctx):
        return False
    return True


# =============================================================================
# 18. COMMAND HANDLERS
# =============================================================================

@bot.message_handler(commands=["start"])
def cmd_start(message: types.Message) -> None:
    ctx = ctx_from_message(message)
    clear_pending(ctx.chat_id, ctx.user_id)
    ai_session_set(ctx.chat_id, False)
    if not gate(ctx):
        return
    logic_welcome(ctx)


@bot.message_handler(commands=["help"])
def cmd_help(message: types.Message) -> None:
    ctx = ctx_from_message(message)
    if not gate(ctx, require_unlocked=False):
        return
    say(ctx, bot_help_text())


@bot.message_handler(commands=["cancel"])
def cmd_cancel(message: types.Message) -> None:
    ctx = ctx_from_message(message)
    had_pending = clear_pending(ctx.chat_id, ctx.user_id)
    had_ai = ai_session_active(ctx.chat_id)
    ai_session_set(ctx.chat_id, False)
    gh_set(ctx.user_id, None)
    say(ctx, t("cancelled") if (had_pending or had_ai) else t("nothing_to_cancel"))


@bot.message_handler(commands=["uploadfile"])
def cmd_uploadfile(message: types.Message) -> None:
    ctx = ctx_from_message(message)
    if not gate(ctx):
        return
    logic_upload(ctx)


@bot.message_handler(commands=["checkfiles", "myfiles"])
def cmd_checkfiles(message: types.Message) -> None:
    ctx = ctx_from_message(message)
    if not gate(ctx):
        return
    logic_files(ctx)


@bot.message_handler(commands=["restart"])
def cmd_restart(message: types.Message) -> None:
    ctx = ctx_from_message(message)
    if not gate(ctx):
        return
    logic_restart_mine(ctx)


@bot.message_handler(commands=["stop"])
def cmd_stop(message: types.Message) -> None:
    ctx = ctx_from_message(message)
    if not gate(ctx):
        return
    logic_stop_mine(ctx)


@bot.message_handler(commands=["stopall"])
def cmd_stop_all(message: types.Message) -> None:
    ctx = ctx_from_message(message)
    if not gate(ctx, require_unlocked=False) or not require_admin(ctx):
        return
    stopped = stop_all_scripts()
    say(ctx, t("stop_all_admin", count=stopped) if stopped else t("no_scripts_running"))


@bot.message_handler(commands=["botspeed", "speed"])
def cmd_botspeed(message: types.Message) -> None:
    ctx = ctx_from_message(message)
    if not gate(ctx, require_unlocked=False):
        return
    logic_speed(ctx)


@bot.message_handler(commands=["statistics", "stats"])
def cmd_statistics(message: types.Message) -> None:
    ctx = ctx_from_message(message)
    if not gate(ctx, require_unlocked=False):
        return
    logic_stats(ctx)


@bot.message_handler(commands=["ping"])
def cmd_ping(message: types.Message) -> None:
    ctx = ctx_from_message(message)
    if not gate(ctx, require_unlocked=False):
        return
    started = time.monotonic()
    sent = say(ctx, "🏓 Pong!")
    latency = round((time.monotonic() - started) * 1000, 1)
    if sent:
        edit_text(ctx.chat_id, sent.message_id, f"🏓 Pong! {latency} ms")


@bot.message_handler(commands=["github"])
def cmd_github(message: types.Message) -> None:
    ctx = ctx_from_message(message)
    if not gate(ctx):
        return
    logic_github(ctx)


@bot.message_handler(commands=["packages"])
def cmd_packages(message: types.Message) -> None:
    ctx = ctx_from_message(message)
    if not gate(ctx):
        return
    logic_packages(ctx)


@bot.message_handler(commands=["ai", "agent"])
def cmd_ai(message: types.Message) -> None:
    ctx = ctx_from_message(message)
    if not gate(ctx):
        return
    logic_ai(ctx)


@bot.message_handler(commands=["model"])
def cmd_model(message: types.Message) -> None:
    ctx = ctx_from_message(message)
    if not gate(ctx, require_unlocked=False):
        return
    if not AI_ENABLED:
        say(ctx, t("ai_disabled"))
        return
    say(ctx, t("ai_model_current", model=f"{current_model_key()} ({AVAILABLE_MODELS[current_model_key()]})"))


@bot.message_handler(commands=["setmodel"])
def cmd_setmodel(message: types.Message) -> None:
    ctx = ctx_from_message(message)
    if not gate(ctx, require_unlocked=False) or not require_admin(ctx):
        return
    if not AI_ENABLED:
        say(ctx, t("ai_disabled"))
        return
    say(ctx, t("ai_model_prompt"), reply_markup=model_markup())


@bot.message_handler(commands=["adminpanel"])
def cmd_adminpanel(message: types.Message) -> None:
    ctx = ctx_from_message(message)
    if not gate(ctx, require_unlocked=False) or not require_admin(ctx):
        return
    logic_admin_panel(ctx)


@bot.message_handler(commands=["subscriptions"])
def cmd_subscriptions(message: types.Message) -> None:
    ctx = ctx_from_message(message)
    if not gate(ctx, require_unlocked=False) or not require_admin(ctx):
        return
    logic_subs_panel(ctx)


@bot.message_handler(commands=["broadcast"])
def cmd_broadcast(message: types.Message) -> None:
    ctx = ctx_from_message(message)
    if not gate(ctx, require_unlocked=False) or not require_admin(ctx):
        return
    logic_broadcast(ctx)


@bot.message_handler(commands=["lockbot"])
def cmd_lockbot(message: types.Message) -> None:
    ctx = ctx_from_message(message)
    if not gate(ctx, require_unlocked=False) or not require_admin(ctx):
        return
    logic_lock_toggle(ctx)


@bot.message_handler(commands=["runningallcode", "runall"])
def cmd_runall(message: types.Message) -> None:
    ctx = ctx_from_message(message)
    if not gate(ctx, require_unlocked=False) or not require_admin(ctx):
        return
    threading.Thread(target=logic_run_all, args=(ctx,), name="run-all", daemon=True).start()


@bot.message_handler(commands=["ban"])
def cmd_ban(message: types.Message) -> None:
    ctx = ctx_from_message(message)
    if not gate(ctx, require_unlocked=False) or not require_admin(ctx):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        say(ctx, t("ban_usage"))
        return
    try:
        target = int(parts[1])
    except ValueError:
        say(ctx, t("invalid_user_id"))
        return
    if is_admin(target):
        say(ctx, t("ban_protected"))
        return
    if ban_user(target):
        stop_user_scripts(target)
        say(ctx, t("ban_done", user_id=target))
        send_text(target, t("ban_notice"))
    else:
        say(ctx, t("ban_failed", user_id=target))


@bot.message_handler(commands=["unban"])
def cmd_unban(message: types.Message) -> None:
    ctx = ctx_from_message(message)
    if not gate(ctx, require_unlocked=False) or not require_admin(ctx):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        say(ctx, t("unban_usage"))
        return
    try:
        target = int(parts[1])
    except ValueError:
        say(ctx, t("invalid_user_id"))
        return
    if unban_user(target):
        say(ctx, t("unban_done", user_id=target))
        send_text(target, t("unban_notice"))
    else:
        say(ctx, t("unban_missing", user_id=target))


# =============================================================================
# 19. DOCUMENT HANDLER
# =============================================================================

@bot.message_handler(content_types=["document"])
def on_document(message: types.Message) -> None:
    ctx = ctx_from_message(message)
    if not gate(ctx):
        return

    document = message.document
    raw_name = document.file_name or ""
    pending_action = get_pending(ctx.chat_id, ctx.user_id)

    # requirements.txt sent from the package menu (administrators only)
    if pending_action == "pkg_install" and raw_name.lower() == "requirements.txt" and is_admin(ctx.user_id):
        clear_pending(ctx.chat_id, ctx.user_id)
        file_info = api_call(bot.get_file, document.file_id)
        payload = api_call(bot.download_file, file_info.file_path) if file_info else None
        if not payload:
            say(ctx, t("generic_error"))
            return
        temp_dir = tempfile.mkdtemp(prefix="reqs_")
        try:
            path = os.path.join(temp_dir, "requirements.txt")
            with open(path, "wb") as handle:
                handle.write(payload[: 256 * 1024])
            say(ctx, t("pkg_batch_start", count="all listed"))
            _ok, detail = install_requirements_file(path)
            send_text(ctx.chat_id, detail)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
        return

    clear_pending(ctx.chat_id, ctx.user_id)
    ai_session_set(ctx.chat_id, False)

    if not raw_name:
        say(ctx, t("upload_no_name"))
        return

    file_name = sanitize_file_name(raw_name)
    if not is_safe_file_name(file_name):
        say(ctx, t("upload_bad_name"))
        return
    if not file_name.lower().endswith(ALLOWED_UPLOAD_EXTENSIONS):
        say(ctx, t("upload_bad_ext"))
        return
    if (document.file_size or 0) > MAX_UPLOAD_BYTES:
        say(ctx, t("upload_too_large", limit_mb=MAX_UPLOAD_BYTES // (1024 * 1024)))
        return

    error = upload_capacity_error(ctx.user_id)
    if error:
        say(ctx, error)
        return
    if not file_name.lower().endswith(".zip") and find_file_record(ctx.user_id, file_name):
        say(ctx, t("upload_duplicate", file_name=file_name))
        return
    if pending_upload_count(ctx.user_id) >= 5:
        say(ctx, t("upload_pending_exists", count=pending_upload_count(ctx.user_id)))
        return

    submit_for_approval(ctx, document.file_id, file_name,
                        os.path.splitext(file_name)[1].lstrip(".").lower(),
                        document.file_size or 0)


# =============================================================================
# 20. TEXT HANDLERS (order matters: pending input, AI session, buttons, fallback)
# =============================================================================

@bot.message_handler(
    func=lambda m: bool(get_pending(m.chat.id, m.from_user.id))
    and not is_command(m.text) and not is_menu_text(m.text),
    content_types=["text"],
)
def on_pending_input(message: types.Message) -> None:
    ctx = ctx_from_message(message)
    action = get_pending(ctx.chat_id, ctx.user_id)
    if not action:
        return
    if not ensure_access(ctx):
        return

    processor_entry = PENDING_PROCESSORS.get(action)
    if not processor_entry:
        clear_pending(ctx.chat_id, ctx.user_id)
        return
    processor, scope = processor_entry

    if scope == "owner" and not is_owner(ctx.user_id):
        clear_pending(ctx.chat_id, ctx.user_id)
        say(ctx, t("owner_only"))
        return
    if scope == "admin" and not is_admin(ctx.user_id):
        clear_pending(ctx.chat_id, ctx.user_id)
        say(ctx, t("admin_only"))
        return

    # gh_url re-arms itself on invalid input; every other action is single-shot.
    clear_pending(ctx.chat_id, ctx.user_id)
    try:
        processor(ctx, message)
    except Exception:
        logger.exception("Pending action %s failed for %s", action, ctx.user_id)
        say(ctx, t("generic_error"))


@bot.message_handler(
    func=lambda m: ai_session_active(m.chat.id) and not is_command(m.text) and not is_menu_text(m.text),
    content_types=["text"],
)
def on_ai_message(message: types.Message) -> None:
    ctx = ctx_from_message(message)
    if not gate(ctx):
        return
    try:
        handle_ai_message(ctx, message.text or "")
    except Exception:
        logger.exception("AI turn failed for %s", ctx.user_id)
        say(ctx, t("generic_error"))


@bot.message_handler(func=lambda m: is_menu_text(m.text), content_types=["text"])
def on_menu_button(message: types.Message) -> None:
    ctx = ctx_from_message(message)
    label = message.text
    clear_pending(ctx.chat_id, ctx.user_id)
    if label != BTN_AGENT:
        ai_session_set(ctx.chat_id, False)

    informational = label in (BTN_SPEED, BTN_STATUS, BTN_UPDATES, BTN_CONTACT)
    if not gate(ctx, require_unlocked=not informational):
        return
    if label in ADMIN_ONLY_BUTTONS and not require_admin(ctx):
        return

    handler = BUTTON_TEXT_TO_LOGIC.get(label)
    if not handler:
        return
    try:
        if label == BTN_RUN_ALL:
            threading.Thread(target=handler, args=(ctx,), name="run-all", daemon=True).start()
        else:
            handler(ctx)
    except Exception:
        logger.exception("Menu action %r failed for %s", label, ctx.user_id)
        say(ctx, t("generic_error"))


@bot.message_handler(content_types=["text"])
def on_other_text(message: types.Message) -> None:
    ctx = ctx_from_message(message)
    if not gate(ctx, require_unlocked=False):
        return
    say(ctx, t("menu_hint"), reply_markup=main_inline_menu(ctx.user_id))


# =============================================================================
# 21. CALLBACK ROUTER
# =============================================================================

UNLOCKED_CALLBACKS = {"nav:main", "nav:speed", "nav:stats", "nav:updates", "pkg:close"}


def handle_verification_callback(call: types.CallbackQuery, ctx: Ctx, data: str) -> None:
    try:
        target = int(data.split(":", 1)[1])
    except (IndexError, ValueError):
        answer_call(ctx.call_id, t("unknown_action"), alert=True)
        return
    if target != ctx.user_id:
        answer_call(ctx.call_id, t("verify_not_yours"), alert=True)
        return
    if is_banned(ctx.user_id):
        answer_call(ctx.call_id, t("banned"), alert=True)
        return

    missing, unchecked = channel_membership(ctx.user_id)
    if not missing and not unchecked:
        mark_verified(ctx.user_id)
        answer_call(ctx.call_id, t("verify_ok"), alert=True)
        if call.message:
            edit_text(ctx.chat_id, call.message.message_id, t("verify_ok"))
        return
    if missing:
        answer_call(ctx.call_id, t("verify_missing"), alert=True)
        return
    answer_call(ctx.call_id, t("verify_unavailable"), alert=True)


def handle_upload_decision(call: types.CallbackQuery, ctx: Ctx, data: str) -> None:
    if not is_admin(ctx.user_id):
        answer_call(ctx.call_id, t("admin_only"), alert=True)
        return
    parts = data.split(":")
    if len(parts) != 3:
        answer_call(ctx.call_id, t("unknown_action"), alert=True)
        return
    decision = parts[1]
    try:
        upload_id = int(parts[2])
    except ValueError:
        answer_call(ctx.call_id, t("unknown_action"), alert=True)
        return

    pending = get_pending_upload(upload_id)
    if not pending:
        answer_call(ctx.call_id, t("upload_gone"), alert=True)
        if call.message:
            api_call(bot.edit_message_reply_markup, ctx.chat_id, call.message.message_id, reply_markup=None)
        return

    owner_id = int(pending["user_id"])
    file_name = pending["file_name"]

    if decision == "ok":
        answer_call(ctx.call_id, t("approve_working"))
        if call.message:
            api_call(bot.edit_message_reply_markup, ctx.chat_id, call.message.message_id, reply_markup=None)

        def _approve() -> None:
            success, target_id, final_name = process_approved_upload(upload_id, ctx.chat_id)
            if success and target_id:
                send_text(target_id, t("upload_approved_user", file_name=final_name or file_name))

        threading.Thread(target=_approve, name=f"approve-{upload_id}", daemon=True).start()
        return

    answer_call(ctx.call_id, t("reject_done"))
    delete_pending_upload(upload_id)
    if call.message:
        api_call(bot.edit_message_reply_markup, ctx.chat_id, call.message.message_id, reply_markup=None)
    send_text(owner_id, t("upload_rejected_user", file_name=file_name))


def handle_file_callback(ctx: Ctx, call: types.CallbackQuery, action: str, raw_id: str) -> None:
    try:
        file_row_id = int(raw_id)
    except ValueError:
        answer_call(ctx.call_id, t("unknown_action"), alert=True)
        return
    record = get_file_record(file_row_id)
    if not record:
        answer_call(ctx.call_id, t("file_not_found"), alert=True)
        return
    if not can_manage(ctx, record):
        answer_call(ctx.call_id, t("permission_denied"), alert=True)
        return

    owner_id = int(record["user_id"])
    file_name = record["file_name"]
    message_id = call.message.message_id if call.message else None

    if action == "f":
        answer_call(ctx.call_id)
        show_file_controls(ctx, record, message_id)
        return

    if action == "st":
        if is_running(owner_id, file_name):
            answer_call(ctx.call_id, t("already_running"), alert=True)
            return
        answer_call(ctx.call_id, t("starting", file_name=file_name))
        started, message = start_script(owner_id, file_name, record["file_type"], chat_id=ctx.chat_id)
        if not started:
            send_text(ctx.chat_id, message)
        time.sleep(1)
        show_file_controls(ctx, record, message_id)
        return

    if action == "sp":
        if not stop_script(owner_id, file_name):
            answer_call(ctx.call_id, t("not_running"), alert=True)
            show_file_controls(ctx, record, message_id)
            return
        answer_call(ctx.call_id, t("stopped", file_name=file_name))
        show_file_controls(ctx, record, message_id)
        return

    if action == "rs":
        answer_call(ctx.call_id, t("restarting", file_name=file_name))
        stop_script(owner_id, file_name)
        time.sleep(1)
        started, message = start_script(owner_id, file_name, record["file_type"], chat_id=ctx.chat_id)
        if not started:
            send_text(ctx.chat_id, message)
        time.sleep(1)
        show_file_controls(ctx, record, message_id)
        return

    if action == "lg":
        folder = user_folder(owner_id)
        log_path = log_path_for(folder, file_name)
        if not os.path.isfile(log_path) or os.path.getsize(log_path) == 0:
            answer_call(ctx.call_id, t("logs_empty", file_name=file_name), alert=True)
            return
        answer_call(ctx.call_id)
        if os.path.getsize(log_path) > LOG_PREVIEW_CHARS:
            with open(log_path, "rb") as handle:
                handle.seek(max(0, os.path.getsize(log_path) - LOG_MAX_BYTES))
                blob = io.BytesIO(handle.read())
            blob.name = f"{os.path.splitext(file_name)[0]}.log"
            if api_call(bot.send_document, ctx.chat_id, blob,
                        caption=stylish_text(t("logs_as_file", file_name=file_name))) is None:
                send_text(ctx.chat_id, read_log_tail(log_path, LOG_PREVIEW_CHARS), styled=False)
            return
        send_text(ctx.chat_id, t("logs_title", file_name=file_name))
        send_text(ctx.chat_id, read_log_tail(log_path, LOG_PREVIEW_CHARS) or "-", styled=False)
        return

    if action == "fx":
        remaining = cooldown_left(ctx.user_id, "fix", 20)
        if remaining:
            answer_call(ctx.call_id, t("cooldown", seconds=remaining), alert=True)
            return
        answer_call(ctx.call_id, "🔧 Checking the log…")
        threading.Thread(target=fix_modules_for_file, args=(ctx, record),
                         name=f"fix-{file_row_id}", daemon=True).start()
        return

    if action == "dl":
        answer_call(ctx.call_id)
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("🗑️ Confirm delete", callback_data=f"dlc:{file_row_id}"),
            types.InlineKeyboardButton("🔙 Back", callback_data=f"f:{file_row_id}"),
        )
        if message_id:
            edit_text(ctx.chat_id, message_id, t("delete_confirm", file_name=file_name), reply_markup=markup)
        else:
            say(ctx, t("delete_confirm", file_name=file_name), reply_markup=markup)
        return

    if action == "dlc":
        stop_script(owner_id, file_name)
        delete_file_assets(user_folder(owner_id), file_name)
        remove_file_record(owner_id, file_name)
        answer_call(ctx.call_id, t("deleted", file_name=file_name))
        if message_id:
            edit_text(ctx.chat_id, message_id, t("deleted", file_name=file_name))
        else:
            say(ctx, t("deleted", file_name=file_name))
        return

    answer_call(ctx.call_id, t("unknown_action"))


@bot.callback_query_handler(func=lambda call: True)
def on_callback(call: types.CallbackQuery) -> None:
    data = call.data or ""
    ctx = ctx_from_call(call)
    message_id = call.message.message_id if call.message else None

    try:
        if data.startswith("vfy:"):
            handle_verification_callback(call, ctx, data)
            return

        if is_banned(ctx.user_id):
            answer_call(ctx.call_id, t("banned"), alert=True)
            return

        if data.startswith("up:"):
            handle_upload_decision(call, ctx, data)
            return

        if not ensure_access(ctx):
            return

        if bot_is_locked() and not is_admin(ctx.user_id) and data not in UNLOCKED_CALLBACKS:
            answer_call(ctx.call_id, t("bot_locked"), alert=True)
            return

        action, _, argument = data.partition(":")

        if action in ("f", "st", "sp", "rs", "lg", "fx", "dl", "dlc"):
            handle_file_callback(ctx, call, action, argument)
            return

        if data == "nav:main":
            answer_call(ctx.call_id)
            logic_welcome(ctx)
            return
        if data == "nav:upload":
            answer_call(ctx.call_id)
            logic_upload(ctx)
            return
        if data == "nav:files":
            answer_call(ctx.call_id)
            logic_files(ctx, message_id)
            return
        if data == "nav:speed":
            answer_call(ctx.call_id)
            logic_speed(ctx)
            return
        if data == "nav:stats":
            answer_call(ctx.call_id)
            logic_stats(ctx)
            return
        if data == "nav:updates":
            answer_call(ctx.call_id)
            logic_updates(ctx)
            return
        if data == "nav:github":
            answer_call(ctx.call_id)
            logic_github(ctx)
            return
        if data == "nav:ai":
            answer_call(ctx.call_id)
            logic_ai(ctx)
            return
        if data == "nav:packages":
            answer_call(ctx.call_id)
            logic_packages(ctx)
            return
        if data == "nav:admin":
            if not require_admin(ctx):
                return
            answer_call(ctx.call_id)
            logic_admin_panel(ctx, message_id)
            return
        if data == "nav:subs":
            if not require_admin(ctx):
                return
            answer_call(ctx.call_id)
            logic_subs_panel(ctx, message_id)
            return
        if data == "nav:broadcast":
            if not require_admin(ctx):
                return
            answer_call(ctx.call_id)
            logic_broadcast(ctx)
            return

        if data in ("gh:public", "gh:private"):
            session = gh_session(ctx.user_id)
            if not session:
                answer_call(ctx.call_id, t("gh_session_expired"), alert=True)
                return
            answer_call(ctx.call_id)
            if data == "gh:private":
                ask_for(ctx, "gh_token", t("gh_token_prompt"))
            else:
                threading.Thread(target=github_fetch_and_submit, args=(ctx, None),
                                 name="gh-download", daemon=True).start()
            return

        if data == "pkg:rec":
            if not require_admin(ctx):
                return
            answer_call(ctx.call_id)
            clear_pending(ctx.chat_id, ctx.user_id)

            def _install_recommended() -> None:
                send_text(ctx.chat_id, t("pkg_batch_start", count=len(RECOMMENDED_PACKAGES)))
                ok = failed = 0
                for package in RECOMMENDED_PACKAGES:
                    success, _detail = install_pip_package(package)
                    ok += 1 if success else 0
                    failed += 0 if success else 1
                send_text(ctx.chat_id, t("pkg_batch_done", ok=ok, failed=failed))

            threading.Thread(target=_install_recommended, name="pkg-batch", daemon=True).start()
            return

        if data == "pkg:close":
            answer_call(ctx.call_id, t("cancelled"))
            clear_pending(ctx.chat_id, ctx.user_id)
            if message_id:
                api_call(bot.delete_message, ctx.chat_id, message_id)
            return

        if data == "adm:lock" or data == "adm:unlock":
            if not require_admin(ctx):
                return
            set_bot_locked(data == "adm:lock")
            answer_call(ctx.call_id, t("lock_state", state="locked" if data == "adm:lock" else "unlocked"))
            if message_id:
                edit_text(ctx.chat_id, message_id, t("menu_hint"), reply_markup=main_inline_menu(ctx.user_id))
            return

        if data == "adm:runall":
            if not require_admin(ctx):
                return
            answer_call(ctx.call_id, t("run_all_working"))
            threading.Thread(target=logic_run_all, args=(ctx,), name="run-all", daemon=True).start()
            return

        if data == "adm:addadmin":
            if not require_owner(ctx):
                return
            answer_call(ctx.call_id)
            ask_for(ctx, "admin_add", t("admin_add_prompt"))
            return

        if data == "adm:deladmin":
            if not require_owner(ctx):
                return
            answer_call(ctx.call_id)
            ask_for(ctx, "admin_remove", t("admin_remove_prompt"))
            return

        if data == "adm:listadmins":
            if not require_admin(ctx):
                return
            answer_call(ctx.call_id)
            listing = "\n".join(
                f"- {admin_id}" + (" (owner)" if admin_id == OWNER_ID else "")
                for admin_id in sorted(admin_ids())
            )
            say(ctx, t("admin_list", admins=listing or "-"))
            return

        if data == "adm:setlimit":
            if not require_admin(ctx):
                return
            answer_call(ctx.call_id)
            ask_for(ctx, "set_limit", t("limit_prompt"))
            return

        if data == "adm:model":
            if not require_admin(ctx):
                return
            answer_call(ctx.call_id)
            if not AI_ENABLED:
                say(ctx, t("ai_disabled"))
                return
            if message_id:
                edit_text(ctx.chat_id, message_id, t("ai_model_prompt"), reply_markup=model_markup())
            else:
                say(ctx, t("ai_model_prompt"), reply_markup=model_markup())
            return

        if action == "mdl":
            if not require_admin(ctx):
                return
            if argument not in AVAILABLE_MODELS:
                answer_call(ctx.call_id, t("ai_model_invalid"), alert=True)
                return
            set_current_model(argument)
            answer_call(ctx.call_id, t("ai_model_changed", model=argument))
            if message_id:
                edit_text(ctx.chat_id, message_id,
                          t("ai_model_changed", model=f"{argument} ({AVAILABLE_MODELS[argument]})"))
            return

        if data.startswith("sub:"):
            if not require_admin(ctx):
                return
            answer_call(ctx.call_id)
            if data == "sub:add":
                ask_for(ctx, "sub_add", t("subs_add_prompt"))
            elif data == "sub:del":
                ask_for(ctx, "sub_remove", t("subs_remove_prompt"))
            elif data == "sub:check":
                ask_for(ctx, "sub_check", t("subs_check_prompt"))
            return

        if data.startswith("bc:"):
            if not require_admin(ctx):
                return
            parts = data.split(":")
            if len(parts) != 3:
                answer_call(ctx.call_id, t("unknown_action"))
                return
            if parts[1] == "no":
                take_broadcast(parts[2])
                answer_call(ctx.call_id, t("cancelled"))
                if message_id:
                    api_call(bot.delete_message, ctx.chat_id, message_id)
                return
            draft = take_broadcast(parts[2])
            if not draft:
                answer_call(ctx.call_id, t("broadcast_expired"), alert=True)
                return
            answer_call(ctx.call_id, t("broadcast_running"))
            if message_id:
                edit_text(ctx.chat_id, message_id, t("broadcast_running"))
            threading.Thread(target=execute_broadcast, args=(draft, ctx.chat_id),
                             name="broadcast", daemon=True).start()
            return

        answer_call(ctx.call_id, t("unknown_action"))

    except Exception:
        logger.exception("Callback %r failed for %s", data[:60], ctx.user_id)
        answer_call(ctx.call_id, t("generic_error"), alert=True)


# =============================================================================
# 22. AUTO-RECOVERY WORKER
# =============================================================================

def recovery_worker() -> None:
    """Restarts crashed scripts with a bounded number of attempts per window."""
    while not SHUTTING_DOWN.is_set():
        if SHUTTING_DOWN.wait(RECOVERY_INTERVAL_SECONDS):
            return
        try:
            with _PROC_LOCK:
                snapshot = list(_processes.items())
            for key, record in snapshot:
                if process_alive(record):
                    continue
                with _PROC_LOCK:
                    if _processes.get(key) is not record:
                        continue
                    _processes.pop(key, None)
                _close_handle(record)

                now = time.monotonic()
                attempts = record.recovery_attempts
                window_start = record.recovery_window_start
                if now - window_start > RECOVERY_WINDOW_SECONDS:
                    window_start = now
                    attempts = 0

                if not find_file_record(record.owner_id, record.file_name):
                    logger.info("Not recovering %s: the file record is gone", key)
                    continue
                if attempts >= RECOVERY_MAX_ATTEMPTS:
                    logger.warning("Auto-recovery gave up on %s", key)
                    if record.chat_id:
                        send_text(record.chat_id, t("crash_giveup", file_name=record.file_name))
                    continue

                attempts += 1
                logger.info("Auto-recovery restarting %s (attempt %s)", key, attempts)
                if record.chat_id:
                    send_text(record.chat_id, t("crash_notice", file_name=record.file_name, attempt=attempts))

                started, _message = start_script(record.owner_id, record.file_name,
                                                 record.file_type, record.chat_id)
                if started:
                    with _PROC_LOCK:
                        fresh = _processes.get(key)
                        if fresh:
                            fresh.recovery_attempts = attempts
                            fresh.recovery_window_start = window_start
                            fresh.autofix_done = True
        except Exception:
            logger.exception("Auto-recovery cycle failed")


# =============================================================================
# 23. HEALTH ENDPOINT
# =============================================================================

class _HealthHandler(BaseHTTPRequestHandler):
    server_version = "script-hosting-bot"

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        body = b"OK"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return


def start_health_server() -> Optional[ThreadingHTTPServer]:
    port = _env_int("HEALTH_PORT", _env_int("PORT", 0))
    if port <= 0:
        return None
    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
    except OSError as exc:
        logger.warning("Health endpoint disabled, port %s unavailable: %s", port, exc)
        return None
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="health", daemon=True).start()
    logger.info("Health endpoint listening on port %s", port)
    return server


# =============================================================================
# 24. SHUTDOWN AND ENTRY POINT
# =============================================================================

_CLEANED_UP = threading.Event()


def cleanup() -> None:
    if _CLEANED_UP.is_set():
        return
    _CLEANED_UP.set()
    SHUTTING_DOWN.set()
    logger.warning("Shutting down: stopping hosted scripts")
    stopped = stop_all_scripts()
    logger.warning("Stopped %s script(s)", stopped)
    db.close()


atexit.register(cleanup)


def _handle_signal(signum: int, _frame: Any) -> None:
    logger.warning("Received signal %s", signum)
    SHUTTING_DOWN.set()
    with contextlib.suppress(Exception):
        bot.stop_polling()


for _sig_name in ("SIGTERM", "SIGINT"):
    _sig = getattr(signal, _sig_name, None)
    if _sig is not None:
        with contextlib.suppress(ValueError, OSError):
            signal.signal(_sig, _handle_signal)


def publish_commands() -> None:
    commands = [
        types.BotCommand("start", "Main menu"),
        types.BotCommand("help", "How to use the bot"),
        types.BotCommand("uploadfile", "Upload a .py, .js or .zip file"),
        types.BotCommand("checkfiles", "List and control your files"),
        types.BotCommand("restart", "Restart your scripts"),
        types.BotCommand("stop", "Stop your scripts"),
        types.BotCommand("github", "Deploy from a GitHub repository"),
        types.BotCommand("packages", "Package installer or request"),
        types.BotCommand("botspeed", "Host and latency info"),
        types.BotCommand("statistics", "Bot statistics"),
        types.BotCommand("cancel", "Leave the current input mode"),
    ]
    api_call(bot.set_my_commands, commands)


def main() -> None:
    bot_start_time()
    logger.info(
        "Starting bot | owner=%s admins=%s channels=%s node=%s ai=%s data=%s",
        OWNER_ID, len(admin_ids()), len(REQUIRED_CHANNELS), NODE_AVAILABLE, AI_ENABLED, DATA_DIR,
    )
    start_health_server()
    threading.Thread(target=recovery_worker, name="recovery", daemon=True).start()
    api_call(bot.remove_webhook)
    publish_commands()

    while not SHUTTING_DOWN.is_set():
        try:
            bot.infinity_polling(timeout=30, long_polling_timeout=25,
                                 skip_pending=True, logger_level=logging.ERROR)
        except Exception:
            logger.exception("Polling loop crashed")
        if SHUTTING_DOWN.is_set():
            break
        logger.info("Restarting polling in 5 seconds")
        time.sleep(5)

    cleanup()


if __name__ == "__main__":
    main()
