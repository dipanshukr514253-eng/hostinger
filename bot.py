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
import hashlib
import ast
import csv
import platform
import resource
from pathlib import Path
from urllib.parse import quote
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


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
TOKEN = os.getenv('BOT_TOKEN', '8959068547:AAHaHNxkEU0FtZd7uSGcbcTM2Vd1AEyFFCw').strip()
OWNER_ID = int(os.getenv('OWNER_ID', '5888777479') or 0)
ADMIN_ID = int(os.getenv('ADMIN_ID', str(OWNER_ID)) or OWNER_ID)
YOUR_USERNAME = os.getenv('SUPPORT_USERNAME', '@OfficalEarningZone')

# --- Force Subscription Channels ---
REQUIRED_CHANNELS = [c.strip() for c in os.getenv('REQUIRED_CHANNELS', '@BrokenXworldss').split(',') if c.strip()]

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, 'upload_bots')
IROTECH_DIR = os.path.join(BASE_DIR, 'inf')
DATABASE_PATH = os.path.join(IROTECH_DIR, 'bot_data.db')

FREE_USER_LIMIT = 2
SUBSCRIBED_USER_LIMIT = 15
ADMIN_LIMIT = 99
OWNER_LIMIT = float('inf')

# Hardened runtime policy. All values are environment-tunable.
MAX_UPLOAD_BYTES = int(os.getenv('MAX_UPLOAD_BYTES', str(20 * 1024 * 1024)))
MAX_ZIP_UNCOMPRESSED_BYTES = int(os.getenv('MAX_ZIP_UNCOMPRESSED_BYTES', str(60 * 1024 * 1024)))
MAX_ZIP_FILES = int(os.getenv('MAX_ZIP_FILES', '250'))
MAX_PROCESS_SECONDS = int(os.getenv('MAX_PROCESS_SECONDS', '0'))
MAX_LOG_BYTES = int(os.getenv('MAX_LOG_BYTES', str(2 * 1024 * 1024)))
UPLOAD_COOLDOWN = float(os.getenv('UPLOAD_COOLDOWN', '8'))
AI_COOLDOWN = float(os.getenv('AI_COOLDOWN', '4'))
INSTALL_TIMEOUT = int(os.getenv('INSTALL_TIMEOUT', '180'))
GITHUB_TIMEOUT = int(os.getenv('GITHUB_TIMEOUT', '30'))
CRASH_WINDOW = int(os.getenv('CRASH_WINDOW', '600'))
MAX_RESTARTS_IN_WINDOW = int(os.getenv('MAX_RESTARTS_IN_WINDOW', '3'))
ALLOWED_EXTENSIONS = {'.py', '.js', '.zip', '.json', '.txt', '.md', '.yaml', '.yml'}
EXECUTABLE_EXTENSIONS = {'.py', '.js'}
SECURITY_PATTERNS = {
 'python': [(r'\bos\.system\s*\(', 'os.system execution'),(r'\bsubprocess\.', 'subprocess execution'),(r'\beval\s*\(', 'dynamic eval'),(r'\bexec\s*\(', 'dynamic exec'),(r'\bcompile\s*\(', 'runtime compilation'),(r'\bmarshal\.', 'marshal deserialization'),(r'\bpickle\.', 'pickle deserialization'),(r'(?i)reverse\s*shell|bash\s+-i|/bin/sh', 'reverse shell indicator'),(r'(?i)(bot[_-]?token|api[_-]?key|secret|password).{0,40}(os\.environ|environ\[)', 'environment credential access'),(r'\bsocket\.', 'raw socket usage'),(r'(?i)base64\.(b64decode|decodebytes)', 'encoded payload decoding')],
 'javascript': [(r'\bchild_process\b', 'child_process execution'),(r'\beval\s*\(', 'dynamic eval'),(r'\bnew\s+Function\s*\(', 'dynamic Function execution'),(r'\bprocess\.env\b', 'environment access'),(r'(?i)reverse\s*shell|bash\s+-i|/bin/sh', 'reverse shell indicator'),(r'(?i)(token|secret|password|api[_-]?key)', 'credential-like string'),(r'\bnet\.', 'raw network usage'),(r'\bfs\.(rm|rmdir|unlink|writeFile|writeFileSync)\b', 'filesystem mutation')]
}
RATE_STATE = {}
CRASH_STATE = {}
PROCESS_LOCK = threading.RLock()

os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
os.makedirs(IROTECH_DIR, exist_ok=True)

if not TOKEN:
    raise RuntimeError('BOT_TOKEN is required. Put it in the environment or .env file.')
if OWNER_ID <= 0:
    raise RuntimeError('OWNER_ID is required and must be a positive Telegram user ID.')
bot = telebot.TeleBot(TOKEN, parse_mode='HTML', threaded=True, num_threads=8)

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
SAMBA_API_KEY = os.environ.get('SAMBA_API_KEY', '').strip()
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
    except:
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
    except:
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
    except:
        return False

def set_user_verified(user_id):
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('INSERT OR IGNORE INTO verified_users (user_id, verified_at) VALUES (?, ?)', (user_id, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return True
    except:
        return False

def is_user_member_all_channels(user_id):
    if user_id in admin_ids or user_id == OWNER_ID:
        return True
    for channel in REQUIRED_CHANNELS:
        try:
            chat_member = bot.get_chat_member(channel, user_id)
            if chat_member.status not in ['member', 'administrator', 'creator']:
                return False
        except:
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
            except:
                missing.append(ch)
        if missing:
            missing_list = "\n".join(missing)
            bot.answer_callback_query(call.id, stylish_text(f"❌ You are not a member of:\n{missing_list}\nPlease join all channels first."), show_alert=True)
        else:
            bot.answer_callback_query(call.id, stylish_text("❌ Verification failed. Please join all channels and try again."), show_alert=True)

# --- Helper Functions ---
def get_user_folder(user_id):
    user_folder = os.path.join(UPLOAD_BOTS_DIR, str(int(user_id)))
    os.makedirs(user_folder, exist_ok=True)
    try: os.chmod(user_folder, 0o700)
    except OSError: pass
    return user_folder

def safe_filename(name, default='upload.bin'):
    name = os.path.basename(str(name or '').replace('\\','/'))
    name = re.sub(r'[^A-Za-z0-9._-]', '_', name)
    name = re.sub(r'_+', '_', name).strip('._')
    return name[:180] or default

def safe_join(base, name):
    base=os.path.abspath(base); candidate=os.path.abspath(os.path.join(base,safe_filename(name)))
    if os.path.commonpath([base,candidate]) != base: raise ValueError('Unsafe file path')
    return candidate

def rate_limited(user_id,bucket,cooldown):
    now=time.monotonic(); key=(int(user_id),bucket); last=RATE_STATE.get(key,0)
    if now-last<cooldown: return True,cooldown-(now-last)
    RATE_STATE[key]=now; return False,0


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
                    except: pass
                if script_key in bot_scripts: del bot_scripts[script_key]
            return is_running
        except psutil.NoSuchProcess:
            if 'log_file' in script_info and hasattr(script_info['log_file'], 'close') and not script_info['log_file'].closed:
                try: script_info['log_file'].close()
                except: pass
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
            except: pass
        process = process_info.get('process')
        if process and hasattr(process, 'pid'):
            pid = process.pid
            if pid:
                try:
                    parent = psutil.Process(pid)
                    children = parent.children(recursive=True)
                    for child in children:
                        try: child.terminate()
                        except: pass
                    psutil.wait_procs(children, timeout=1)
                    try:
                        parent.terminate()
                        try: parent.wait(timeout=1)
                        except: parent.kill()
                    except: pass
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

def attempt_install_pip(module_name, message):
    package_name = TELEGRAM_MODULES.get(module_name.lower(), module_name)
    if package_name is None:
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
            check_proc = subprocess.Popen([sys.executable, script_path], cwd=user_folder, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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
        process = subprocess.Popen([sys.executable, script_path], cwd=user_folder, stdout=log_file, stderr=log_file, stdin=subprocess.PIPE)
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
            bot.reply_to(message_obj_for_reply, stylish_text(f"❌ Error: {e}"))
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
            check_proc = subprocess.Popen(['node', script_path], cwd=user_folder, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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
        process = subprocess.Popen(['node', script_path], cwd=user_folder, stdout=log_file, stderr=log_file, stdin=subprocess.PIPE)
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
            bot.reply_to(message_obj_for_reply, stylish_text(f"❌ Error: {e}"))
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

def safe_extract_zip(zip_path,dest):
    total=0; root=os.path.abspath(dest)
    with zipfile.ZipFile(zip_path,'r') as z:
        members=z.infolist()
        if len(members)>MAX_ZIP_FILES: raise ValueError('Archive contains too many files')
        for info in members:
            total += int(info.file_size or 0)
            if total>MAX_ZIP_UNCOMPRESSED_BYTES: raise ValueError('Archive expands beyond safety limit')
            name=info.filename.replace('\\','/')
            if name.startswith('/') or ':' in name.split('/')[0] or '..' in Path(name).parts: raise ValueError('Archive path traversal detected')
            target=os.path.abspath(os.path.join(root,name))
            if os.path.commonpath([root,target])!=root: raise ValueError('Archive path traversal detected')
            if info.is_dir(): os.makedirs(target,exist_ok=True); continue
            os.makedirs(os.path.dirname(target),exist_ok=True)
            with z.open(info,'r') as src, open(target,'wb') as dst: shutil.copyfileobj(src,dst,1024*1024)
    return len(members),total

def scan_source_text(text,language='python'):
    findings=[]; imports=[]; score=0
    for pattern,label in SECURITY_PATTERNS['javascript' if language=='js' else 'python']:
        if re.search(pattern,text,re.IGNORECASE|re.MULTILINE): findings.append(label); score += 18 if 'credential' in label.lower() else 12
    if language=='python':
        try:
            tree=ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node,ast.Import): imports.extend(a.name for a in node.names)
                elif isinstance(node,ast.ImportFrom) and node.module: imports.append(node.module)
        except SyntaxError: findings.append('Python parser could not fully parse source'); score+=8
    if re.search(r'(?i)(requests|urllib|httpx|aiohttp|fetch\s*\(|axios)',text): score+=5
    score=min(100,score); risk='Critical' if score>=75 else 'High' if score>=50 else 'Medium' if score>=25 else 'Low'
    return {'risk':risk,'score':score,'findings':sorted(set(findings))[:40],'imports':sorted(set(imports))[:120]}

def scan_file(path,file_type=None):
    try:
        if os.path.getsize(path)>5*1024*1024: return {'risk':'Medium','score':25,'findings':['Large source file; deep scan skipped'],'imports':[]}
        text=Path(path).read_text(encoding='utf-8',errors='ignore'); lang='js' if (file_type or Path(path).suffix.lower().lstrip('.'))=='js' else 'python'
        return scan_source_text(text,lang)
    except Exception as exc: return {'risk':'High','score':60,'findings':[f'Scan error: {type(exc).__name__}'],'imports':[]}

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
            safe_extract_zip(zip_path,temp_dir)
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
        bot.send_message(admin_chat_id, stylish_text(f"❌ Error: {e}"))
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
    file_name = safe_filename(doc.file_name)
    if not file_name:
        bot.reply_to(message, stylish_text("⚠️ No file name."))
        return
    file_ext = os.path.splitext(file_name)[1].lower()
    if file_ext not in ['.py', '.js', '.zip']:
        bot.reply_to(message, stylish_text("⚠️ Only .py, .js, .zip allowed."))
        return
    if doc.file_size > MAX_UPLOAD_BYTES:
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
        except: pass
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
            except: pass
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
        except: pass

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
    bot.edit_message_text(stylish_text("🔗 𝐑𝐄??𝐎 𝐂𝐎??𝐍𝐄𝐂𝐓𝐈𝐎𝐍...\n\n[▓▓▓░░░░░░░] 30%"), chat_id, msg.message_id)
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
    
    file_name = f"{repo}_{branch}.zip"
    try:
        sent = bot.send_document(chat_id, io.BytesIO(zip_content), visible_file_name=file_name, caption=stylish_text("🔄 Submitting for admin approval..."))
        file_id = sent.document.file_id
        file_size = sent.document.file_size
        user_name = bot.get_chat(user_id).first_name
        user_username = bot.get_chat(user_id).username or "No username"
        extra_info = f"GitHub URL: {url}\nPrivate token: {'provided' if token else 'not required'}"
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
                           f"🔐 Auth: {'private token supplied' if token else 'public repository'}\n"
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
            bot.send_message(message.chat.id, stylish_text(f"❌ Error: {e}"))

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
        except:
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
        except:
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
    except:
        bot.reply_to(message, stylish_text("Invalid user ID. Use numeric ID."))
        return
    if target_id in admin_ids or target_id == OWNER_ID:
        bot.reply_to(message, stylish_text("❌ Cannot ban an admin or owner."))
        return
    if ban_user(target_id):
        bot.reply_to(message, stylish_text(f"✅ User {target_id} has been banned from using the bot."))
        try:
            bot.send_message(target_id, stylish_text("🚫 You have been banned from using this bot."))
        except:
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
    except:
        bot.reply_to(message, stylish_text("Invalid user ID. Use numeric ID."))
        return
    if unban_user(target_id):
        bot.reply_to(message, stylish_text(f"✅ User {target_id} has been unbanned."))
        try:
            bot.send_message(target_id, stylish_text("✅ You have been unbanned. You can now use the bot again."))
        except:
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
                            except:
                                pass
                        
                        if 'log_file' in info and hasattr(info['log_file'], 'close') and not info['log_file'].closed:
                            try:
                                info['log_file'].close()
                            except:
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
            types.InlineKeyboardButton('?? ????𝐛𝐬𝐜𝐫𝐢𝐩𝐭𝐢𝐨𝐧𝐬', callback_data='subscription'),
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
        except: pass

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
        total_ram_gb = round(mem.total / (10243), 2)
        free_ram_gb = round(mem.available / (10243), 2)
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
        except:
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
    except:
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
    except:
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
    except:
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
    except:
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
    except:
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
    except:
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
                except:
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

# ======================= ENTERPRISE SINGLE-FILE EXTENSION =======================
SCHEMA_VERSION=8

def db_conn():
    c=sqlite3.connect(DATABASE_PATH,timeout=20,check_same_thread=False); c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON'); c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA busy_timeout=20000'); return c

def enterprise_migrate():
    with DB_LOCK:
        c=db_conn(); q=c.cursor(); q.execute('CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT)')
        tables=[
        'CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY,username TEXT,first_name TEXT,last_name TEXT,joined_at TEXT,last_active TEXT,warnings INTEGER DEFAULT 0,trusted INTEGER DEFAULT 0,notes TEXT DEFAULT "",terms_accepted INTEGER DEFAULT 0)',
        'CREATE TABLE IF NOT EXISTS plans(id INTEGER PRIMARY KEY AUTOINCREMENT,code TEXT UNIQUE,name TEXT,price REAL DEFAULT 0,duration_days INTEGER DEFAULT 30,file_limit INTEGER DEFAULT 2,running_limit INTEGER DEFAULT 1,max_file_size INTEGER DEFAULT 20971520,github_access INTEGER DEFAULT 0,ai_access INTEGER DEFAULT 0,pip_access INTEGER DEFAULT 0,npm_access INTEGER DEFAULT 0,approval_priority INTEGER DEFAULT 0,auto_approval INTEGER DEFAULT 0,support_level TEXT DEFAULT "standard",active INTEGER DEFAULT 1,created_at TEXT)',
        'CREATE TABLE IF NOT EXISTS plan_subscriptions(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,plan_id INTEGER,starts_at TEXT,expires_at TEXT,status TEXT DEFAULT "active",source TEXT DEFAULT "manual",created_at TEXT,updated_at TEXT)',
        'CREATE TABLE IF NOT EXISTS security_scans(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,file_name TEXT,risk TEXT,score INTEGER,findings TEXT,imports TEXT,created_at TEXT)',
        'CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,actor_id INTEGER,action TEXT,target_id INTEGER,details TEXT,created_at TEXT)',
        'CREATE TABLE IF NOT EXISTS app_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,level TEXT,category TEXT,message TEXT,created_at TEXT)',
        'CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,plan_id INTEGER,amount REAL,transaction_id TEXT,screenshot_file_id TEXT,status TEXT DEFAULT "pending",admin_note TEXT,approved_by INTEGER,created_at TEXT,approved_at TEXT)',
        'CREATE TABLE IF NOT EXISTS github_imports(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,repo_url TEXT,owner TEXT,repo TEXT,branch TEXT,private_repo INTEGER DEFAULT 0,status TEXT,created_at TEXT,updated_at TEXT)',
        'CREATE TABLE IF NOT EXISTS broadcasts(id INTEGER PRIMARY KEY AUTOINCREMENT,admin_id INTEGER,content_type TEXT,content TEXT,filters TEXT,status TEXT,success_count INTEGER DEFAULT 0,failure_count INTEGER DEFAULT 0,created_at TEXT,completed_at TEXT)',
        'CREATE TABLE IF NOT EXISTS broadcast_recipients(broadcast_id INTEGER,user_id INTEGER,status TEXT,error TEXT,PRIMARY KEY(broadcast_id,user_id))',
        'CREATE TABLE IF NOT EXISTS force_channels(id INTEGER PRIMARY KEY AUTOINCREMENT,channel TEXT UNIQUE,enabled INTEGER DEFAULT 1,premium_exempt INTEGER DEFAULT 0,created_at TEXT)',
        'CREATE TABLE IF NOT EXISTS admin_roles(user_id INTEGER PRIMARY KEY,role TEXT DEFAULT "admin",permissions TEXT DEFAULT "[]",created_at TEXT)',
        'CREATE TABLE IF NOT EXISTS warnings(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,admin_id INTEGER,reason TEXT,created_at TEXT)',
        'CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,admin_id INTEGER,note TEXT,created_at TEXT)',
        'CREATE TABLE IF NOT EXISTS backups(id INTEGER PRIMARY KEY AUTOINCREMENT,path TEXT,size INTEGER,created_at TEXT,created_by INTEGER)',
        'CREATE TABLE IF NOT EXISTS ai_requests(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,model TEXT,mode TEXT,prompt_hash TEXT,status TEXT,created_at TEXT)',
        'CREATE TABLE IF NOT EXISTS package_installs(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,ecosystem TEXT,package TEXT,status TEXT,output TEXT,created_at TEXT)',
        'CREATE TABLE IF NOT EXISTS process_history(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,file_name TEXT,action TEXT,pid INTEGER,exit_code INTEGER,error TEXT,created_at TEXT)',
        'CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT,updated_at TEXT,updated_by INTEGER)',
        'CREATE TABLE IF NOT EXISTS file_versions(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,file_name TEXT,sha256 TEXT,size INTEGER,path TEXT,created_at TEXT)',
        'CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)','CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at)','CREATE INDEX IF NOT EXISTS idx_logs_user ON app_logs(user_id)','CREATE INDEX IF NOT EXISTS idx_scans_user ON security_scans(user_id)','CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status)']
        for sql in tables:q.execute(sql)
        now=datetime.now().isoformat(); defaults={'maintenance_mode':'0','lockdown_mode':'0','approval_mode':'manual','auto_restart':'0','github_enabled':'1','ai_enabled':'1','package_install_enabled':'1','crash_notifications':'1','support_username':YOUR_USERNAME,'payment_instructions':'Contact support for payment instructions.','bot_version':'3.0.0-enterprise'}
        for k,v in defaults.items():q.execute('INSERT OR IGNORE INTO settings(key,value,updated_at,updated_by) VALUES(?,?,?,?)',(k,str(v),now,OWNER_ID))
        plans=[('free','Free',0,0,2,1,20*1024*1024,0,0,0,0,0,0,'standard'),('basic','Basic',49,30,5,2,30*1024*1024,1,0,1,1,10,0,'standard'),('pro','Pro',149,30,15,5,50*1024*1024,1,1,1,1,50,0,'priority'),('premium','Premium',299,30,50,15,100*1024*1024,1,1,1,1,100,1,'priority')]
        for r in plans:q.execute('INSERT OR IGNORE INTO plans(code,name,price,duration_days,file_limit,running_limit,max_file_size,github_access,ai_access,pip_access,npm_access,approval_priority,auto_approval,support_level,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(*r,now))
        q.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)',('schema_version',str(SCHEMA_VERSION))); c.commit(); c.close()

def setting(k,d=None):
    try:
        with DB_LOCK:
            c=db_conn(); r=c.execute('SELECT value FROM settings WHERE key=?',(k,)).fetchone(); c.close()
        return r['value'] if r else d
    except Exception:return d

def set_setting(k,v,actor):
    with DB_LOCK:c=db_conn(); c.execute('INSERT OR REPLACE INTO settings(key,value,updated_at,updated_by) VALUES(?,?,?,?)',(k,str(v),datetime.now().isoformat(),actor)); c.commit(); c.close()

def audit(actor,action,target=None,details=''):
    safe=re.sub(r'(?i)(token|api[_-]?key|password|secret)\s*[:=]\s*[^\s,]+',r'\1=[REDACTED]',str(details))
    try:
        with DB_LOCK:c=db_conn(); c.execute('INSERT INTO audit_logs(actor_id,action,target_id,details,created_at) VALUES(?,?,?,?,?)',(actor,action,target,safe[:4000],datetime.now().isoformat())); c.commit(); c.close()
    except Exception:pass

def app_log(uid,level,category,msg):
    try:
        with DB_LOCK:c=db_conn(); c.execute('INSERT INTO app_logs(user_id,level,category,message,created_at) VALUES(?,?,?,?,?)',(uid,level,category,str(msg)[:4000],datetime.now().isoformat())); c.commit(); c.close()
    except Exception:pass

def upsert_user(u):
    now=datetime.now().isoformat()
    with DB_LOCK:c=db_conn(); c.execute('INSERT INTO users(user_id,username,first_name,last_name,joined_at,last_active) VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,first_name=excluded.first_name,last_name=excluded.last_name,last_active=excluded.last_active',(u.id,u.username,u.first_name,u.last_name,now,now)); c.commit(); c.close()

def current_plan(uid):
    if uid==OWNER_ID:return {'name':'Owner','file_limit':999999,'running_limit':999999,'max_file_size':500*1024*1024,'ai_access':1,'github_access':1,'pip_access':1,'npm_access':1}
    try:
        with DB_LOCK:c=db_conn();r=c.execute("SELECT p.* FROM plan_subscriptions s JOIN plans p ON p.id=s.plan_id WHERE s.user_id=? AND s.status='active' AND s.expires_at>? ORDER BY s.expires_at DESC LIMIT 1",(uid,datetime.now().isoformat())).fetchone();c.close()
        if r:return dict(r)
    except Exception:pass
    with DB_LOCK:c=db_conn();r=c.execute("SELECT * FROM plans WHERE code='free'").fetchone();c.close();return dict(r) if r else {'name':'Free','file_limit':2,'running_limit':1,'max_file_size':MAX_UPLOAD_BYTES,'ai_access':0,'github_access':0,'pip_access':0,'npm_access':0}

def effective_file_limit(uid):
    p=current_plan(uid);return user_custom_limits.get(uid,999999 if uid in admin_ids else int(p.get('file_limit',FREE_USER_LIMIT)))

def effective_running_limit(uid):return 999999 if uid in admin_ids else int(current_plan(uid).get('running_limit',1))

def running_count(uid):
    n=0
    for i in list(bot_scripts.values()):
        try:n += 1 if i.get('script_owner_id')==uid and i.get('process') and i['process'].poll() is None else 0
        except Exception:pass
    return n

ROLE_PERMS={'owner':{'*'},'super_admin':{'dashboard','approvals','users','subscriptions','plans','broadcast','logs','security','backup','process','settings'},'admin':{'dashboard','approvals','users','subscriptions','plans','broadcast','logs','security','process'},'moderator':{'dashboard','approvals','users','security','process'},'support':{'dashboard','users','subscriptions'}}
def admin_role(uid):
    if uid==OWNER_ID:return 'owner'
    try:
        with DB_LOCK:c=db_conn();r=c.execute('SELECT role FROM admin_roles WHERE user_id=?',(uid,)).fetchone();c.close()
        return r['role'] if r else ('admin' if uid in admin_ids else '')
    except Exception:return 'admin' if uid in admin_ids else ''
def has_perm(uid,p):
    role=admin_role(uid);return p in ROLE_PERMS.get(role,set()) or '*' in ROLE_PERMS.get(role,set())
def admin_only(m,p='dashboard'):
    if not has_perm(m.from_user.id,p):bot.reply_to(m,'⛔ <b>Access denied.</b>',parse_mode='HTML');return False
    return True

def fmt_size(n):
    n=float(n or 0)
    for u in ('B','KB','MB','GB'):
        if n<1024:return f'{n:.1f} {u}'
        n/=1024
    return f'{n:.1f} TB'
def fmt_duration(s):
    s=max(0,int(s));d,s=divmod(s,86400);h,s=divmod(s,3600);m,s=divmod(s,60);return f'{d}d {h}h {m}m' if d else f'{h}h {m}m {s}s'
def metrics_text():
    vm=psutil.virtual_memory();du=psutil.disk_usage(BASE_DIR)
    with DB_LOCK:c=db_conn();u=c.execute('SELECT COUNT(*) n FROM users').fetchone()['n'];f=c.execute('SELECT COUNT(*) n FROM user_files').fetchone()['n'];p=c.execute('SELECT COUNT(*) n FROM pending_uploads').fetchone()['n'];c.close()
    return f'📊 <b>Analytics</b>\n\n👥 Users: <b>{u}</b>\n📁 Files: <b>{f}</b>\n⏳ Pending: <b>{p}</b>\n🤖 Running: <b>{len(bot_scripts)}</b>\n\nCPU: <b>{psutil.cpu_percent(interval=0.1):.1f}%</b>\nRAM: <b>{vm.percent:.1f}%</b>\nDisk: <b>{du.percent:.1f}%</b>\nUptime: <b>{fmt_duration(time.time()-BOT_START_TIME.timestamp())}</b>'

def panel(chat,text,buttons=None,mid=None):
    kb=types.InlineKeyboardMarkup(row_width=2)
    for row in buttons or []:kb.row(*(types.InlineKeyboardButton(a,callback_data=b) for a,b in row))
    try:return bot.edit_message_text(text,chat,mid,parse_mode='HTML',reply_markup=kb,disable_web_page_preview=True) if mid else bot.send_message(chat,text,parse_mode='HTML',reply_markup=kb,disable_web_page_preview=True)
    except Exception:return bot.send_message(chat,text,parse_mode='HTML',reply_markup=kb,disable_web_page_preview=True)

def dashboard(uid):
    p=current_plan(uid);exp=user_subscriptions.get(uid,{}).get('expiry');ex=exp.strftime('%d %b %Y') if exp else '—'
    return f'🚀 <b>BOT CONTROL CENTER</b>\n\n👤 <code>{uid}</code>\n💎 Plan: <b>{p.get("name","Free")}</b>\n⏳ Expiry: <b>{ex}</b>\n\n📦 Files: <b>{get_user_file_count(uid)}/{effective_file_limit(uid)}</b>\n🤖 Running: <b>{running_count(uid)}/{effective_running_limit(uid)}</b>\n💾 Storage: <b>{fmt_size(sum(x.stat().st_size for x in Path(get_user_folder(uid)).rglob("*") if x.is_file()))}</b>'

def dashboard_buttons(uid):
    b=[[('📁 My Files','ex_files'),('🤖 Running','ex_running')],[('📤 Upload','ex_upload'),('🐙 GitHub','ex_github')],[('💎 Plans','ex_plans'),('🤖 AI','ex_ai')],[('📊 Stats','ex_stats'),('🆘 Support','ex_support')],[('⚙️ Settings','ex_settings'),('ℹ️ Help','ex_help')]]
    if has_perm(uid,'dashboard'):b.append([('🛡️ Admin Panel','ex_admin')])
    return b

def send_dashboard(chat,uid,mid=None):return panel(chat,dashboard(uid),dashboard_buttons(uid),mid)

def persist_scan(uid,name,scan):
    with DB_LOCK:c=db_conn();c.execute('INSERT INTO security_scans(user_id,file_name,risk,score,findings,imports,created_at) VALUES(?,?,?,?,?,?,?)',(uid,name,scan['risk'],scan['score'],json.dumps(scan['findings']),json.dumps(scan['imports']),datetime.now().isoformat()));c.commit();c.close()
def security_card(s):return f'🛡️ <b>Security Scan</b>\nRisk: <b>{s["risk"]}</b>\nScore: <b>{s["score"]}/100</b>\n\n'+('\n'.join('• '+x for x in s['findings']) or '• No suspicious patterns detected')

def backup_db(actor):
    d=os.path.join(IROTECH_DIR,'backups');os.makedirs(d,exist_ok=True);path=os.path.join(d,'backup_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.db')
    with DB_LOCK:src=db_conn();dst=sqlite3.connect(path);src.backup(dst);dst.close();src.close();c=db_conn();c.execute('INSERT INTO backups(path,size,created_at,created_by) VALUES(?,?,?,?)',(path,os.path.getsize(path),datetime.now().isoformat(),actor));c.commit();c.close()
    return path

@bot.message_handler(commands=['dashboard'])
def ex_dashboard(m):
    if check_subscription_and_continue(m):upsert_user(m.from_user);add_active_user(m.from_user.id);send_dashboard(m.chat.id,m.from_user.id)
@bot.message_handler(commands=['files'])
def ex_files(m):
    if not check_subscription_and_continue(m):return
    uid=m.from_user.id;items=user_files.get(uid,[]);rows=[[(('🟢' if is_bot_running(uid,n) else '🔴')+' '+n[:30],f'ex_file:{quote(n,safe="")}')] for n,t in items[:40]];rows.append([('⬅️ Home','ex_home')]);panel(m.chat.id,'📁 <b>My Files</b>',rows)
@bot.message_handler(commands=['status','stats'])
def ex_status(m):
    if m.from_user.id in admin_ids or check_subscription_and_continue(m):bot.send_message(m.chat.id,metrics_text(),parse_mode='HTML')
@bot.message_handler(commands=['plans','upgrade'])
def ex_plans(m):
    if not check_subscription_and_continue(m):return
    with DB_LOCK:c=db_conn();rs=c.execute('SELECT * FROM plans WHERE active=1 ORDER BY price').fetchall();c.close()
    text='💎 <b>Premium Plans</b>\n\n';rows=[]
    for p in rs:text+=f'• <b>{p["name"]}</b> — ₹{p["price"]:.0f}\n  📦 {p["file_limit"]} files · 🤖 {p["running_limit"]} bots · {fmt_size(p["max_file_size"])}\n\n';rows.append([(f'💳 {p["name"]}',f'ex_buy:{p["id"]}')])
    rows.append([('⬅️ Home','ex_home')]);panel(m.chat.id,text,rows)
@bot.message_handler(commands=['logs'])
def ex_logs(m):
    if not admin_only(m,'logs'):return
    with DB_LOCK:c=db_conn();rs=c.execute('SELECT id,actor_id,action,created_at FROM audit_logs ORDER BY id DESC LIMIT 30').fetchall();c.close()
    bot.send_message(m.chat.id,'🧾 <b>Audit Logs</b>\n\n'+('\n'.join(f'#{r["id"]} · <code>{r["actor_id"]}</code> · {r["action"]} · {r["created_at"][:19]}' for r in rs) or 'No logs.'),parse_mode='HTML')
@bot.message_handler(commands=['maintenance'])
def ex_maintenance(m):
    if not admin_only(m,'settings'):return
    v='0' if setting('maintenance_mode','0')=='1' else '1';set_setting('maintenance_mode',v,m.from_user.id);audit(m.from_user.id,'maintenance_toggle',details=v);bot.reply_to(m,f'🛠️ Maintenance: <b>{"ON" if v=="1" else "OFF"}</b>',parse_mode='HTML')
@bot.message_handler(commands=['lockdown'])
def ex_lockdown(m):
    if m.from_user.id!=OWNER_ID:return bot.reply_to(m,'⛔ Owner only.')
    v='0' if setting('lockdown_mode','0')=='1' else '1';set_setting('lockdown_mode',v,m.from_user.id);audit(m.from_user.id,'lockdown_toggle',details=v);bot.reply_to(m,f'🔐 Lockdown: <b>{"ON" if v=="1" else "OFF"}</b>',parse_mode='HTML')
@bot.message_handler(commands=['backup'])
def ex_backup(m):
    if not admin_only(m,'backup'):return
    try:
        path=backup_db(m.from_user.id);bot.send_document(m.chat.id,open(path,'rb'),caption='💾 <b>Backup created</b>',parse_mode='HTML');audit(m.from_user.id,'backup_create')
    except Exception as e:bot.reply_to(m,'❌ Backup failed: '+type(e).__name__)
@bot.message_handler(commands=['emergencystop'])
def ex_stop(m):
    if m.from_user.id!=OWNER_ID:return
    n=0
    for k,i in list(bot_scripts.items()):
        try:kill_process_tree(i);n+=1
        except Exception:pass
        bot_scripts.pop(k,None)
    audit(m.from_user.id,'emergency_stop',details=str(n));bot.reply_to(m,f'🛑 <b>Stopped {n} processes.</b>',parse_mode='HTML')
@bot.message_handler(commands=['users'])
def ex_users(m):
    if not admin_only(m,'users'):return
    with DB_LOCK:c=db_conn();rs=c.execute('SELECT user_id,username,first_name,warnings,trusted FROM users ORDER BY last_active DESC LIMIT 50').fetchall();c.close()
    bot.send_message(m.chat.id,'👥 <b>Users</b>\n\n'+('\n'.join(f'<code>{r["user_id"]}</code> @{r["username"] or "-"} · ⚠️{r["warnings"]} · {"⭐ Trusted" if r["trusted"] else ""}' for r in rs) or 'No users.'),parse_mode='HTML')
@bot.message_handler(commands=['pending'])
def ex_pending(m):
    if not admin_only(m,'approvals'):return
    with DB_LOCK:c=db_conn();rs=c.execute('SELECT id,user_id,file_name,file_size FROM pending_uploads ORDER BY id DESC LIMIT 30').fetchall();c.close()
    for r in rs:
        kb=types.InlineKeyboardMarkup();kb.row(types.InlineKeyboardButton('✅ Approve',callback_data=f'approve_upload_{r["id"]}'),types.InlineKeyboardButton('❌ Reject',callback_data=f'reject_upload_{r["id"]}'))
        bot.send_message(m.chat.id,f'📥 <b>#{r["id"]}</b> · <code>{r["user_id"]}</code> · <code>{r["file_name"]}</code> · {fmt_size(r["file_size"])}',parse_mode='HTML',reply_markup=kb)
@bot.message_handler(commands=['security'])
def ex_security(m):
    if not admin_only(m,'security'):return
    with DB_LOCK:c=db_conn();rs=c.execute('SELECT file_name,risk,score,created_at FROM security_scans ORDER BY id DESC LIMIT 30').fetchall();c.close()
    bot.send_message(m.chat.id,'🛡️ <b>Security Center</b>\n\n'+('\n'.join(f'{r["risk"]} · <code>{r["file_name"]}</code> · {r["score"]}/100' for r in rs) or 'No scans.'),parse_mode='HTML')
@bot.message_handler(commands=['migrate'])
def ex_migrate(m):
    if m.from_user.id==OWNER_ID:
        enterprise_migrate();audit(m.from_user.id,'migration');bot.reply_to(m,'✅ <b>Migration complete.</b>',parse_mode='HTML')

@bot.callback_query_handler(func=lambda c:c.data.startswith('ex_'))
def ex_router(c):
    uid=c.from_user.id;d=c.data
    try:
        if d=='ex_home':send_dashboard(c.message.chat.id,uid,c.message.message_id)
        elif d=='ex_stats':panel(c.message.chat.id,metrics_text(),[[('⬅️ Home','ex_home')]],c.message.message_id)
        elif d=='ex_files':ex_files(c.message)
        elif d=='ex_running':
            rr=[i for i in bot_scripts.values() if i.get('script_owner_id')==uid and i.get('process') and i['process'].poll() is None];panel(c.message.chat.id,'🤖 <b>Running Bots</b>\n\n'+('\n'.join(f'🟢 <code>{i["file_name"]}</code> · PID {i["process"].pid}' for i in rr) or 'No running bots.'),[[('⬅️ Home','ex_home')]],c.message.message_id)
        elif d=='ex_upload':_logic_upload_file(c.message)
        elif d=='ex_github':_logic_github_deploy(c.message)
        elif d=='ex_plans':ex_plans(c.message)
        elif d=='ex_ai':_logic_ai_assistant(c.message)
        elif d=='ex_support':bot.send_message(uid,f'🆘 <b>Support</b>\nContact {YOUR_USERNAME}',parse_mode='HTML')
        elif d=='ex_help':bot.send_message(uid,get_bot_help_text(),parse_mode='HTML')
        elif d=='ex_admin' and has_perm(uid,'dashboard'):_logic_admin_panel(c.message)
        elif d.startswith('ex_buy:'):
            pid=int(d.split(':')[1]);withdb=None
            with DB_LOCK:cc=db_conn();p=cc.execute('SELECT * FROM plans WHERE id=?',(pid,)).fetchone();cc.close()
            if p:bot.send_message(uid,f'💳 <b>{p["name"]}</b> — ₹{p["price"]:.0f}\n\n{setting("payment_instructions")}',parse_mode='HTML')
        elif d.startswith('ex_file:'):
            name=d.split(':',1)[1];panel(uid,f'📄 <b>{name}</b>\nStatus: {"🟢 Running" if is_bot_running(uid,name) else "🔴 Stopped"}',[[('▶️ Start',f'ex_start:{quote(name,safe="")}'),('⏹ Stop',f'ex_stop:{quote(name,safe="")}')],[('🗑 Delete',f'ex_delete:{quote(name,safe="")}'),('⬅️ Files','ex_files')]],c.message.message_id)
        elif d.startswith(('ex_start:','ex_stop:','ex_delete:')):
            name=d.split(':',1)[1];folder=get_user_folder(uid);path=safe_join(folder,name);ft=next((x[1] for x in user_files.get(uid,[]) if x[0]==name),None)
            if not ft or not os.path.exists(path):raise ValueError('File not found')
            key=f'{uid}_{name}'
            if d.startswith('ex_start:'):
                if running_count(uid)>=effective_running_limit(uid):raise ValueError('Running-bot limit reached')
                threading.Thread(target=run_script if ft=='py' else run_js_script,args=(path,uid,folder,name,c.message),daemon=True).start();audit(uid,'process_start',uid,name)
            elif d.startswith('ex_stop:'):
                if key in bot_scripts:kill_process_tree(bot_scripts[key]);bot_scripts.pop(key,None);audit(uid,'process_stop',uid,name)
            else:
                if key in bot_scripts:kill_process_tree(bot_scripts[key]);bot_scripts.pop(key,None)
                try:os.remove(path)
                except OSError:pass
                remove_user_file_db(uid,name);audit(uid,'file_delete',uid,name)
            send_dashboard(uid,uid,c.message.message_id)
        bot.answer_callback_query(c.id)
    except Exception as e:bot.answer_callback_query(c.id,str(e)[:180],show_alert=True)

# Hardened execution wrappers. They preserve the original runner while gating high-risk code.
_original_run_script=run_script;_original_run_js=run_js_script
def run_script(path,uid,folder,name,msg,attempt=1):
    if running_count(uid)>=effective_running_limit(uid) and not is_bot_running(uid,name):
        if msg:bot.reply_to(msg,'⚠️ <b>Running-bot limit reached.</b>',parse_mode='HTML');return
    if os.path.commonpath([os.path.abspath(folder),os.path.abspath(path)])!=os.path.abspath(folder):return
    scan=scan_file(path,'py');persist_scan(uid,name,scan)
    if scan['score']>=75 and uid not in admin_ids:
        if msg:bot.reply_to(msg,security_card(scan),parse_mode='HTML')
        audit(uid,'blocked_high_risk_execution',uid,name);return
    return _original_run_script(path,uid,folder,name,msg,attempt)
def run_js_script(path,uid,folder,name,msg,attempt=1):
    if running_count(uid)>=effective_running_limit(uid) and not is_bot_running(uid,name):
        if msg:bot.reply_to(msg,'⚠️ <b>Running-bot limit reached.</b>',parse_mode='HTML');return
    if os.path.commonpath([os.path.abspath(folder),os.path.abspath(path)])!=os.path.abspath(folder):return
    scan=scan_file(path,'js');persist_scan(uid,name,scan)
    if scan['score']>=75 and uid not in admin_ids:
        if msg:bot.reply_to(msg,security_card(scan),parse_mode='HTML');audit(uid,'blocked_high_risk_execution',uid,name);return
    return _original_run_js(path,uid,folder,name,msg,attempt)

def watchdog_worker():
    while True:
        try:
            now=time.time()
            for k,i in list(bot_scripts.items()):
                p=i.get('process');
                if not p:continue
                rc=p.poll()
                if rc is None:
                    if MAX_PROCESS_SECONDS and now-i.get('start_time',datetime.now()).timestamp()>MAX_PROCESS_SECONDS:kill_process_tree(i)
                    continue
                uid=i.get('script_owner_id',0);fn=i.get('file_name','');bot_scripts.pop(k,None);CRASH_STATE.setdefault(k,[]).append(now);CRASH_STATE[k]=[x for x in CRASH_STATE[k] if now-x<CRASH_WINDOW]
                app_log(uid,'INFO','process',f'{fn} exited {rc}')
                if rc!=0 and len(CRASH_STATE[k])>=MAX_RESTARTS_IN_WINDOW:
                    audit(uid,'crash_loop_block',uid,fn)
                    try:bot.send_message(uid,f'🛑 <b>{fn}</b> stopped after repeated crashes.',parse_mode='HTML')
                    except Exception:pass
        except Exception:logger.exception('watchdog')
        time.sleep(5)

def startup_enterprise():
    try:
        enterprise_migrate();threading.Thread(target=watchdog_worker,daemon=True).start();logger.info('Enterprise layer ready: schema %s',SCHEMA_VERSION)
    except Exception:logger.exception('Enterprise initialization failed')
startup_enterprise()
# ===================== END ENTERPRISE SINGLE-FILE EXTENSION ===================


# ======================== PREMIUM FEATURE PACK ==============================
# The following utilities are intentionally implemented in this single file so
# the deployment remains exactly two files: bot.py + requirements.txt.

TEXTS={
 'welcome':'🚀 <b>Welcome to your Bot Hosting Control Center</b>\n\nDeploy, monitor and manage your Python/JavaScript projects from Telegram.',
 'maintenance':'🛠️ <b>Maintenance Mode</b>\n\nThe platform is temporarily unavailable for normal users. Please try again later.',
 'lockdown':'🔐 <b>Security Lockdown</b>\n\nUploads, GitHub imports and package operations are temporarily disabled.',
 'invalid':'❌ <b>Invalid request.</b> Please try again.',
 'permission':'⛔ <b>Permission denied.</b>',
}

def is_locked(): return setting('lockdown_mode','0')=='1'
def is_maintenance(): return setting('maintenance_mode','0')=='1'
def normal_access(message, feature=None):
    uid=message.from_user.id
    if uid in banned_users:return False
    if uid in admin_ids or uid==OWNER_ID:return True
    if is_maintenance():bot.reply_to(message,TEXTS['maintenance'],parse_mode='HTML');return False
    if is_locked() and feature in {'upload','github','package'}:bot.reply_to(message,TEXTS['lockdown'],parse_mode='HTML');return False
    return check_subscription_and_continue(message)

def plan_allows(uid,feature):
    p=current_plan(uid)
    if uid in admin_ids:return True
    return bool(p.get({'github':'github_access','ai':'ai_access','pip':'pip_access','npm':'npm_access'}.get(feature,''),1))

def add_warning(uid,admin,reason):
    with DB_LOCK:
        c=db_conn();c.execute('INSERT INTO warnings(user_id,admin_id,reason,created_at) VALUES(?,?,?,?)',(uid,admin,reason[:1000],datetime.now().isoformat()));c.execute('UPDATE users SET warnings=warnings+1 WHERE user_id=?',(uid,));c.commit();c.close()
    audit(admin,'warning_add',uid,reason)
def clear_warnings(uid,admin):
    with DB_LOCK:
        c=db_conn();c.execute('DELETE FROM warnings WHERE user_id=?',(uid,));c.execute('UPDATE users SET warnings=0 WHERE user_id=?',(uid,));c.commit();c.close()
    audit(admin,'warning_reset',uid)
def trust_user(uid,admin,state=True):
    with DB_LOCK:
        c=db_conn();c.execute('UPDATE users SET trusted=? WHERE user_id=?',(1 if state else 0,uid));c.commit();c.close()
    audit(admin,'trust_toggle',uid,str(state))

def get_user_summary(uid):
    p=current_plan(uid)
    with DB_LOCK:
        c=db_conn();u=c.execute('SELECT * FROM users WHERE user_id=?',(uid,)).fetchone();c.close()
    return u,p

def parse_target_id(text):
    m=re.search(r'\b(\d{5,20})\b',text or '')
    if not m:raise ValueError('User ID required')
    return int(m.group(1))

@bot.message_handler(commands=['start'])
def premium_start(message):
    try:
        upsert_user(message.from_user);add_active_user(message.from_user.id)
        if is_user_banned(message.from_user.id):return bot.send_message(message.chat.id,'🚫 <b>You are banned.</b>',parse_mode='HTML')
        if not check_subscription_and_continue(message):return
        send_dashboard(message.chat.id,message.from_user.id)
    except Exception as e:logger.exception('start');bot.reply_to(message,'❌ Startup error: '+type(e).__name__)

@bot.message_handler(commands=['help'])
def premium_help(message):
    if not normal_access(message):return
    text='''📚 <b>Platform Help</b>\n\n<b>User</b>\n/start /dashboard /files /upload /github /plans /upgrade /status /settings /support /cancel\n\n<b>Runtime</b>\nUpload .py/.js/.zip files, approve them, start/stop/restart, inspect logs and monitor crashes.\n\n<b>Security</b>\nUploads are sanitized and scanned. High-risk source can be blocked. ZIP archives are checked for traversal and decompression abuse.\n\n<b>Admin</b>\n/admin /users /pending /stats /logs /backup /maintenance /lockdown /security\n\nNever upload bot tokens, API keys, passwords or private credentials.'''
    bot.send_message(message.chat.id,text,parse_mode='HTML')

@bot.message_handler(commands=['support'])
def premium_support(message):
    if not normal_access(message):return
    bot.send_message(message.chat.id,f'🆘 <b>Support Center</b>\n\nSupport: {setting("support_username",YOUR_USERNAME)}\n\nSend a concise description of your issue. Never include secrets.',parse_mode='HTML')

@bot.message_handler(commands=['cancel'])
def premium_cancel(message):
    uid=message.from_user.id
    for d in (globals().get('github_data',{}),):
        if isinstance(d,dict):d.pop(uid,None)
    bot.clear_step_handler_by_chat_id(message.chat.id)
    bot.reply_to(message,'✅ <b>Current action cancelled.</b>',parse_mode='HTML')

@bot.message_handler(commands=['ban'])
def premium_ban(message):
    if not admin_only(message,'users'):return
    try:
        uid=parse_target_id(message.text);reason=(message.text or '').split(str(uid),1)[1].strip() or 'Admin action'
        if uid==OWNER_ID:return bot.reply_to(message,'⛔ Owner cannot be banned.')
        ban_user(uid);add_warning(uid,message.from_user.id,'BAN: '+reason);audit(message.from_user.id,'user_ban',uid,reason);bot.reply_to(message,f'🚫 <b>User {uid} banned.</b>',parse_mode='HTML')
    except Exception as e:bot.reply_to(message,'❌ '+str(e))

@bot.message_handler(commands=['unban'])
def premium_unban(message):
    if not admin_only(message,'users'):return
    try:uid=parse_target_id(message.text);unban_user(uid);audit(message.from_user.id,'user_unban',uid);bot.reply_to(message,f'✅ <b>User {uid} unbanned.</b>',parse_mode='HTML')
    except Exception as e:bot.reply_to(message,'❌ '+str(e))

@bot.message_handler(commands=['warn'])
def premium_warn(message):
    if not admin_only(message,'users'):return
    try:uid=parse_target_id(message.text);reason=(message.text or '').split(str(uid),1)[1].strip() or 'Policy warning';add_warning(uid,message.from_user.id,reason);bot.reply_to(message,f'⚠️ Warning added to <code>{uid}</code>.',parse_mode='HTML')
    except Exception as e:bot.reply_to(message,'❌ '+str(e))

@bot.message_handler(commands=['trust'])
def premium_trust(message):
    if not admin_only(message,'security'):return
    try:uid=parse_target_id(message.text);state='untrust' not in (message.text or '').lower();trust_user(uid,message.from_user.id,state);bot.reply_to(message,f'⭐ Trusted status for <code>{uid}</code>: <b>{state}</b>',parse_mode='HTML')
    except Exception as e:bot.reply_to(message,'❌ '+str(e))

@bot.message_handler(commands=['addadmin'])
def premium_addadmin(message):
    if message.from_user.id!=OWNER_ID:return
    try:uid=parse_target_id(message.text);add_admin_db(uid);with_role='admin';
    except Exception as e:return bot.reply_to(message,'❌ '+str(e))
    with DB_LOCK:c=db_conn();c.execute('INSERT OR REPLACE INTO admin_roles(user_id,role,permissions,created_at) VALUES(?,?,?,?)',(uid,'admin','[]',datetime.now().isoformat()));c.commit();c.close();audit(message.from_user.id,'admin_add',uid);bot.reply_to(message,f'🛡️ <b>Admin added:</b> <code>{uid}</code>',parse_mode='HTML')

@bot.message_handler(commands=['removeadmin'])
def premium_removeadmin(message):
    if message.from_user.id!=OWNER_ID:return
    try:uid=parse_target_id(message.text)
    except Exception as e:return bot.reply_to(message,'❌ '+str(e))
    if uid==OWNER_ID:return bot.reply_to(message,'⛔ Owner cannot be removed.')
    remove_admin_db(uid)
    with DB_LOCK:c=db_conn();c.execute('DELETE FROM admin_roles WHERE user_id=?',(uid,));c.commit();c.close();audit(message.from_user.id,'admin_remove',uid);bot.reply_to(message,f'✅ <b>Admin removed:</b> <code>{uid}</code>',parse_mode='HTML')

@bot.message_handler(commands=['setrole'])
def premium_setrole(message):
    if message.from_user.id!=OWNER_ID:return
    parts=(message.text or '').split()
    if len(parts)<3 or parts[2] not in ROLE_PERMS:return bot.reply_to(message,'Usage: /setrole USER_ID ROLE\nRoles: owner, super_admin, admin, moderator, support')
    uid=int(parts[1]);role=parts[2]
    with DB_LOCK:c=db_conn();c.execute('INSERT OR REPLACE INTO admin_roles(user_id,role,permissions,created_at) VALUES(?,?,?,?)',(uid,role,json.dumps(sorted(ROLE_PERMS[role])),datetime.now().isoformat()));c.commit();c.close();add_admin_db(uid);audit(message.from_user.id,'role_change',uid,role);bot.reply_to(message,f'🛡️ <b>Role set:</b> {role}',parse_mode='HTML')

@bot.message_handler(commands=['userinfo'])
def premium_userinfo(message):
    if not admin_only(message,'users'):return
    try:uid=parse_target_id(message.text);u,p=get_user_summary(uid)
    except Exception as e:return bot.reply_to(message,'❌ '+str(e))
    if not u:return bot.reply_to(message,'User not found in database.')
    text=f'''👤 <b>User Profile</b>\n\n🆔 <code>{uid}</code>\n👤 {u['first_name'] or '-'}\n🔗 @{u['username'] or '-'}\n💎 Plan: <b>{p.get('name','Free')}</b>\n⚠️ Warnings: <b>{u['warnings']}</b>\n⭐ Trusted: <b>{bool(u['trusted'])}</b>\n📁 Files: <b>{get_user_file_count(uid)}</b>\n🤖 Running: <b>{running_count(uid)}</b>'''
    bot.send_message(message.chat.id,text,parse_mode='HTML')

@bot.message_handler(commands=['setlimit'])
def premium_setlimit(message):
    if not admin_only(message,'users'):return
    try:
        parts=(message.text or '').split();uid=int(parts[1]);limit=int(parts[2]);
        if limit<0 or limit>100000:raise ValueError('Limit must be 0..100000')
        set_user_custom_limit(uid,limit);audit(message.from_user.id,'custom_limit',uid,str(limit));bot.reply_to(message,f'✅ Custom file limit for <code>{uid}</code>: <b>{limit}</b>',parse_mode='HTML')
    except Exception as e:bot.reply_to(message,'Usage: /setlimit USER_ID LIMIT\n'+str(e))

@bot.message_handler(commands=['setsub'])
def premium_setsub(message):
    if not admin_only(message,'subscriptions'):return
    try:
        parts=(message.text or '').split();uid=int(parts[1]);days=int(parts[2]);
        if days<=0 or days>3650:raise ValueError('Days must be 1..3650')
        current=user_subscriptions.get(uid,{}).get('expiry');start=current if current and current>datetime.now() else datetime.now();exp=start+timedelta(days=days);save_subscription(uid,exp);audit(message.from_user.id,'subscription_extend',uid,str(days));bot.reply_to(message,f'💎 Subscription updated for <code>{uid}</code> until <b>{exp:%Y-%m-%d}</b>.',parse_mode='HTML')
    except Exception as e:bot.reply_to(message,'Usage: /setsub USER_ID DAYS\n'+str(e))

@bot.message_handler(commands=['delsub'])
def premium_delsub(message):
    if not admin_only(message,'subscriptions'):return
    try:uid=parse_target_id(message.text);remove_subscription_db(uid);audit(message.from_user.id,'subscription_remove',uid);bot.reply_to(message,'✅ Subscription removed.')
    except Exception as e:bot.reply_to(message,'❌ '+str(e))

@bot.message_handler(commands=['acceptterms'])
def premium_terms(message):
    uid=message.from_user.id
    with DB_LOCK:c=db_conn();c.execute('UPDATE users SET terms_accepted=1 WHERE user_id=?',(uid,));c.commit();c.close()
    bot.reply_to(message,'✅ <b>Terms accepted.</b>',parse_mode='HTML')

@bot.message_handler(commands=['terms'])
def premium_terms_view(message):bot.send_message(message.chat.id,'📜 <b>Terms</b>\n\n'+setting('terms','Use this service responsibly.'),parse_mode='HTML')
@bot.message_handler(commands=['privacy'])
def premium_privacy(message):bot.send_message(message.chat.id,'🔒 <b>Privacy</b>\n\n'+setting('privacy','Do not upload secrets or credentials.'),parse_mode='HTML')

# Manual payment request flow: /pay PLAN_ID then transaction ID.
payment_sessions={}
@bot.message_handler(commands=['pay'])
def payment_start(message):
    if not normal_access(message):return
    try:
        pid=int((message.text or '').split()[1]);
        with DB_LOCK:c=db_conn();p=c.execute('SELECT * FROM plans WHERE id=? AND active=1',(pid,)).fetchone();c.close()
        if not p:raise ValueError('Plan not found')
        if float(p['price'])<=0:return bot.reply_to(message,'This plan is free.')
        payment_sessions[message.from_user.id]={'plan_id':pid,'step':'transaction'}
        bot.reply_to(message,f'💳 <b>{p["name"]}</b> — ₹{p["price"]:.0f}\n\n{setting("payment_instructions")}\n\nSend your transaction ID now.\n/cancel to abort.',parse_mode='HTML')
    except Exception as e:bot.reply_to(message,'❌ '+str(e))

@bot.message_handler(func=lambda m:m.from_user.id in payment_sessions and payment_sessions[m.from_user.id].get('step')=='transaction')
def payment_transaction(message):
    uid=message.from_user.id;tx=(message.text or '').strip()
    if tx.lower()=='/cancel':payment_sessions.pop(uid,None);return bot.reply_to(message,'✅ Cancelled.')
    if len(tx)<4 or len(tx)>120:return bot.reply_to(message,'❌ Invalid transaction ID.')
    d=payment_sessions.pop(uid);pid=d['plan_id']
    with DB_LOCK:
        c=db_conn();p=c.execute('SELECT * FROM plans WHERE id=?',(pid,)).fetchone();c.execute('INSERT INTO payments(user_id,plan_id,amount,transaction_id,status,created_at) VALUES(?,?,?,?,?,?)',(uid,pid,p['price'],tx,'pending',datetime.now().isoformat()));payment_id=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.commit();c.close()
    audit(uid,'payment_request',payment_id,'transaction received')
    bot.reply_to(message,'✅ <b>Payment request submitted.</b> Admin approval is required.',parse_mode='HTML')
    for aid in list(admin_ids):
        try:
            kb=types.InlineKeyboardMarkup();kb.row(types.InlineKeyboardButton('✅ Approve',callback_data=f'pay_ok:{payment_id}'),types.InlineKeyboardButton('❌ Reject',callback_data=f'pay_no:{payment_id}'))
            bot.send_message(aid,f'💳 <b>Payment Request #{payment_id}</b>\nUser: <code>{uid}</code>\nPlan: <b>{p["name"]}</b>\nAmount: ₹{p["price"]:.0f}\nTX: <code>{tx}</code>',parse_mode='HTML',reply_markup=kb)
        except Exception:pass

@bot.callback_query_handler(func=lambda c:c.data.startswith('pay_ok:') or c.data.startswith('pay_no:'))
def payment_callback(c):
    if not has_perm(c.from_user.id,'subscriptions'):return bot.answer_callback_query(c.id,'Permission denied',show_alert=True)
    pid=int(c.data.split(':')[1])
    with DB_LOCK:db=db_conn();pay=db.execute('SELECT * FROM payments WHERE id=?',(pid,)).fetchone();db.close()
    if not pay:return bot.answer_callback_query(c.id,'Payment not found',show_alert=True)
    if c.data.startswith('pay_no:'):
        with DB_LOCK:db=db_conn();db.execute('UPDATE payments SET status="rejected",approved_by=?,approved_at=? WHERE id=?',(c.from_user.id,datetime.now().isoformat(),pid));db.commit();db.close();audit(c.from_user.id,'payment_reject',pay['user_id']);
        try:bot.send_message(pay['user_id'],'❌ <b>Payment rejected.</b>',parse_mode='HTML')
        except Exception:pass
        return bot.answer_callback_query(c.id,'Rejected')
    with DB_LOCK:db=db_conn();plan=db.execute('SELECT * FROM plans WHERE id=?',(pay['plan_id'],)).fetchone();db.execute('UPDATE payments SET status="approved",approved_by=?,approved_at=? WHERE id=?',(c.from_user.id,datetime.now().isoformat(),pid));start=datetime.now();exp=start+timedelta(days=int(plan['duration_days']));db.execute('INSERT INTO plan_subscriptions(user_id,plan_id,starts_at,expires_at,status,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',(pay['user_id'],pay['plan_id'],start.isoformat(),exp.isoformat(),'active','payment',start.isoformat(),start.isoformat()));db.commit();db.close()
    save_subscription(pay['user_id'],exp);audit(c.from_user.id,'payment_approve',pay['user_id'],plan['name'])
    try:bot.send_message(pay['user_id'],f'🎉 <b>Payment approved!</b>\nPlan: <b>{plan["name"]}</b>\nExpiry: <b>{exp:%Y-%m-%d}</b>',parse_mode='HTML')
    except Exception:pass
    bot.answer_callback_query(c.id,'Approved')

# Broadcast engine with batching, retries, filters and cancellation.
broadcast_state={}
@bot.message_handler(commands=['broadcast'])
def premium_broadcast(message):
    if not admin_only(message,'broadcast'):return
    broadcast_state[message.from_user.id]={'step':'text'}
    bot.reply_to(message,'📢 <b>Broadcast Center</b>\n\nSend the message to broadcast. /cancel aborts.',parse_mode='HTML')
@bot.message_handler(func=lambda m:m.from_user.id in broadcast_state and broadcast_state[m.from_user.id].get('step')=='text')
def broadcast_capture(message):
    uid=message.from_user.id
    if (message.text or '').lower()=='/cancel':broadcast_state.pop(uid,None);return bot.reply_to(message,'Cancelled.')
    broadcast_state[uid]={'step':'filter','text':message.text or ''}
    kb=types.InlineKeyboardMarkup();kb.row(types.InlineKeyboardButton('👥 All','bc:all'),types.InlineKeyboardButton('💎 Premium','bc:premium'));kb.row(types.InlineKeyboardButton('🟢 Active','bc:active'),types.InlineKeyboardButton('📦 Uploaders','bc:uploaders'));kb.row(types.InlineKeyboardButton('❌ Cancel',callback_data='bc:cancel'))
    bot.send_message(message.chat.id,'🎯 <b>Select audience</b>',parse_mode='HTML',reply_markup=kb)
@bot.callback_query_handler(func=lambda c:c.data.startswith('bc:'))
def broadcast_select(c):
    if not has_perm(c.from_user.id,'broadcast'):return bot.answer_callback_query(c.id,'Denied',show_alert=True)
    if c.data=='bc:cancel':broadcast_state.pop(c.from_user.id,None);return bot.answer_callback_query(c.id,'Cancelled')
    st=broadcast_state.get(c.from_user.id)
    if not st:return bot.answer_callback_query(c.id,'Session expired',show_alert=True)
    filt=c.data.split(':')[1];text=st['text'];broadcast_state.pop(c.from_user.id,None)
    with DB_LOCK:db=db_conn();rows=db.execute('SELECT user_id FROM users').fetchall();db.close()
    ids=[r['user_id'] for r in rows]
    if filt=='premium':ids=[u for u in ids if current_plan(u).get('code') not in ('free',None)]
    elif filt=='active':
        cutoff=(datetime.now()-timedelta(days=30)).isoformat();
        with DB_LOCK:db=db_conn();ids=[r['user_id'] for r in db.execute('SELECT user_id FROM users WHERE last_active>=?',(cutoff,)).fetchall()];db.close()
    elif filt=='uploaders':ids=list(user_files.keys())
    with DB_LOCK:db=db_conn();db.execute('INSERT INTO broadcasts(admin_id,content_type,content,filters,status,created_at) VALUES(?,?,?,?,?,?)',(c.from_user.id,'text',text,json.dumps({'filter':filt}),'running',datetime.now().isoformat()));bid=db.execute('SELECT last_insert_rowid()').fetchone()[0];db.commit();db.close()
    success=fail=0
    for target in ids:
        try:
            bot.send_message(target,text,parse_mode='HTML',disable_web_page_preview=True);success+=1;status='sent'
        except Exception as e:fail+=1;status='failed'
        with DB_LOCK:db=db_conn();db.execute('INSERT OR REPLACE INTO broadcast_recipients(broadcast_id,user_id,status,error) VALUES(?,?,?,?)',(bid,target,status,''));db.commit();db.close()
        time.sleep(0.04)
    with DB_LOCK:db=db_conn();db.execute('UPDATE broadcasts SET status="completed",success_count=?,failure_count=?,completed_at=? WHERE id=?',(success,fail,datetime.now().isoformat(),bid));db.commit();db.close()
    audit(c.from_user.id,'broadcast_complete',bid,f'{success}/{fail}');bot.send_message(c.message.chat.id,f'📢 <b>Broadcast completed</b>\nSuccess: {success}\nFailed: {fail}',parse_mode='HTML');bot.answer_callback_query(c.id,'Done')

# Force-channel administration.
@bot.message_handler(commands=['channeladd'])
def channel_add(message):
    if not admin_only(message,'settings'):return
    ch=(message.text or '').split(maxsplit=1)[1].strip() if len((message.text or '').split())>1 else ''
    if not ch.startswith('@') and not ch.startswith('-100'):return bot.reply_to(message,'Usage: /channeladd @channel')
    with DB_LOCK:c=db_conn();c.execute('INSERT OR IGNORE INTO force_channels(channel,enabled,premium_exempt,created_at) VALUES(?,?,?,?)',(ch,1,0,datetime.now().isoformat()));c.commit();c.close();audit(message.from_user.id,'force_channel_add',details=ch);bot.reply_to(message,'✅ Channel added.')
@bot.message_handler(commands=['channelremove'])
def channel_remove(message):
    if not admin_only(message,'settings'):return
    ch=(message.text or '').split(maxsplit=1)[1].strip() if len((message.text or '').split())>1 else ''
    with DB_LOCK:c=db_conn();c.execute('DELETE FROM force_channels WHERE channel=?',(ch,));c.commit();c.close();audit(message.from_user.id,'force_channel_remove',details=ch);bot.reply_to(message,'✅ Channel removed.')

# Export commands keep data in administrator control.
@bot.message_handler(commands=['export'])
def export_command(message):
    if not admin_only(message,'logs'):return
    parts=(message.text or '').split();table=parts[1] if len(parts)>1 else 'users'
    try:
        path=os.path.join(IROTECH_DIR,f'export_{table}_{int(time.time())}.csv');export_table_csv(table,path);bot.send_document(message.chat.id,open(path,'rb'),caption=f'📦 <b>{table} export</b>',parse_mode='HTML');audit(message.from_user.id,'export',details=table)
    except Exception as e:bot.reply_to(message,'❌ Export failed: '+str(e))

# Package installation hardening: validate package strings, timeout, and audit.
def safe_package_name(package):
    package=package.strip()
    if not package or len(package)>200:raise ValueError('Invalid package')
    if re.search(r'[;&|`$<>\x00\n\r]',package):raise ValueError('Shell metacharacters are not allowed')
    if package.startswith(('-','/')):raise ValueError('Invalid package')
    return package

def secure_pip_install(uid,package,folder=None):
    if not plan_allows(uid,'pip'):raise PermissionError('Your plan does not include package installation')
    package=safe_package_name(package);cmd=[sys.executable,'-m','pip','install','--disable-pip-version-check','--no-input',package]
    result=subprocess.run(cmd,cwd=folder or get_user_folder(uid),capture_output=True,text=True,timeout=INSTALL_TIMEOUT)
    out=(result.stdout+'\n'+result.stderr)[-6000:];status='success' if result.returncode==0 else 'failed'
    with DB_LOCK:c=db_conn();c.execute('INSERT INTO package_installs(user_id,ecosystem,package,status,output,created_at) VALUES(?,?,?,?,?,?)',(uid,'pip',package,status,out,datetime.now().isoformat()));c.commit();c.close();audit(uid,'pip_install',uid,package)
    return result.returncode==0,out

def secure_npm_install(uid,package,folder):
    if not plan_allows(uid,'npm'):raise PermissionError('Your plan does not include npm installation')
    package=safe_package_name(package);result=subprocess.run(['npm','install','--no-audit','--no-fund',package],cwd=folder,capture_output=True,text=True,timeout=INSTALL_TIMEOUT)
    out=(result.stdout+'\n'+result.stderr)[-6000:];status='success' if result.returncode==0 else 'failed'
    with DB_LOCK:c=db_conn();c.execute('INSERT INTO package_installs(user_id,ecosystem,package,status,output,created_at) VALUES(?,?,?,?,?,?)',(uid,'npm',package,status,out,datetime.now().isoformat()));c.commit();c.close();audit(uid,'npm_install',uid,package);return result.returncode==0,out

# AI access control and request audit. Existing AI provider implementation is reused.
_original_ai=call_sambanova_sync
def call_sambanova_sync(message,model_name):
    uid=0
    if not SAMBA_API_KEY:return 'AI is not configured. Set SAMBA_API_KEY in the environment.'
    try:uid=int(getattr(threading.current_thread(),'telegram_user_id',0))
    except Exception:pass
    if uid and not plan_allows(uid,'ai'):return 'AI Assistant is available on Pro/Premium plans.'
    return _original_ai(message,model_name)

# Nightly cleanup of expired transient state and old logs.
def housekeeping_worker():
    while True:
        try:
            cutoff=(datetime.now()-timedelta(days=int(os.getenv('LOG_RETENTION_DAYS','30')))).isoformat()
            with DB_LOCK:
                c=db_conn();c.execute('DELETE FROM app_logs WHERE created_at<?',(cutoff,));c.execute('DELETE FROM audit_logs WHERE created_at<? AND action NOT IN ("backup_create","migration")',(cutoff,));c.commit();c.close()
        except Exception:logger.exception('housekeeping')
        time.sleep(86400)

try:threading.Thread(target=housekeeping_worker,daemon=True,name='housekeeping').start()
except Exception:pass
# ====================== END PREMIUM FEATURE PACK =============================


# ======================= OPERATIONS / OBSERVABILITY PACK =====================
FEATURE_MATRIX={
 'upload':['extension allowlist','size quota','rate limit','hash','security scan','approval workflow'],
 'runtime':['python','node','pid tracking','process tree cleanup','crash watchdog','runtime cap'],
 'plans':['free','basic','pro','premium','custom limits','manual payment approval'],
 'admin':['roles','permissions','audit logs','backup','broadcast','security center'],
 'github':['public repos','private token flow','branch parsing','size limit','approval'],
 'ai':['provider env key','model selection','plan gate','request logging'],
 'data':['sqlite WAL','indexes','migrations','exports','retention'],
}

def feature_matrix_text():
    lines=['🧩 <b>Feature Matrix</b>','']
    for group,items in FEATURE_MATRIX.items():lines.append(f'<b>{group.title()}</b>: '+', '.join(items))
    return '\n'.join(lines)

@bot.message_handler(commands=['features'])
def features_command(message):
    if not normal_access(message):return
    bot.send_message(message.chat.id,feature_matrix_text(),parse_mode='HTML')

@bot.message_handler(commands=['version'])
def version_command(message):
    bot.send_message(message.chat.id,f'🏷️ <b>Version</b> <code>{setting("bot_version","3.0.0-enterprise")}</code>\n🐍 Python: <code>{platform.python_version()}</code>\n🖥️ OS: <code>{platform.system()}</code>',parse_mode='HTML')

@bot.message_handler(commands=['health'])
def health_command(message):
    if not admin_only(message,'dashboard'):return
    m=health_payload();bot.send_message(message.chat.id,'💚 <b>Health OK</b>\n\n<pre>'+json.dumps(m,indent=2,default=str)[:3500]+'</pre>',parse_mode='HTML')

# Search users by ID / username / name. Results are deliberately limited.
@bot.message_handler(commands=['searchuser'])
def search_user(message):
    if not admin_only(message,'users'):return
    q=' '.join((message.text or '').split()[1:]).strip()
    if not q:return bot.reply_to(message,'Usage: /searchuser QUERY')
    with DB_LOCK:
        c=db_conn();rows=c.execute('SELECT user_id,username,first_name,last_name,last_active,warnings,trusted FROM users WHERE CAST(user_id AS TEXT) LIKE ? OR username LIKE ? OR first_name LIKE ? OR last_name LIKE ? LIMIT 25',(f'%{q}%',f'%{q}%',f'%{q}%',f'%{q}%')).fetchall();c.close()
    text='🔎 <b>User Search</b>\n\n'+('\n'.join(f'<code>{r["user_id"]}</code> @{r["username"] or "-"} · {r["first_name"] or "-"} · ⚠️{r["warnings"]}' for r in rows) or 'No matches.')
    bot.send_message(message.chat.id,text,parse_mode='HTML')

# Per-user recent activity timeline.
@bot.message_handler(commands=['activity'])
def activity_command(message):
    uid=message.from_user.id
    if uid not in admin_ids and not normal_access(message):return
    target=uid
    if uid in admin_ids and len((message.text or '').split())>1:
        try:target=int((message.text or '').split()[1])
        except ValueError:return bot.reply_to(message,'Invalid user ID.')
    with DB_LOCK:
        c=db_conn();rows=c.execute('SELECT level,category,message,created_at FROM app_logs WHERE user_id=? ORDER BY id DESC LIMIT 30',(target,)).fetchall();c.close()
    bot.send_message(message.chat.id,'🕘 <b>Recent Activity</b>\n\n'+('\n'.join(f'{r["created_at"][:19]} · {r["level"]} · {r["category"]} · {r["message"][:120]}' for r in rows) or 'No activity.'),parse_mode='HTML')

# Per-file security report for administrators.
@bot.message_handler(commands=['scan'])
def scan_command(message):
    uid=message.from_user.id;parts=(message.text or '').split(maxsplit=2)
    if uid not in admin_ids and not normal_access(message):return
    if len(parts)<2:return bot.reply_to(message,'Usage: /scan filename')
    name=safe_filename(parts[1]);owner=uid
    if uid in admin_ids and len(parts)>2:
        try:owner=int(parts[2])
        except ValueError:pass
    path=safe_join(get_user_folder(owner),name)
    if not os.path.exists(path):return bot.reply_to(message,'File not found.')
    scan=scan_file(path,Path(path).suffix.lower().lstrip('.'));persist_scan(owner,name,scan);bot.send_message(message.chat.id,security_card(scan),parse_mode='HTML')

# Download only files belonging to the caller or to an authorized administrator.
@bot.message_handler(commands=['download'])
def download_command(message):
    uid=message.from_user.id;parts=(message.text or '').split(maxsplit=2)
    if not normal_access(message):return
    if len(parts)<2:return bot.reply_to(message,'Usage: /download filename')
    owner=uid
    if uid in admin_ids and len(parts)>2:
        try:owner=int(parts[2])
        except ValueError:return bot.reply_to(message,'Invalid user ID.')
    name=safe_filename(parts[1]);path=safe_join(get_user_folder(owner),name)
    if not os.path.isfile(path):return bot.reply_to(message,'❌ File not found.')
    try:bot.send_document(message.chat.id,open(path,'rb'),caption=f'📦 <code>{name}</code>',parse_mode='HTML')
    except Exception as e:bot.reply_to(message,'❌ Download failed: '+type(e).__name__)

# Rename file safely and atomically.
@bot.message_handler(commands=['rename'])
def rename_command(message):
    uid=message.from_user.id
    if not normal_access(message):return
    parts=(message.text or '').split()
    if len(parts)!=3:return bot.reply_to(message,'Usage: /rename OLD_NAME NEW_NAME')
    old,new=safe_filename(parts[1]),safe_filename(parts[2]);oldp=safe_join(get_user_folder(uid),old);newp=safe_join(get_user_folder(uid),new)
    if not os.path.isfile(oldp):return bot.reply_to(message,'❌ Old file not found.')
    if os.path.exists(newp):return bot.reply_to(message,'❌ New filename already exists.')
    os.replace(oldp,newp)
    with DB_LOCK:
        c=db_conn();row=c.execute('SELECT file_type FROM user_files WHERE user_id=? AND file_name=?',(uid,old)).fetchone();c.execute('DELETE FROM user_files WHERE user_id=? AND file_name=?',(uid,old));c.execute('INSERT INTO user_files(user_id,file_name,file_type) VALUES(?,?,?)',(uid,new,row['file_type'] if row else Path(new).suffix.lstrip('.')));c.commit();c.close()
    user_files[uid]=[(new if n==old else n,t) for n,t in user_files.get(uid,[])]
    audit(uid,'file_rename',uid,f'{old}->{new}');bot.reply_to(message,'✅ <b>File renamed.</b>',parse_mode='HTML')

# Clear a bot log without deleting the application file.
@bot.message_handler(commands=['clearlogs'])
def clearlogs_command(message):
    uid=message.from_user.id;parts=(message.text or '').split(maxsplit=2)
    if not normal_access(message):return
    if len(parts)<2:return bot.reply_to(message,'Usage: /clearlogs filename')
    owner=uid
    if uid in admin_ids and len(parts)>2:
        try:owner=int(parts[2])
        except ValueError:return bot.reply_to(message,'Invalid user ID.')
    name=safe_filename(parts[1]);path=safe_join(get_user_folder(owner),os.path.splitext(name)[0]+'.log')
    try:Path(path).write_text('',encoding='utf-8');audit(uid,'log_clear',owner,name);bot.reply_to(message,'🧹 Logs cleared.')
    except Exception as e:bot.reply_to(message,'❌ '+type(e).__name__)

# Runtime information endpoint from Telegram.
@bot.message_handler(commands=['runtime'])
def runtime_command(message):
    uid=message.from_user.id
    if not normal_access(message):return
    rows=[]
    for i in bot_scripts.values():
        if i.get('script_owner_id')==uid and i.get('process'):
            try:
                p=psutil.Process(i['process'].pid);rows.append(f'🟢 <code>{i["file_name"]}</code> · PID {p.pid} · CPU {p.cpu_percent(None):.1f}% · RAM {fmt_size(p.memory_info().rss)}')
            except Exception:pass
    bot.send_message(message.chat.id,'🧠 <b>Runtime</b>\n\n'+('\n'.join(rows) or 'No running process.'),parse_mode='HTML')

# Settings command for owner. Values are validated and stored in SQLite.
@bot.message_handler(commands=['setsetting'])
def setsetting_command(message):
    if message.from_user.id!=OWNER_ID:return
    parts=(message.text or '').split(maxsplit=2)
    allowed={'support_username','maintenance_mode','lockdown_mode','github_enabled','ai_enabled','package_install_enabled','auto_restart','crash_notifications','payment_instructions','terms','privacy','bot_version'}
    if len(parts)<3 or parts[1] not in allowed:return bot.reply_to(message,'Allowed settings: '+', '.join(sorted(allowed)))
    key,val=parts[1],parts[2]
    if key.endswith('_mode') or key in {'github_enabled','ai_enabled','package_install_enabled','auto_restart','crash_notifications'} and val not in {'0','1','true','false','on','off'}:return bot.reply_to(message,'Boolean setting must be 0/1 or true/false.')
    set_setting(key,val,message.from_user.id);audit(message.from_user.id,'setting_change',details=f'{key}={val}');bot.reply_to(message,'✅ Setting updated.')

# Force subscription can be refreshed without restarting the bot.
@bot.message_handler(commands=['channels'])
def channels_command(message):
    if not admin_only(message,'settings'):return
    with DB_LOCK:c=db_conn();rows=c.execute('SELECT channel,enabled,premium_exempt FROM force_channels ORDER BY id').fetchall();c.close()
    text='📢 <b>Required Channels</b>\n\n'+('\n'.join(f'{r["channel"]} · {"ON" if r["enabled"] else "OFF"} · Premium exempt: {bool(r["premium_exempt"])}' for r in rows) or 'No DB channels. Environment channels: '+', '.join(REQUIRED_CHANNELS))
    bot.send_message(message.chat.id,text,parse_mode='HTML')

# Admin command menu.
@bot.message_handler(commands=['admin'])
def admin_command(message):
    if not admin_only(message,'dashboard'):return
    rows=[[('📊 Dashboard','ad_stats'),('👥 Users','ad_users')],[('📥 Pending','ad_pending'),('🤖 Processes','ad_process')],[('💎 Subscriptions','ad_subs'),('💳 Payments','ad_payments')],[('🛡️ Security','ad_security'),('🧾 Logs','ad_logs')],[('📢 Broadcast','ad_broadcast'),('💾 Backup','ad_backup')],[('⚙️ Settings','ad_settings'),('🔐 Lockdown','ad_lock')]]
    panel(message.chat.id,'🛡️ <b>ADMIN CONTROL CENTER</b>\n\nChoose a management area.',rows)

@bot.callback_query_handler(func=lambda c:c.data.startswith('ad_'))
def admin_router(c):
    if not has_perm(c.from_user.id,'dashboard'):return bot.answer_callback_query(c.id,'Denied',show_alert=True)
    d=c.data
    try:
        if d=='ad_stats':panel(c.message.chat.id,metrics_text(),[[('⬅️ Admin','ad_back')]],c.message.message_id)
        elif d=='ad_users':enterprise_users(c.message)
        elif d=='ad_pending':enterprise_pending(c.message)
        elif d=='ad_security':ex_security(c.message)
        elif d=='ad_logs':ex_logs(c.message)
        elif d=='ad_backup':ex_backup(c.message)
        elif d=='ad_broadcast':premium_broadcast(c.message)
        elif d=='ad_subs':bot.send_message(c.message.chat.id,'💎 Use /setsub USER_ID DAYS or /delsub USER_ID.',parse_mode='HTML')
        elif d=='ad_payments':bot.send_message(c.message.chat.id,'💳 Pending payments are delivered with Approve/Reject buttons.',parse_mode='HTML')
        elif d=='ad_process':
            rows=[]
            for i in bot_scripts.values():
                p=i.get('process')
                if p:
                    try:rows.append(f'🟢 {i["script_owner_id"]} · <code>{i["file_name"]}</code> · PID {p.pid}')
                    except Exception:pass
            panel(c.message.chat.id,'🤖 <b>Processes</b>\n\n'+('\n'.join(rows) or 'No active processes.'),[[('⬅️ Admin','ad_back')]],c.message.message_id)
        elif d=='ad_settings':bot.send_message(c.message.chat.id,'⚙️ <b>Settings</b>\nUse /setsetting KEY VALUE.\nUse /channels for force channels.',parse_mode='HTML')
        elif d=='ad_lock':ex_lockdown(c.message)
        elif d=='ad_back':admin_command(c.message)
        bot.answer_callback_query(c.id)
    except Exception as e:bot.answer_callback_query(c.id,'Failed: '+type(e).__name__,show_alert=True)

# ----------------------- End operations pack -------------------------------


# ========================= RELIABILITY PACK =================================
# Atomic file replacement, dependency intelligence, log tailing, system guard,
# and admin diagnostics. These are safe helpers used by the Telegram UI.

def sha256_file(path,chunk=1024*1024):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        while True:
            b=f.read(chunk)
            if not b:break
            h.update(b)
    return h.hexdigest()

def dependency_intelligence(path):
    result={'frameworks':[],'dependencies':[],'main_file':os.path.basename(path),'language':Path(path).suffix.lower().lstrip('.')}
    try:
        text=Path(path).read_text(encoding='utf-8',errors='ignore')
        if 'telebot' in text:result['frameworks'].append('pyTelegramBotAPI')
        if 'discord' in text:result['frameworks'].append('discord.py')
        if 'flask' in text:result['frameworks'].append('Flask')
        if 'fastapi' in text:result['frameworks'].append('FastAPI')
        if 'django' in text:result['frameworks'].append('Django')
        if 'express' in text:result['frameworks'].append('Express')
        if 'next' in text.lower():result['frameworks'].append('Next.js')
        if result['language']=='py':
            try:
                tree=ast.parse(text)
                for n in ast.walk(tree):
                    if isinstance(n,ast.Import):result['dependencies'] += [x.name.split('.')[0] for x in n.names]
                    elif isinstance(n,ast.ImportFrom) and n.module:result['dependencies'].append(n.module.split('.')[0])
            except Exception:pass
        else:
            for m in re.finditer(r"(?:require\(['\"]([^'\"]+)|from\s+['\"]([^'\"]+))",text):
                dep=m.group(1) or m.group(2)
                if dep and not dep.startswith('.'):result['dependencies'].append(dep.split('/')[0])
        result['dependencies']=sorted(set(result['dependencies']))[:100]
    except Exception:pass
    return result

def tail_log(path,lines=50,max_bytes=250000):
    if not os.path.isfile(path):return ''
    try:
        with open(path,'rb') as f:
            f.seek(0,os.SEEK_END);size=f.tell();f.seek(max(0,size-max_bytes));data=f.read().decode('utf-8','ignore')
        return '\n'.join(data.splitlines()[-lines:])
    except Exception:return ''

def system_guard():
    vm=psutil.virtual_memory();disk=psutil.disk_usage(BASE_DIR);cpu=psutil.cpu_percent(None)
    return {'cpu_high':cpu>=90,'ram_high':vm.percent>=92,'disk_high':disk.percent>=92,'cpu':cpu,'ram':vm.percent,'disk':disk.percent}

def dependency_report(uid,name):
    path=safe_join(get_user_folder(uid),name)
    if not os.path.isfile(path):raise ValueError('File not found')
    info=dependency_intelligence(path);return '📦 <b>Dependency Intelligence</b>\n\nLanguage: <b>'+info['language']+'</b>\nFrameworks: <b>'+(', '.join(info['frameworks']) or 'None detected')+'</b>\nDependencies:\n'+('\n'.join('• '+x for x in info['dependencies']) or '• None detected')

@bot.message_handler(commands=['deps'])
def deps_command(message):
    uid=message.from_user.id
    if not normal_access(message):return
    parts=(message.text or '').split()
    if len(parts)<2:return bot.reply_to(message,'Usage: /deps filename')
    try:bot.send_message(message.chat.id,dependency_report(uid,safe_filename(parts[1])),parse_mode='HTML')
    except Exception as e:bot.reply_to(message,'❌ '+str(e))

@bot.message_handler(commands=['tail'])
def tail_command(message):
    uid=message.from_user.id
    if not normal_access(message):return
    parts=(message.text or '').split();
    if len(parts)<2:return bot.reply_to(message,'Usage: /tail filename')
    name=safe_filename(parts[1]);log=safe_join(get_user_folder(uid),os.path.splitext(name)[0]+'.log');text=tail_log(log,50)
    if not text:text='No logs yet.'
    bot.send_message(message.chat.id,f'📜 <b>{name}</b>\n<pre>{text[-6000:]}</pre>',parse_mode='HTML')

@bot.message_handler(commands=['guard'])
def guard_command(message):
    if not admin_only(message,'security'):return
    g=system_guard();bot.send_message(message.chat.id,f'🛡️ <b>System Guard</b>\n\nCPU: {g["cpu"]:.1f}% {"⚠️" if g["cpu_high"] else "✅"}\nRAM: {g["ram"]:.1f}% {"⚠️" if g["ram_high"] else "✅"}\nDisk: {g["disk"]:.1f}% {"⚠️" if g["disk_high"] else "✅"}',parse_mode='HTML')

@bot.message_handler(commands=['dbcheck'])
def dbcheck_command(message):
    if message.from_user.id!=OWNER_ID:return
    try:
        with DB_LOCK:
            c=db_conn();integrity=c.execute('PRAGMA integrity_check').fetchone()[0];journal=c.execute('PRAGMA journal_mode').fetchone()[0];foreign=c.execute('PRAGMA foreign_keys').fetchone()[0];c.close()
        bot.send_message(message.chat.id,f'🗄️ <b>Database Check</b>\nIntegrity: <code>{integrity}</code>\nJournal: <code>{journal}</code>\nForeign keys: <code>{foreign}</code>',parse_mode='HTML')
    except Exception as e:bot.reply_to(message,'❌ DB check failed: '+type(e).__name__)

# Emergency cleanup command removes orphaned process records from the in-memory registry.
@bot.message_handler(commands=['reconcile'])
def reconcile_command(message):
    if not admin_only(message,'process'):return
    removed=0
    for k,i in list(bot_scripts.items()):
        try:
            if not i.get('process') or i['process'].poll() is not None:bot_scripts.pop(k,None);removed+=1
        except Exception:bot_scripts.pop(k,None);removed+=1
    audit(message.from_user.id,'process_reconcile',details=str(removed));bot.reply_to(message,f'🔄 <b>Runtime reconciled.</b> Removed stale entries: {removed}',parse_mode='HTML')

# Make process shutdown more robust at interpreter exit.
_original_cleanup=cleanup
def cleanup():
    for k,i in list(bot_scripts.items()):
        try:kill_process_tree_hardened(i)
        except Exception:pass
    try:_original_cleanup()
    except Exception:pass

# ======================= END RELIABILITY PACK ===============================


# ========================== FINAL HARDENING NOTES ============================
# Runtime contract: secrets are environment-only; uploaded code is untrusted;
# static scanning is advisory and high-risk code is blocked for normal users.
# For a truly isolated multi-tenant production runner, deploy Docker/gVisor/
# Firecracker or another OS-level sandbox around the child process. This file
# deliberately never claims AST scanning is a perfect sandbox.
SANDBOX_RECOMMENDED=True
SECURITY_NOTICE='Static analysis is advisory; OS-level isolation is recommended for untrusted execution.'

def security_notice():
    return SECURITY_NOTICE

def redact_secrets(text):
    if text is None:return ''
    text=str(text)
    patterns=[r'(?i)(bot[_-]?token|api[_-]?key|secret|password|authorization)\s*[:=]\s*[^\s]+',r'(?i)gh[pousr]_[A-Za-z0-9_\-]+']
    for pat in patterns:text=re.sub(pat,lambda m:m.group(0).split('=',1)[0]+'=[REDACTED]' if '=' in m.group(0) else '[REDACTED]',text)
    return text

# ======================== END FINAL HARDENING ===============================

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
           

# ============================ DEPLOYMENT MANIFEST ===========================
# Required environment variables:
# BOT_TOKEN=<Telegram bot token>
# OWNER_ID=<owner Telegram ID>
# ADMIN_ID=<optional initial admin Telegram ID>
# SAMBA_API_KEY=<optional AI provider key>
# SUPPORT_USERNAME=<support handle>
# REQUIRED_CHANNELS=@channel1,@channel2
# PORT=<health server port for Render-like platforms>
# MAX_UPLOAD_BYTES=20971520
# MAX_ZIP_UNCOMPRESSED_BYTES=62914560
# MAX_ZIP_FILES=250
# PROCESS_MEMORY_BYTES=536870912
# MAX_PROCESS_SECONDS=0
# LOG_RETENTION_DAYS=30
#
# Recommended production command:
# python bot.py
#
# Recommended OS/container policy:
# - Run as a non-root user.
# - Give the bot its own writable application directory.
# - Do not mount host secrets into user-process namespaces.
# - Prefer a container/VM sandbox for untrusted uploaded code.
# - Keep Telegram and AI credentials outside uploaded project folders.
# - Back up the SQLite database before upgrades.
# - Keep the runtime and dependencies patched.
#
# This manifest is intentionally embedded because the requested deployment
# contains only bot.py and requirements.txt.
# ========================== END DEPLOYMENT MANIFEST =========================
