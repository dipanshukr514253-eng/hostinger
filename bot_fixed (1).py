# -*- coding: utf-8 -*-
import telebot
from telebot import util
import subprocess
import os
import zipfile
import tempfile
import shutil
from telebot import types
import time
from datetime import datetime, timedelta
import psutil
import sqlite3
import json
import logging
import signal
import threading
import re
import sys
import atexit
import requests
import io
from urllib.parse import urlparse
import urllib3
import random


try:
    import resource as _resource
except ImportError:
    _resource = None


def _limit_resources():
    """Runs inside the child process (POSIX only): caps memory and process count for hosted scripts."""
    if _resource is None:
        return
    try:
        _resource.setrlimit(_resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        _resource.setrlimit(_resource.RLIMIT_NPROC, (64, 64))
    except Exception:
        pass


_POPEN_LIMITS = {'preexec_fn': _limit_resources} if os.name == 'posix' else {}


# --- Web Server / Port ---
from http.server import BaseHTTPRequestHandler, HTTPServer

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

    def log_message(self, format, *args):
        pass

def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()

threading.Thread(target=start_web_server, daemon=True).start()
# ----------------------------

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Configuration ---
TOKEN = ''
OWNER_ID = 5888777479
ADMIN_ID = 5888777479
YOUR_USERNAME = '@OfficalEarningZone'

# --- Force Subscription Channels ---
REQUIRED_CHANNELS = [
    "@BrokenXworldss"
]

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, 'upload_bots')
IROTECH_DIR = os.path.join(BASE_DIR, 'inf')
DATABASE_PATH = os.path.join(IROTECH_DIR, 'bot_data.db')

FREE_USER_LIMIT = 2
SUBSCRIBED_USER_LIMIT = 15
ADMIN_LIMIT = 99
OWNER_LIMIT = float('inf')

os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
os.makedirs(IROTECH_DIR, exist_ok=True)

bot = telebot.TeleBot(TOKEN)

bot_scripts = {}
user_subscriptions = {}
user_files = {}
active_users = set()
admin_ids = {ADMIN_ID, OWNER_ID}
user_custom_limits = {}
bot_locked = False
banned_users = set()

# Auto-recovery tracking
auto_recovery_last_restart = {}

# --- Persistent uptime across restarts ---
PERSISTENT_START_FILE = os.path.join(IROTECH_DIR, 'bot_start_time.txt')

def get_persistent_start_time():
    if os.path.exists(PERSISTENT_START_FILE):
        try:
            with open(PERSISTENT_START_FILE, 'r') as f:
                timestamp = f.read().strip()
                return datetime.fromisoformat(timestamp)
        except Exception as e:
            logging.error(f"Failed to read persistent start time: {e}")
    now = datetime.now()
    try:
        with open(PERSISTENT_START_FILE, 'w') as f:
            f.write(now.isoformat())
    except Exception as e:
        logging.error(f"Failed to write persistent start time: {e}")
    return now

BOT_START_TIME = get_persistent_start_time()

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==================== SAMBANOVA AI CONFIGURATION ====================
SAMBA_API_KEY = os.environ.get('SAMBA_API_KEY')
if not SAMBA_API_KEY:
    raise RuntimeError('SAMBA_API_KEY environment variable is not set. Set it before starting the bot (never hardcode API keys).')
SAMBA_URL = "https://api.sambanova.ai/v1/chat/completions"

AVAILABLE_MODELS = {
    'llama': 'Meta-Llama-3.3-70B-Instruct',
    'deepseek': 'DeepSeek-V3.1',
    'minimax': 'MiniMax-M2.7',
    'gpt-oss': 'gpt-oss-120b'
}
DEFAULT_MODEL = 'llama'
global_model = DEFAULT_MODEL
# =====================================================================

# --- Keyboard Layouts ---
COMMAND_BUTTONS_LAYOUT_USER_SPEC = [
    ["📢 𝐔𝐩𝐝𝐚𝐭𝐞𝐬 𝐂𝐡𝐚𝐧𝐧𝐞𝐥"],
    ["🌏 Upload", "📁 𝐌𝐲 𝐅𝐢𝐥𝐞𝐬"],
    ["⚡ 𝐁𝐨𝐭 𝐒𝐩𝐞𝐞𝐝", "🚀 𝐒𝐭𝐚𝐭𝐮𝐬"],
    ["🔄 𝐑𝐞𝐬𝐭𝐚𝐫𝐭", "⏹ 𝐒𝐭𝐨𝐩"],
    ["⚙️ Recommended Install"],
    ["🌐 𝐆𝐈𝐓𝐇𝐔𝐁", "📞 𝐂𝐨𝐧𝐭𝐚𝐜𝐭 𝐎𝐰𝐧𝐞𝐫"]
]
ADMIN_COMMAND_BUTTONS_LAYOUT_USER_SPEC = [
    ["📢 𝐔𝐩𝐝𝐚𝐭𝐞𝐬 𝐂𝐡𝐚𝐧𝐧𝐞𝐥"],
    ["🌏 Upload", "📁 𝐌𝐲 𝐅𝐢𝐥𝐞𝐬"],
    ["⚡ 𝐁𝐨𝐭 𝐒𝐩𝐞𝐞𝐝", "🚀 𝐒𝐭𝐚𝐭𝐮𝐬"],
    ["🔄 𝐑𝐞𝐬𝐭𝐚𝐫𝐭", "⏹ 𝐒𝐭𝐨𝐩"],
    ["💳 𝐒𝐮𝐛𝐬𝐜𝐫𝐢𝐩𝐭𝐢𝐨𝐧𝐬", "📢 𝐁𝐫𝐨𝐚𝐝𝐜𝐚𝐬𝐭"],
    ["🔒 𝐋𝐨𝐜𝐤 𝐁𝐨𝐭", "🟢 𝐑𝐮𝐧𝐧𝐢𝐧𝐠 𝐀𝐥𝐥 𝐂𝐨𝐝𝐞"],
    ["🛠️ 𝐀𝐝𝐦𝐢𝐧 𝐏𝐚𝐧𝐞𝐥", "⚙️ Recommended Install"],
    ["🤖 𝐀𝐆𝐄𝐍𝐓", "🌐 𝐆𝐈𝐓𝐇𝐔𝐁"],
    ["📞 𝐂𝐨𝐧𝐭𝐚𝐜𝐭 𝐎𝐰𝐧𝐞𝐫"]
]

# --- Database Setup ---
DB_LOCK = threading.Lock()

def upgrade_db():
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute("PRAGMA table_info(pending_uploads)")
    columns = [col[1] for col in c.fetchall()]
    if 'extra_info' not in columns:
        c.execute("ALTER TABLE pending_uploads ADD COLUMN extra_info TEXT")
        logger.info("Added extra_info column to pending_uploads")
    c.execute('''CREATE TABLE IF NOT EXISTS user_limits (
        user_id INTEGER PRIMARY KEY,
        custom_limit INTEGER
    )''')
    conn.commit()
    conn.close()

def init_db():
    logger.info(f"Initializing database at: {DATABASE_PATH}")
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS subscriptions (user_id INTEGER PRIMARY KEY, expiry TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS user_files (user_id INTEGER, file_name TEXT, file_type TEXT, PRIMARY KEY (user_id, file_name))''')
        c.execute('''CREATE TABLE IF NOT EXISTS active_users (user_id INTEGER PRIMARY KEY)''')
        c.execute('''CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)''')
        c.execute('''CREATE TABLE IF NOT EXISTS pending_uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, file_id TEXT, file_name TEXT, file_type TEXT,
            file_size INTEGER, user_name TEXT, user_username TEXT,
            timestamp TEXT, extra_info TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS verified_users (user_id INTEGER PRIMARY KEY, verified_at TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS banned_users (user_id INTEGER PRIMARY KEY, banned_at TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS user_limits (user_id INTEGER PRIMARY KEY, custom_limit INTEGER)''')
        c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (OWNER_ID,))
        if ADMIN_ID != OWNER_ID:
            c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (ADMIN_ID,))
        conn.commit()
        conn.close()
        upgrade_db()
    except Exception as e:
        logger.error(f"Database init error: {e}")

def load_data():
    global banned_users, user_custom_limits
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT user_id, expiry FROM subscriptions')
        for user_id, expiry in c.fetchall():
            try:
                user_subscriptions[user_id] = {'expiry': datetime.fromisoformat(expiry)}
            except ValueError:
                pass
        c.execute('SELECT user_id, file_name, file_type FROM user_files')
        for user_id, file_name, file_type in c.fetchall():
            user_files.setdefault(user_id, []).append((file_name, file_type))
        c.execute('SELECT user_id FROM active_users')
        active_users.update(row[0] for row in c.fetchall())
        c.execute('SELECT user_id FROM admins')
        admin_ids.update(row[0] for row in c.fetchall())
        c.execute('SELECT user_id FROM banned_users')
        banned_users = set(row[0] for row in c.fetchall())
        c.execute('SELECT user_id, custom_limit FROM user_limits')
        user_custom_limits = {row[0]: row[1] for row in c.fetchall()}
        conn.close()
    except Exception as e:
        logger.error(f"Data load error: {e}")

init_db()
load_data()

# --- stylish_text ---
def stylish_text(text: str) -> str:
    text = re.sub(r'</?code>', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    mapping = {
        'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ꜰ', 'g': 'ɢ',
        'h': 'ʜ', 'i': 'ɪ', 'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ',
        'o': 'ᴏ', 'p': 'ᴘ', 'q': 'ǫ', 'r': 'ʀ', 's': 'ꜱ', 't': 'ᴛ', 'u': 'ᴜ',
        'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x', 'y': 'ʏ', 'z': 'ᴢ',
        'A': 'A', 'B': 'B', 'C': 'C', 'D': 'D', 'E': 'E', 'F': 'F', 'G': 'G',
        'H': 'H', 'I': 'I', 'J': 'J', 'K': 'K', 'L': 'L', 'M': 'M', 'N': 'N',
        'O': 'O', 'P': 'P', 'Q': 'Q', 'R': 'R', 'S': 'S', 'T': 'T', 'U': 'U',
        'V': 'V', 'W': 'W', 'X': 'X', 'Y': 'Y', 'Z': 'Z'
    }
    return ''.join(mapping.get(ch, ch) for ch in text)

# --- Ban / Unban ---
def ban_user(user_id):
    if user_id in admin_ids or user_id == OWNER_ID:
        return False
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('INSERT OR IGNORE INTO banned_users (user_id, banned_at) VALUES (?, ?)', (user_id, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        banned_users.add(user_id)
        return True
    except Exception:
        return False

def unban_user(user_id):
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('DELETE FROM banned_users WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
        banned_users.discard(user_id)
        return True
    except Exception:
        return False

def is_user_banned(user_id):
    return user_id in banned_users

# --- Custom Limit Management ---
def set_user_custom_limit(user_id, limit):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO user_limits (user_id, custom_limit) VALUES (?, ?)', (user_id, limit))
        conn.commit()
        conn.close()
        user_custom_limits[user_id] = limit

def remove_user_custom_limit(user_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('DELETE FROM user_limits WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
        if user_id in user_custom_limits:
            del user_custom_limits[user_id]

def get_user_file_limit(user_id):
    if user_id in user_custom_limits:
        return user_custom_limits[user_id]
    if user_id == OWNER_ID:
        return OWNER_LIMIT
    if user_id in admin_ids:
        return ADMIN_LIMIT
    if user_id in user_subscriptions and user_subscriptions[user_id]['expiry'] > datetime.now():
        return SUBSCRIBED_USER_LIMIT
    return FREE_USER_LIMIT

def get_user_file_count(user_id):
    return len(user_files.get(user_id, []))

# --- Channel verification ---
def is_user_verified(user_id):
    if user_id in admin_ids or user_id == OWNER_ID:
        return True
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT 1 FROM verified_users WHERE user_id = ?', (user_id,))
        result = c.fetchone() is not None
        conn.close()
        return result
    except Exception:
        return False

def set_user_verified(user_id):
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('INSERT OR IGNORE INTO verified_users (user_id, verified_at) VALUES (?, ?)', (user_id, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False

def is_user_member_all_channels(user_id):
    if user_id in admin_ids or user_id == OWNER_ID:
        return True
    for channel in REQUIRED_CHANNELS:
        try:
            chat_member = bot.get_chat_member(channel, user_id)
            if chat_member.status not in ['member', 'administrator', 'creator']:
                return False
        except Exception:
            return False
    return True

def send_join_prompt(chat_id, user_id):
    text = (
        "🔐 Jᴏɪɴ Aʟʟ Cʜᴀɴɴᴇʟs Tᴏ Uɴʟᴏᴄᴋ Tʜᴇ Bᴏᴛ 🚀\n"
        "📢 Cᴏᴍᴘʟᴇᴛᴇ Aʟʟ Cʜᴀɴɴᴇʟ Jᴏɪɴs Tᴏ Gᴇᴛ Aᴄᴄᴇss ✅\n"
        "⚡ Aғᴛᴇʀ Jᴏɪɴɪɴɢ, Cʟɪᴄᴋ \"Vᴇʀɪғʏ\" Tᴏ Cᴏɴᴛɪɴᴜᴇ. 🔓"
    )
    markup = types.InlineKeyboardMarkup(row_width=1)
    for ch in REQUIRED_CHANNELS:
        markup.add(types.InlineKeyboardButton("CLICK", url=f"https://t.me/{ch.lstrip('@')}"))
    markup.add(types.InlineKeyboardButton("✅ VERIFY", callback_data=f"verify_channel_{user_id}"))
    bot.send_message(chat_id, text, parse_mode=None, reply_markup=markup, disable_web_page_preview=True)

def check_subscription_and_continue(message=None, call=None):
    user_id = (message.from_user.id if message else call.from_user.id)
    chat_id = (message.chat.id if message else call.message.chat.id)
    if is_user_banned(user_id):
        bot.send_message(chat_id, stylish_text("🚫 You are banned from using this bot."))
        return False
    if user_id in admin_ids or user_id == OWNER_ID:
        return True
    if is_user_verified(user_id):
        return True
    if is_user_member_all_channels(user_id):
        set_user_verified(user_id)
        return True
    else:
        send_join_prompt(chat_id, user_id)
        return False

@bot.callback_query_handler(func=lambda call: call.data.startswith('verify_channel_'))
def verify_channel_callback(call):
    user_id = int(call.data.split('_')[-1])
    if user_id != call.from_user.id:
        bot.answer_callback_query(call.id, stylish_text("This verification is not for you."), show_alert=True)
        return
    if is_user_verified(user_id):
        bot.answer_callback_query(call.id, stylish_text("You are already verified."), show_alert=True)
        bot.edit_message_text(stylish_text("✅ You are already verified. You can now use the bot."),
                              call.message.chat.id, call.message.message_id)
        return
    if is_user_member_all_channels(user_id):
        set_user_verified(user_id)
        bot.answer_callback_query(call.id, stylish_text("✅ Verification successful! You can now use the bot."), show_alert=True)
        bot.edit_message_text(stylish_text("✅ Verification successful! You can now use the bot.\nSend /start to begin."),
                              call.message.chat.id, call.message.message_id)
    else:
        missing = []
        for ch in REQUIRED_CHANNELS:
            try:
                member = bot.get_chat_member(ch, user_id)
                if member.status not in ['member', 'administrator', 'creator']:
                    missing.append(ch)
            except Exception:
                missing.append(ch)
        if missing:
            missing_list = "\n".join(missing)
            bot.answer_callback_query(call.id, stylish_text(f"❌ You are not a member of:\n{missing_list}\nPlease join all channels first."), show_alert=True)
        else:
            bot.answer_callback_query(call.id, stylish_text("❌ Verification failed. Please join all channels and try again."), show_alert=True)

# --- Helper Functions ---
def get_user_folder(user_id):
    user_folder = os.path.join(UPLOAD_BOTS_DIR, str(user_id))
    os.makedirs(user_folder, exist_ok=True)
    return user_folder

def is_bot_running(script_owner_id, file_name):
    script_key = f"{script_owner_id}_{file_name}"
    script_info = bot_scripts.get(script_key)
    if script_info and script_info.get('process'):
        try:
            proc = psutil.Process(script_info['process'].pid)
            is_running = proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
            if not is_running:
                if 'log_file' in script_info and hasattr(script_info['log_file'], 'close') and not script_info['log_file'].closed:
                    try: script_info['log_file'].close()
                    except Exception: pass
                if script_key in bot_scripts: del bot_scripts[script_key]
            return is_running
        except psutil.NoSuchProcess:
            if 'log_file' in script_info and hasattr(script_info['log_file'], 'close') and not script_info['log_file'].closed:
                try: script_info['log_file'].close()
                except Exception: pass
            if script_key in bot_scripts: del bot_scripts[script_key]
            return False
        except Exception as e:
            logger.error(f"Error checking process {script_key}: {e}")
            return False
    return False

def kill_process_tree(process_info):
    try:
        if 'log_file' in process_info and hasattr(process_info['log_file'], 'close') and not process_info['log_file'].closed:
            try: process_info['log_file'].close()
            except Exception: pass
        process = process_info.get('process')
        if process and hasattr(process, 'pid'):
            pid = process.pid
            if pid:
                try:
                    parent = psutil.Process(pid)
                    children = parent.children(recursive=True)
                    for child in children:
                        try: child.terminate()
                        except Exception: pass
                    psutil.wait_procs(children, timeout=1)
                    try:
                        parent.terminate()
                        try: parent.wait(timeout=1)
                        except Exception: parent.kill()
                    except Exception: pass
                except psutil.NoSuchProcess:
                    pass
    except Exception as e:
        logger.error(f"Error killing process tree: {e}")

TELEGRAM_MODULES = {
    'telebot': 'pyTelegramBotAPI',
    'telegram': 'python-telegram-bot',
    'python_telegram_bot': 'python-telegram-bot',
    'aiogram': 'aiogram',
    'pyrogram': 'pyrogram',
    'telethon': 'telethon',
    'telethon.sync': 'telethon',
    'from telethon.sync import telegramclient': 'telethon',
    'telepot': 'telepot',
    'pytg': 'pytg',
    'tgcrypto': 'tgcrypto',
    'telegram_upload': 'telegram-upload',
    'telegram_send': 'telegram-send',
    'telegram_text': 'telegram-text',
    'mtproto': 'telegram-mtproto',
    'tl': 'telethon',
    'telegram_utils': 'telegram-utils',
    'telegram_logger': 'telegram-logger',
    'telegram_handlers': 'python-telegram-handlers',
    'telegram_redis': 'telegram-redis',
    'telegram_sqlalchemy': 'telegram-sqlalchemy',
    'telegram_payment': 'telegram-payment',
    'telegram_shop': 'telegram-shop-sdk',
    'pytest_telegram': 'pytest-telegram',
    'telegram_debug': 'telegram-debug',
    'telegram_scraper': 'telegram-scraper',
    'telegram_analytics': 'telegram-analytics',
    'telegram_nlp': 'telegram-nlp-toolkit',
    'telegram_ai': 'telegram-ai',
    'telegram_api': 'telegram-api-client',
    'telegram_web': 'telegram-web-integration',
    'telegram_games': 'telegram-games',
    'telegram_quiz': 'telegram-quiz-bot',
    'telegram_ffmpeg': 'telegram-ffmpeg',
    'telegram_media': 'telegram-media-utils',
    'telegram_2fa': 'telegram-twofa',
    'telegram_crypto': 'telegram-crypto-bot',
    'telegram_i18n': 'telegram-i18n',
    'telegram_translate': 'telegram-translate',
    'bs4': 'beautifulsoup4',
    'requests': 'requests',
    'pillow': 'Pillow',
    'cv2': 'opencv-python',
    'yaml': 'PyYAML',
    'dotenv': 'python-dotenv',
    'dateutil': 'python-dateutil',
    'pandas': 'pandas',
    'numpy': 'numpy',
    'flask': 'Flask',
    'django': 'Django',
    'sqlalchemy': 'SQLAlchemy',
    'asyncio': None,
    'json': None,
    'datetime': None,
    'os': None,
    'sys': None,
    're': None,
    'time': None,
    'math': None,
    'random': None,
    'logging': None,
    'threading': None,
    'subprocess': None,
    'zipfile': None,
    'tempfile': None,
    'shutil': None,
    'sqlite3': None,
    'psutil': 'psutil',
    'atexit': None
}

PACKAGE_NAME_RE = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,62}[A-Za-z0-9])?$')
PACKAGE_BLOCKLIST = {'pip', 'setuptools', 'wheel'}

def is_safe_package(name):
    """Reject anything that is not a plain package name (blocks URLs, flags, local paths)."""
    if not name or name.lower() in PACKAGE_BLOCKLIST:
        return False
    return bool(PACKAGE_NAME_RE.match(name))

def attempt_install_pip(module_name, message):
    package_name = TELEGRAM_MODULES.get(module_name.lower(), module_name)
    if package_name is None:
        return False
    if not is_safe_package(package_name):
        bot.reply_to(message, stylish_text(f"❌ Package '{package_name}' is not allowed."))
        return False
    try:
        bot.reply_to(message, stylish_text(f"🐍 Installing {package_name}..."))
        result = subprocess.run([sys.executable, '-m', 'pip', 'install', package_name], capture_output=True, text=True)
        if result.returncode == 0:
            bot.reply_to(message, stylish_text(f"✅ Package {package_name} installed."))
            return True
        else:
            bot.reply_to(message, stylish_text(f"❌ Failed to install {package_name}."))
            return False
    except Exception as e:
        bot.reply_to(message, stylish_text(f"❌ Install error: {e}"))
        return False

def attempt_install_npm(module_name, user_folder, message):
    if not is_safe_package(module_name):
        bot.reply_to(message, stylish_text(f"❌ Package '{module_name}' is not allowed."))
        return False
    try:
        bot.reply_to(message, stylish_text(f"🟠 Installing Node package {module_name}..."))
        result = subprocess.run(['npm', 'install', module_name], cwd=user_folder, capture_output=True, text=True)
        if result.returncode == 0:
            bot.reply_to(message, stylish_text(f"✅ Node package {module_name} installed."))
            return True
        else:
            bot.reply_to(message, stylish_text(f"❌ Failed to install {module_name}."))
            return False
    except Exception as e:
        bot.reply_to(message, stylish_text(f"❌ NPM error: {e}"))
        return False

def run_script(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply, attempt=1):
    max_attempts = 2
    if attempt > max_attempts:
        if message_obj_for_reply:
            bot.reply_to(message_obj_for_reply, stylish_text(f"❌ Failed to run '{file_name}' after {max_attempts} attempts."))
        return
    script_key = f"{script_owner_id}_{file_name}"
    logger.info(f"Attempt {attempt} to run Python: {script_path}")
    try:
        if not os.path.exists(script_path):
            if message_obj_for_reply:
                bot.reply_to(message_obj_for_reply, stylish_text(f"❌ Script '{file_name}' not found!"))
            remove_user_file_db(script_owner_id, file_name)
            return
        if attempt == 1:
            check_proc = subprocess.Popen([sys.executable, script_path], cwd=user_folder, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **_POPEN_LIMITS)
            try:
                _, stderr = check_proc.communicate(timeout=5)
                if check_proc.returncode != 0 and stderr:
                    match = re.search(r"ModuleNotFoundError: No module named '(.+?)'", stderr)
                    if match:
                        module_name = match.group(1)
                        if attempt_install_pip(module_name, message_obj_for_reply):
                            if message_obj_for_reply:
                                bot.reply_to(message_obj_for_reply, stylish_text(f"🔄 Retrying '{file_name}'..."))
                            time.sleep(2)
                            threading.Thread(target=run_script, args=(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply, attempt+1)).start()
                            return
                        else:
                            if message_obj_for_reply:
                                bot.reply_to(message_obj_for_reply, stylish_text(f"❌ Missing module {module_name}. Install failed."))
                            return
            except subprocess.TimeoutExpired:
                check_proc.kill()
                check_proc.communicate()
        log_file_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        log_file = open(log_file_path, 'w', encoding='utf-8')
        process = subprocess.Popen([sys.executable, script_path], cwd=user_folder, stdout=log_file, stderr=log_file, stdin=subprocess.PIPE, **_POPEN_LIMITS)
        bot_scripts[script_key] = {
            'process': process,
            'log_file': log_file,
            'file_name': file_name,
            'chat_id': message_obj_for_reply.chat.id if message_obj_for_reply else None,
            'script_owner_id': script_owner_id,
            'start_time': datetime.now(),
            'user_folder': user_folder,
            'type': 'py',
            'script_key': script_key
        }
        if message_obj_for_reply:
            bot.reply_to(message_obj_for_reply, stylish_text(f"✅ Python script '{file_name}' started! (PID: {process.pid})"))
    except Exception as e:
        if message_obj_for_reply:
            logger.error(f"Script runtime error: {e}", exc_info=True)
            bot.reply_to(message_obj_for_reply, stylish_text("❌ Something went wrong while running your script. Please try again or contact an admin."))
        if script_key in bot_scripts:
            kill_process_tree(bot_scripts[script_key])
            del bot_scripts[script_key]

def run_js_script(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply, attempt=1):
    max_attempts = 2
    if attempt > max_attempts:
        if message_obj_for_reply:
            bot.reply_to(message_obj_for_reply, stylish_text(f"❌ Failed to run '{file_name}' after {max_attempts} attempts."))
        return
    script_key = f"{script_owner_id}_{file_name}"
    logger.info(f"Attempt {attempt} to run JS: {script_path}")
    try:
        if not os.path.exists(script_path):
            if message_obj_for_reply:
                bot.reply_to(message_obj_for_reply, stylish_text(f"❌ JS script '{file_name}' not found!"))
            remove_user_file_db(script_owner_id, file_name)
            return
        if attempt == 1:
            check_proc = subprocess.Popen(['node', script_path], cwd=user_folder, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **_POPEN_LIMITS)
            try:
                _, stderr = check_proc.communicate(timeout=5)
                if check_proc.returncode != 0 and stderr:
                    match = re.search(r"Cannot find module '(.+?)'", stderr)
                    if match:
                        module_name = match.group(1)
                        if not module_name.startswith('.') and not module_name.startswith('/'):
                            if attempt_install_npm(module_name, user_folder, message_obj_for_reply):
                                if message_obj_for_reply:
                                    bot.reply_to(message_obj_for_reply, stylish_text(f"🔄 Retrying '{file_name}'..."))
                                time.sleep(2)
                                threading.Thread(target=run_js_script, args=(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply, attempt+1)).start()
                                return
                            else:
                                if message_obj_for_reply:
                                    bot.reply_to(message_obj_for_reply, stylish_text(f"❌ Missing Node module {module_name}."))
                                return
            except subprocess.TimeoutExpired:
                check_proc.kill()
                check_proc.communicate()
        log_file_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        log_file = open(log_file_path, 'w', encoding='utf-8')
        process = subprocess.Popen(['node', script_path], cwd=user_folder, stdout=log_file, stderr=log_file, stdin=subprocess.PIPE, **_POPEN_LIMITS)
        bot_scripts[script_key] = {
            'process': process,
            'log_file': log_file,
            'file_name': file_name,
            'chat_id': message_obj_for_reply.chat.id if message_obj_for_reply else None,
            'script_owner_id': script_owner_id,
            'start_time': datetime.now(),
            'user_folder': user_folder,
            'type': 'js',
            'script_key': script_key
        }
        if message_obj_for_reply:
            bot.reply_to(message_obj_for_reply, stylish_text(f"✅ JS script '{file_name}' started! (PID: {process.pid})"))
    except Exception as e:
        if message_obj_for_reply:
            logger.error(f"Script runtime error: {e}", exc_info=True)
            bot.reply_to(message_obj_for_reply, stylish_text("❌ Something went wrong while running your script. Please try again or contact an admin."))
        if script_key in bot_scripts:
            kill_process_tree(bot_scripts[script_key])
            del bot_scripts[script_key]

# --- Database Operations ---
def save_user_file(user_id, file_name, file_type='py'):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR REPLACE INTO user_files (user_id, file_name, file_type) VALUES (?, ?, ?)',
                      (user_id, file_name, file_type))
            conn.commit()
            user_files.setdefault(user_id, [])
            user_files[user_id] = [(fn, ft) for fn, ft in user_files[user_id] if fn != file_name]
            user_files[user_id].append((file_name, file_type))
        except Exception as e:
            logger.error(f"Error saving file: {e}")
        finally:
            conn.close()

def remove_user_file_db(user_id, file_name):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM user_files WHERE user_id = ? AND file_name = ?', (user_id, file_name))
            conn.commit()
            if user_id in user_files:
                user_files[user_id] = [f for f in user_files[user_id] if f[0] != file_name]
                if not user_files[user_id]:
                    del user_files[user_id]
        except Exception as e:
            logger.error(f"Error removing file: {e}")
        finally:
            conn.close()

def add_active_user(user_id):
    active_users.add(user_id)
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR IGNORE INTO active_users (user_id) VALUES (?)', (user_id,))
            conn.commit()
        except Exception as e:
            logger.error(f"Error adding active user: {e}")
        finally:
            conn.close()

def save_subscription(user_id, expiry):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            expiry_str = expiry.isoformat()
            c.execute('INSERT OR REPLACE INTO subscriptions (user_id, expiry) VALUES (?, ?)', (user_id, expiry_str))
            conn.commit()
            user_subscriptions[user_id] = {'expiry': expiry}
        except Exception as e:
            logger.error(f"Error saving subscription: {e}")
        finally:
            conn.close()

def remove_subscription_db(user_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM subscriptions WHERE user_id = ?', (user_id,))
            conn.commit()
            if user_id in user_subscriptions:
                del user_subscriptions[user_id]
        except Exception as e:
            logger.error(f"Error removing subscription: {e}")
        finally:
            conn.close()

def add_admin_db(admin_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (admin_id,))
            conn.commit()
            admin_ids.add(admin_id)
        except Exception as e:
            logger.error(f"Error adding admin: {e}")
        finally:
            conn.close()

def remove_admin_db(admin_id):
    if admin_id == OWNER_ID:
        return False
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM admins WHERE user_id = ?', (admin_id,))
            conn.commit()
            if c.rowcount > 0:
                admin_ids.discard(admin_id)
                return True
            return False
        except Exception as e:
            logger.error(f"Error removing admin: {e}")
            return False
        finally:
            conn.close()

def add_pending_upload(user_id, file_id, file_name, file_type, file_size, user_name, user_username, extra_info=""):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            timestamp = datetime.now().isoformat()
            c.execute('''INSERT INTO pending_uploads 
                         (user_id, file_id, file_name, file_type, file_size, user_name, user_username, timestamp, extra_info)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                      (user_id, file_id, file_name, file_type, file_size, user_name, user_username, timestamp, extra_info))
            conn.commit()
            return c.lastrowid
        except Exception as e:
            logger.error(f"Error adding pending upload: {e}")
            return None
        finally:
            conn.close()

def get_pending_upload(upload_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('SELECT id, user_id, file_id, file_name, file_type, file_size, user_name, user_username, extra_info FROM pending_uploads WHERE id = ?', (upload_id,))
            row = c.fetchone()
            if row:
                return {'id': row[0], 'user_id': row[1], 'file_id': row[2], 'file_name': row[3],
                        'file_type': row[4], 'file_size': row[5], 'user_name': row[6], 'user_username': row[7], 'extra_info': row[8]}
            return None
        except Exception as e:
            logger.error(f"Error getting pending upload: {e}")
            return None
        finally:
            conn.close()

def delete_pending_upload(upload_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM pending_uploads WHERE id = ?', (upload_id,))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error deleting pending upload: {e}")
            return False
        finally:
            conn.close()

def process_approved_file(upload_id, admin_chat_id, user_message_obj=None):
    pending = get_pending_upload(upload_id)
    if not pending:
        bot.send_message(admin_chat_id, stylish_text(f"❌ Pending upload {upload_id} not found."))
        return False
    user_id = pending['user_id']
    file_id = pending['file_id']
    file_name = pending['file_name']
    file_ext = os.path.splitext(file_name)[1].lower()
    file_type = pending['file_type']
    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    if current_files >= file_limit:
        limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
        bot.send_message(admin_chat_id, stylish_text(f"⚠️ User limit reached ({current_files}/{limit_str}). Cannot approve."))
        delete_pending_upload(upload_id)
        return False
    try:
        file_info = bot.get_file(file_id)
        downloaded = bot.download_file(file_info.file_path)
        user_folder = get_user_folder(user_id)
        if file_ext == '.zip':
            temp_dir = tempfile.mkdtemp(prefix=f"user_{user_id}_zip_")
            zip_path = os.path.join(temp_dir, file_name)
            with open(zip_path, 'wb') as f:
                f.write(downloaded)
            with zipfile.ZipFile(zip_path, 'r') as z:
                _base = os.path.realpath(temp_dir)
                for _member in z.namelist():
                    _dest = os.path.realpath(os.path.join(temp_dir, _member))
                    if _dest != _base and not _dest.startswith(_base + os.sep):
                        raise ValueError('Unsafe file path inside zip (path traversal blocked)')
                z.extractall(temp_dir)
            extracted = os.listdir(temp_dir)
            py_files = [f for f in extracted if f.endswith('.py')]
            js_files = [f for f in extracted if f.endswith('.js')]
            req_file = 'requirements.txt' if 'requirements.txt' in extracted else None
            pkg_json = 'package.json' if 'package.json' in extracted else None
            if req_file:
                try:
                    subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', os.path.join(temp_dir, req_file)], check=True, capture_output=True)
                    bot.send_message(admin_chat_id, stylish_text("✅ Python deps installed."))
                except Exception as e:
                    bot.send_message(admin_chat_id, stylish_text(f"❌ Python deps failed: {e}"))
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    delete_pending_upload(upload_id)
                    return False
            if pkg_json:
                try:
                    subprocess.run(['npm', 'install'], cwd=temp_dir, check=True, capture_output=True)
                    bot.send_message(admin_chat_id, stylish_text("✅ Node deps installed."))
                except Exception as e:
                    bot.send_message(admin_chat_id, stylish_text(f"❌ Node deps failed: {e}"))
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    delete_pending_upload(upload_id)
                    return False
            main_script = None
            for p in ['main.py', 'bot.py', 'app.py']:
                if p in py_files:
                    main_script = p
                    file_type = 'py'
                    break
            if not main_script:
                for p in ['index.js', 'main.js', 'bot.js', 'app.js']:
                    if p in js_files:
                        main_script = p
                        file_type = 'js'
                        break
            if not main_script and py_files:
                main_script = py_files[0]
                file_type = 'py'
            elif not main_script and js_files:
                main_script = js_files[0]
                file_type = 'js'
            if not main_script:
                bot.send_message(admin_chat_id, stylish_text("❌ No .py or .js script found in zip."))
                shutil.rmtree(temp_dir, ignore_errors=True)
                delete_pending_upload(upload_id)
                return False
            for item in os.listdir(temp_dir):
                src = os.path.join(temp_dir, item)
                dst = os.path.join(user_folder, item)
                if os.path.isdir(dst):
                    shutil.rmtree(dst)
                elif os.path.exists(dst):
                    os.remove(dst)
                shutil.move(src, dst)
            shutil.rmtree(temp_dir, ignore_errors=True)
            save_user_file(user_id, main_script, file_type)
            script_path = os.path.join(user_folder, main_script)
            if file_type == 'py':
                threading.Thread(target=run_script, args=(script_path, user_id, user_folder, main_script, user_message_obj)).start()
            else:
                threading.Thread(target=run_js_script, args=(script_path, user_id, user_folder, main_script, user_message_obj)).start()
            bot.send_message(admin_chat_id, stylish_text(f"✅ Approved and started: {main_script}"))
            return True
        else:
            file_path = os.path.join(user_folder, file_name)
            with open(file_path, 'wb') as f:
                f.write(downloaded)
            save_user_file(user_id, file_name, file_type)
            if file_type == 'py':
                threading.Thread(target=run_script, args=(file_path, user_id, user_folder, file_name, user_message_obj)).start()
            else:
                threading.Thread(target=run_js_script, args=(file_path, user_id, user_folder, file_name, user_message_obj)).start()
            bot.send_message(admin_chat_id, stylish_text(f"✅ Approved and started: {file_name}"))
            return True
    except Exception as e:
        logger.error(f"Error in process_approved_file: {e}", exc_info=True)
        bot.send_message(admin_chat_id, stylish_text("❌ Processing failed — details are in the server logs."))
        return False
    finally:
        delete_pending_upload(upload_id)

# --- Document Handler ---
@bot.message_handler(content_types=['document'])
def handle_file_upload_doc(message):
    if not check_subscription_and_continue(message):
        return
    user_id = message.from_user.id
    doc = message.document
    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, stylish_text("⚠️ Bot locked, cannot accept files."))
        return
    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    if current_files >= file_limit:
        limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
        bot.reply_to(message, stylish_text(f"⚠️ File limit ({current_files}/{limit_str}) reached."))
        return
    file_name = doc.file_name
    if not file_name:
        bot.reply_to(message, stylish_text("⚠️ No file name."))
        return
    file_ext = os.path.splitext(file_name)[1].lower()
    if file_ext not in ['.py', '.js', '.zip']:
        bot.reply_to(message, stylish_text("⚠️ Only .py, .js, .zip allowed."))
        return
    if doc.file_size > 20 * 1024 * 1024:
        bot.reply_to(message, stylish_text("⚠️ File too large (max 20MB)."))
        return
    user_name = message.from_user.first_name
    user_username = message.from_user.username or "No username"
    upload_id = add_pending_upload(
        user_id=user_id,
        file_id=doc.file_id,
        file_name=file_name,
        file_type=file_ext[1:],
        file_size=doc.file_size,
        user_name=user_name,
        user_username=user_username,
        extra_info=""
    )
    if not upload_id:
        bot.reply_to(message, stylish_text("❌ Internal error, please try later."))
        return
    bot.reply_to(message, stylish_text(f"✅ File {file_name} submitted for admin approval. You will be notified when approved or rejected."))
    for admin_id in admin_ids:
        try:
            caption = (f"📥 New file requires approval\n"
                       f"👤 User: {user_name} (@{user_username})\n"
                       f"🆔 User ID: {user_id}\n"
                       f"📄 File: {file_name}\n"
                       f"📏 Size: {doc.file_size // 1024} KB\n"
                       f"🆔 Upload ID: {upload_id}")
            sent = bot.send_document(admin_id, doc.file_id, caption=stylish_text(caption))
            markup = types.InlineKeyboardMarkup()
            markup.add(
                types.InlineKeyboardButton("✅ Approve", callback_data=f"approve_upload_{upload_id}"),
                types.InlineKeyboardButton("❌ Reject", callback_data=f"reject_upload_{upload_id}")
            )
            bot.edit_message_reply_markup(admin_id, sent.message_id, reply_markup=markup)
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")

# --- Approval / Rejection Callback ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('approve_upload_') or call.data.startswith('reject_upload_'))
def handle_approval_callback(call):
    if not check_subscription_and_continue(None, call):
        return
    admin_id = call.from_user.id
    if admin_id not in admin_ids:
        bot.answer_callback_query(call.id, stylish_text("⚠️ Only admins can approve/reject."), show_alert=True)
        return
    upload_id = int(call.data.split('_')[-1])
    pending = get_pending_upload(upload_id)
    if not pending:
        bot.answer_callback_query(call.id, stylish_text("⚠️ This upload request no longer exists."), show_alert=True)
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception: pass
        return
    user_id = pending['user_id']
    file_name = pending['file_name']
    if call.data.startswith('approve_upload_'):
        bot.answer_callback_query(call.id, stylish_text("✅ Approving and starting..."))
        success = process_approved_file(upload_id, admin_chat_id=call.message.chat.id, user_message_obj=call.message)
        if success:
            try:
                bot.send_message(user_id, stylish_text(f"✅ Your file {file_name} has been approved and is now running."))
            except Exception as e:
                logger.error(f"Could not notify user {user_id}: {e}")
            try:
                bot.edit_message_caption(
                    caption=stylish_text(call.message.caption + "\n\n✅ APPROVED"),
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=None
                )
            except Exception: pass
        else:
            bot.send_message(call.message.chat.id, stylish_text(f"❌ Failed to process file for user {user_id}."))
    else:
        bot.answer_callback_query(call.id, stylish_text("❌ Rejected."))
        delete_pending_upload(upload_id)
        reject_msg = "AGLI BAR SE YE FILE RUN MT KARNA SIR"
        try:
            bot.send_message(user_id, stylish_text(f"❌ Your file {file_name} was rejected by admin.\n\n{reject_msg}"))
        except Exception as e:
            logger.error(f"Could not notify user {user_id}: {e}")
        try:
            bot.edit_message_caption(
                caption=stylish_text(call.message.caption + "\n\n❌ REJECTED"),
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=None
            )
        except Exception: pass

# ======================= GITHUB DEPLOY =======================
def parse_github_url(url):
    url = re.sub(r'\.git$', '', url)
    if 'github.com' not in url:
        raise ValueError("Not a valid GitHub URL")
    parts = url.split('github.com/')[-1].split('/')
    if len(parts) < 2:
        raise ValueError("Invalid GitHub URL format")
    owner = parts[0]
    repo = parts[1]
    branch = 'main'
    if len(parts) >= 4 and parts[2] == 'tree':
        branch = parts[3]
    return owner, repo, branch

def download_github_repo(owner, repo, branch, token=None):
    url = f"https://api.github.com/repos/{owner}/{repo}/zipball/{branch}"
    headers = {}
    if token:
        headers['Authorization'] = f'token {token}'
    resp = requests.get(url, headers=headers, stream=True)
    if resp.status_code == 404:
        raise Exception("Repository or branch not found")
    if resp.status_code == 401:
        raise Exception("Invalid or missing access token (private repo)")
    if resp.status_code != 200:
        raise Exception(f"GitHub API error: {resp.status_code}")
    content_length = resp.headers.get('content-length')
    if content_length and int(content_length) > 20 * 1024 * 1024:
        raise Exception("Repository ZIP exceeds 20MB limit")
    return resp.content

github_data = {}

def _logic_github_deploy(message):
    if not check_subscription_and_continue(message):
        return
    user_id = message.from_user.id
    if get_user_file_count(user_id) >= get_user_file_limit(user_id):
        bot.reply_to(message, stylish_text("⚠️ You have reached your file limit. Delete some files first."))
        return
    github_data[user_id] = {'step': 'url'}
    bot.reply_to(message, stylish_text("📦 Send me the GitHub repository URL.\nExample: https://github.com/user/repo\n\nSend /cancel to abort."))

@bot.message_handler(func=lambda m: m.from_user.id in github_data and github_data[m.from_user.id]['step'] == 'url')
def github_get_url(message):
    if not check_subscription_and_continue(message):
        return
    user_id = message.from_user.id
    if message.text and message.text.lower() == '/cancel':
        del github_data[user_id]
        bot.reply_to(message, stylish_text("❌ GitHub deploy cancelled."))
        return
    url = message.text.strip()
    try:
        owner, repo, branch = parse_github_url(url)
    except Exception as e:
        bot.reply_to(message, stylish_text(f"❌ Invalid GitHub URL: {e}"))
        return
    github_data[user_id]['url'] = url
    github_data[user_id]['owner'] = owner
    github_data[user_id]['repo'] = repo
    github_data[user_id]['branch'] = branch
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("🔒 Private", callback_data=f"github_private_{user_id}"),
        types.InlineKeyboardButton("🌐 Public", callback_data=f"github_public_{user_id}")
    )
    bot.reply_to(message, stylish_text("Is this a private repository?"), reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('github_private_') or call.data.startswith('github_public_'))
def github_repo_type(call):
    user_id = int(call.data.split('_')[-1])
    if call.from_user.id != user_id:
        bot.answer_callback_query(call.id, "Not for you", show_alert=True)
        return
    if user_id not in github_data:
        bot.answer_callback_query(call.id, "Session expired", show_alert=True)
        return
    if call.data.startswith('github_private_'):
        github_data[user_id]['step'] = 'token'
        bot.edit_message_text("🔑 Send your GitHub personal access token (with `repo` scope).\nSend /cancel to abort.",
                              call.message.chat.id, call.message.message_id)
    else:
        github_data[user_id]['token'] = None
        _process_github_download(call.message.chat.id, user_id)
    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda m: m.from_user.id in github_data and github_data[m.from_user.id].get('step') == 'token')
def github_get_token(message):
    if not check_subscription_and_continue(message):
        return
    user_id = message.from_user.id
    if message.text and message.text.lower() == '/cancel':
        del github_data[user_id]
        bot.reply_to(message, stylish_text("❌ GitHub deploy cancelled."))
        return
    token = message.text.strip()
    github_data[user_id]['token'] = token
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except Exception:
        pass
    _process_github_download(message.chat.id, user_id)

def _process_github_download(chat_id, user_id):
    data = github_data.get(user_id)
    if not data:
        bot.send_message(chat_id, stylish_text("Session expired. Start again."))
        return
    url = data['url']
    owner = data['owner']
    repo = data['repo']
    branch = data['branch']
    token = data.get('token')
    
    msg = bot.send_message(chat_id, stylish_text("📡 𝐄𝐒𝐓𝐀𝐁𝐋𝐈𝐒𝐇𝐈𝐍𝐆 𝐑𝐄𝐏𝐎 𝐋𝐈𝐍𝐊...\n\n[▓░░░░░░░░░] 10%"))
    time.sleep(1.5)
    bot.edit_message_text(stylish_text("📡 𝐄𝐒𝐓𝐀𝐁𝐋𝐈𝐒𝐇𝐈𝐍𝐆 𝐑𝐄𝐏𝐎 𝐋𝐈𝐍𝐊...\n\n[▓▓░░░░░░░░] 20%"), chat_id, msg.message_id)
    time.sleep(1)
    bot.edit_message_text(stylish_text("🔗 𝐑𝐄𝐏𝐎 𝐂𝐎𝐍𝐍𝐄𝐂𝐓𝐈𝐎𝐍...\n\n[▓▓▓░░░░░░░] 30%"), chat_id, msg.message_id)
    time.sleep(1)
    bot.edit_message_text(stylish_text("🌐 𝐂𝐎𝐍𝐍𝐄𝐂𝐓𝐈𝐍𝐆 𝐓𝐎 𝐑𝐄𝐏𝐎...\n\n[▓▓▓▓░░░░░░] 40%"), chat_id, msg.message_id)
    time.sleep(0.8)
    bot.edit_message_text(stylish_text("🌐 𝐂𝐎𝐍𝐍𝐄𝐂𝐓𝐈𝐍𝐆 𝐓𝐎 𝐑𝐄𝐏𝐎...\n\n[▓▓▓▓▓░░░░░] 55%"), chat_id, msg.message_id)
    time.sleep(0.8)
    bot.edit_message_text(stylish_text("🌐 𝐂𝐎𝐍𝐍𝐄𝐂𝐓𝐈𝐍𝐆 𝐓𝐎 𝐑𝐄𝐏𝐎...\n\n[▓▓▓▓▓▓▓░░░] 70%"), chat_id, msg.message_id)
    time.sleep(0.8)
    bot.edit_message_text(stylish_text("📥 𝐃𝐎𝐖𝐍𝐋𝐎𝐀𝐃𝐈𝐍𝐆 𝐑𝐄𝐏𝐎...\n\n[▓▓▓▓▓▓▓▓▓░] 90%"), chat_id, msg.message_id)
    time.sleep(1)
    bot.edit_message_text(stylish_text("📥 𝐃𝐎𝐖𝐍𝐋𝐎𝐀𝐃𝐈𝐍𝐆 𝐑𝐄𝐏𝐎...\n\n[▓▓▓▓▓▓▓▓▓▓] 100%"), chat_id, msg.message_id)
    time.sleep(0.5)
    
    try:
        zip_content = download_github_repo(owner, repo, branch, token)
        bot.edit_message_text(stylish_text("✅ 𝐒𝐔𝐂𝐂𝐄𝐒𝐒𝐅𝐔𝐋\n\nRepository downloaded successfully. Submitting for admin approval..."), chat_id, msg.message_id)
    except Exception as e:
        bot.edit_message_text(stylish_text(f"❌ Download failed: {e}"), chat_id, msg.message_id)
        del github_data[user_id]
        return
    
    token_provided = bool(token)
    data['token'] = None
    token = None
    file_name = f"{repo}_{branch}.zip"
    try:
        sent = bot.send_document(chat_id, io.BytesIO(zip_content), visible_file_name=file_name, caption=stylish_text("🔄 Submitting for admin approval..."))
        file_id = sent.document.file_id
        file_size = sent.document.file_size
        user_name = bot.get_chat(user_id).first_name
        user_username = bot.get_chat(user_id).username or "No username"
        extra_info = f"GitHub URL: {url}\nToken: {'[provided - hidden]' if token_provided else 'Not required (public repo)'}"
        upload_id = add_pending_upload(
            user_id=user_id,
            file_id=file_id,
            file_name=file_name,
            file_type='zip',
            file_size=file_size,
            user_name=user_name,
            user_username=user_username,
            extra_info=extra_info
        )
        if not upload_id:
            bot.send_message(chat_id, stylish_text("❌ Internal error, try again later."))
            return
        for admin_id in admin_ids:
            try:
                caption = (f"📥 New GitHub repo requires approval\n"
                           f"👤 User: {user_name} (@{user_username})\n"
                           f"🆔 User ID: {user_id}\n"
                           f"📦 Repo URL: {url}\n"
                           f"🔑 Token: {'[provided - hidden]' if token_provided else 'Public repo (no token)'}\n"
                           f"📄 File: {file_name}\n"
                           f"📏 Size: {file_size // 1024} KB\n"
                           f"🆔 Upload ID: {upload_id}")
                sent_admin = bot.send_document(admin_id, file_id, caption=stylish_text(caption))
                markup = types.InlineKeyboardMarkup()
                markup.add(
                    types.InlineKeyboardButton("✅ Approve", callback_data=f"approve_upload_{upload_id}"),
                    types.InlineKeyboardButton("❌ Reject", callback_data=f"reject_upload_{upload_id}")
                )
                bot.edit_message_reply_markup(admin_id, sent_admin.message_id, reply_markup=markup)
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")
        bot.send_message(chat_id, stylish_text(f"✅ GitHub repository submitted for admin approval.\nYou will be notified when approved/rejected."))
    except Exception as e:
        bot.send_message(chat_id, stylish_text(f"❌ Failed to submit: {e}"))
    finally:
        del github_data[user_id]

# ======================= RECOMMENDED INSTALL =======================
def _logic_recommended_install(message):
    if not check_subscription_and_continue(message):
        return
    text = (
        "📦 Python Package Installer\n\n"
        "Send me the package name to install.\n"
        "Examples:\n"
        "• requests\n"
        "• numpy\n"
        "• pandas==1.5.0\n"
        "• git+https://github.com/user/repo.git\n\n"
        "Or send a requirements.txt file.\n\n"
        "Recommended packages:\n"
        "pip, setuptools, wheel, requests, numpy, pandas, flask, aiohttp, pyrogram, python-dotenv, beautifulsoup4, lxml, pillow, matplotlib, scipy, scikit-learn, pytest\n\n"
        "Send ✅ to start installation or type a package name to install it manually."
    )
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("✅ Install Recommended", callback_data="install_recommended"))
    markup.add(types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_install"))
    bot.reply_to(message, stylish_text(text), reply_markup=markup)
    bot.register_next_step_handler(message, process_manual_package_install)

def process_manual_package_install(message):
    if not check_subscription_and_continue(message):
        return
    text = message.text.strip()
    if text == "✅":
        recommended = ["pip", "setuptools", "wheel", "requests", "numpy", "pandas", "flask", "aiohttp", "pyrogram", "python-dotenv", "beautifulsoup4", "lxml", "pillow", "matplotlib", "scipy", "scikit-learn", "pytest"]
        bot.reply_to(message, stylish_text(f"🚀 Installing {len(recommended)} recommended packages... This may take a while."))
        success = 0
        failed = 0
        for pkg in recommended:
            try:
                result = subprocess.run([sys.executable, '-m', 'pip', 'install', pkg], capture_output=True, text=True)
                if result.returncode == 0:
                    success += 1
                else:
                    failed += 1
                    logger.error(f"Failed to install {pkg}: {result.stderr}")
            except Exception as e:
                failed += 1
                logger.error(f"Error installing {pkg}: {e}")
            time.sleep(0.5)
        bot.send_message(message.chat.id, stylish_text(f"✅ Installation complete.\n✅ Success: {success}\n❌ Failed: {failed}"))
    elif text.lower() == '/cancel':
        bot.reply_to(message, stylish_text("Installation cancelled."))
    else:
        bot.reply_to(message, stylish_text(f"📦 Installing {text}..."))
        try:
            result = subprocess.run([sys.executable, '-m', 'pip', 'install', text], capture_output=True, text=True)
            if result.returncode == 0:
                bot.send_message(message.chat.id, stylish_text(f"✅ Successfully installed {text}"))
            else:
                error_msg = result.stderr[:500]
                bot.send_message(message.chat.id, stylish_text(f"❌ Failed to install {text}\nError: {error_msg}"))
        except Exception as e:
            logger.error(f"Install command error: {e}", exc_info=True)
            bot.send_message(message.chat.id, stylish_text("❌ Kuch gadbad ho gayi. Dobara try karo ya admin ko batao."))

@bot.callback_query_handler(func=lambda call: call.data == "install_recommended")
def install_recommended_callback(call):
    if not check_subscription_and_continue(None, call):
        return
    bot.answer_callback_query(call.id, "Installing recommended packages...")
    recommended = ["pip", "setuptools", "wheel", "requests", "numpy", "pandas", "flask", "aiohttp", "pyrogram", "python-dotenv", "beautifulsoup4", "lxml", "pillow", "matplotlib", "scipy", "scikit-learn", "pytest"]
    bot.send_message(call.message.chat.id, stylish_text(f"🚀 Installing {len(recommended)} packages... Please wait."))
    success = 0
    failed = 0
    for pkg in recommended:
        try:
            result = subprocess.run([sys.executable, '-m', 'pip', 'install', pkg], capture_output=True, text=True)
            if result.returncode == 0:
                success += 1
            else:
                failed += 1
        except Exception:
            failed += 1
        time.sleep(0.5)
    bot.send_message(call.message.chat.id, stylish_text(f"✅ Done.\n✅ Success: {success}\n❌ Failed: {failed}"))

@bot.callback_query_handler(func=lambda call: call.data == "cancel_install")
def cancel_install_callback(call):
    bot.answer_callback_query(call.id, "Cancelled.")
    bot.delete_message(call.message.chat.id, call.message.message_id)

# ======================= AI ASSISTANT - SAMBANOVA INTEGRATION =======================
def call_sambanova_sync(message: str, model_name: str) -> str:
    headers = {
        'Authorization': f'Bearer {SAMBA_API_KEY}',
        'Content-Type': 'application/json'
    }
    payload = {
        'model': model_name,
        'messages': [
            {'role': 'system', 'content': 'You are a helpful AI assistant.'},
            {'role': 'user', 'content': message}
        ],
        'temperature': 0.7,
        'max_tokens': 500,
        'top_p': 0.95
    }
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.post(SAMBA_URL, headers=headers, json=payload, timeout=30)
            if response.status_code == 429:
                wait = (2 ** attempt) + random.uniform(0, 1)
                time.sleep(wait)
                continue
            if response.status_code == 200:
                data = response.json()
                return data['choices'][0]['message']['content']
            else:
                return f"⚠️ API error {response.status_code}: {response.text[:200]}"
        except Exception as e:
            if attempt == max_retries - 1:
                return f"❌ Network error: {str(e)}"
            time.sleep(2 ** attempt)
    return "❌ Max retries exceeded."

def auto_fix_modules_from_text(user_id: int, text: str, chat_id: int):
    missing_modules = set()
    matches = re.findall(r"ModuleNotFoundError: No module named '(.+?)'", text)
    matches.extend(re.findall(r"ImportError: No module named '(.+?)'", text))
    matches.extend(re.findall(r"No module named '(.+?)'", text))
    
    for mod in matches:
        mod = mod.strip().strip("'\"")
        if mod and not mod.startswith('.') and mod not in ['sys', 'os', 're', 'time', 'json', 'datetime']:
            missing_modules.add(mod)
    
    if not missing_modules:
        bot.send_message(chat_id, stylish_text("ℹ️ No missing modules found in your message. If you need help, just ask me directly."))
        return
    
    bot.send_message(chat_id, stylish_text(f"🔍 Detected missing modules: {', '.join(missing_modules)}\n\n🔄 Installing them automatically..."))
    
    installed = 0
    failed = 0
    results = []
    for mod in missing_modules:
        try:
            result = subprocess.run([sys.executable, '-m', 'pip', 'install', mod], capture_output=True, text=True)
            if result.returncode == 0:
                installed += 1
                results.append(f"✅ {mod}")
            else:
                failed += 1
                results.append(f"❌ {mod} - {result.stderr[:100]}")
        except Exception as e:
            failed += 1
            results.append(f"❌ {mod} - {str(e)}")
        time.sleep(0.5)
    
    summary = f"🔧 Auto-fix completed:\n" + "\n".join(results) + f"\n\n✅ Installed: {installed}\n❌ Failed: {failed}\n\n💡 After installation, restart your script using the Restart button."
    bot.send_message(chat_id, stylish_text(summary))

def get_bot_help_text() -> str:
    return (
        "🤖 𝐀𝐆𝐄𝐍𝐓 𝐇𝐄𝐋𝐏 𝐆𝐔𝐈𝐃𝐄\n\n"
        "📌 𝐁𝐚𝐬𝐢𝐜 𝐂𝐨𝐦𝐦𝐚𝐧𝐝𝐬\n"
        "/start - Main menu\n"
        "/uploadfile - Upload .py / .js / .zip\n"
        "/checkfiles - See your uploaded files\n"
        "/restart - Restart all your scripts\n"
        "/stop - Stop all your scripts\n"
        "/botspeed - Check bot speed & system info\n"
        "/statistics - Bot statistics\n"
        "/model - Show current AI model\n"
        "/setmodel - Change AI model (admin only)\n\n"
        "📂 𝐅𝐢𝐥𝐞 𝐌𝐚𝐧𝐚𝐠𝐞𝐦𝐞𝐧𝐭\n"
        "• Upload file → Admin approves → Script starts automatically\n"
        "• From 'My Files' you can: Start, Stop, Restart, Delete, View Logs, AI Fix\n"
        "• Supported: .py (Python), .js (Node.js), .zip (extracted, auto-detects main script)\n\n"
        "🔧 𝐀𝐈 𝐅𝐢𝐱\n"
        "Automatically installs missing Python modules from error logs.\n"
        "Click 'AI Fix' on any file or just send me the error message here!\n\n"
        "⚙️ 𝐑𝐞𝐜𝐨𝐦𝐦𝐞𝐧𝐝𝐞𝐝 𝐈𝐧𝐬𝐭𝐚𝐥𝐥\n"
        "Install common Python packages (requests, numpy, flask, etc.) in one click.\n\n"
        "🌐 𝐆𝐢𝐭𝐇𝐮𝐛 𝐃𝐞𝐩𝐥𝐨𝐲\n"
        "Send a GitHub repo URL, bot will download zip and submit for approval.\n\n"
        "🤖 𝐀𝐈 𝐀𝐠𝐞𝐧𝐭\n"
        "Powered by SambaNova AI. Supports models: Llama, DeepSeek, MiniMax, GPT-OSS.\n"
        "Admins can change the model with /setmodel.\n\n"
        "👑 𝐀𝐝𝐦𝐢𝐧 𝐅𝐞𝐚𝐭𝐮𝐫𝐞𝐬 (only for admins/owner)\n"
        "• Add/Remove admins\n"
        "• Set custom file limits per user\n"
        "• Add/Remove subscriptions (premium users get 15 files)\n"
        "• Broadcast message to all users\n"
        "• Lock/unlock bot\n"
        "• Run all user scripts\n"
        "• Change AI model (/setmodel)\n\n"
        "💡 𝐓𝐢𝐩𝐬\n"
        "• Free users: 2 files max, Premium: 15, Admin: 999, Owner: unlimited\n"
        "• Script logs are saved as `.log` file in your folder\n"
        "• If your script crashes, check logs and use AI Fix\n"
        "• You can ask me any coding question, I'll use the selected AI model to answer.\n\n"
        "🤖 Simply type your question or send an error message, and I'll help!"
    )

def handle_deepseek_chat(message):
    if not check_subscription_and_continue(message):
        return
    if not message.text:
        bot.reply_to(message, stylish_text("Please send a text message or an error log."))
        bot.register_next_step_handler(message, handle_deepseek_chat)
        return
    user_text = message.text.strip()
    if user_text.lower() == '/cancel':
        bot.clear_step_handler_by_chat_id(message.chat.id)
        bot.reply_to(message, stylish_text("AI Agent mode cancelled."))
        return
    
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    help_keywords = ['how to use', 'help', 'commands', 'kya kar sakta', 'kaise use', 'guide', 'features', 'what can you do', 'bot kaise chalaye']
    if any(keyword in user_text.lower() for keyword in help_keywords):
        bot.send_chat_action(chat_id, 'typing')
        bot.reply_to(message, stylish_text(get_bot_help_text()))
        bot.register_next_step_handler(message, handle_deepseek_chat)
        return
    
    error_patterns = ['ModuleNotFoundError', 'ImportError', 'No module named', 'module not found']
    if any(pattern in user_text for pattern in error_patterns):
        bot.send_chat_action(chat_id, 'typing')
        thinking = bot.reply_to(message, stylish_text("🔍 Detecting missing modules and fixing automatically..."))
        auto_fix_modules_from_text(user_id, user_text, chat_id)
        try:
            bot.delete_message(chat_id, thinking.message_id)
        except Exception:
            pass
        bot.register_next_step_handler(message, handle_deepseek_chat)
        return
    
    bot.send_chat_action(chat_id, 'typing')
    thinking_msg = bot.reply_to(message, stylish_text("🤔 Thinking..."))
    model_full = AVAILABLE_MODELS[global_model]
    response = call_sambanova_sync(user_text, model_full)
    if len(response) > 4000:
        response = response[:4000] + "... (truncated)"
    bot.edit_message_text(stylish_text(response), chat_id, thinking_msg.message_id)
    bot.register_next_step_handler(message, handle_deepseek_chat)

def _logic_ai_assistant(message):
    if not check_subscription_and_continue(message):
        return
    welcome_text = (
        f"🤖 Aɪ Aɢᴇɴᴛ\n\n"
        f"⚡ Cᴜʀʀᴇɴᴛ Aɪ Mᴏᴅᴇʟ: *{global_model}* ({AVAILABLE_MODELS[global_model]})\n\n"
        "📌 Fᴇᴀᴛᴜʀᴇs:\n\n"
        "• 📦 Aᴜᴛᴏ-ꜰɪx – Sᴇɴᴅ ᴀɴʏ `ModuleNotFoundError` ᴏʀ `ImportError`, I ᴡɪʟʟ ɪɴꜱᴛᴀʟʟ ᴍɪꜱꜱɪɴɢ ᴘᴀᴄᴋᴀɢᴇs.\n"
        "• 📄 Cʜᴇᴄᴋ Lᴏɢs – Aꜱᴋ ᴍᴇ ᴛᴏ ꜱʜᴏᴡ ʟᴏɢꜱ ᴏꜰ ʏᴏᴜʀ ꜰɪʟᴇ.\n"
        "• 💡 Bᴏᴛ Uꜱᴀɢᴇ – Tʏᴘᴇ `how to use` ᴏʀ `help` ꜰᴏʀ ᴄᴏᴍᴘʟᴇᴛᴇ ɢᴜɪᴅᴇ.\n"
        "• 🚀 Cᴏᴅɪɴɢ Qᴜᴇꜱᴛɪᴏɴꜱ – Aꜱᴋ ᴍᴇ ᴀɴʏᴛʜɪɴɢ, I ᴜꜱᴇ ᴛʜᴇ ꜱᴇʟᴇᴄᴛᴇᴅ Aɪ ᴍᴏᴅᴇʟ.\n\n"
        "🤖 Aᴅᴍɪɴꜱ ᴄᴀɴ ᴄʜᴀɴɢᴇ ᴛʜᴇ ᴍᴏᴅᴇʟ ᴜꜱɪɴɢ `/setmodel`.\n\n"
        "📌 Jᴜꜱᴛ ꜱᴇɴᴅ ʏᴏᴜʀ ᴇʀʀᴏʀ, Qᴜᴇꜱᴛɪᴏɴ, ᴏʀ ᴛʏᴘᴇ `help` "
        "ᴀɴᴅ I ᴡɪʟʟ ᴀꜱꜱɪꜱᴛ ʏᴏᴜ ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ.\n\n"
        "🤖 Aɪ Pᴏᴡᴇʀᴇᴅ • 24×7 Aᴄᴛɪᴠᴇ"
    )
    bot.reply_to(message, stylish_text(welcome_text), parse_mode="Markdown")
    bot.register_next_step_handler(message, handle_deepseek_chat)

# ======================= AI FIX =======================
def ai_fix_script(owner_id, file_name, chat_id, message_id):
    folder = get_user_folder(owner_id)
    log_path = os.path.join(folder, f"{os.path.splitext(file_name)[0]}.log")
    if not os.path.exists(log_path):
        bot.send_message(chat_id, stylish_text(f"No log file found for {file_name}. Run the script first to generate errors."))
        return
    with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
        log_content = f.read()
    missing_modules = set()
    matches = re.findall(r"ModuleNotFoundError: No module named '(.+?)'", log_content)
    matches.extend(re.findall(r"ImportError: No module named '(.+?)'", log_content))
    for mod in matches:
        mod = mod.strip().strip("'\"")
        missing_modules.add(mod)
    if not missing_modules:
        bot.send_message(chat_id, stylish_text(f"✅ No missing modules found in log of {file_name}. The script might have other errors. Use /checklogs {file_name} to see details."))
        return
    installed = 0
    failed = 0
    results = []
    for mod in missing_modules:
        bot.send_message(chat_id, stylish_text(f"📦 Installing {mod}..."))
        try:
            result = subprocess.run([sys.executable, '-m', 'pip', 'install', mod], capture_output=True, text=True)
            if result.returncode == 0:
                installed += 1
                results.append(f"✅ {mod}")
            else:
                failed += 1
                results.append(f"❌ {mod} - {result.stderr[:100]}")
        except Exception as e:
            failed += 1
            results.append(f"❌ {mod} - {str(e)}")
        time.sleep(0.5)
    summary = f"🔧 AI Fix completed for {file_name}:\n" + "\n".join(results) + f"\n\n✅ Installed: {installed}\n❌ Failed: {failed}"
    bot.send_message(chat_id, stylish_text(summary))
    bot.send_message(chat_id, stylish_text("💡 Restart the script using the Restart button to apply changes."))

@bot.callback_query_handler(func=lambda call: call.data.startswith('aifix_'))
def ai_fix_callback(call):
    if not check_subscription_and_continue(None, call):
        return
    try:
        _, owner_id_str, file_name = call.data.split('_', 2)
        owner_id = int(owner_id_str)
        if call.from_user.id != owner_id and call.from_user.id not in admin_ids:
            bot.answer_callback_query(call.id, stylish_text("Permission denied."), show_alert=True)
            return
        bot.answer_callback_query(call.id, stylish_text("AI Fix running... This may take a moment."))
        threading.Thread(target=ai_fix_script, args=(owner_id, file_name, call.message.chat.id, call.message.message_id)).start()
    except Exception as e:
        logger.error(f"AI Fix error: {e}")
        bot.answer_callback_query(call.id, stylish_text(f"Error: {e}"), show_alert=True)

# ======================= BAN / UNBAN COMMANDS =======================
@bot.message_handler(commands=['ban'])
def cmd_ban(message):
    if not check_subscription_and_continue(message):
        return
    user_id = message.from_user.id
    if user_id not in admin_ids and user_id != OWNER_ID:
        bot.reply_to(message, stylish_text("⚠️ Admin only command."))
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, stylish_text("Usage: /ban user_id\nExample: /ban 123456789"))
        return
    try:
        target_id = int(parts[1])
    except Exception:
        bot.reply_to(message, stylish_text("Invalid user ID. Use numeric ID."))
        return
    if target_id in admin_ids or target_id == OWNER_ID:
        bot.reply_to(message, stylish_text("❌ Cannot ban an admin or owner."))
        return
    if ban_user(target_id):
        bot.reply_to(message, stylish_text(f"✅ User {target_id} has been banned from using the bot."))
        try:
            bot.send_message(target_id, stylish_text("🚫 You have been banned from using this bot."))
        except Exception:
            pass
    else:
        bot.reply_to(message, stylish_text(f"❌ Failed to ban user {target_id}."))

@bot.message_handler(commands=['unban'])
def cmd_unban(message):
    if not check_subscription_and_continue(message):
        return
    user_id = message.from_user.id
    if user_id not in admin_ids and user_id != OWNER_ID:
        bot.reply_to(message, stylish_text("⚠️ Admin only command."))
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, stylish_text("Usage: /unban user_id\nExample: /unban 123456789"))
        return
    try:
        target_id = int(parts[1])
    except Exception:
        bot.reply_to(message, stylish_text("Invalid user ID. Use numeric ID."))
        return
    if unban_user(target_id):
        bot.reply_to(message, stylish_text(f"✅ User {target_id} has been unbanned."))
        try:
            bot.send_message(target_id, stylish_text("✅ You have been unbanned. You can now use the bot again."))
        except Exception:
            pass
    else:
        bot.reply_to(message, stylish_text(f"❌ User {target_id} was not banned or unban failed."))

# ======================= ADMIN: STOP ALL RUNNING SCRIPTS =======================
@bot.message_handler(commands=['stop'])
def cmd_stop_all(message):
    if not check_subscription_and_continue(message):
        return
    user_id = message.from_user.id
    if user_id not in admin_ids and user_id != OWNER_ID:
        bot.reply_to(message, stylish_text("⚠️ Admin only command."))
        return
    running = list(bot_scripts.items())
    if not running:
        bot.reply_to(message, stylish_text("ℹ️ No scripts are currently running."))
        return
    stopped = 0
    for key, info in running:
        try:
            kill_process_tree(info)
            stopped += 1
        except Exception as e:
            logger.error(f"Failed to stop {key}: {e}")
    bot_scripts.clear()
    bot.reply_to(message, stylish_text(f"✅ Stopped {stopped} running script(s)."))

# ======================= USER STOP ALL SCRIPTS =======================
def _logic_stop_my_scripts(message):
    if not check_subscription_and_continue(message):
        return
    user_id = message.from_user.id
    files = user_files.get(user_id, [])
    if not files:
        bot.reply_to(message, stylish_text("📂 You have no uploaded files to stop."))
        return
    stopped = 0
    for file_name, ftype in files:
        script_key = f"{user_id}_{file_name}"
        if script_key in bot_scripts:
            kill_process_tree(bot_scripts[script_key])
            del bot_scripts[script_key]
            stopped += 1
            time.sleep(0.2)
    bot.reply_to(message, stylish_text(f"⏹ Stopped {stopped} of your script(s)."))

# ======================= USER RESTART ALL SCRIPTS =======================
def _logic_restart_my_scripts(message):
    if not check_subscription_and_continue(message):
        return
    user_id = message.from_user.id
    files = user_files.get(user_id, [])
    if not files:
        bot.reply_to(message, stylish_text("📂 You have no uploaded files to restart."))
        return
    bot.reply_to(message, stylish_text("🔄 Restarting all your scripts..."))
    stopped = 0
    started = 0
    for file_name, ftype in files:
        script_key = f"{user_id}_{file_name}"
        if script_key in bot_scripts:
            kill_process_tree(bot_scripts[script_key])
            del bot_scripts[script_key]
            stopped += 1
            time.sleep(0.3)
    for file_name, ftype in files:
        folder = get_user_folder(user_id)
        script_path = os.path.join(folder, file_name)
        if not os.path.exists(script_path):
            bot.send_message(message.chat.id, stylish_text(f"⚠️ File {file_name} not found locally, skipping."))
            continue
        if ftype == 'py':
            threading.Thread(target=run_script, args=(script_path, user_id, folder, file_name, message)).start()
        elif ftype == 'js':
            threading.Thread(target=run_js_script, args=(script_path, user_id, folder, file_name, message)).start()
        else:
            continue
        started += 1
        time.sleep(0.5)
    bot.send_message(message.chat.id, stylish_text(f"✅ Restarted {started} of your script(s). (Stopped {stopped} before restart)"))

# ======================= AUTO-RECOVERY SYSTEM =======================
def auto_recovery_worker():
    while True:
        time.sleep(30)
        try:
            current_time = time.time()
            for script_key, info in list(bot_scripts.items()):
                try:
                    proc = info.get('process')
                    if not proc or not hasattr(proc, 'pid'):
                        continue
                    pid = proc.pid
                    if not pid:
                        continue
                    try:
                        p = psutil.Process(pid)
                        if not p.is_running() or p.status() == psutil.STATUS_ZOMBIE:
                            raise psutil.NoSuchProcess(pid)
                    except psutil.NoSuchProcess:
                        last = auto_recovery_last_restart.get(script_key, 0)
                        if current_time - last < 60:
                            continue
                        auto_recovery_last_restart[script_key] = current_time
                        
                        owner_id = info.get('script_owner_id')
                        file_name = info.get('file_name')
                        chat_id = info.get('chat_id')
                        file_type = info.get('type')
                        user_folder = info.get('user_folder')
                        
                        if not owner_id or not file_name:
                            continue
                        
                        logger.info(f"Auto-recovery: Restarting {script_key} (crashed)")
                        if chat_id:
                            try:
                                bot.send_message(chat_id, stylish_text(f"🔄 Auto-Recovery: {file_name} crashed and is being restarted..."))
                            except Exception:
                                pass
                        
                        if 'log_file' in info and hasattr(info['log_file'], 'close') and not info['log_file'].closed:
                            try:
                                info['log_file'].close()
                            except Exception:
                                pass
                        del bot_scripts[script_key]
                        
                        script_path = os.path.join(user_folder, file_name)
                        if not os.path.exists(script_path):
                            logger.warning(f"Auto-recovery: {script_path} missing, cannot restart")
                            continue
                        if file_type == 'py':
                            threading.Thread(target=run_script, args=(script_path, owner_id, user_folder, file_name, None)).start()
                        elif file_type == 'js':
                            threading.Thread(target=run_js_script, args=(script_path, owner_id, user_folder, file_name, None)).start()
                except Exception as e:
                    logger.error(f"Auto-recovery error for {script_key}: {e}")
        except Exception as e:
            logger.error(f"Auto-recovery worker error: {e}")

recovery_thread = threading.Thread(target=auto_recovery_worker, daemon=True)
recovery_thread.start()

# ======================= RESTART COMMAND =======================
@bot.message_handler(commands=['restart'])
def cmd_restart_all(message):
    if not check_subscription_and_continue(message):
        return
    user_id = message.from_user.id
    if user_id in admin_ids or user_id == OWNER_ID:
        running_scripts = []
        for key, info in list(bot_scripts.items()):
            try:
                parts = key.split('_', 1)
                if len(parts) == 2:
                    owner_id = int(parts[0])
                    file_name = parts[1]
                    ftype = None
                    if owner_id in user_files:
                        for fname, ft in user_files[owner_id]:
                            if fname == file_name:
                                ftype = ft
                                break
                    if ftype:
                        running_scripts.append((owner_id, file_name, ftype))
            except Exception as e:
                logger.error(f"Error capturing script {key}: {e}")
        if not running_scripts:
            bot.reply_to(message, stylish_text("ℹ️ No scripts are currently running."))
            return
        stopped = 0
        for key, info in list(bot_scripts.items()):
            try:
                kill_process_tree(info)
                stopped += 1
            except Exception as e:
                logger.error(f"Failed to stop {key}: {e}")
        bot_scripts.clear()
        bot.reply_to(message, stylish_text(f"🛑 Stopped {stopped} script(s). Now restarting all user scripts..."))
        started = 0
        for owner_id, file_name, ftype in running_scripts:
            folder = get_user_folder(owner_id)
            script_path = os.path.join(folder, file_name)
            if not os.path.exists(script_path):
                logger.warning(f"Cannot restart {file_name} (user {owner_id}) - file missing")
                continue
            if ftype == 'py':
                threading.Thread(target=run_script, args=(script_path, owner_id, folder, file_name, message)).start()
            elif ftype == 'js':
                threading.Thread(target=run_js_script, args=(script_path, owner_id, folder, file_name, message)).start()
            else:
                continue
            started += 1
            time.sleep(0.5)
        bot.send_message(message.chat.id, stylish_text(f"✅ Restarted {started} script(s) for all users."))
        return
    _logic_restart_my_scripts(message)

# --- Menu Creation ---
def create_control_buttons(script_owner_id, file_name, is_running=True):
    markup = types.InlineKeyboardMarkup(row_width=2)
    if is_running:
        markup.row(
            types.InlineKeyboardButton("🔴 Stop", callback_data=f'stop_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("🔄 Restart", callback_data=f'restart_{script_owner_id}_{file_name}')
        )
        markup.row(
            types.InlineKeyboardButton("🗑️ Delete", callback_data=f'delete_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("📜 Logs", callback_data=f'logs_{script_owner_id}_{file_name}')
        )
        markup.row(
            types.InlineKeyboardButton("🤖 AI Fix", callback_data=f'aifix_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("🔙 Back", callback_data='check_files')
        )
    else:
        markup.row(
            types.InlineKeyboardButton("🟢 Start", callback_data=f'start_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("🗑️ Delete", callback_data=f'delete_{script_owner_id}_{file_name}')
        )
        markup.row(
            types.InlineKeyboardButton("📜 View Logs", callback_data=f'logs_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("🤖 AI Fix", callback_data=f'aifix_{script_owner_id}_{file_name}')
        )
        markup.row(types.InlineKeyboardButton("🔙 Back to Files", callback_data='check_files'))
    return markup

def create_main_menu_inline(user_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [
        types.InlineKeyboardButton('📢 𝐔𝐩𝐝𝐚𝐭𝐞𝐬 𝐂𝐡𝐚𝐧𝐧𝐞𝐥', callback_data='updates_channel'),
        types.InlineKeyboardButton('🌏 Upload', callback_data='upload'),
        types.InlineKeyboardButton('📁 𝐌𝐲 𝐅𝐢𝐥𝐞𝐬', callback_data='check_files'),
        types.InlineKeyboardButton('⚡ 𝐁𝐨𝐭 𝐒𝐩𝐞𝐞𝐝', callback_data='speed'),
        types.InlineKeyboardButton('⚙️ Recommended Install', callback_data='recommended_install'),
        types.InlineKeyboardButton('🤖 AI Assistant', callback_data='ai_assistant'),
        types.InlineKeyboardButton('🌐 𝐆𝐈𝐓𝐇𝐔𝐁', callback_data='github_deploy'),
        types.InlineKeyboardButton('📞 𝐂𝐨𝐧𝐭𝐚𝐜𝐭 𝐎𝐰𝐧𝐞𝐫', url=f"https://t.me/{YOUR_USERNAME.replace('@', '')}")
    ]
    if user_id in admin_ids:
        admin_buttons = [
            types.InlineKeyboardButton('💳 𝐒𝐮𝐛𝐬𝐜𝐫𝐢𝐩𝐭𝐢𝐨𝐧𝐬', callback_data='subscription'),
            types.InlineKeyboardButton('🚀 𝐒𝐭𝐚𝐭𝐮𝐬', callback_data='stats'),
            types.InlineKeyboardButton('🔒 𝐋𝐨𝐜𝐤 𝐁𝐨𝐭' if not bot_locked else '🔓 Unlock Bot', callback_data='lock_bot' if not bot_locked else 'unlock_bot'),
            types.InlineKeyboardButton('📢 𝐁𝐫𝐨𝐚𝐝𝐜𝐚𝐬𝐭', callback_data='broadcast'),
            types.InlineKeyboardButton('🛠️ 𝐀𝐝𝐦𝐢𝐧 𝐏𝐚𝐧𝐞𝐥', callback_data='admin_panel'),
            types.InlineKeyboardButton('🟢 Run All User Scripts', callback_data='run_all_scripts')
        ]
        markup.add(buttons[0])
        markup.add(buttons[1], buttons[2])
        markup.add(buttons[3], admin_buttons[0])
        markup.add(admin_buttons[1], admin_buttons[3])
        markup.add(admin_buttons[2], admin_buttons[5])
        markup.add(admin_buttons[4])
        markup.add(buttons[4], buttons[5])
        markup.add(buttons[6])
        markup.add(buttons[7])
    else:
        markup.add(buttons[0])
        markup.add(buttons[1], buttons[2])
        markup.add(buttons[3])
        markup.add(types.InlineKeyboardButton('🚀 𝐒𝐭𝐚𝐭𝐮𝐬', callback_data='stats'))
        markup.add(buttons[4], buttons[5])
        markup.add(buttons[6])
        markup.add(buttons[7])
    return markup

def create_reply_keyboard_main_menu(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    layout = ADMIN_COMMAND_BUTTONS_LAYOUT_USER_SPEC if user_id in admin_ids else COMMAND_BUTTONS_LAYOUT_USER_SPEC
    for row in layout:
        markup.add(*[types.KeyboardButton(text) for text in row])
    return markup

def create_admin_panel():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('➕ Add Admin', callback_data='add_admin'),
        types.InlineKeyboardButton('➖ Remove Admin', callback_data='remove_admin')
    )
    markup.row(types.InlineKeyboardButton('📋 List Admins', callback_data='list_admins'))
    markup.row(types.InlineKeyboardButton('🔧 Set User Limit', callback_data='set_user_limit'))
    markup.row(types.InlineKeyboardButton('🤖 Change AI Model', callback_data='change_ai_model'))
    markup.row(types.InlineKeyboardButton('🔙 Back to Main', callback_data='back_to_main'))
    return markup

def create_subscription_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('➕ Add Subscription', callback_data='add_subscription'),
        types.InlineKeyboardButton('➖ Remove Subscription', callback_data='remove_subscription')
    )
    markup.row(types.InlineKeyboardButton('🔍 Check Subscription', callback_data='check_subscription'))
    markup.row(types.InlineKeyboardButton('🔙 Back to Main', callback_data='back_to_main'))
    return markup

def create_model_selection_markup():
    markup = types.InlineKeyboardMarkup(row_width=2)
    for model_key in AVAILABLE_MODELS:
        markup.add(types.InlineKeyboardButton(f"{model_key.upper()} – {AVAILABLE_MODELS[model_key]}", callback_data=f"setmodel_{model_key}"))
    markup.add(types.InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_panel"))
    return markup

# --- Logic Functions ---
def _logic_send_welcome(message):
    if not check_subscription_and_continue(message):
        return
    user_id = message.from_user.id
    chat_id = message.chat.id
    user_name = message.from_user.first_name
    user_username = message.from_user.username or "Not set"
    if bot_locked and user_id not in admin_ids:
        bot.send_message(chat_id, stylish_text("⚠️ Bot locked by admin."))
        return
    if user_id not in active_users:
        add_active_user(user_id)
        try:
            owner_msg = (f"🎉 New user!\n👤 {user_name}\n✳️ @{user_username}\n🆔 ID: {user_id}")
            bot.send_message(OWNER_ID, stylish_text(owner_msg))
        except Exception: pass

    current_files = get_user_file_count(user_id)
    box = (
        "┏━━━━━━━━━━━━━━━━━━━━━━┓\n"
        "┃        𝐂ʜιᴋσσ 𝐇σsᴛιηɢ                         ┃\n"        "┃                                              ┃\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
        f"👤 Wᴇʟᴄᴏᴍᴇ {user_name}!\n"
        f"🆔 Uꜱᴇʀ ɪᴅ: {user_id}\n\n"
        f"📁 Fɪʟᴇꜱ: {current_files}\n\n"
        "⚡ Fᴇᴀᴛᴜʀᴇꜱ:\n"
        "• Aᴜᴛᴏ-Rᴇᴄᴏᴠᴇʀʏ Sʏꜱᴛᴇᴍ\n"
        "• Pʏᴛʜᴏɴ / Jꜱ / Zɪᴘ Sᴜᴘᴘᴏʀᴛ\n\n"
        "Uꜱᴇ Tʜᴇ Bᴜᴛᴛᴏɴ Bᴇʟᴏᴡ Tᴏ Nᴀᴠɪɢᴀᴛᴇ."
    )
    try:
        photos = bot.get_user_profile_photos(user_id, limit=1)
        if photos.total_count > 0:
            file_id = photos.photos[0][-1].file_id
            bot.send_photo(chat_id, file_id, caption=stylish_text(box), reply_markup=create_reply_keyboard_main_menu(user_id))
        else:
            bot.send_message(chat_id, stylish_text(box), reply_markup=create_reply_keyboard_main_menu(user_id))
    except Exception as e:
        logger.error(f"Error sending welcome photo: {e}")
        bot.send_message(chat_id, stylish_text(box), reply_markup=create_reply_keyboard_main_menu(user_id))

def _logic_updates_channel(message):
    if not check_subscription_and_continue(message):
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(" TELEGRAM TEAM", url="https://t.me/BrokenXworld")
    )
    bot.reply_to(message, stylish_text("📢 Our Channels:"), reply_markup=markup)

def _logic_upload_file(message):
    if not check_subscription_and_continue(message):
        return
    user_id = message.from_user.id
    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, stylish_text("⚠️ Bot locked."))
        return
    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    if current_files >= file_limit:
        limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
        bot.reply_to(message, stylish_text(f"⚠️ Limit reached ({current_files}/{limit_str}). Delete files first."))
        return
    bot.reply_to(message, stylish_text("📤 Send your .py, .js or .zip file. It will be sent to admins for approval."))

def _logic_check_files(message):
    if not check_subscription_and_continue(message):
        return
    user_id = message.from_user.id
    files = user_files.get(user_id, [])
    if not files:
        bot.reply_to(message, stylish_text("📂 No files uploaded yet."))
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for fname, ftype in sorted(files):
        is_running = is_bot_running(user_id, fname)
        status = "🟢 Running" if is_running else "🔴 Stopped"
        markup.add(types.InlineKeyboardButton(f"{fname} ({ftype}) - {status}", callback_data=f'file_{user_id}_{fname}'))
    bot.reply_to(message, stylish_text("📂 Your files:"), reply_markup=markup)

def _logic_bot_speed(message):
    if not check_subscription_and_continue(message):
        return
    user_id = message.from_user.id
    start = time.time()
    wait = bot.reply_to(message, stylish_text("🏃 Testing speed..."))
    try:
        bot.send_chat_action(message.chat.id, 'typing')
        latency = round((time.time() - start) * 1000, 2)
        latency_sec = round(latency / 1000, 4)
        cpu_freq = psutil.cpu_freq()
        cpu_ghz = round(cpu_freq.current / 1000, 1) if cpu_freq else 0.0
        mem = psutil.virtual_memory()
        total_ram_gb = round(mem.total / (1024**3), 2)
        free_ram_gb = round(mem.available / (1024**3), 2)
        status = "🔓 Unlocked" if not bot_locked else "🔒 Locked"
        if user_id == OWNER_ID:
            level = "👑 Owner"
        elif user_id in admin_ids:
            level = "🛡️ Admin"
        elif user_id in user_subscriptions and user_subscriptions[user_id]['expiry'] > datetime.now():
            level = "⭐ Premium"
        else:
            level = "🆓 Free"
        msg = (f"⚡ 𝗕𝗢𝗧 𝗦𝗣𝗘𝗘𝗗: {latency_sec} seconds\n"
               f"⚙️ 𝗖𝗣𝗨: {cpu_ghz} GHz\n"
               f"💾 𝗥𝗔𝗠: {total_ram_gb} GB\n"
               f"🟢 𝗙𝗥𝗘𝗘: {free_ram_gb} GB\n"
               f"🚦 𝗦𝘁𝗮𝘁𝘂𝘀: {status}\n"
               f"👤 𝗟𝗲𝘃𝗲𝗹: {level}")
        bot.edit_message_text(stylish_text(msg), message.chat.id, wait.message_id)
    except Exception as e:
        bot.edit_message_text(stylish_text("❌ Speed test error."), message.chat.id, wait.message_id)

def _logic_contact_owner(message):
    if not check_subscription_and_continue(message):
        return
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton('📞 Contact Owner', url=f"https://t.me/{YOUR_USERNAME.replace('@', '')}"))
    bot.reply_to(message, stylish_text("Contact owner:"), reply_markup=markup)

def _logic_statistics(message):
    if not check_subscription_and_continue(message):
        return
    user_id = message.from_user.id
    total_users = len(active_users)
    total_files = sum(len(f) for f in user_files.values())
    
    # ✅ Fixed: iterate over a copy to avoid dictionary changed size
    running = 0
    for key, info in list(bot_scripts.items()):
        try:
            if is_bot_running(int(key.split('_')[0]), info['file_name']):
                running += 1
        except Exception:
            pass
    
    now = datetime.now()
    uptime_delta = now - BOT_START_TIME
    days = uptime_delta.days
    hours, remainder = divmod(uptime_delta.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{days}d {hours}h {minutes}m {seconds}s"
    
    if user_id in admin_ids:
        msg = (f"📊 STATUS\n"
               f"👥 Users: {total_users}\n"
               f"📂 Files: {total_files}\n"
               f"🟢 Running: {running}\n"
               f"⏱️ Uptime: {uptime_str}\n"
               f"🔒 Bot locked: {bot_locked}")
    else:
        msg = (f"📊 STATUS\n"
               f"👥 Users: {total_users}\n"
               f"📂 Files: {total_files}\n"
               f"🟢 Running: {running}\n"
               f"⏱️ Uptime: {uptime_str}")
    bot.reply_to(message, stylish_text(msg))

def _logic_subscriptions_panel(message):
    if not check_subscription_and_continue(message):
        return
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, stylish_text("⚠️ Admin only."))
        return
    bot.reply_to(message, stylish_text("💳 Subscription Management"), reply_markup=create_subscription_menu())

def _logic_broadcast_init(message):
    if not check_subscription_and_continue(message):
        return
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, stylish_text("⚠️ Admin only."))
        return
    msg = bot.reply_to(message, stylish_text("📢 Send broadcast message.\n/cancel to abort."))
    bot.register_next_step_handler(msg, process_broadcast_message)

def process_broadcast_message(message):
    if message.from_user.id not in admin_ids:
        return
    if message.text and message.text.lower() == '/cancel':
        bot.reply_to(message, stylish_text("Broadcast cancelled."))
        return
    content = message.text
    if not content:
        bot.reply_to(message, stylish_text("Cannot broadcast empty text."))
        return
    target = len(active_users)
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ Confirm", callback_data=f"confirm_broadcast_{message.message_id}"),
        types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_broadcast")
    )
    bot.reply_to(message, stylish_text(f"⚠️ Confirm broadcast to {target} users:\n\n{content[:500]}"), reply_markup=markup)

def handle_confirm_broadcast(call):
    admin_id = call.from_user.id
    if admin_id not in admin_ids:
        bot.answer_callback_query(call.id, stylish_text("Admin only."), show_alert=True)
        return
    original = call.message.reply_to_message
    if not original or not original.text:
        bot.answer_callback_query(call.id, stylish_text("No broadcast message found."))
        return
    text = original.text
    bot.answer_callback_query(call.id, stylish_text("Broadcasting..."))
    bot.edit_message_text(stylish_text("📢 Broadcasting..."), call.message.chat.id, call.message.message_id, reply_markup=None)
    threading.Thread(target=execute_broadcast, args=(text, call.message.chat.id)).start()

def handle_cancel_broadcast(call):
    bot.answer_callback_query(call.id, stylish_text("Cancelled."))
    bot.delete_message(call.message.chat.id, call.message.message_id)

def execute_broadcast(text, admin_chat_id):
    sent = 0
    failed = 0
    for uid in list(active_users):
        if is_user_banned(uid):
            continue
        try:
            bot.send_message(uid, stylish_text(text))
            sent += 1
        except Exception:
            failed += 1
        time.sleep(0.05)
    bot.send_message(admin_chat_id, stylish_text(f"📢 Broadcast done.\n✅ Sent: {sent}\n❌ Failed: {failed}"))

def _logic_toggle_lock_bot(message):
    if not check_subscription_and_continue(message):
        return
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, stylish_text("⚠️ Admin only."))
        return
    global bot_locked
    bot_locked = not bot_locked
    status = "locked" if bot_locked else "unlocked"
    bot.reply_to(message, stylish_text(f"🔒 Bot {status}."))

def _logic_admin_panel(message):
    if not check_subscription_and_continue(message):
        return
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, stylish_text("⚠️ Admin only."))
        return
    bot.reply_to(message, stylish_text("🛠️ Admin Panel"), reply_markup=create_admin_panel())

def _logic_run_all_scripts(message):
    if not check_subscription_and_continue(message):
        return
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, stylish_text("⚠️ Admin only."))
        return
    bot.reply_to(message, stylish_text("⏳ Starting all user scripts..."))
    started = 0
    for uid, files in list(user_files.items()):
        if is_user_banned(uid):
            continue
        folder = get_user_folder(uid)
        for fname, ftype in files:
            if not is_bot_running(uid, fname):
                path = os.path.join(folder, fname)
                if os.path.exists(path):
                    if ftype == 'py':
                        threading.Thread(target=run_script, args=(path, uid, folder, fname, message)).start()
                    else:
                        threading.Thread(target=run_js_script, args=(path, uid, folder, fname, message)).start()
                    started += 1
                    time.sleep(0.5)
    bot.send_message(message.chat.id, stylish_text(f"✅ Attempted to start {started} scripts."))

# --- Model management commands ---
@bot.message_handler(commands=['model'])
def cmd_show_model(message):
    if not check_subscription_and_continue(message):
        return
    bot.reply_to(message, stylish_text(f"🧠 Current AI model: *{global_model}* ({AVAILABLE_MODELS[global_model]})", parse_mode="Markdown"))

@bot.message_handler(commands=['setmodel'])
def cmd_set_model(message):
    if not check_subscription_and_continue(message):
        return
    user_id = message.from_user.id
    if user_id not in admin_ids:
        bot.reply_to(message, stylish_text("⛔ Only admins can change the AI model."))
        return
    markup = create_model_selection_markup()
    bot.reply_to(message, stylish_text("Select a new AI model:"), reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('setmodel_'))
def set_model_callback(call):
    user_id = call.from_user.id
    if user_id not in admin_ids:
        bot.answer_callback_query(call.id, stylish_text("Not authorized."), show_alert=True)
        return
    model_key = call.data.split('_')[1]
    if model_key in AVAILABLE_MODELS:
        global global_model
        global_model = model_key
        bot.answer_callback_query(call.id, stylish_text(f"✅ Model changed to {model_key.upper()}"))
        bot.edit_message_text(stylish_text(f"✅ AI model changed to *{model_key}* ({AVAILABLE_MODELS[model_key]})", parse_mode="Markdown"),
                              call.message.chat.id, call.message.message_id)
    else:
        bot.answer_callback_query(call.id, stylish_text("Invalid model."), show_alert=True)

# --- Custom Limit Management (set) ---
def process_set_user_limit(message):
    if message.from_user.id not in admin_ids:
        return
    text = message.text.strip()
    if text.lower() == '/cancel':
        bot.reply_to(message, stylish_text("Cancelled."))
        return
    parts = text.split()
    if len(parts) != 2:
        bot.reply_to(message, stylish_text("Invalid format. Use: user_id limit\nExample: 123456789 50"))
        return
    try:
        uid = int(parts[0])
        limit = int(parts[1])
        if limit < 0:
            bot.reply_to(message, stylish_text("Limit must be >= 0."))
            return
    except Exception:
        bot.reply_to(message, stylish_text("Invalid user ID or limit (must be numbers)."))
        return
    set_user_custom_limit(uid, limit)
    bot.reply_to(message, stylish_text(f"✅ User {uid} now has a custom file limit of {limit}."))

# --- Button handlers & command handlers ---
BUTTON_TEXT_TO_LOGIC = {
    "📢 𝐔𝐩𝐝𝐚𝐭𝐞𝐬 𝐂𝐡𝐚𝐧𝐧𝐞𝐥": _logic_updates_channel,
    "🌏 Upload": _logic_upload_file,
    "📁 𝐌𝐲 𝐅𝐢𝐥𝐞𝐬": _logic_check_files,
    "⚡ 𝐁𝐨𝐭 𝐒𝐩𝐞𝐞𝐝": _logic_bot_speed,
    "📞 𝐂𝐨𝐧𝐭𝐚𝐜𝐭 𝐎𝐰𝐧𝐞𝐫": _logic_contact_owner,
    "🚀 𝐒𝐭𝐚𝐭𝐮𝐬": _logic_statistics,
    "🔄 𝐑𝐞𝐬𝐭𝐚𝐫𝐭": _logic_restart_my_scripts,
    "⏹ 𝐒𝐭𝐨𝐩": _logic_stop_my_scripts,
    "💳 𝐒𝐮𝐛𝐬𝐜𝐫𝐢𝐩𝐭𝐢𝐨𝐧𝐬": _logic_subscriptions_panel,
    "📢 𝐁𝐫𝐨𝐚𝐝𝐜𝐚𝐬𝐭": _logic_broadcast_init,
    "🔒 𝐋𝐨𝐜𝐤 𝐁𝐨𝐭": _logic_toggle_lock_bot,
    "🟢 𝐑𝐮𝐧𝐧𝐢𝐧𝐠 𝐀𝐥𝐥 𝐂𝐨𝐝𝐞": _logic_run_all_scripts,
    "🛠️ 𝐀𝐝𝐦𝐢𝐧 𝐏𝐚𝐧𝐞𝐥": _logic_admin_panel,
    "⚙️ Recommended Install": _logic_recommended_install,
    "🤖 𝐀𝐆𝐄𝐍𝐓": _logic_ai_assistant,
    "🌐 𝐆𝐈𝐓𝐇𝐔𝐁": _logic_github_deploy,
}

@bot.message_handler(func=lambda m: m.text in BUTTON_TEXT_TO_LOGIC)
def handle_button_text(message):
    if not check_subscription_and_continue(message):
        return
    BUTTON_TEXT_TO_LOGIC[message.text](message)

@bot.message_handler(commands=['start', 'help'])
def cmd_start(message):
    if not check_subscription_and_continue(message):
        return
    _logic_send_welcome(message)

@bot.message_handler(commands=['uploadfile'])
def cmd_upload(message):
    if not check_subscription_and_continue(message):
        return
    _logic_upload_file(message)

@bot.message_handler(commands=['checkfiles'])
def cmd_check(message):
    if not check_subscription_and_continue(message):
        return
    _logic_check_files(message)

@bot.message_handler(commands=['botspeed'])
def cmd_speed(message):
    if not check_subscription_and_continue(message):
        return
    _logic_bot_speed(message)

@bot.message_handler(commands=['statistics'])
def cmd_stats(message):
    if not check_subscription_and_continue(message):
        return
    _logic_statistics(message)

@bot.message_handler(commands=['broadcast'])
def cmd_broadcast(message):
    if not check_subscription_and_continue(message):
        return
    _logic_broadcast_init(message)

@bot.message_handler(commands=['lockbot'])
def cmd_lock(message):
    if not check_subscription_and_continue(message):
        return
    _logic_toggle_lock_bot(message)

@bot.message_handler(commands=['adminpanel'])
def cmd_admin(message):
    if not check_subscription_and_continue(message):
        return
    _logic_admin_panel(message)

@bot.message_handler(commands=['runningallcode'])
def cmd_runall(message):
    if not check_subscription_and_continue(message):
        return
    _logic_run_all_scripts(message)

@bot.message_handler(commands=['ping'])
def ping(message):
    if not check_subscription_and_continue(message):
        return
    start = time.time()
    m = bot.reply_to(message, stylish_text("Pong!"))
    latency = round((time.time() - start) * 1000, 2)
    bot.edit_message_text(stylish_text(f"Pong! {latency} ms"), message.chat.id, m.message_id)

# --- Main Callback Handler ---
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    if call.data.startswith('verify_channel_'):
        verify_channel_callback(call)
        return
    if not check_subscription_and_continue(None, call):
        return
    global bot_locked
    user_id = call.from_user.id
    data = call.data
    if bot_locked and user_id not in admin_ids and data not in ['speed', 'stats', 'back_to_main', 'recommended_install', 'ai_assistant', 'updates_channel', 'github_deploy']:
        bot.answer_callback_query(call.id, stylish_text("Bot locked."), show_alert=True)
        return
    if data == 'upload':
        _logic_upload_file(call.message)
        bot.answer_callback_query(call.id)
    elif data == 'check_files':
        _logic_check_files(call.message)
        bot.answer_callback_query(call.id)
    elif data == 'speed':
        _logic_bot_speed(call.message)
        bot.answer_callback_query(call.id)
    elif data == 'stats':
        _logic_statistics(call.message)
        bot.answer_callback_query(call.id)
    elif data == 'back_to_main':
        _logic_send_welcome(call.message)
        bot.answer_callback_query(call.id)
    elif data == 'recommended_install':
        _logic_recommended_install(call.message)
        bot.answer_callback_query(call.id)
    elif data == 'ai_assistant':
        _logic_ai_assistant(call.message)
        bot.answer_callback_query(call.id)
    elif data == 'updates_channel':
        _logic_updates_channel(call.message)
        bot.answer_callback_query(call.id)
    elif data == 'github_deploy':
        _logic_github_deploy(call.message)
        bot.answer_callback_query(call.id)
    elif data == 'subscription':
        if user_id in admin_ids:
            _logic_subscriptions_panel(call.message)
        else:
            bot.answer_callback_query(call.id, stylish_text("Admin only."), show_alert=True)
    elif data == 'broadcast':
        if user_id in admin_ids:
            _logic_broadcast_init(call.message)
        else:
            bot.answer_callback_query(call.id, stylish_text("Admin only."), show_alert=True)
    elif data == 'lock_bot':
        if user_id in admin_ids:
            bot_locked = True
            bot.answer_callback_query(call.id, stylish_text("Bot locked."))
            _logic_send_welcome(call.message)
    elif data == 'unlock_bot':
        if user_id in admin_ids:
            bot_locked = False
            bot.answer_callback_query(call.id, stylish_text("Bot unlocked."))
            _logic_send_welcome(call.message)
    elif data == 'run_all_scripts':
        if user_id in admin_ids:
            _logic_run_all_scripts(call.message)
        else:
            bot.answer_callback_query(call.id, stylish_text("Admin only."), show_alert=True)
    elif data == 'admin_panel':
        if user_id in admin_ids:
            _logic_admin_panel(call.message)
        else:
            bot.answer_callback_query(call.id, stylish_text("Admin only."), show_alert=True)
    elif data == 'change_ai_model':
        if user_id in admin_ids:
            markup = create_model_selection_markup()
            bot.edit_message_text(stylish_text("Select a new AI model:"), call.message.chat.id, call.message.message_id, reply_markup=markup)
        else:
            bot.answer_callback_query(call.id, stylish_text("Admin only."), show_alert=True)
    elif data.startswith('setmodel_'):
        set_model_callback(call)
    elif data == 'add_admin':
        if user_id == OWNER_ID:
            msg = bot.send_message(call.message.chat.id, stylish_text("👑 Enter user ID to add as admin.\n/cancel"))
            bot.register_next_step_handler(msg, process_add_admin_id)
            bot.answer_callback_query(call.id)
        else:
            bot.answer_callback_query(call.id, stylish_text("Owner only."), show_alert=True)
    elif data == 'remove_admin':
        if user_id == OWNER_ID:
            msg = bot.send_message(call.message.chat.id, stylish_text("👑 Enter admin ID to remove.\n/cancel"))
            bot.register_next_step_handler(msg, process_remove_admin_id)
            bot.answer_callback_query(call.id)
        else:
            bot.answer_callback_query(call.id, stylish_text("Owner only."), show_alert=True)
    elif data == 'list_admins':
        if user_id in admin_ids:
            admins_str = "\n".join(f"- {aid} {'(Owner)' if aid == OWNER_ID else ''}" for aid in sorted(admin_ids))
            bot.send_message(call.message.chat.id, stylish_text(f"👑 Admins:\n{admins_str}"))
            bot.answer_callback_query(call.id)
        else:
            bot.answer_callback_query(call.id, stylish_text("Admin only."), show_alert=True)
    elif data == 'set_user_limit':
        if user_id in admin_ids:
            msg = bot.send_message(call.message.chat.id, stylish_text("🔧 Send user ID and new limit.\nFormat: `123456789 50`\nUse /cancel to abort."), parse_mode='Markdown')
            bot.register_next_step_handler(msg, process_set_user_limit)
            bot.answer_callback_query(call.id)
        else:
            bot.answer_callback_query(call.id, stylish_text("Admin only."), show_alert=True)
    elif data == 'add_subscription':
        if user_id in admin_ids:
            msg = bot.send_message(call.message.chat.id, stylish_text("💳 Enter user_id days (e.g., 12345678 30)\n/cancel"))
            bot.register_next_step_handler(msg, process_add_subscription)
            bot.answer_callback_query(call.id)
        else:
            bot.answer_callback_query(call.id, stylish_text("Admin only."), show_alert=True)
    elif data == 'remove_subscription':
        if user_id in admin_ids:
            msg = bot.send_message(call.message.chat.id, stylish_text("💳 Enter user ID to remove subscription.\n/cancel"))
            bot.register_next_step_handler(msg, process_remove_subscription)
            bot.answer_callback_query(call.id)
        else:
            bot.answer_callback_query(call.id, stylish_text("Admin only."), show_alert=True)
    elif data == 'check_subscription':
        if user_id in admin_ids:
            msg = bot.send_message(call.message.chat.id, stylish_text("💳 Enter user ID to check subscription.\n/cancel"))
            bot.register_next_step_handler(msg, process_check_subscription)
            bot.answer_callback_query(call.id)
        else:
            bot.answer_callback_query(call.id, stylish_text("Admin only."), show_alert=True)
    elif data.startswith('confirm_broadcast_'):
        handle_confirm_broadcast(call)
    elif data == 'cancel_broadcast':
        handle_cancel_broadcast(call)
    elif data.startswith('file_'):
        file_control_callback(call)
    elif data.startswith('start_'):
        start_bot_callback(call)
    elif data.startswith('stop_'):
        stop_bot_callback(call)
    elif data.startswith('restart_'):
        restart_bot_callback(call)
    elif data.startswith('delete_'):
        delete_bot_callback(call)
    elif data.startswith('logs_'):
        logs_bot_callback(call)
    elif data.startswith('aifix_'):
        ai_fix_callback(call)
    elif data == 'install_recommended':
        install_recommended_callback(call)
    elif data == 'cancel_install':
        cancel_install_callback(call)
    else:
        bot.answer_callback_query(call.id, stylish_text("Unknown action."))

# --- Admin & Subscription processing helpers ---
def process_add_admin_id(message):
    if message.from_user.id != OWNER_ID:
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, stylish_text("Cancelled."))
        return
    try:
        aid = int(message.text.strip())
        if aid == OWNER_ID:
            bot.reply_to(message, stylish_text("Owner is already admin."))
            return
        add_admin_db(aid)
        bot.reply_to(message, stylish_text(f"✅ User {aid} is now admin."))
    except Exception:
        bot.reply_to(message, stylish_text("Invalid ID. Use numeric ID."))

def process_remove_admin_id(message):
    if message.from_user.id != OWNER_ID:
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, stylish_text("Cancelled."))
        return
    try:
        aid = int(message.text.strip())
        if aid == OWNER_ID:
            bot.reply_to(message, stylish_text("Cannot remove owner."))
            return
        if remove_admin_db(aid):
            bot.reply_to(message, stylish_text(f"✅ Admin {aid} removed."))
        else:
            bot.reply_to(message, stylish_text("User was not admin."))
    except Exception:
        bot.reply_to(message, stylish_text("Invalid ID."))

def process_add_subscription(message):
    if message.from_user.id not in admin_ids:
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, stylish_text("Cancelled."))
        return
    try:
        parts = message.text.split()
        uid = int(parts[0])
        days = int(parts[1])
        current = user_subscriptions.get(uid, {}).get('expiry')
        start = current if current and current > datetime.now() else datetime.now()
        new_expiry = start + timedelta(days=days)
        save_subscription(uid, new_expiry)
        bot.reply_to(message, stylish_text(f"✅ Subscription for {uid} added. Expires {new_expiry.strftime('%Y-%m-%d')}"))
    except Exception:
        bot.reply_to(message, stylish_text("Invalid format. Use user_id days"))

def process_remove_subscription(message):
    if message.from_user.id not in admin_ids:
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, stylish_text("Cancelled."))
        return
    try:
        uid = int(message.text.strip())
        if uid in user_subscriptions:
            remove_subscription_db(uid)
            bot.reply_to(message, stylish_text(f"✅ Subscription removed for {uid}"))
        else:
            bot.reply_to(message, stylish_text("User has no active subscription."))
    except Exception:
        bot.reply_to(message, stylish_text("Invalid user ID."))

def process_check_subscription(message):
    if message.from_user.id not in admin_ids:
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, stylish_text("Cancelled."))
        return
    try:
        uid = int(message.text.strip())
        if uid in user_subscriptions:
            exp = user_subscriptions[uid]['expiry']
            if exp > datetime.now():
                days = (exp - datetime.now()).days
                bot.reply_to(message, stylish_text(f"✅ User {uid} has active sub. Expires {exp.strftime('%Y-%m-%d')} ({days} days left)"))
            else:
                bot.reply_to(message, stylish_text(f"⚠️ User {uid} subscription expired on {exp.strftime('%Y-%m-%d')}"))
        else:
            bot.reply_to(message, stylish_text(f"ℹ️ User {uid} has no subscription."))
    except Exception:
        bot.reply_to(message, stylish_text("Invalid user ID."))

# --- File control callbacks ---
def file_control_callback(call):
    try:
        _, owner_id_str, file_name = call.data.split('_', 2)
        owner_id = int(owner_id_str)
        if call.from_user.id != owner_id and call.from_user.id not in admin_ids:
            bot.answer_callback_query(call.id, stylish_text("You can only manage your own files."), show_alert=True)
            _logic_check_files(call.message)
            return
        files = user_files.get(owner_id, [])
        if not any(f[0] == file_name for f in files):
            bot.answer_callback_query(call.id, stylish_text("File not found."), show_alert=True)
            _logic_check_files(call.message)
            return
        is_running = is_bot_running(owner_id, file_name)
        ftype = next((f[1] for f in files if f[0] == file_name), '?')
        text = f"⚙️ Controls for {file_name} ({ftype}) of User {owner_id}\nStatus: {'🟢 Running' if is_running else '🔴 Stopped'}"
        bot.edit_message_text(stylish_text(text), call.message.chat.id, call.message.message_id,
                              reply_markup=create_control_buttons(owner_id, file_name, is_running))
        bot.answer_callback_query(call.id)
    except Exception as e:
        logger.error(f"file_control error: {e}")

def start_bot_callback(call):
    try:
        _, owner_id_str, file_name = call.data.split('_', 2)
        owner_id = int(owner_id_str)
        if call.from_user.id != owner_id and call.from_user.id not in admin_ids:
            bot.answer_callback_query(call.id, stylish_text("Permission denied."), show_alert=True)
            return
        if is_bot_running(owner_id, file_name):
            bot.answer_callback_query(call.id, stylish_text("Already running."), show_alert=True)
            return
        files = user_files.get(owner_id, [])
        ftype = next((f[1] for f in files if f[0] == file_name), None)
        if not ftype:
            bot.answer_callback_query(call.id, stylish_text("File not found."), show_alert=True)
            return
        folder = get_user_folder(owner_id)
        path = os.path.join(folder, file_name)
        if not os.path.exists(path):
            bot.answer_callback_query(call.id, stylish_text("File missing."), show_alert=True)
            return
        bot.answer_callback_query(call.id, stylish_text(f"Starting {file_name}..."))
        if ftype == 'py':
            threading.Thread(target=run_script, args=(path, owner_id, folder, file_name, call.message)).start()
        else:
            threading.Thread(target=run_js_script, args=(path, owner_id, folder, file_name, call.message)).start()
        time.sleep(1)
        is_running = is_bot_running(owner_id, file_name)
        text = f"⚙️ Controls for {file_name} ({ftype}) of User {owner_id}\nStatus: {'🟢 Running' if is_running else '🟡 Starting...'}"
        bot.edit_message_text(stylish_text(text), call.message.chat.id, call.message.message_id,
                              reply_markup=create_control_buttons(owner_id, file_name, is_running))
    except Exception as e:
        logger.error(f"start error: {e}")

def stop_bot_callback(call):
    try:
        _, owner_id_str, file_name = call.data.split('_', 2)
        owner_id = int(owner_id_str)
        if call.from_user.id != owner_id and call.from_user.id not in admin_ids:
            bot.answer_callback_query(call.id, stylish_text("Permission denied."), show_alert=True)
            return
        if not is_bot_running(owner_id, file_name):
            bot.answer_callback_query(call.id, stylish_text("Not running."), show_alert=True)
            return
        script_key = f"{owner_id}_{file_name}"
        if script_key in bot_scripts:
            kill_process_tree(bot_scripts[script_key])
            del bot_scripts[script_key]
        bot.answer_callback_query(call.id, stylish_text(f"Stopped {file_name}."))
        files = user_files.get(owner_id, [])
        ftype = next((f[1] for f in files if f[0] == file_name), '?')
        text = f"⚙️ Controls for {file_name} ({ftype}) of User {owner_id}\nStatus: 🔴 Stopped"
        bot.edit_message_text(stylish_text(text), call.message.chat.id, call.message.message_id,
                              reply_markup=create_control_buttons(owner_id, file_name, False))
    except Exception as e:
        logger.error(f"stop error: {e}")

def restart_bot_callback(call):
    try:
        _, owner_id_str, file_name = call.data.split('_', 2)
        owner_id = int(owner_id_str)
        if call.from_user.id != owner_id and call.from_user.id not in admin_ids:
            bot.answer_callback_query(call.id, stylish_text("Permission denied."), show_alert=True)
            return
        script_key = f"{owner_id}_{file_name}"
        if script_key in bot_scripts:
            kill_process_tree(bot_scripts[script_key])
            del bot_scripts[script_key]
        time.sleep(1)
        files = user_files.get(owner_id, [])
        ftype = next((f[1] for f in files if f[0] == file_name), None)
        if not ftype:
            bot.answer_callback_query(call.id, stylish_text("File not found."), show_alert=True)
            return
        folder = get_user_folder(owner_id)
        path = os.path.join(folder, file_name)
        if not os.path.exists(path):
            bot.answer_callback_query(call.id, stylish_text("File missing."), show_alert=True)
            return
        bot.answer_callback_query(call.id, stylish_text(f"Restarting {file_name}..."))
        if ftype == 'py':
            threading.Thread(target=run_script, args=(path, owner_id, folder, file_name, call.message)).start()
        else:
            threading.Thread(target=run_js_script, args=(path, owner_id, folder, file_name, call.message)).start()
        time.sleep(1)
        is_running = is_bot_running(owner_id, file_name)
        text = f"⚙️ Controls for {file_name} ({ftype}) of User {owner_id}\nStatus: {'🟢 Running' if is_running else '🟡 Starting...'}"
        bot.edit_message_text(stylish_text(text), call.message.chat.id, call.message.message_id,
                              reply_markup=create_control_buttons(owner_id, file_name, is_running))
    except Exception as e:
        logger.error(f"restart error: {e}")

def delete_bot_callback(call):
    try:
        _, owner_id_str, file_name = call.data.split('_', 2)
        owner_id = int(owner_id_str)
        if call.from_user.id != owner_id and call.from_user.id not in admin_ids:
            bot.answer_callback_query(call.id, stylish_text("Permission denied."), show_alert=True)
            return
        script_key = f"{owner_id}_{file_name}"
        if script_key in bot_scripts:
            kill_process_tree(bot_scripts[script_key])
            del bot_scripts[script_key]
        folder = get_user_folder(owner_id)
        file_path = os.path.join(folder, file_name)
        log_path = os.path.join(folder, f"{os.path.splitext(file_name)[0]}.log")
        for p in (file_path, log_path):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        remove_user_file_db(owner_id, file_name)
        bot.answer_callback_query(call.id, stylish_text(f"Deleted {file_name}."))
        bot.edit_message_text(stylish_text(f"🗑️ Deleted {file_name} (User {owner_id})"), call.message.chat.id, call.message.message_id)
    except Exception as e:
        logger.error(f"delete error: {e}")

def logs_bot_callback(call):
    try:
        _, owner_id_str, file_name = call.data.split('_', 2)
        owner_id = int(owner_id_str)
        if call.from_user.id != owner_id and call.from_user.id not in admin_ids:
            bot.answer_callback_query(call.id, stylish_text("Permission denied."), show_alert=True)
            return
        folder = get_user_folder(owner_id)
        log_path = os.path.join(folder, f"{os.path.splitext(file_name)[0]}.log")
        if not os.path.exists(log_path):
            bot.answer_callback_query(call.id, stylish_text("No logs yet."), show_alert=True)
            return
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            logs = f.read()
        if len(logs) > 4000:
            logs = logs[-4000:]
            logs = "...\n" + logs
        bot.send_message(call.message.chat.id, stylish_text(f"📜 Logs for {file_name}:\n{logs}"))
        bot.answer_callback_query(call.id)
    except Exception as e:
        logger.error(f"logs error: {e}")
        bot.answer_callback_query(call.id, stylish_text("Error reading logs."), show_alert=True)

# --- Cleanup and Main ---
def cleanup():
    logger.warning("Shutting down, killing all scripts...")
    for key, info in list(bot_scripts.items()):
        kill_process_tree(info)
    logger.warning("Cleanup done.")
atexit.register(cleanup)

# ======================= SELF‑RESTART MAIN LOOP =======================
if __name__ == '__main__':
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=30)
        except Exception as e:
            logger.error(f"Polling error: {e}")
            logger.info("Restarting bot in 5 seconds...")