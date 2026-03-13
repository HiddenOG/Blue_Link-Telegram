#lot.py

from telegram import (
    Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, 
    InlineKeyboardButton, InlineKeyboardMarkup, ForceReply,
    InlineQueryResultArticle, InputTextMessageContent, InlineQueryResultsButton,
    WebAppInfo, KeyboardButton, BotCommand
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, ConversationHandler, CallbackQueryHandler,
    InlineQueryHandler
)

from difflib import get_close_matches
from concurrent.futures import ThreadPoolExecutor
from supabase import Client


import logging
import time
import os
import asyncio
import warnings
warnings.filterwarnings("ignore", category=FutureWarning, module="google.generativeai")
import google.generativeai as genai
import re
import json
import hashlib


import warnings
warnings.filterwarnings("ignore")

# numpy removed — was unused
import threading
import uuid
from functools import wraps

from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
import dashboard
from bot_state import (
    CACHE_DATA, CACHE_LOCK, USER_REGISTRATIONS, PENDING_REGISTRATIONS,
    COLUMN_MAP, get_row_value, _normalize_text, get_user_businesses,
    get_business_count, user_has_business, user_is_premium, user_is_free_tier,
    supabase, refresh_cache_from_supabase, start_cache_refresh_loop,
    CACHE_REFRESH_INTERVAL, get_cached_businesses, row_is_premium,
    is_ad_boosted
)







load_dotenv()
ADMIN_ID = os.getenv("ADMIN_ID") 

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Per-user rate limiting: {user_id: [timestamp, timestamp, ...]}
user_gemini_calls = {}
PER_USER_DAILY_LIMIT = 20

WEB_APP_URL = os.getenv("WEB_APP_URL", "https://your-ngrok-url.ngrok-free.app").rstrip('/')

# Monnify payment config
# Monnify (Deprecated) / Paystack Config
MONNIFY_API_KEY = os.getenv("MONNIFY_API_KEY", "")
MONNIFY_CONTRACT_CODE = os.getenv("MONNIFY_CONTRACT_CODE", "")
PAYSTACK_PUBLIC_KEY = os.getenv("PAYSTACK_PUBLIC_KEY", "")

# Construct URLs correctly
# Ensure WEB_APP_URL starts with https
if WEB_APP_URL and not WEB_APP_URL.startswith("http"):
    WEB_APP_URL = f"https://{WEB_APP_URL}"
    
# Remove trailing slash if present for consistency
WEB_APP_URL = WEB_APP_URL.rstrip('/')

# Helper to append query params
def append_query(url, params):
    sep = '&' if '?' in url else '?'
    return f"{url}{sep}{params}"

# 1. Base Dashboard URL (index.html)
if "ngrok-free.app" in WEB_APP_URL:
    DASHBOARD_URL = append_query(WEB_APP_URL, "ngrok-skip-browser-warning=true")
    REGISTER_URL = append_query(f"{WEB_APP_URL}/register.html", "ngrok-skip-browser-warning=true")
    CATALOG_URL = append_query(f"{WEB_APP_URL}/catalog.html", "ngrok-skip-browser-warning=true")
else:
    DASHBOARD_URL = WEB_APP_URL
    REGISTER_URL = f"{WEB_APP_URL}/register.html"
    CATALOG_URL = f"{WEB_APP_URL}/catalog.html"

# Validation Logging
import logging
logging.info(f"🚀 CONFIG: WEB_APP_URL='{WEB_APP_URL}'")
logging.info(f"🚀 CONFIG: DASHBOARD_URL='{DASHBOARD_URL}'")
logging.info(f"🚀 CONFIG: REGISTER_URL='{REGISTER_URL}'")


GEMINI_MODEL_FOR_SEARCH = "models/gemini-2.5-flash" 
GEMINI_MODEL_FOR_CHAT = "models/gemini-2.5-flash" 
genai.configure(api_key=GEMINI_API_KEY)


# INDEX_LOCK moved to where it's needed
# CACHE_REFRESH_INTERVAL moved to bot_state.py

DIR_ROWS = []
DIR_TEXTS = []
LOCATIONS_RAW = set()
LOCATION_TOKENS = set()
executor = ThreadPoolExecutor(max_workers=5)
UPGRADE_PROOF = 13  

from coin_system import (
    INITIAL_BLUE_COINS,
    FREE_TIER_MAX_BUSINESSES,
    get_user_coins,
    set_user_coins,
    add_coins,
    deduct_coin,
    COIN_PRICES,
    can_request_service,
    record_service_request,
    can_click_business_link,
    record_link_click
)

# --- Logging ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# supabase = create_client(SUPABASE_URL, SUPABASE_KEY) 
# Moved to bot_state.py


# State and Column Mapping moved to bot_state.py


def validate_env_vars():
    """Validate all required environment variables on startup"""
    required = {
        "TELEGRAM_TOKEN": "Telegram bot token",
        "GEMINI_API_KEY": "Google Gemini API key",
        "SUPABASE_URL": "Supabase project URL",
        "SUPABASE_KEY": "Supabase API key",
        "ADMIN_ID": "Admin Telegram ID"
    }
    
    missing = []
    for var, description in required.items():
        if not os.getenv(var):
            missing.append(f"{var} ({description})")
    
    if missing:
        error_msg = "❌ Missing required environment variables:\n" + "\n".join(missing)
        logging.error(error_msg)
        raise ValueError(error_msg)
    
    logging.info("✅ All environment variables validated")

# Call it immediately
validate_env_vars()


# Helper functions moved to bot_state.py






def safe_int_conversion(value, default=None):
    """Safely convert to int, return default if fails"""
    if value is None:
        return default
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        logging.error(f"Invalid int conversion: {value}")
        return default


def sanitize_user_input(text: str) -> str:
    text = text.strip().replace('\n', ' ')
    return text[:1000] # Limit length to prevent extreme entries

def _row_to_doc(row):
    """Updated to use helper function"""
    name = get_row_value(row, 'Business Name')
    services = get_row_value(row, 'Business Services')
    location = get_row_value(row, 'Business Location')
    desc = get_row_value(row, 'Business Description')
    return f"Business Name: {name}. Services: {services}. Location: {location}. Description: {desc}"


def build_directory_index(force=False):
    """Build search index from cache (updated for Supabase column names)"""
    global DIR_ROWS, DIR_TEXTS, LOCATIONS_RAW, LOCATION_TOKENS
    
    # Simple lock for index building
    if not hasattr(build_directory_index, "_lock"):
        build_directory_index._lock = threading.Lock()

    with build_directory_index._lock:

        response = supabase.table('businesses').select('*').execute()
        records = response.data

        DIR_ROWS = records
        DIR_TEXTS = []
        LOCATIONS_RAW = set()
        LOCATION_TOKENS = set()

        for r in records:
            doc = _row_to_doc(r)
            DIR_TEXTS.append(doc)

            # ✅ FIX: Use snake_case column name
            loc = _normalize_text(r.get('business_location', ''))
            if loc:
                LOCATIONS_RAW.add(loc)
                for tok in re.split(r"[^a-z0-9]+", loc):
                    if tok:
                        LOCATION_TOKENS.add(tok)
# FIX: Replace the refresh_cache_from_sheets() function completely

# refresh_cache_from_supabase was here
# start_cache_refresh_loop was here


# --- Gemini-powered search functions ---

async def load_rate_limits_from_db():
    """Load today's per-user Gemini API calls from database on startup"""
    global user_gemini_calls
    
    try:
        today_start = datetime.combine(datetime.now(timezone.utc).date(), datetime.min.time()).replace(tzinfo=timezone.utc)
        
        response = await asyncio.to_thread(
            lambda: supabase.table('gemini_api_calls')
                .select('*')
                .gte('timestamp', today_start.isoformat())
                .execute()
        )
        
        user_gemini_calls.clear()
        
        for record in response.data:
            timestamp_str = record.get('timestamp')
            uid = record.get('user_id')
            if timestamp_str and uid:
                try:
                    timestamp = datetime.fromisoformat(timestamp_str)
                    if timestamp.tzinfo is None:
                        timestamp = timestamp.replace(tzinfo=timezone.utc)
                    uid = int(uid)
                    if uid not in user_gemini_calls:
                        user_gemini_calls[uid] = []
                    user_gemini_calls[uid].append(timestamp)
                except:
                    pass
        
        total = sum(len(v) for v in user_gemini_calls.values())
        logging.info(f"✅ Loaded {total} Gemini API calls for {len(user_gemini_calls)} users")
        
    except Exception as e:
        logging.error(f"❌ Failed to load rate limits: {e}")


async def save_rate_limit_to_db(user_id):
    """Save Gemini API call to database with user_id"""
    try:
        await asyncio.to_thread(
            lambda: supabase.table('gemini_api_calls')
                .insert({
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'user_id': user_id
                })
                .execute()
        )
    except Exception as e:
        logging.error(f"❌ Failed to save rate limit: {e}")


def check_rate_limit(user_id=None):
    """Check if a user can make another Gemini call (20 per user per day, resets at midnight)"""
    if not user_id:
        return True  # Allow if no user_id (shouldn't happen)
    
    now = datetime.now(timezone.utc)
    today_start = datetime.combine(now.date(), datetime.min.time()).replace(tzinfo=timezone.utc)
    
    # Initialize user's call list if needed
    if user_id not in user_gemini_calls:
        user_gemini_calls[user_id] = []
    
    # Remove calls from previous days
    user_gemini_calls[user_id] = [t for t in user_gemini_calls[user_id] if t > today_start]
    usage = len(user_gemini_calls[user_id])
    
    if usage >= PER_USER_DAILY_LIMIT:
        hours_left = (today_start + timedelta(days=1) - now).total_seconds() / 3600
        logging.warning(f"⚠️ User {user_id} hit rate limit: {PER_USER_DAILY_LIMIT}/day. Resets in {hours_left:.1f}h")
        return False
    
    user_gemini_calls[user_id].append(now)
    
    # Save to database
    asyncio.create_task(save_rate_limit_to_db(user_id))
    
    remaining = PER_USER_DAILY_LIMIT - len(user_gemini_calls[user_id])
    logging.info(f"✅ User {user_id} Gemini call {usage + 1}/{PER_USER_DAILY_LIMIT} (remaining: {remaining})")
    return True

async def send_admin_alert(message: str, bot=None):
    """Send alert to admin. Pass the bot instance to avoid creating a new one."""
    try:
        if bot:
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=message,
                parse_mode='Markdown'
            )
        else:
            logging.warning("send_admin_alert called without bot instance — skipping")
    except Exception as e:
        logging.error(f"Failed to send admin alert: {e}")

def log_usage_stats():
    """Log daily Gemini usage for monitoring"""
    total_calls = sum(len(v) for v in user_gemini_calls.values())
    active_users = len([v for v in user_gemini_calls.values() if v])
    logging.info(f"📊 Gemini usage today: {total_calls} calls across {active_users} users (limit: {PER_USER_DAILY_LIMIT}/user/day)")



# Simple response cache (string matching, not vectors)
simple_cache = {}

# Per-user conversation memory {user_id: [{"role": "user"/"model", "parts": [text]}]}
conversation_memory = {}
MAX_MEMORY_TURNS = 10  # 5 exchanges (user + model)

# Long-term user facts (persisted to Supabase)
# {user_id: ["User's name is John", "Lives in Lagos", ...]}
USER_FACTS = {}
MAX_FACTS_PER_USER = 20

REMEMBER_TAG_RE = re.compile(r'\[REMEMBER:\s*(.+?)\]', re.IGNORECASE)

async def load_user_facts_from_db():
    """Load all user facts from Supabase on startup."""
    global USER_FACTS
    try:
        response = await asyncio.to_thread(
            lambda: supabase.table('user_memory').select('*').execute()
        )
        loaded = 0
        for row in response.data:
            uid = row.get('telegram_id')
            facts = row.get('facts', [])
            if uid and facts:
                USER_FACTS[int(uid)] = facts
                loaded += 1
        logging.info(f"🧠 Loaded long-term memory for {loaded} users")
    except Exception as e:
        logging.warning(f"⚠️ Could not load user memory (table may not exist yet): {e}")

async def save_user_fact(user_id, new_fact):
    """Save a new fact for a user to memory + Supabase."""
    if user_id not in USER_FACTS:
        USER_FACTS[user_id] = []
    
    # Avoid duplicates (case-insensitive)
    for existing in USER_FACTS[user_id]:
        if existing.lower().strip() == new_fact.lower().strip():
            return  # Already stored
    
    USER_FACTS[user_id].append(new_fact)
    # Trim to max
    USER_FACTS[user_id] = USER_FACTS[user_id][-MAX_FACTS_PER_USER:]
    
    try:
        await asyncio.to_thread(
            lambda: supabase.table('user_memory').upsert({
                'telegram_id': user_id,
                'facts': USER_FACTS[user_id]
            }, on_conflict='telegram_id').execute()
        )
        logging.info(f"🧠 Saved fact for user {user_id}: {new_fact}")
    except Exception as e:
        logging.error(f"❌ Failed to save user fact: {e}")

async def save_user_fact_clear(user_id):
    """Clear all facts for a user from Supabase."""
    try:
        await asyncio.to_thread(
            lambda: supabase.table('user_memory')
                .delete()
                .eq('telegram_id', user_id)
                .execute()
        )
        logging.info(f"🧠 Cleared all memory for user {user_id} from database")
    except Exception as e:
        logging.error(f"❌ Failed to clear user memory: {e}")

def get_cached_response(query_type: str, query_text: str):
    """Check if exact or very similar query already exists"""
    query_normalized = query_text.strip().lower()
    cache_key = f"{query_type}:{query_normalized}"
    
    # Exact match
    if cache_key in simple_cache:
        cached_entry = simple_cache[cache_key]
        age_seconds = (datetime.now(timezone.utc) - cached_entry['timestamp']).total_seconds()
        if age_seconds < 1800:  # Cache expires after 30mins
            logging.info(f"✅ Cache HIT: {query_type} '{query_text[:30]}...'")
            return cached_entry['response']
    
    return None

def store_response(query_type: str, query_text: str, response):
    """Store response in simple cache"""
    query_normalized = query_text.strip().lower()
    cache_key = f"{query_type}:{query_normalized}"
    simple_cache[cache_key] = {
        'response': response,
        'timestamp': datetime.now(timezone.utc)
    }
    logging.info(f"💾 Cached: {query_type} '{query_text[:30]}...'")

def ask_gemini_search_query(user_query, business_data):
    cached = get_cached_response("search", user_query)
    if cached is not None:
        return cached
    
    # Check global rate limit BEFORE Gemini call (no user context in search)
    if not check_rate_limit(user_id=0):
        logging.error("❌ Rate limit hit - returning empty results")
        return []
    
    model = genai.GenerativeModel(GEMINI_MODEL_FOR_SEARCH)
    business_text_list = [
        f"{_row_to_doc(row)} (Original Index: {i})"
        for i, row in enumerate(business_data)
    ]
    business_context = "\n".join(business_text_list)

    prompt = f"""
You are an expert Nigerian business directory assistant. Your goal is to identify businesses that best match the user's request from the provided list.

# Instructions:
- Analyze the user's query and identify the requested service and location.
- Scan the list of businesses below.
- Return ONLY the "Original Index" of the businesses that are a good match for the user's query.
- Prioritize businesses that clearly offer the service AND are in the specified location.
- If multiple businesses match, return all relevant indices.
- If no matching business is found → tell them its not available then politely encourage them to register their business or try again with '🔍 Find a Service' using clear and short words
# Example:
User Query: I need a plumber in Lagos
Businesses:
Business Name: John Plumbing. Services: Plumbing. Location: Lagos. Description: Reliable plumber. (Original Index: 0)
Business Name: Mary Hair. Services: Hairdressing. Location: Abuja. Description: Hair salon. (Original Index: 1)
Output: 0

User Query: Find an electrician near Ikeja
Businesses:
Business Name: Bolt Electric. Services: Electrician. Location: Ikeja. Description: Fast electrical repairs. (Original Index: 0)
Business Name: Water Works. Services: Plumbing. Location: Ikeja. Description: Pipe repairs. (Original Index: 1)
Output: 0

User Query: Best restaurants in Kano
Businesses:
Business Name: Mama's Kitchen. Services: Restaurant. Location: Kano. Description: Local Nigerian food. (Original Index: 0)
Business Name: Tech Solutions. Services: IT Support. Location: Lagos. Description: Computer repair. (Original Index: 1)
Business Name: Fine Diners. Services: Restaurant. Location: Kano. Description: Fine dining experience. (Original Index: 2)
Output: 0, 2

User Query: Where can I get tailoring?
Businesses:
Business Name: Sew Perfect. Services: Tailoring. Location: Enugu. Description: Custom clothes. (Original Index: 0)
Output: 0

User Query: I need a car wash in Ibadan.
Businesses:
Business Name: Auto Fix. Services: Mechanic. Location: Lagos. Description: Car repairs. (Original Index: 0)
Output: NONE

# Businesses to search through:
{business_context}

# User Query:
{user_query}

Output:
"""
    try:
        response = model.generate_content(prompt)
        result_text = response.text.strip()
        if result_text.lower() == "none":
            results = []
        else:
            indices = []
            for s_idx in result_text.split(','):
                try:
                    indices.append(int(s_idx.strip()))
                except ValueError:
                    logging.warning(f"Gemini returned non-integer index: {s_idx}. Full response: {result_text}")
            
            results = [business_data[i] for i in indices if 0 <= i < len(business_data)]
        
        store_response("search", user_query, results)
        return results
    
    except Exception as e:
        logging.error("Gemini search query error: %s", str(e))
        return []

def smart_directory_search(query: str, top_k: int = 5):
    """Uses cached data instead of live Sheet queries."""
    # Get data from cache (instant, no API call)
    cached_businesses = get_cached_businesses()
    
    if not cached_businesses:
        logging.warning("Cache is empty, using fallback...")
        # Fallback: use old method if cache is empty
        global DIR_ROWS
        if not DIR_ROWS:
            build_directory_index()
        cached_businesses = DIR_ROWS
    
    # Use Gemini to find matches from cached data
    gemini_matches = ask_gemini_search_query(query, cached_businesses)
    results_with_dummy_scores = [(row, 1.0) for row in gemini_matches[:top_k]]
    
    return results_with_dummy_scores

def find_best_service_gemini(user_input: str, available_services: list):
    if not available_services:
        return None

    # Normalize input
    user_input_norm = user_input.strip().lower()
    
    # First, try exact match (case-insensitive)
    for service in available_services:
        if service.strip().lower() == user_input_norm:
            return service
    
    # Second, try fuzzy matching (fallback if Gemini fails)
    close_matches = get_close_matches(user_input_norm, 
                                     [s.lower() for s in available_services], 
                                     n=1, cutoff=0.6)
    
    if close_matches:
        # Find original service name (with proper casing)
        for service in available_services:
            if service.lower() == close_matches[0]:
                return service
    
    cached = get_cached_response("service", user_input)
    if cached is not None:
        return cached
    
    if not check_rate_limit(user_id=0):
        logging.warning("⚠️ Rate limit hit, using fuzzy match fallback")
        return close_matches[0] if close_matches else None

    # Third, try Gemini for semantic matching
    try:
        model = genai.GenerativeModel(GEMINI_MODEL_FOR_SEARCH)
        services_list_str = "\n".join([f"- {s}" for s in available_services])

        prompt = f"""
The user is looking for a service. From the list of available services provided, identify the single best match for the user's input.
Return ONLY the exact text of the matched service. If no good match is found, return "NONE".

# Available Services:
{services_list_str}

# User Input:
{user_input}

Output:
"""
        response = model.generate_content(prompt)
        matched_service = response.text.strip()
        if matched_service.lower() == "none" or matched_service not in available_services:
            result = None
        else:
            result = matched_service
        
        store_response("service", user_input, result)
        return result
        
    except Exception as e:
        logging.error("Gemini service matching error: %s", str(e))
        # If Gemini fails, return the fuzzy match result or None
        return None

def find_best_location_gemini(user_input: str, available_locations: list):
    if not available_locations:
        return None

    # Normalize input
    user_input_norm = user_input.strip().lower()
    
    # First, try exact match (case-insensitive)
    for location in available_locations:
        if location.strip().lower() == user_input_norm:
            return location
    
    # Second, try fuzzy matching (fallback if Gemini fails)
    close_matches = get_close_matches(user_input_norm, 
                                     [loc.lower() for loc in available_locations], 
                                     n=1, cutoff=0.6)
    
    if close_matches:
        # Find original location name (with proper casing)
        for location in available_locations:
            if location.lower() == close_matches[0]:
                return location         

    cached = get_cached_response("location", user_input)
    if cached is not None:
        return cached
    
    # Only use Gemini if needed AND within rate limit
    if not check_rate_limit(user_id=0):
        logging.warning("⚠️ Rate limit hit, using fuzzy match fallback")
        return close_matches[0] if close_matches else None

    # Third, try Gemini for semantic matching
    try:
        model = genai.GenerativeModel(GEMINI_MODEL_FOR_SEARCH)
        locations_list_str = "\n".join([f"- {loc}" for loc in available_locations])

        prompt = f"""
The user has provided a location. From the list of available locations provided, identify the single best match for the user's input.
Return ONLY the exact text of the matched location. If no good match is found, return "NONE".

# Available Locations:
{locations_list_str}

# User Input:
{user_input}

Output:
"""
        response = model.generate_content(prompt)
        matched_location = response.text.strip()
        if matched_location.lower() == "none" or matched_location not in available_locations:
            result = None
        else:
            result = matched_location
        
        store_response("location", user_input, result)
        return result
    
    except Exception as e:
        logging.error("Gemini location matching error: %s", str(e))
        # If Gemini fails, return fuzzy match or None
        return None

def format_matches_reply(matches, customer_id=None):
    """Format search results with Request Service buttons"""
    if not matches:
        return None, None

    sorted_matches = sorted(
        matches,
        key=lambda m: (0 if is_ad_boosted(m[0]) else 1)
    )

    reply = "✅ *Here are some businesses that match your request:*\n\n"
    buttons = []
    
    for row, _ in sorted_matches:
        business_id = row.get('id')
        business_name = get_row_value(row, 'Business Name') or 'Unknown'
        
        reply += f"🏷️ *{business_name}*\n"
        reply += f"📍 {get_row_value(row, 'Business Location') or 'N/A'}\n"
        reply += f"🛠️ {get_row_value(row, 'Business Services') or 'N/A'}\n"
        if is_ad_boosted(row):
            reply += f" ⭐ *FEATURED AD*\n"
        
        telegram_id = row.get('telegram_id')
        if not telegram_id:
            reply += f"📜 *Legacy Listing*\n"

            
        phone = str(get_row_value(row, 'Phone Number') or '').strip()
        if phone:
            if phone.startswith('0'):
                phone = '234' + phone[1:] 
            elif phone.startswith('+'):
                phone = phone[1:]
            elif not phone.startswith('234'):
                phone = '234' + phone
          

        pkg = str(get_row_value(row, 'Preferred Listing Package') or '').strip().title()
        if pkg:
            tag = "💎 Premium" if row_is_premium(row) else "🆓 Free"
            reply += f"{tag}\n"
        
        # Open business profile web UI
        if business_id and phone:
            telegram_id = row.get('telegram_id')
            
            if telegram_id:
                button_text = f"📞 Request Service - {business_name[:20]}"
            else:
                button_text = f"💬 Contact - {business_name[:20]}"
            
            # Build profile URL with ngrok bypass
            profile_url = f"{WEB_APP_URL}/business.html?id={business_id}"
            if customer_id:
                profile_url += f"&uid={customer_id}"
            if "ngrok-free.app" in WEB_APP_URL:
                profile_url += "&ngrok-skip-browser-warning=true"
            
            buttons.append([
                InlineKeyboardButton(
                    button_text, 
                    web_app=WebAppInfo(url=profile_url)
                )
            ])

        reply += "➖➖➖➖\n"
    
    keyboard = InlineKeyboardMarkup(buttons) if buttons else None
    return reply, keyboard

from bot_knowledge import BOT_KNOWLEDGE

GEMINI_SYSTEM_PROMPT = f"""You are Blue, a friendly Nigerian business directory assistant running inside Telegram.
Always stay in character as a human assistant (never mention AI, bots, or language models).

# Personality & Tone:
- Be warm, casual, and helpful — like a friendly Nigerian business advisor
- Use Nigerian Pidgin only if the user does. Otherwise use clear, simple English.
- Keep answers short and punchy (SMS/WhatsApp style). No long essays.
- Don't use "Ah", "Uhmm", or asterisks (**) for emphasis. Be eloquent and direct.
- If user mentions a Nigerian location (e.g. Ikeja, Warri) — highlight it naturally.

# Core Rules:
- NEVER give fake contacts, phone numbers, or service details. Only direct them to use the bot's features.
- If user asks about a service → tell them to tap "🔍 Find a Service" or type /find
- If user asks about registering → tell them to tap "🏪 Register Your Business" or type /register
- If user asks about admin help → tell them to tap "📩 Message Admin"
- If user asks about news/updates → tell them to tap "📢 Join Our Channel"
- If user asks about cancelling → tell them to tap /cancel or "🔁 Start Over"
- If the question is unrelated to businesses → give a short polite answer, then gently redirect
- If user spams → politely tell them to slow down

# Long-Term Memory:
- If the user tells you personal information worth remembering (their name, location, preferences, what they do, etc.), include a [REMEMBER: fact] tag at the END of your reply.
- Example: if user says "my name is John", your response should end with [REMEMBER: User's name is John]
- You can include multiple [REMEMBER: ...] tags if needed.
- These tags are automatically stripped before the user sees your reply.
- Only remember genuinely useful personal facts, not every random statement.
- If the user tells you to forget something, output [REMEMBER: CLEAR] to reset their memory.

# Complete Bot Knowledge (use this to answer ALL questions accurately):
{BOT_KNOWLEDGE}
"""

async def ask_gemini(prompt, user_id=None):
    """Chat with Gemini AI — async to avoid blocking the event loop."""
    if not check_rate_limit(user_id):
        return (
            "⚠️ You've reached your daily limit (20 messages/day)\n"
            "👇 Please tap a button below to continue.\n" "If you don't see them, tap the ⌨️ keyboard icon."
                )
    
    try:
        model = genai.GenerativeModel(
            GEMINI_MODEL_FOR_CHAT,
            system_instruction=GEMINI_SYSTEM_PROMPT
        )
        
        # Build conversation with history
        history = []
        
        # Inject long-term facts as context
        if user_id and user_id in USER_FACTS and USER_FACTS[user_id]:
            facts_text = "Things I remember about this user from previous conversations:\n"
            facts_text += "\n".join(f"- {f}" for f in USER_FACTS[user_id])
            history.append({"role": "user", "parts": [facts_text]})
            history.append({"role": "model", "parts": ["Got it, I'll keep these in mind."]})
        
        if user_id and user_id in conversation_memory:
            history.extend(conversation_memory[user_id].copy())
        
        # Add current user message
        history.append({"role": "user", "parts": [prompt]})
        
        # Run Gemini call in a thread to avoid blocking the bot event loop
        response = await asyncio.to_thread(model.generate_content, history)
        reply = response.text.strip()
        
        # Extract and save [REMEMBER: ...] tags
        if user_id:
            remember_matches = REMEMBER_TAG_RE.findall(reply)
            for fact in remember_matches:
                fact = fact.strip()
                if fact.upper() == 'CLEAR':
                    USER_FACTS.pop(user_id, None)
                    asyncio.create_task(save_user_fact_clear(user_id))
                    logging.info(f"🧠 Cleared memory for user {user_id}")
                else:
                    asyncio.create_task(save_user_fact(user_id, fact))
            
            # Strip tags from reply before sending to user
            reply = REMEMBER_TAG_RE.sub('', reply).strip()
        
        # Save to session memory
        if user_id:
            if user_id not in conversation_memory:
                conversation_memory[user_id] = []
            conversation_memory[user_id].append({"role": "user", "parts": [prompt]})
            conversation_memory[user_id].append({"role": "model", "parts": [reply]})
            # Trim to sliding window
            conversation_memory[user_id] = conversation_memory[user_id][-MAX_MEMORY_TURNS:]
        
        return reply
    except asyncio.TimeoutError:
        logging.error("Gemini API timeout")
        return "⚠️ Something went wrong. Please try again in a moment."
    except Exception as e:
        logging.error("Gemini chat error: %s", str(e))
        return "👇 Please tap a button below to continue.\n" "If you don't see them, tap the ⌨️ keyboard icon."

async def check_gemini_usage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to check Gemini usage"""
    if int(update.effective_user.id) != int(ADMIN_ID):
        await update.message.reply_text("❌ Unauthorized")
        return
    
    now = datetime.now(timezone.utc)
    today_start = datetime.combine(now.date(), datetime.min.time()).replace(tzinfo=timezone.utc)
    
    # Count per-user usage
    total_calls = 0
    active_users = 0
    user_details = []
    
    for uid, calls in user_gemini_calls.items():
        today_calls = [t for t in calls if t > today_start]
        if today_calls:
            active_users += 1
            total_calls += len(today_calls)
            user_details.append(f"  User {uid}: {len(today_calls)}/{PER_USER_DAILY_LIMIT}")
    
    msg = (
        f"📊 *Gemini Usage Report*\n\n"
        f"Limit: {PER_USER_DAILY_LIMIT} calls/user/day\n"
        f"Active users today: {active_users}\n"
        f"Total calls today: {total_calls}\n\n"
    )
    
    if user_details:
        msg += "*Per-user breakdown:*\n"
        msg += "\n".join(user_details[:15])  # Show top 15
        if len(user_details) > 15:
            msg += f"\n  ...and {len(user_details) - 15} more"
    
    await update.message.reply_text(msg, parse_mode='Markdown')

# --- States for Find & Register Flows ---
SERVICE_TYPE, LOCATION = range(2)
REGISTER_NAME, REGISTER_BUIS_NAME, REGISTER_SERVICE, REGISTER_LOCATION, REGISTER_PHONE, REGISTER_DESCRIPTION, REGISTER_PHOTOS, CONFIRM_REGISTRATION, WAITING_APPROVAL = range(2, 11)
BUY_COINS, COIN_PAYMENT_PROOF = range(20, 22)  # ✅ NEW: Coin purchase states
AD_BOOST_PAYMENT = range(30, 31)  

GEMINI_FREECHAT = 999
ADMIN_USERNAME = "Godsonodele"
CHANNEL_USERNAME = "bluemarkG"

user_last_click = {}
user_last_warn = {}

COOLDOWN_SECONDS = 5
WARN_COOLDOWN = 3

def cooldown_guard(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        now = time.time()

        if update.message and update.message.text:
            txt = update.message.text.strip().lower()
            if txt in ["cancel", "❌ cancel", "🔁 start over"]:
                return await func(update, context, *args, **kwargs)

        last_time = user_last_click.get(user_id, 0)
        if now - last_time < COOLDOWN_SECONDS:
            last_warn = user_last_warn.get(user_id, 0)
            if now - last_warn > WARN_COOLDOWN:
                try:
                    await update.message.reply_text("⏳ No spam please — one button at a time.")
                except Exception:
                    pass
                user_last_warn[user_id] = now
            return ConversationHandler.END if context.user_data.get("in_conversation") else None

        user_last_click[user_id] = now
        return await func(update, context, *args, **kwargs)
    return wrapper



def get_main_keyboard(user_id=None):
    """Return the main bot keyboard - reusable across all reply handlers."""
    
    # Append UID to dashboard URL for fallback
    dash_url = DASHBOARD_URL
    if user_id:
        dash_url = append_query(dash_url, f"uid={user_id}")
        
    # Append UID to catalog URL for consistency
    catalog_url = CATALOG_URL
    if user_id:
        catalog_url = append_query(catalog_url, f"uid={user_id}")
        
    reply_keyboard = [['🔍 Find a Service', '🏪 Register Your Business'],
                      ['💰 Buy Blue Coins', KeyboardButton('📊 My Dashboard', web_app=WebAppInfo(url=dash_url))],
                      [KeyboardButton('📋 Business Catalog', web_app=WebAppInfo(url=catalog_url)), '📢 Boost with Ads']
    ]
    return ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=False, resize_keyboard=True, input_field_placeholder="Tap a button below 👇")


@cooldown_guard
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # --- Handle /start claim_TOKEN deep link ---
    msg_text = update.message.text or ''
    if 'claim_' in msg_text:
        import re
        match = re.search(r'claim_([a-f0-9\-]+)', msg_text)
        if match:
            claim_token = match.group(1)
            await handle_claim_deeplink(update, context, claim_token)
            return ConversationHandler.END
    
    # Clear conversation memory on standard /start for fresh context
    conversation_memory.pop(user_id, None)
    markup = get_main_keyboard(user_id)

    top_buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("📩 Message Admin", url=f"https://t.me/{ADMIN_USERNAME}")],
        [InlineKeyboardButton("📢 Join Our Channel", url=f"https://t.me/{CHANNEL_USERNAME}")]
    ])
    await update.message.reply_text(
        "📢 Need help, want to advertise, or stay updated?\nTap below 👇",
        reply_markup=top_buttons
    )

    welcome_message = (
    "👋 *Welcome to BlueLink!*\n\n"
    "Find trusted service providers and register your business!\n\n"
    "🔎 Find a Service - Browse our directory\n"
    "🪙 Register Your Business - Get listed (Free!)\n"
    "💰 Blue Coins - Pay-per-lead system\n"
    "📊 My Dashboard - Track your business\n\n"
    "tap the ⌨️ keyboard icon to see the buttons"
    )
    
    # ✅ SAFE: Handle both old and new user data structures
    if user_has_business(user_id):
        businesses = get_user_businesses(user_id)
        coins = get_user_coins(user_id)
        
        welcome_message += f"\n\n📋 *Your Businesses:*"
        for biz in businesses[:3]:
            name = biz.get('business_name', 'Unknown')
            service = biz.get('service', 'N/A')
            loc = biz.get('location', 'N/A')
            welcome_message += f"\n  • *{name}* - {service} ({loc})"
        if len(businesses) > 3:
            welcome_message += f"\n  ...and {len(businesses) - 3} more"
        welcome_message += f"\n\n💰 Blue Coins: {coins}"
    
    await update.message.reply_text(welcome_message, reply_markup=markup, parse_mode='Markdown')
    await update.message.reply_text(
        "💬 *Ask me anything!*\nWhat do you want me to do for you? 😊",
        parse_mode='Markdown'
    )
    return ConversationHandler.END


async def handle_claim_deeplink(update: Update, context: ContextTypes.DEFAULT_TYPE, token: str):
    """Process a business claim from a /start claim_TOKEN deep link."""
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or 'User'
    
    try:
        # Look up token
        token_resp = await asyncio.to_thread(
            lambda: supabase.table('claim_tokens')
                .select('*')
                .eq('token', token)
                .execute()
        )
        
        if not token_resp.data:
            await update.message.reply_text(
                "❌ *Invalid claim link*\n\n"
                "This claim link is invalid or has expired.",
                parse_mode='Markdown'
            )
            return
        
        token_data = token_resp.data[0]
        
        if token_data.get('claimed_by'):
            await update.message.reply_text(
                "⚠️ *Already Claimed*\n\n"
                "This business has already been claimed by someone.",
                parse_mode='Markdown'
            )
            return
        
        business_id = token_data['business_id']
        
        # Get business name
        cached = get_cached_businesses()
        biz_name = 'Unknown'
        for b in cached:
            if str(get_row_value(b, 'id')) == str(business_id):
                biz_name = get_row_value(b, 'Business Name') or 'Unknown'
                break
        
        # Swap ownership: update telegram_id on the business
        await asyncio.to_thread(
            lambda: supabase.table('businesses')
                .update({'telegram_id': str(user_id)})
                .eq('id', business_id)
                .execute()
        )
        
        # Mark token as claimed
        await asyncio.to_thread(
            lambda: supabase.table('claim_tokens')
                .update({
                    'claimed_by': user_id,
                    'claimed_at': datetime.now().isoformat()
                })
                .eq('token', token)
                .execute()
        )
        
        # Give starter coins
        from coin_system import add_coins
        try:
            add_coins(user_id, 3)
        except Exception:
            pass
        
        # Refresh cache
        await refresh_cache_from_supabase()
        
        # Build dashboard URL
        dash_url = DASHBOARD_URL
        dash_url = append_query(dash_url, f"uid={user_id}")
        
        markup = get_main_keyboard(user_id)
        
        await update.message.reply_text(
            f"🎉 *Business Claimed Successfully!*\n\n"
            f"🏢 *{biz_name}* is now yours!\n\n"
            f"✅ You are now the verified owner.\n"
            f"💰 3 starter Blue Coins added to your account.\n\n"
            f"📊 Tap *My Dashboard* below to view and edit your business details.",
            parse_mode='Markdown',
            reply_markup=markup
        )
        
        # Notify admin
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    f"✅ *Business Claimed!*\n\n"
                    f"🏢 {biz_name} (ID: {business_id})\n"
                    f"👤 Claimed by: {user_name} (`{user_id}`)\n"
                ),
                parse_mode='Markdown'
            )
        except Exception:
            pass
        
        logging.info(f"✅ Business {business_id} claimed by user {user_id}")
        
    except Exception as e:
        logging.error(f"❌ Claim error: {e}")
        await update.message.reply_text(
            "❌ Something went wrong while claiming this business.\n"
            "Please try again or contact admin."
        )


async def generate_claim_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command: /claimlink <business_id> — Generate a claim link for a business."""
    if int(update.effective_user.id) != int(ADMIN_ID):
        await update.message.reply_text("❌ Unauthorized")
        return
    
    args = context.args
    if not args:
        await update.message.reply_text(
            "❌ *Usage:* `/claimlink <business_id>`\n\n"
            "Example: `/claimlink 42`\n"
            "This generates a claim link for the business.",
            parse_mode='Markdown'
        )
        return
    
    try:
        business_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Business ID must be a number.")
        return
    
    # Verify business exists
    cached = get_cached_businesses()
    biz_name = None
    for b in cached:
        if str(get_row_value(b, 'id')) == str(business_id):
            biz_name = get_row_value(b, 'Business Name') or 'Unknown'
            break
    
    if not biz_name:
        await update.message.reply_text(f"❌ Business ID {business_id} not found.")
        return
    
    # Generate token
    import uuid
    token = str(uuid.uuid4())
    
    # Store in DB
    await asyncio.to_thread(
        lambda: supabase.table('claim_tokens')
            .insert({
                'token': token,
                'business_id': business_id
            })
            .execute()
    )
    
    # Build claim URL
    claim_url = f"{WEB_APP_URL}/claim.html?token={token}"
    if 'ngrok-free.app' in WEB_APP_URL:
        claim_url = append_query(claim_url, 'ngrok-skip-browser-warning=true')
    
    await update.message.reply_text(
        f"🔗 *Claim Link Generated!*\n\n"
        f"🏢 *Business:* {biz_name}\n"
        f"🆔 *ID:* {business_id}\n\n"
        f"📋 *Claim URL:*\n`{claim_url}`\n\n"
        f"Send this link to the business owner.\n"
        f"When they open it and tap Claim, ownership transfers to their Telegram ID.",
        parse_mode='Markdown'
    )
    
    logging.info(f"🔗 Claim link generated for business {business_id}: {token}")

async def handle_after_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    user_id = update.effective_user.id
    reply = await ask_gemini(user_text, user_id=user_id)
    await update.message.reply_text(reply, reply_markup=get_main_keyboard(user_id))
    return ConversationHandler.END

async def _show_services_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Build and send the service selection keyboard from both tables."""
    rows = get_cached_businesses()
    logging.info(f"📊 Cache has {len(rows)} businesses")

    services = set()
    for row in rows:
        svc = str(row.get('business_services', '')).strip()
        if svc and svc.lower() != 'none':
            services.add(svc)

    # Pull extra services from business_services relational table
    try:
        extra_svc_resp = await asyncio.to_thread(
            lambda: supabase.table('business_services').select('service_category').execute()
        )
        for r in (extra_svc_resp.data or []):
            svc = str(r.get('service_category', '')).strip()
            if svc:
                services.add(svc)
    except Exception as svc_err:
        logging.warning(f"⚠️ Could not load extra services: {svc_err}")

    services = sorted(services)
    logging.info(f"✅ Extracted {len(services)} unique services: {services[:5]}")

    context.user_data['all_services'] = services
    keyboard = [[s] for s in services]
    keyboard.append(['Cancel'])

    await update.message.reply_text(
        "What service do you need? (Choose below or type manually):",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return SERVICE_TYPE


@cooldown_guard
async def find_service_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.sleep(0.3)
    context.user_data.clear()
    context.user_data["flow_started"] = True
    return await _show_services_keyboard(update, context)


async def ask_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip().lower() == "cancel":
        return await cancel(update, context)

    # Handle back to services — use helper directly, no cooldown guard
    if update.message.text.strip() == "⬅️ Back to Services":
        return await _show_services_keyboard(update, context)

    user_input = update.message.text.strip()
    available_services = context.user_data.get('all_services', [])

    selected_service = None
    user_input_lower = user_input.lower()
    
    # STEP 1: Try exact match first
    for service in available_services:
        if service.lower() == user_input_lower:
            selected_service = service
            logging.info(f"✅ Exact service match: {selected_service}")
            break
    
    # STEP 2: Try fuzzy matching
    if not selected_service:
        from difflib import get_close_matches
        matches = get_close_matches(user_input, available_services, n=1, cutoff=0.7)
        if matches:
            selected_service = matches[0]
            logging.info(f"✅ Fuzzy service match: {selected_service}")
    
    # STEP 3: Last resort - use Gemini for semantic matching
    if not selected_service:
        try:
            logging.info(f"⚠️ No direct match. Trying Gemini for: {user_input}")
            selected_service = find_best_service_gemini(user_input, available_services)
            if selected_service:
                logging.info(f"✅ Gemini service match: {selected_service}")
        except Exception as e:
            logging.error(f"Gemini service matching failed: {e}")

    if not selected_service:
        await update.message.reply_text(
            "⚠️ That service is not listed or does not match anything in our directory.\n"
            "Please choose from the available services below or type Cancel:"
        )
        keyboard = [[s] for s in available_services]
        keyboard.append(['Cancel'])

        await update.message.reply_text(
            "Available services:",
            reply_markup=ReplyKeyboardMarkup(
                keyboard, one_time_keyboard=True, resize_keyboard=True
            )
        )
        return SERVICE_TYPE

    context.user_data['service'] = selected_service

    # ── Find locations for selected_service across BOTH tables ──────────
    rows = get_cached_businesses()
    logging.info(f"🔍 Looking for locations for service: {selected_service}")

    locations = set()

    # 1. Main table: businesses whose primary service matches
    for row in rows:
        row_service = str(row.get('business_services', '')).strip().lower()
        if row_service == selected_service.lower():
            location = str(row.get('business_location', '')).strip()
            if location:
                locations.add(location)

    # 2. Relational table: businesses that have this as an extra service
    biz_ids_via_table = set()
    try:
        svc_resp = await asyncio.to_thread(
            lambda: supabase.table('business_services')
                .select('business_id')
                .ilike('service_category', selected_service)
                .execute()
        )
        biz_ids_via_table = {r['business_id'] for r in (svc_resp.data or [])}
    except Exception as e:
        logging.warning(f"⚠️ business_services lookup failed: {e}")

    if biz_ids_via_table:
        biz_map = {row.get('id'): row for row in rows}
        for bid in biz_ids_via_table:
            biz = biz_map.get(bid)
            if biz:
                loc = str(biz.get('business_location', '')).strip()
                if loc:
                    locations.add(loc)
        # Also check business_locations for their extra locations
        try:
            loc_resp = await asyncio.to_thread(
                lambda: supabase.table('business_locations')
                    .select('location')
                    .in_('business_id', list(biz_ids_via_table))
                    .execute()
            )
            for r in (loc_resp.data or []):
                if r.get('location'):
                    locations.add(r['location'].strip())
        except Exception as e:
            logging.warning(f"⚠️ business_locations lookup failed: {e}")

    # Also store the business_ids we found so search_and_reply can use them
    context.user_data['service_biz_ids'] = list(biz_ids_via_table)

    locations = sorted(locations)
    logging.info(f"✅ Found {len(locations)} locations: {locations}")
    context.user_data['locations'] = locations
    # ────────────────────────────────────────────────────────────────────

    if not locations:
        await update.message.reply_text(
            f"⚠️ No listed locations for *{selected_service}*.\n"
            "Type your location manually:",
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardMarkup(
                [['⬅️ Back to Services'], ['Cancel']],
                one_time_keyboard=True, resize_keyboard=True
            )
        )
        return LOCATION

    keyboard = [[loc] for loc in locations]
    keyboard.append(['⬅️ Back to Services'])
    keyboard.append(['Cancel'])

    await update.message.reply_text(
        f"Which area/location are you in for *{selected_service}*?\n"
        "Choose below or type manually the location close to you:",
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return LOCATION

@cooldown_guard
async def search_and_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip().lower() == "cancel":
        return await cancel(update, context)

    user_id = update.effective_user.id
    user_service = context.user_data.get('service', '').strip()
    user_location_input = update.message.text.strip()
    valid_locations = context.user_data.get('locations', [])

    matched_location = None
    user_input_lower = user_location_input.lower()
    
    # STEP 1: Try exact match first (fastest, most reliable)
    for location in valid_locations:
        if location.lower() == user_input_lower:
            matched_location = location
            logging.info(f"✅ Exact match found: {matched_location}")
            break
    
    # STEP 2: Try fuzzy matching (typo tolerance)
    if not matched_location:
        from difflib import get_close_matches
        matches = get_close_matches(user_location_input, valid_locations, n=1, cutoff=0.7)
        if matches:
            matched_location = matches[0]
            logging.info(f"✅ Fuzzy match found: {matched_location}")
    
    # STEP 3: Last resort - use Gemini for semantic matching (only for tough cases)
    if not matched_location:
        try:
            logging.info(f"⚠️ No direct match found. Trying Gemini for: {user_location_input}")
            matched_location = find_best_location_gemini(user_location_input, valid_locations)
            if matched_location:
                logging.info(f"✅ Gemini match found: {matched_location}")
        except Exception as e:
            logging.error(f"Gemini location matching failed: {e}")

    # If still no match, ask user to choose from list
    if not matched_location:
        await update.message.reply_text(
            "⚠️ That location is not listed for the selected service.\nPlease choose from the available locations below:"
        )
        keyboard = [[loc] for loc in valid_locations]
        keyboard.append(['Cancel'])
        await update.message.reply_text(
            "Here are valid locations again available for now:",
            reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        )
        return LOCATION

    # Direct database query from CACHE (NO GEMINI for search results)
    cached_businesses = get_cached_businesses()
    
    if not cached_businesses:
        logging.warning("Cache empty, querying Supabase directly")
        try:
            response = await asyncio.to_thread(
                lambda: supabase.table('businesses').select('*').execute()
            )
            rows = response.data
        except Exception as e:
            logging.error(f"Supabase query failed: {e}")
            await update.message.reply_text("❌ Search failed. Please try again.")
            return ConversationHandler.END
    else:
        rows = cached_businesses
    
    matched_ids = set()
    matches = []

    # Match from main businesses table (primary service + location)
    for row in rows:
        row_service = str(get_row_value(row, 'Business Services')).strip().lower()
        row_location = str(get_row_value(row, 'Business Location')).strip().lower()
        if row_service == user_service.lower() and row_location == matched_location.lower():
            rid = row.get('id')
            if rid not in matched_ids:
                matched_ids.add(rid)
                matches.append((row, 1.0))

    # Also match businesses found via relational service table
    service_biz_ids = context.user_data.get('service_biz_ids', [])
    if service_biz_ids:
        biz_map = {r.get('id'): r for r in rows}
        for bid in service_biz_ids:
            if bid in matched_ids:
                continue
            biz = biz_map.get(bid)
            if not biz:
                continue
            row_location = str(get_row_value(biz, 'Business Location')).strip().lower()
            if row_location == matched_location.lower():
                matched_ids.add(bid)
                matches.append((biz, 1.0))
            else:
                # Check extra locations in business_locations table
                try:
                    loc_check = await asyncio.to_thread(
                        lambda: supabase.table('business_locations')
                            .select('location')
                            .eq('business_id', bid)
                            .ilike('location', matched_location)
                            .execute()
                    )
                    if loc_check.data:
                        matched_ids.add(bid)
                        matches.append((biz, 1.0))
                except Exception:
                    pass

    # Sort: Premium first
    matches = sorted(matches, key=lambda m: (0 if row_is_premium(m[0]) else 1))

    if not matches:
        await update.message.reply_text(
            f"❌ No results found for *{user_service.title()}* in *{matched_location}*.\n\n"
            "You can try again with a different service/location or register your business.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup([['🔁 Start Over', '🏪 Register Your Business']], one_time_keyboard=True, resize_keyboard=True)
        )
        return ConversationHandler.END

    reply, keyboard = format_matches_reply(matches, customer_id=user_id)

    if keyboard:
        await update.message.reply_text(
            reply, 
            parse_mode='Markdown', 
            disable_web_page_preview=True,
            reply_markup=keyboard
        )
    else:
        await update.message.reply_text(reply, parse_mode='Markdown', disable_web_page_preview=True)
    
    # Show upgrade button if user has free tier business
    if user_has_business(user_id) and user_is_free_tier(user_id):
        await update.message.reply_text(
            "💡 *Want your business to appear at the top?*\n"
            "Boost with ads for priority listing!\n\n"
            "Would you like to upgrade or start over to find another service?",
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardMarkup([['🔁 Start Over', '📢 Boost with Ads']], one_time_keyboard=True, resize_keyboard=True)
        )
    else:
        await update.message.reply_text(
            "Would you like to find another service? (start over) or register your business?",
            reply_markup=ReplyKeyboardMarkup([['🔁 Start Over', '🏪 Register Your Business']], one_time_keyboard=True, resize_keyboard=True)
        )
    return ConversationHandler.END

def is_duplicate(phone, name, telegram_id=None):
    """
    Premium users can own multiple businesses.
    Only flag as duplicate if ANOTHER user owns the phone/business.
    """
    try:
        records = get_cached_businesses()
        phone_norm = str(phone or "").strip()
        name_norm = str(name or "").strip().lower()

        for row in records:
            # ✅ FIX: Use snake_case column names
            existing_name = str(row.get("business_name", "")).strip().lower()
            existing_phone = str(row.get("phone_number", "")).strip()
            existing_telegram_id = str(row.get("telegram_id", "")).strip()

            # --- PHONE CHECK: Allow if SAME owner ---
            if phone_norm and existing_phone and phone_norm == existing_phone:
                if existing_telegram_id:
                    try:
                        if int(existing_telegram_id) == int(telegram_id):
                            # ✅ Same user owns this phone → allow (branch/hotline)
                            logging.info(f"✅ Phone {phone} already owned by user {telegram_id} (multi-business)")
                            return False
                        else:
                            # ⚠️ Different user owns this phone → flag as duplicate
                            logging.warning(f"⚠️ Phone {phone} owned by different user")
                            return True
                    except ValueError:
                        pass
                else:
                    # Old entry without Telegram ID → assume duplicate for safety
                    return True

            # --- NAME CHECK: Allow if SAME owner ---
            if name_norm and existing_name and name_norm == existing_name:
                if existing_telegram_id:
                    try:
                        if str(existing_telegram_id).strip() == str(telegram_id).strip():
                            logging.info(f"✅ Business '{name}' already owned by user {telegram_id}")
                            return False
                        else:
                            # ⚠️ Different user owns this name → flag
                            return True
                    except ValueError:
                        pass
                else:
                    return True
                    
    except Exception as e:
        logging.error("Duplicate check failed: %s", e)
    
    return False

def has_pending_registration(telegram_id):
    """Check if user has ANY pending registration or upgrade"""
    return telegram_id in PENDING_REGISTRATIONS

def has_pending_new_registration(telegram_id):
    """Check if user has a pending NEW business registration (not upgrade)"""
    if telegram_id not in PENDING_REGISTRATIONS:
        return False
    
    pending = PENDING_REGISTRATIONS[telegram_id]
    # Check if it's NOT an upgrade (i.e., it's a new registration)
    return pending.get('type') != 'ad_boost' and 'coin_purchase'
    


def has_pending_upgrade(telegram_id):
    """Check if user has a PENDING UPGRADE specifically (not new registration)"""
    if telegram_id not in PENDING_REGISTRATIONS:
        return False
    
    pending = PENDING_REGISTRATIONS[telegram_id]
    
    # ✅ FIX: Check for upgrade type OR presence of upgrade_telegram_id
    is_upgrade_type = pending.get('type') == 'upgrade'
    has_upgrade_marker = 'upgrade_telegram_id' in pending
    is_premium_tier = pending.get('tier') == 'premium'
    has_proof = 'proof' in pending
    
    return (is_upgrade_type or has_upgrade_marker) and is_premium_tier and has_proof

def has_pending_coin_purchase(telegram_id):
    """Check if user has a pending coin purchase request"""
    if telegram_id not in PENDING_REGISTRATIONS:
        return False
    
    pending = PENDING_REGISTRATIONS[telegram_id]
    return pending.get('type') == 'coin_purchase'


def has_pending_ad_boost(telegram_id):
    """Check if user has a pending ad boost request"""
    if telegram_id not in PENDING_REGISTRATIONS:
        return False
    
    pending = PENDING_REGISTRATIONS[telegram_id]
    return pending.get('type') == 'ad_boost'

def is_valid_phone(phone: str) -> bool:
    return re.fullmatch(r"\d{11}", phone) is not None



async def inline_search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Updated inline search with snake_case column names"""
    query = update.inline_query.query.strip()

    if not query:
        await update.inline_query.answer([], cache_time=1,
            button=InlineQueryResultsButton(text="Type a service or location to search...", start_parameter="help"))
        return

    try:
        matches = smart_directory_search(query, top_k=5)
    except Exception as e:
        logging.error("Inline Gemini search error: %s", e)
        await update.inline_query.answer([], cache_time=1,
            button=InlineQueryResultsButton(text="Error during search. Try again later.", start_parameter="error"))
        return

    results = []

    for i, (row, _) in enumerate(matches):
        # ✅ FIX: Use snake_case column names
        title = row.get("business_name", "Unknown")
        service = row.get("business_services", "")
        location = row.get("business_location", "")
        phone = str(row.get("phone_number", "")).strip()
        description = row.get("business_description", "")


        if phone.startswith("0"):
            phone = "234" + phone[1:]
        elif phone.startswith("+"):
            phone = phone[1:]
        elif not phone.startswith("234"):
            phone = "234" + phone

        message_text = (
            f"🏷 *{title}*\n"
            f"📍 {location}\n"
            f"🛠 {service}\n"
            f"💬 WhatsApp: https://wa.me/{phone}"
        )
        if description:
            message_text += f"\n\n📝 _{description}_"


        results.append(
            InlineQueryResultArticle(
                id=str(uuid.uuid4()),
                title=f"{title} — {location}",
                description=f"{service} • tap to open",
                input_message_content=InputTextMessageContent(message_text, parse_mode='Markdown', disable_web_page_preview=True)
            )
        )

    await update.inline_query.answer(results, cache_time=1)

async def fallback_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text

    try:
        matches = smart_directory_search(user_text)
        
        # ✅ FIX: Use the new function that returns both reply AND keyboard
        reply, keyboard = format_matches_reply(matches, customer_id=update.effective_user.id)
        
        if reply:
            # ✅ FIX: Send with keyboard if it exists
            if keyboard:
                await update.message.reply_text(
                    reply, 
                    parse_mode='Markdown', 
                    disable_web_page_preview=True,
                    reply_markup=keyboard
                )
            else:
                await update.message.reply_text(
                    reply, 
                    parse_mode='Markdown', 
                    disable_web_page_preview=True
                )
            
            await update.message.reply_text(
                "Want to try another request or register your business?",
                reply_markup=ReplyKeyboardMarkup(
                    [['🔁 Start Over', '🏪 Register Your Business']],
                    one_time_keyboard=True, resize_keyboard=True
                )
            )
            return ConversationHandler.END
    except Exception as e:
        logging.error("Gemini directory search failed in fallback_chat: %s", e)

    logging.info("💬 Falling back to general Gemini chat for: %s", user_text)
    reply = await ask_gemini(user_text, user_id=update.effective_user.id)
    
    # ✅ FIX: Handle empty replies from Gemini
    if reply and reply.strip():
        await update.message.reply_text(reply, reply_markup=get_main_keyboard(update.effective_user.id))
    else:
        await update.message.reply_text(
            "👇 Please tap a button below to continue.\n"
            "If you don't see them, tap the ⌨️ keyboard icon.",
            reply_markup=get_main_keyboard(update.effective_user.id)
        )
    
    return ConversationHandler.END


async def handle_find_back_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle ⬅️ Back to Services button in the Find Service flow."""
    context.user_data.pop('service', None)
    context.user_data.pop('locations', None)
    # Delegate to the shared helper so both the main businesses table
    # AND the business_services relational table are included.
    return await _show_services_keyboard(update, context)

@cooldown_guard
async def register_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    context.user_data["flow_started"] = True
    
    # ✅ NEW: Check for pending registration (no tier restrictions)
    if has_pending_new_registration(user_id):
        await update.message.reply_text(
            "⏳ *Pending Admin Approval*\n\n"
            "You already have a registration waiting for admin review.\n\n"
            f"Please wait or [contact admin](https://t.me/{ADMIN_USERNAME}) for updates.",
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardMarkup([['🔁 Start Over']], one_time_keyboard=True, resize_keyboard=True),
            disable_web_page_preview=True
        )
        return ConversationHandler.END
    
    # ✅ NEW: No business limit - everyone can register unlimited businesses!
    business_count = get_business_count(user_id)
    
    if business_count > 0:
        await update.message.reply_text(
            f"🎉 You have {business_count} business(es) registered!\n\n"
            f"Let's add another one!",
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardMarkup(
                [
                    [KeyboardButton(text='📝 Register Business', web_app=WebAppInfo(url=f"{WEB_APP_URL}/register.html?uid={user_id}&ngrok-skip-browser-warning=true"))],
                    ['Register via Telegram', 'Cancel']
                ], 
                one_time_keyboard=True, 
                resize_keyboard=True
            )
        )
    else:
        await update.message.reply_text(
            "📝 How would you like to register your business?",
            reply_markup=ReplyKeyboardMarkup(
                [
                    [KeyboardButton(text='📝 Register Business', web_app=WebAppInfo(url=f"{WEB_APP_URL}/register.html?uid={user_id}&ngrok-skip-browser-warning=true"))],
                    ['Register via Telegram', 'Cancel']
                ], 
                one_time_keyboard=True, 
                resize_keyboard=True
            )
        )
    
    return REGISTER_NAME

async def register_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()

    if text == 'register via google form':
        return await register_google_form(update, context)
    
    if text == 'cancel':
        return await cancel(update, context)
    
    if text == 'register via telegram':
        user_first = update.message.from_user.first_name
        context.user_data['reg_name'] = user_first
        context.user_data['telegram_id'] = update.effective_user.id
        await update.message.reply_text(
            f"Hello *{user_first}*, What is your business name?",
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardMarkup([['Cancel']], one_time_keyboard=True, resize_keyboard=True)
        )
        return REGISTER_BUIS_NAME
    
    # ✅ FIX: If invalid input, stay in current state and ask again
    await update.message.reply_text(
        "⚠️ Please choose an option from the buttons below:",
        reply_markup=ReplyKeyboardMarkup(
            [['Register via Telegram', 'Register via Google Form'], ['Cancel']], 
            one_time_keyboard=True, 
            resize_keyboard=True
        )
    )
    return REGISTER_NAME  # ← Stay in this state

async def register_buis_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip().lower() == "cancel":
        return await cancel(update, context)

    context.user_data['reg_buis_name'] = sanitize_user_input(update.message.text.strip())
    await update.message.reply_text(
        "📞 Please enter your *11-digit phone number* (WhatsApp preferred): \n" 
        "Numbers are secured with encryption",
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardMarkup([['Cancel']], one_time_keyboard=True, resize_keyboard=True)
    )
    return REGISTER_PHONE

async def register_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip().lower() == "cancel":
        return await cancel(update, context)

    phone = update.message.text.strip()
    telegram_id = context.user_data.get('telegram_id')
    is_premium = user_is_premium(telegram_id)

    if not is_valid_phone(phone):
        await update.message.reply_text(
            "❌ Invalid phone number.\nPlease enter a valid *11-digit phone number*.",
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardMarkup(
                [['Cancel']], one_time_keyboard=True, resize_keyboard=True
            )
        )
        return REGISTER_PHONE

    # Enhanced duplicate check
    if is_duplicate(phone, context.user_data.get('reg_buis_name'), telegram_id):
        if is_premium:
            # Premium user trying to use someone else's phone → BLOCK IT (not your number)
            await update.message.reply_text(
                f"❌ Phone number {phone} is registered to *another business owner*.\n\n"
                "You can only use phone numbers you own.\n\n"
                "Please enter a different phone number or contact admin if this is an error.",
                parse_mode='Markdown',
                reply_markup=ReplyKeyboardMarkup([['Cancel']], one_time_keyboard=True, resize_keyboard=True)
            )
            return REGISTER_PHONE  # Wait for confirmation
        else:
            # Free user → standard duplicate rejection
            await update.message.reply_text(
                "⚠️ This phone number or business already exists.\n\n"
                "🔑 *One business per account policy for free tier*\n\n"
                "If this is yours, contact admin.",
                parse_mode='Markdown',
                reply_markup=ReplyKeyboardMarkup([['Cancel']], one_time_keyboard=True, resize_keyboard=True)
            )
            return REGISTER_PHONE

    context.user_data['reg_phone'] = phone

    await update.message.reply_text(
        "🛠️ What *services* do you offer?\n"
        "(Example: Plumber, Hair vendor, Electrician, Data reseller)",
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardMarkup([['Cancel']], one_time_keyboard=True, resize_keyboard=True)
    )
    return REGISTER_SERVICE

async def register_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip().lower() == "cancel":
        return await cancel(update, context)

    context.user_data['reg_service'] = sanitize_user_input(update.message.text.strip())
    await update.message.reply_text(
        "📍 Where is your business located?(State-Area): e.g Lagos-Ikeja | Delta-Warri",
        reply_markup=ReplyKeyboardMarkup([['Cancel']], one_time_keyboard=True, resize_keyboard=True)
    )
    return REGISTER_LOCATION

async def register_google_form(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply_markup = ReplyKeyboardMarkup([['🔁 Start Over']], one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text(
        "🌐 You can register via Google Form here:\nhttps://forms.gle/set6Et3cW3Sy2FH7A",
        reply_markup=reply_markup, parse_mode='Markdown',
        disable_web_page_preview=False
    )
    return ConversationHandler.END

async def register_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip().lower() == "cancel":
        return await cancel(update, context)
    context.user_data['reg_location'] = sanitize_user_input(update.message.text.strip())
    await update.message.reply_text(
        "🧾 Brief description of your business:(What is your business about?)",
        reply_markup=ReplyKeyboardMarkup([['Cancel']], one_time_keyboard=True, resize_keyboard=True)
    )
    return REGISTER_DESCRIPTION

async def register_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip().lower() == "cancel":
        return await cancel(update, context)
    
    context.user_data['reg_description'] = sanitize_user_input(update.message.text)
    
    # ✅ NEW: Ask for photos (optional step)
    await update.message.reply_text(
        "📸 *Business Photos (Optional)*\n\n"
        "Upload up to *3 photos* of your:\n"
        "• Products\n"
        "• Services\n"
        "• Shop/workspace\n"
        "• Previous work\n\n"
        "💡 Photos help customers trust your business!\n\n"
        "Send photos one by one, or tap *Skip* to continue without photos.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(
            [['⏭️ Skip Photos', '❌ Cancel']], 
            one_time_keyboard=True, 
            resize_keyboard=True
        )
    )
    
    # Initialize photos list
    context.user_data['reg_photos'] = []
    
    return REGISTER_PHOTOS

async def register_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo uploads (up to 3 photos)"""
    
    # Check for skip
    if update.message.text and "skip" in update.message.text.lower():
        logging.info(f"User {update.effective_user.id} skipped photos")
        return await show_confirmation(update, context)
    
    # Check for cancel
    if update.message.text and "cancel" in update.message.text.lower():
        return await cancel(update, context)
    
    # Handle photo or document upload
    if update.message.photo or update.message.document:
        photos = context.user_data.get('reg_photos', [])
        
        # Check if already have 3 photos
        if len(photos) >= 3:
            await update.message.reply_text(
                "⚠️ You've already uploaded 3 photos (maximum).\n\n"
                "Tap *Skip Photos* to continue.",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardMarkup(
                    [['⏭️ Skip Photos', '❌ Cancel']], 
                    one_time_keyboard=True, 
                    resize_keyboard=True
                )
            )
            return REGISTER_PHOTOS
        
        # Get file_id (handle compressed or uncompressed)
        if update.message.photo:
            photo_file_id = update.message.photo[-1].file_id
        else:
            # Check if it's an image
            doc = update.message.document
            if not doc.mime_type or not doc.mime_type.startswith('image/'):
                await update.message.reply_text("⚠️ Please send an image file (JPG, PNG), not a document/file.")
                return REGISTER_PHOTOS
            photo_file_id = doc.file_id
            
        photos.append(photo_file_id)

        context.user_data['reg_photos'] = photos
        
        remaining = 3 - len(photos)
        
        if remaining > 0:
            await update.message.reply_text(
                f"✅ Photo {len(photos)}/3 uploaded!\n\n"
                f"You can upload {remaining} more photo(s), or tap *Skip Photos* to continue.",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardMarkup(
                    [['⏭️ Skip Photos', '❌ Cancel']], 
                    one_time_keyboard=True, 
                    resize_keyboard=True
                )
            )
            return REGISTER_PHOTOS
        else:
            # All 3 photos uploaded
            await update.message.reply_text(
                "✅ All 3 photos uploaded!\n\n"
                "Moving to confirmation...",
                parse_mode="Markdown"
            )
            return await show_confirmation(update, context)
    
    else:
        # Invalid input
        photos = context.user_data.get('reg_photos', [])
        await update.message.reply_text(
            f"⚠️ Please send a *photo* (compressed or file).\n"
            f"❌ Text messages are not accepted here.\n\n"

            f"Photos uploaded: {len(photos)}/3\n\n"
            f"Or tap *Skip Photos* to continue without photos.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(
                [['⏭️ Skip Photos', '❌ Cancel']], 
                one_time_keyboard=True, 
                resize_keyboard=True
            )
        )
        return REGISTER_PHOTOS


async def show_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show registration confirmation summary"""
    photos = context.user_data.get('reg_photos', [])
    
    summary = (
        "📋 *Please confirm your details:*\n\n"
        f"👤 Name: {context.user_data.get('reg_name')}\n"
        f"🏪 Business: {context.user_data.get('reg_buis_name')}\n"
        f"🛠 Service: {context.user_data.get('reg_service')}\n"
        f"📍 Location: {context.user_data.get('reg_location')}\n"
        f"📞 Phone: {context.user_data.get('reg_phone')}\n"
        f"📝 Description: {context.user_data.get('reg_description')}\n"
        f"📸 Photos: {len(photos)}/3 uploaded\n\n"
        "✅ Confirm registration?"
    )

    keyboard = [['✅ Yes', '❌ No']]
    await update.message.reply_text(
        summary,
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )

    return CONFIRM_REGISTRATION

# PENDING_REGISTRATIONS is imported from bot_state — do NOT re-declare here

async def confirm_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text.strip()

    if "Yes" in choice:  # Matches "✅ Yes"
        user_id = update.effective_user.id
        photos = context.user_data.get('reg_photos', [])

        business_data = {
            "name": context.user_data.get("reg_name", ""),
            "buis_name": context.user_data.get("reg_buis_name", ""),
            "service": context.user_data.get("reg_service", ""),
            "location": context.user_data.get("reg_location", ""),
            "phone": context.user_data.get("reg_phone", ""),
            "description": context.user_data.get("reg_description", ""),
            "telegram_id": user_id,
            "tier": "free",
            "photo_1": photos[0] if len(photos) > 0 else None,
            "photo_2": photos[1] if len(photos) > 1 else None,
            "photo_3": photos[2] if len(photos) > 2 else None
        }
        # ✅ NEW: No tier selection - everyone is the same!
        business_data['timestamp'] = datetime.now().isoformat()
        business_data['type'] = 'registration'

        PENDING_REGISTRATIONS[user_id] = business_data
        
        try:
            await asyncio.to_thread(
                lambda: supabase.table('pending_registrations')
                    .upsert({
                        'user_id': user_id,
                        'data': json.dumps(business_data),
                        'type': 'registration',
                        'created_at': business_data['timestamp']
                    })
                    .execute()
            )
            logging.info(f"✅ Saved pending registration to DB for user {user_id}")
        except Exception as e:
            logging.error(f"❌ Failed to save pending registration: {e}")

        keyboard = [
            [InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user_id}"),
             InlineKeyboardButton("❌ Reject", callback_data=f"reject_{user_id}")]
        ]
        
        # Send photos to admin if available
        photos = [business_data.get('photo_1'), business_data.get('photo_2'), business_data.get('photo_3')]
        photos = [p for p in photos if p]  # Filter out None values

        caption = (
            f"📢 *New Business Registration:*\n\n"
            f"👤 {business_data['name']}\n"
            f"🏪 {business_data['buis_name']}\n"
            f"🛠 {business_data['service']}\n"
            f"📍 {business_data['location']}\n"
            f"📞 {business_data['phone']}\n"
            f"📝 {business_data['description']}\n"
            f"📸 Photos: {len(photos)}\n"
            f"🆔 Telegram ID: {user_id}"
        )

        if photos:
            # Send first photo with caption
            await context.bot.send_photo(
                chat_id=ADMIN_ID,
                photo=photos[0],
                caption=caption,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
            # Send additional photos if any
            for photo in photos[1:]:
                await context.bot.send_photo(
                    chat_id=ADMIN_ID,
                    photo=photo
                )
        else:
            # No photos - send text only
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=caption,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

        await update.message.reply_text(
            "⏳ Your registration has been sent for *admin approval*.\n\n"
            "✅ Once approved, you'll be ready to receive customer requests!\n\n"
            "💡 Each customer request costs 1 Blue Coin.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup([['🔁 Start Over']], one_time_keyboard=True, resize_keyboard=True)
        )
        return WAITING_APPROVAL

    elif "No" in choice:  # Matches "❌ No"
        await update.message.reply_text(
            "❌ Registration cancelled. Let's start again.",
            reply_markup=ReplyKeyboardMarkup([["🔁 Start Over"]], one_time_keyboard=True, resize_keyboard=True)
        )
        return ConversationHandler.END

    else:
        await update.message.reply_text(
            "⚠️ Please choose *Yes* or *No* by tapping the buttons below.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup([["✅ Yes", "❌ No"]], one_time_keyboard=True, resize_keyboard=True)
        )
        return CONFIRM_REGISTRATION


async def payment_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text.strip().lower()
    user_id = update.effective_user.id
    is_premium = user_is_premium(user_id)
    
    # ✅ SAFE: Premium users still choose tier (but gets instant approval)
    if "free" in choice:
        business_data = {
            "name": context.user_data.get("reg_name", ""),
            "buis_name": context.user_data.get("reg_buis_name", ""),
            "service": context.user_data.get("reg_service", ""),
            "location": context.user_data.get("reg_location", ""),
            "phone": context.user_data.get("reg_phone", ""),
            "description": context.user_data.get("reg_description", ""),
            "telegram_id": user_id,
            "tier": "Premium" if is_premium else "free"  # ✅ Auto-upgrade if premium account
        }

        PENDING_REGISTRATIONS[user_id] = business_data

        keyboard = [
            [InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user_id}"),
             InlineKeyboardButton("❌ Reject", callback_data=f"reject_{user_id}")]
        ]

        tier_label = "Premium (Auto-upgraded)" if is_premium else "Free"
        
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(f"📢 *Business Pending Approval:*\n\n"
                  f"👤 {business_data['name']}\n"
                  f"🏪 {business_data['buis_name']}\n"
                  f"🛠 {business_data['service']}\n"
                  f"📍 {business_data['location']}\n"
                  f"📞 {business_data['phone']}\n"
                  f"📝 {business_data['description']}\n"
                  f"🆔 Telegram ID: {business_data['telegram_id']}\n"
                  f"💠 Tier: {tier_label}"),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        await update.message.reply_text(
            "⏳ Your registration has been sent for *admin approval*.\n"
            "You may continue browsing while waiting.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup([['🔁 Start Over']], one_time_keyboard=True, resize_keyboard=True)
        )
        return WAITING_APPROVAL

    elif "premium" in choice:
        # Existing premium proof flow (unchanged)
        context.user_data["tier"] = "premium"
        await update.message.reply_text(
            "💎 *Premium Listing Selected!*\n\n"
            "Please make a payment to:\n"
            "🏦 Bank: Opay\n"
            "🔢 Account: 9038626938\n"
            "💰 Amount: ₦300\n\n"
            "After payment, upload your *payment receipt/screenshot* here.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup([["❌ Cancel"]], one_time_keyboard=True, resize_keyboard=True)
        )
        return PREMIUM_PROOF

    else:
        await update.message.reply_text(
            "Please choose either *Free Listing* or *Premium Listing*.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(
                [["🆓 Free Listing", "💎 Premium Listing"]], one_time_keyboard=True,
                resize_keyboard=True
            )
        )
        return PAYMENT_CHOICE


# --- Premium proof handler ---

async def premium_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ✅ Accept only photo or document
    if update.message.photo or update.message.document:
        file_id = update.message.photo[-1].file_id if update.message.photo else update.message.document.file_id
        context.user_data['payment_proof'] = file_id

        # Business data
        business_data = {
            "name": context.user_data.get("reg_name", ""),
            "buis_name": context.user_data.get("reg_buis_name", ""),
            "service": context.user_data.get("reg_service", ""),
            "location": context.user_data.get("reg_location", ""),
            "phone": context.user_data.get("reg_phone", ""),
            "description": context.user_data.get("reg_description", ""),
            "telegram_id": context.user_data.get("telegram_id", ""),
            "tier": "premium",
            "proof": file_id
        }

        # Store temporarily until admin decides
        PENDING_REGISTRATIONS[update.effective_user.id] = business_data

        # Inline buttons for admin approval
        keyboard = [
            [InlineKeyboardButton("✅ Approve", callback_data=f"approve_{update.effective_user.id}"),
             InlineKeyboardButton("❌ Reject", callback_data=f"reject_{update.effective_user.id}")]
        ]

        # Send proof + details to admin
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=file_id,
            caption=(
                f"📢 *Premium Business Pending Approval:*\n\n"
                f"👤 {business_data['name']}\n"
                f"🏪 {business_data['buis_name']}\n"
                f"🛠 {business_data['service']}\n"
                f"📍 {business_data['location']}\n"
                f"📞 {business_data['phone']}\n"
                f"📝 {business_data['description']}"
                f"🆔 Telegram ID: {business_data['telegram_id']}"
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        # Acknowledge user
        await update.message.reply_text(
            "⏳ Your payment proof has been sent to admin.\n"
            "Please wait for approval.\n\n"
            "You may continue chatting here while waiting.\n"
            "When you're ready to restart, tap 🔁 Start Over.",
            reply_markup=ReplyKeyboardMarkup([['🔁 Start Over']], one_time_keyboard=True, resize_keyboard=True)
        )
        return WAITING_APPROVAL

    elif update.message.text and "cancel" in update.message.text.lower():
        return await cancel(update, context)

    else:
        # ❌ Wrong input
        await update.message.reply_text(
            "⚠️ Please upload an *image or screenshot of the payment*.\n\n"
            "Or click Cancel to start over.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup([['❌ Cancel']], one_time_keyboard=True, resize_keyboard=True)
        )
        return PREMIUM_PROOF

# NEW: Upgrade to Premium Flow
async def upgrade_to_premium_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    context.user_data["flow_started"] = True

    # Already premium?
    if user_is_premium(user_id):
        business_count = get_business_count(user_id)
        await update.message.reply_text(
            f"ℹ️ *Your account is already Premium!*\n\n"
            f"All your {business_count} business(es) have Premium benefits.",
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardMarkup([['🔁 Start Over']], one_time_keyboard=True, resize_keyboard=True)
        )
        return ConversationHandler.END
    
    # Has pending upgrade?
    if has_pending_upgrade(user_id):
        await update.message.reply_text(
            "⏳ *Upgrade Pending Admin Approval*\n\n"
            "You already have a premium upgrade request waiting.\n\n"
            f"Please wait or [contact admin](https://t.me/{ADMIN_USERNAME}).",
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardMarkup([['🔁 Start Over']], one_time_keyboard=True, resize_keyboard=True),
            disable_web_page_preview=True
        )
        return ConversationHandler.END
    
    # Must have business
    if not user_has_business(user_id):
        await update.message.reply_text(
            "⚠️ You don't have any registered businesses yet.\n"
            "Please register first!",
            reply_markup=ReplyKeyboardMarkup([['🏪 Register Your Business', '🔁 Start Over']], 
                                            one_time_keyboard=True, resize_keyboard=True)
        )
        return ConversationHandler.END
    
    # Show upgrade info
    business_count = get_business_count(user_id)
    businesses = get_user_businesses(user_id)
    
    biz_list = ""
    for i, biz in enumerate(businesses[:3]):
        biz_list += f"\n  • {biz.get('business_name', 'Unknown')}"
    if business_count > 3:
        biz_list += f"\n  • ... and {business_count - 3} more"
    
    context.user_data['upgrade_telegram_id'] = user_id
    
    await update.message.reply_text(
        f"💎 *Upgrade to Premium Account!*\n\n"
        f"Your {business_count} business(es):{biz_list}\n\n"
        f"✨ *Benefits:*\n"
        f"• ALL businesses get priority listing\n"
        f"• Register unlimited businesses\n"
        f"• Enhanced visibility\n\n"
        f"💰 *One-time: ₦300*\n\n"
        f"Payment details:\n"
        f"🏦 Bank: Opay\n"
        f"🔢 Account: 9038626938\n\n"
        f"Upload payment receipt below:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([["❌ Cancel"]], one_time_keyboard=True, resize_keyboard=True)
    )
    return UPGRADE_PROOF

async def upgrade_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo or update.message.document:
        file_id = update.message.photo[-1].file_id if update.message.photo else update.message.document.file_id
        user_id = context.user_data.get('upgrade_telegram_id')
        business_name = context.user_data.get('upgrade_business')
        
        # ✅ FIX: Store complete upgrade request data
        PENDING_REGISTRATIONS[user_id] = {
            "type": "upgrade",  # ← Mark this as an upgrade, not new registration
            "upgrade_telegram_id": user_id,  # ← NOW we store this
            "business_name": business_name,
            "tier": "premium",
            "proof": file_id
        }
        
        # Inline buttons for admin
        keyboard = [
            [InlineKeyboardButton("✅ Approve Upgrade", callback_data=f"upgrade_approve_{user_id}"),
             InlineKeyboardButton("❌ Reject", callback_data=f"upgrade_reject_{user_id}")]
        ]

        # Send to admin
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=file_id,
            caption=(
                f"📢 *Premium Upgrade Request:*\n\n"
                f"🪙 Business: {business_name}\n"
                f"🆔 Telegram ID: {user_id}\n"
                f"📊 Current Tier: Free\n"
                f"⬆️ Requested Tier: Premium"
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        await update.message.reply_text(
            "⏳ Your upgrade request and payment proof have been sent to admin.\n"
            "Please wait for approval.\n\n"
            "You may continue using the bot while waiting.",
            reply_markup=ReplyKeyboardMarkup([['🔁 Start Over']], one_time_keyboard=True, resize_keyboard=True)
        )
        return ConversationHandler.END

    elif update.message.text and "cancel" in update.message.text.lower():
        return await cancel(update, context)

    else:
        await update.message.reply_text(
            "⚠️ Please upload an *image or screenshot of the payment*.\n\n"
            "Or click Cancel to start over.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup([['❌ Cancel']], one_time_keyboard=True, resize_keyboard=True)
        )
        return UPGRADE_PROOF

async def handle_admin_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if int(update.effective_user.id) != int(ADMIN_ID):
        await query.answer("❌ Unauthorized", show_alert=True)
        return
    await query.answer()
    
    data_parts = query.data.split("_")

    if data_parts[0] == "coin":
        action = data_parts[1]  # approve or reject
        user_id = int(data_parts[2])
        pending = PENDING_REGISTRATIONS.get(user_id, {})
        logging.info(f"📦 Pending data: {pending}")
        
        if action == "approve":
            coin_amount = pending.get('coin_amount', 1)
            price = pending.get('price', 200)
            
            try:
                # Add coins to database
                current_coins = get_user_coins(user_id)
                new_balance = current_coins + coin_amount
                
                # Update in database
                await asyncio.to_thread(
                    lambda: supabase.table('user_coins')
                        .update({'coin_balance': new_balance})
                        .eq('telegram_id', user_id)
                        .execute()
                )
                
                # Update in-memory
                add_coins(user_id, coin_amount)
                
                # Record transaction
                transaction = {
                    "telegram_id": user_id,
                    "transaction_type": "purchase",
                    "amount": coin_amount,
                    "price_paid": price,
                    "description": f"Purchased {coin_amount} coins for ₦{price:,}"
                }
                
                await asyncio.to_thread(
                    lambda: supabase.table('coin_transactions').insert(transaction).execute()
                )
                
                # Notify user
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"✅ *Coin Purchase Approved!*\n\n"
                        f"💰 +{coin_amount} Blue Coin{'s' if coin_amount > 1 else ''}\n"
                        f"💵 New Balance: {new_balance} coins\n\n"
                        f"Thank you for your purchase! 🎉"
                    ),
                    parse_mode="Markdown"
                )
                
                status_text = f"✅ Approved - Added {coin_amount} coins"
                
            except Exception as e:
                logging.error(f"Coin approval error: {e}")
                status_text = f"❌ Error: {e}"
        
        elif action == "reject":
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "❌ *Coin Purchase Rejected*\n\n"
                    "Your payment was not approved.\n\n"
                    f"Please [contact admin](https://t.me/{ADMIN_USERNAME}) for details."
                ),
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
            status_text = "❌ Rejected"
        
        # ✅ Delete from both in-memory AND database
        try:
            await asyncio.to_thread(
                lambda: supabase.table('pending_registrations')
                    .delete()
                    .eq('user_id', user_id)
                    .execute()
            )
            logging.info(f"✅ Deleted coin purchase from DB for user {user_id}")
        except Exception as e:
            logging.error(f"❌ Failed to delete from DB: {e}")
        
        PENDING_REGISTRATIONS.pop(user_id, None)
        await query.edit_message_caption((query.message.caption or "") + f"\n\n{status_text}")
        return
    
    # Handle ad boost approvals
    if data_parts[0] == "adboost":
        action = data_parts[1]  # approve or reject
        user_id = int(data_parts[2])
        pending = PENDING_REGISTRATIONS.get(user_id, {})
        
        if action == "approve":
            try:
                from datetime import datetime, timedelta, timezone
                
                # Set boost to expire in 7 days
                boost_expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
                
                # Get selected business from pending data
                boost_biz_id = pending.get('business_id')
                boost_biz_name = pending.get('business_name', 'Your business')
                
                if boost_biz_id:
                    # Boost only the selected business
                    await asyncio.to_thread(
                        lambda: supabase.table('businesses')
                            .update({
                                'is_ad_boosted': True,
                                'ad_boost_expires': boost_expires
                            })
                            .eq('id', boost_biz_id)
                            .execute()
                    )
                else:
                    # Legacy fallback: boost all (for old pending requests)
                    await asyncio.to_thread(
                        lambda: supabase.table('businesses')
                            .update({
                                'is_ad_boosted': True,
                                'ad_boost_expires': boost_expires
                            })
                            .eq('telegram_id', str(user_id))
                            .execute()
                    )
                    boost_biz_name = "All businesses"
                
                # Refresh cache
                await asyncio.sleep(1)
                await refresh_cache_from_supabase()
                
                # Notify user
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"🎉 *Ad Boost Activated!*\n\n"
                        f"✨ *{boost_biz_name}* now appears FIRST in searches!\n\n"
                        f"⏰ Active for: 7 days\n"
                        f"📈 Expires on: {boost_expires[:10]}\n\n"
                        f"💡 You'll get priority placement in all search results.\n\n"
                        f"Thank you! 🚀"
                    ),
                    parse_mode="Markdown"
                )
                
                status_text = f"✅ Approved - {boost_biz_name} boosted for 7 days"
                
            except Exception as e:
                logging.error(f"Ad boost approval error: {e}")
                status_text = f"❌ Error: {e}"
        
        elif action == "reject":
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "❌ *Ad Boost Request Rejected*\n\n"
                    "Your payment was not approved.\n\n"
                    f"Please [contact admin](https://t.me/{ADMIN_USERNAME}) for details."
                ),
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
            status_text = "❌ Rejected"
        
        # ✅ Delete from both in-memory AND database
        try:
            await asyncio.to_thread(
                lambda: supabase.table('pending_registrations')
                    .delete()
                    .eq('user_id', user_id)
                    .execute()
            )
            logging.info(f"✅ Deleted coin purchase from DB for user {user_id}")
        except Exception as e:
            logging.error(f"❌ Failed to delete from DB: {e}")
        
        PENDING_REGISTRATIONS.pop(user_id, None)
        await query.edit_message_caption((query.message.caption or "") + f"\n\n{status_text}")
        return
    
    # Handle upgrade requests
    if data_parts[0] == "upgrade":
        action = data_parts[1]  # approve or reject
        user_id = int(data_parts[2])
        
        if action == "approve":
            try:
                # ✅ FIX: Update using snake_case column name in Supabase
                response = await asyncio.to_thread(
                    lambda: supabase.table('businesses')
                        .update({'preferred_listing_package': 'Premium'})  # ← Changed from 'Preferred Listing Package'
                        .eq('telegram_id', str(user_id))  # ← Match as string
                        .execute()
                )
                
                updated_count = len(response.data) if response.data else 0
                
                if updated_count > 0:
                    await asyncio.sleep(2)
                    await refresh_cache_from_supabase()
                    
                    PENDING_REGISTRATIONS.pop(user_id, None)
            
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=(
                            "🎉 *Congratulations!*\n\n"
                            f"Your account has been upgraded to *Premium*!\n\n"
                            f"✨ All {updated_count} of your businesses now have Premium benefits.\n\n"
                            "Thank you! 💎"
                        ),
                        parse_mode="Markdown"
                    )
                    status_text = f"✅ Upgraded ({updated_count} businesses)"
                else:
                    status_text = "⚠️ User not found"
                    
            except Exception as e:
                logging.error(f"❌ Error upgrading user {user_id}: {e}", exc_info=True)
                status_text = f"❌ Error: {str(e)}"
    
        elif action == "reject":
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "❌ *Upgrade Request Rejected*\n\n"
                    "Your premium upgrade was not approved.\n\n"
                    f"📞 For details, [contact admin](https://t.me/{ADMIN_USERNAME})."
                ),
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
            status_text = "❌ Upgrade Rejected"

        try:
            await asyncio.to_thread(
                lambda: supabase.table('pending_registrations')
                    .delete()
                    .eq('user_id', user_id)
                    .execute()
            )
            logging.info(f"✅ Deleted upgrade from DB for user {user_id}")
        except Exception as e:
            logging.error(f"❌ Failed to delete from DB: {e}")
        
        PENDING_REGISTRATIONS.pop(user_id, None)
        logging.info(f"✅ Cleaned up pending upgrade for user {user_id}")
        
        # Edit admin message
        if query.message.photo:
            await query.edit_message_caption(
                (query.message.caption or "") + f"\n\n{status_text}"
            )
        else:
            await query.edit_message_text(
                (query.message.text or "") + f"\n\n{status_text}"
            )
        return
    
    # Handle regular registration approval/rejection
    try:
        action, user_id = data_parts[0], int(data_parts[1])
    except (ValueError, IndexError):
        await query.answer("⚠️ This button is outdated. Please ask the user to re-submit.", show_alert=True)
        return
    business_data = PENDING_REGISTRATIONS.get(user_id)
    
    # Fallback: check database if not in memory (e.g. after bot restart)
    if not business_data:
        try:
            result = await asyncio.to_thread(
                lambda: supabase.table('pending_registrations')
                    .select('data')
                    .eq('user_id', user_id)
                    .execute()
            )
            if result.data:
                business_data = json.loads(result.data[0]['data'])
                PENDING_REGISTRATIONS[user_id] = business_data
                logging.info(f"📦 Recovered pending registration from DB for user {user_id}")
        except Exception as e:
            logging.error(f"❌ DB lookup for pending registration failed: {e}")
    
    if not business_data:
        if query.message.photo or query.message.document:
            await query.edit_message_caption("⚠️ No pending registration found.")
        else:
            await query.edit_message_text("⚠️ No pending registration found.")
        return

    if action == "approve":
        await save_to_sheet_admin(user_id, context)
        tier_text = business_data.get('tier', 'registration').capitalize()
        current_businesses = get_business_count(user_id)
        current_coins = get_user_coins(user_id)

        if current_businesses == 1 and current_coins == INITIAL_BLUE_COINS:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    f"✅ *Registration Approved!*\n\n"
                    f"Your business is now live! 🎉\n\n"
                    f"💰 You've received {INITIAL_BLUE_COINS} Blue Coins!\n\n"
                    f"💡 *What are Blue Coins?*\n"
                    f"• Customers can request your service\n"
                    f"• Each request costs 1 coin\n"
                    f"• Buy more coins when you run out\n\n"
                    f"📊 Current balance: {current_coins} coins"
                    ),
                parse_mode="Markdown"
            )
        else:
            await context.bot.send_message(
            chat_id=user_id,
            text=(
                f"✅ *Registration Approved!*\n\n"
                f"Your business is now live! 🎉\n\n"
                f"📊 Total businesses: {current_businesses}\n"
                f"💰 Blue Coins: {current_coins}"
            ),
            parse_mode="Markdown"
        )
        status_text = "✅ Approved by Admin"

    elif action == "reject":
        await context.bot.send_message(
            chat_id=user_id,
            text=(f"❌ *System Notification:*\n\n"
                  f"Your registration was *REJECTED by admin*.\n\n"
                  f"📩 You may [message the admin](https://t.me/{ADMIN_USERNAME}) "
                  "for clarification, or try registering again."),
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
        status_text = "❌ Rejected by Admin"

    if query.message.photo or query.message.document:
        await query.edit_message_caption((query.message.caption or "") + f"\n\n{status_text}")
    else:
        await query.edit_message_text((query.message.text or "") + f"\n\n{status_text}")

    # Standardize cleanup after decision
    try:
        await asyncio.to_thread(
            lambda: supabase.table('pending_registrations')
                .delete()
                .eq('user_id', user_id)
                .execute()
        )
        logging.info(f"✅ Deleted registration from DB for user {user_id}")
    except Exception as e:
        logging.error(f"❌ Failed to delete from DB: {e}")

    PENDING_REGISTRATIONS.pop(user_id, None)
    return  # ✅ ADDED: Prevent falling through

    # ✅ REMOVED: Stray debug code that caused AttributeError: 'NoneType' object has no attribute 'reply_text'
    # This code was using update.message in a callback context.


async def handle_scam_flag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Flag a business as reported from the admin notification."""
    query = update.callback_query
    if int(update.effective_user.id) != int(ADMIN_ID):
        await query.answer("❌ Unauthorized", show_alert=True)
        return
    await query.answer("Business flagged!")
    
    biz_id = query.data.split("_")[-1]
    
    try:
        # Update database
        await asyncio.to_thread(
            lambda: supabase.table('businesses')
                .update({'is_reported': True})
                .eq('id', biz_id)
                .execute()
        )
        
        # Refresh cache to ensure it's reflected everywhere
        await refresh_cache_from_supabase()
        
        await query.edit_message_text(
            query.message.text + "\n\n✅ *Status: Flagged as Reported*",
            parse_mode='Markdown'
        )
    except Exception as e:
        logging.error(f"Error flagging business: {e}")
        await query.edit_message_text(
            query.message.text + f"\n\n❌ *Error flagging: {e}*",
            parse_mode='Markdown'
        )


async def handle_review_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin confirm/reject of a customer review."""
    query = update.callback_query
    if int(update.effective_user.id) != int(ADMIN_ID):
        await query.answer("❌ Unauthorized", show_alert=True)
        return
    
    data = query.data  # e.g. "rev_approve_123" or "rev_reject_123"
    parts = data.split("_")
    action = parts[1]  # "approve" or "reject"
    review_id = parts[-1]
    
    new_status = "approved" if action == "approve" else "rejected"
    
    try:
        await asyncio.to_thread(
            lambda: supabase.table('business_reviews')
                .update({'status': new_status})
                .eq('id', int(review_id))
                .execute()
        )
        
        if action == "approve":
            await query.answer("✅ Review approved!")
            await query.edit_message_text(
                query.message.text + "\n\n✅ *Status: Review APPROVED*",
                parse_mode='Markdown'
            )
        else:
            await query.answer("❌ Review rejected.")
            await query.edit_message_text(
                query.message.text + "\n\n❌ *Status: Review REJECTED*",
                parse_mode='Markdown'
            )
    except Exception as e:
        logging.error(f"Error processing review decision: {e}")
        await query.edit_message_text(
            query.message.text + f"\n\n❌ *Error: {e}*",
            parse_mode='Markdown'
        )
async def save_to_sheet(update, context, tier):
    """Save business to Supabase (replaces old Google Sheets function)"""
    # Check duplicate before saving
    name = context.user_data.get('reg_buis_name', '')
    phone = context.user_data.get('reg_phone', '')
    telegram_id = context.user_data.get('telegram_id')
    
    if is_duplicate(phone, name, telegram_id):
        await update.message.reply_text("⚠️ This business already exists.")
        return ConversationHandler.END

    # Encrypt phone number
    phone_encrypted = hashlib.sha256(phone.encode()).hexdigest() if phone else None

    # ✅ Insert into Supabase instead of Google Sheets
    data = {
        "full_name": context.user_data.get('reg_name', ''),
        "business_name": context.user_data.get('reg_buis_name', ''),
        "business_services": context.user_data.get('reg_service', ''),
        "business_location": context.user_data.get('reg_location', ''),
        "phone_number": phone,
        "business_description": context.user_data.get('reg_description', ''),
        "preferred_listing_package": tier,
        "telegram_id": str(telegram_id) if telegram_id else None,
        
        # Additional fields
        "phone_number_encrypted": phone_encrypted,
        "payment_confirmation": tier,
        "business_state_location": context.user_data.get('reg_location', '').split('-')[0] if '-' in context.user_data.get('reg_location', '') else None,
        "approved": False  # Initial state
    }

    
    try:
        # Insert into Supabase
        response = await asyncio.to_thread(
            lambda: supabase.table('businesses').insert(data).execute()
        )
        
        # Refresh cache
        await asyncio.sleep(1)
        await refresh_cache_from_supabase()
        
        # Notify admin
        await notify_admin(context, {
            "name": context.user_data.get("reg_name", ""),
            "buis_name": context.user_data.get("reg_buis_name", ""),
            "service": context.user_data.get("reg_service", ""),
            "location": context.user_data.get("reg_location", ""),
            "phone": phone,
            "description": context.user_data.get("reg_description", ""),
            "telegram_id": telegram_id,
            "tier": tier
        })

        # Tell user
        await update.message.reply_text(
            f"✅ Registration completed as *{tier.upper()}* listing!\n\nWhat would you like to do next?",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup([['🔍 Find a Service', '🏁 Start Over']], one_time_keyboard=True, resize_keyboard=True)
        )
        
        logging.info(f"✅ Business saved to Supabase for user {telegram_id}")
        
    except Exception as e:
        logging.error(f"❌ Failed to save to Supabase: {e}")
        await update.message.reply_text(
            "❌ Registration failed. Please try again or contact admin.",
            reply_markup=ReplyKeyboardMarkup([['🏁 Start Over']], one_time_keyboard=True, resize_keyboard=True)
        )
    
    return ConversationHandler.END

async def save_to_sheet_admin(user_id, context):
    """Save business to Supabase and update cache"""
    business_data = PENDING_REGISTRATIONS.get(user_id)
    if not business_data:
        logging.error(f"[ERROR] No pending registration for user {user_id}")
        return

    # ✅ Use snake_case column names for Supabase
    phone = business_data.get('phone', '')
    
    # Encrypt phone number
    phone_encrypted = hashlib.sha256(phone.encode()).hexdigest() if phone else None
    
    # ✅ Extract location parts
    location = business_data.get('location', '')
    state_location = location.split('-')[0].strip() if '-' in location else location
    
    data = {
        "full_name": business_data.get('name', ''),
        "business_name": business_data.get('buis_name', ''),
        "business_services": business_data.get('service', ''),
        "business_location": location,
        "phone_number": phone,
        "business_description": business_data.get('description', ''),
        "preferred_listing_package": business_data.get('tier', 'free'),
        "telegram_id": str(business_data.get('telegram_id', '')),
        "approved": True,
        
        # Additional fields
        "phone_number_encrypted": phone_encrypted,
        "payment_confirmation": business_data.get('tier', 'free'),
        "online_business_category": None,
        "blue_collar_skillset": None,
        "tech_skillset": None,
        "business_state_location": state_location,
        "service_radius": None,
        
        # ✅ NEW: Photo fields
        "photo_1": business_data.get('photo_1'),
        "photo_2": business_data.get('photo_2'),
        "photo_3": business_data.get('photo_3')
    }
    
    try:
        # Insert into Supabase
        response = await asyncio.to_thread(
            lambda: supabase.table('businesses').insert(data).execute()
        )
        
        # Give 5 Blue Coins on FIRST business approval
        current_business_count = get_business_count(user_id)
        current_coins = get_user_coins(user_id)

        # ── Fan out extra services & locations into relational tables ──────────
        # First service/location stays in main businesses table (already written above).
        # Extra ones (2nd, 3rd...) go into business_services / business_locations.
        try:
            new_id = response.data[0]['id'] if response.data else None
            if new_id:
                # Parse comma-separated services — skip the first one (already in businesses table)
                raw_service = business_data.get('service', '') or ''
                all_services = [s.strip() for s in raw_service.split(',') if s.strip()]
                extra_services = all_services[1:]  # 2nd onwards
                service_rows = [{"business_id": new_id, "service_category": s} for s in extra_services]
                if service_rows:
                    await asyncio.to_thread(
                        lambda: supabase.table('business_services').insert(service_rows).execute()
                    )

                # Parse comma-separated locations — skip the first one (already in businesses table)
                raw_location = business_data.get('location', '') or ''
                all_locations = [l.strip() for l in raw_location.split(',') if l.strip()]
                extra_locations = all_locations[1:]  # 2nd onwards
                location_rows = []
                for loc in extra_locations:
                    parts = loc.split('-', 1)
                    state = parts[0].strip() if len(parts) > 1 else None
                    location_rows.append({"business_id": new_id, "location": loc, "state": state})
                if location_rows:
                    await asyncio.to_thread(
                        lambda: supabase.table('business_locations').insert(location_rows).execute()
                    )
                logging.info(f"✅ Extra services: {len(service_rows)}, extra locations: {len(location_rows)} for business {new_id}")
        except Exception as rel_err:
            logging.error(f"⚠️ Could not insert relational tables: {rel_err}")
        # ────────────────────────────────────────────────────────────────

        if current_business_count == 0:  # First business
            # Add coins to database
            coin_data = {
                "telegram_id": user_id,
                "coin_balance": INITIAL_BLUE_COINS,
                "total_coins_purchased": 0,
                "total_coins_spent": 0
            }

            try:
                await asyncio.to_thread(
                    lambda: supabase.table('user_coins').insert(coin_data).execute()
                )
                
                # Update in-memory
                set_user_coins(user_id, INITIAL_BLUE_COINS)
                
                # Log transaction
                transaction = {
                    "telegram_id": user_id,
                    "transaction_type": "bonus",
                    "amount": INITIAL_BLUE_COINS,
                    "description": "Welcome bonus for first business approval"
                }
                
                await asyncio.to_thread(
                    lambda: supabase.table('coin_transactions').insert(transaction).execute()
                )
                
                logging.info(f"✅ Gave {INITIAL_BLUE_COINS} coins to user {user_id} (first business)")
            
            except Exception as coin_error:
                # If coin insert fails (duplicate), just log it and continue
                logging.warning(f"⚠️ Coin insert failed (user may already have coins): {coin_error}")
                # Refresh cache

        await asyncio.sleep(1)
        await refresh_cache_from_supabase()
        
        # Clear pending
        PENDING_REGISTRATIONS.pop(user_id, None)
        
        logging.info(f"✅ Business saved to Supabase for user {user_id}")
        
    except Exception as e:
        logging.error(f"❌ Failed to save to Supabase: {e}")


async def notify_admin(context, business_data):
    """Send notification to admin when a new business registers"""
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            "📢 *New Business Registered!*\n\n"
            f"👤 Name: {business_data.get('name')}\n"
            f"🛠️ Service: {business_data.get('service')}\n"
            f"📍 Location: {business_data.get('location')}\n"
            f"📞 Phone: {business_data.get('phone')}\n"
            f"📄 Description: {business_data.get('description')}\n"
            f"🆔 Telegram ID: {business_data.get('telegram_id')}"
        ),
        parse_mode="Markdown"
    )

# ===========================
# BUY BLUE COINS FLOW
# ===========================

async def buy_coins_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start buying blue coins"""
    user_id = update.effective_user.id
    
    if not user_has_business(user_id):
        await update.message.reply_text(
            "⚠️ You need to register a business first!",
            reply_markup=ReplyKeyboardMarkup(
                [['🏪 Register Your Business', '🔁 Start Over']], 
                one_time_keyboard=True, 
                resize_keyboard=True
            )
        )
        return ConversationHandler.END
    
    if has_pending_coin_purchase(user_id):
        await update.message.reply_text(
            "⏳ *Pending Coin Purchase*\n\n"
            "You already have a coin purchase waiting for admin approval.\n\n"
            f"Please wait or [contact admin](https://t.me/{ADMIN_USERNAME}) for updates.",
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardMarkup([['🔁 Start Over']], one_time_keyboard=True, resize_keyboard=True),
            disable_web_page_preview=True
        )
    
    current_coins = get_user_coins(user_id)
    
    keyboard = [
        ["💰 1 Coin - ₦200"],
        ["💰 5 Coins - ₦900"],
        ["💰 10 Coins - ₦1,600"],
        ["💰 25 Coins - ₦3,500"],
        ["💰 50 Coins - ₦6,000"],
        ["❌ Cancel"]
    ]
    
    await update.message.reply_text(
        f"💎 *Buy Blue Coins*\n\n"
        f"Your current balance: *{current_coins} coins*\n\n"
        f"💡 *What are Blue Coins?*\n"
        f"• Each customer request costs 1 coin\n"
        f"• Buy more to keep receiving leads\n\n"
        f"📦 *Choose a package:*",
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return BUY_COINS


async def buy_coins_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle coin package selection"""
    choice = update.message.text.strip().lower()
    
    if "cancel" in choice:
        return await cancel(update, context)
    
    # Handle back to packages
    if "back to packages" in choice:
        return await buy_coins_start(update, context)
    
    # Parse the choice (check largest first to avoid substring matches)
    if "50 coin" in choice:
        coin_amount = 50
        price = 6000
    elif "25 coin" in choice:
        coin_amount = 25
        price = 3500
    elif "10 coin" in choice:
        coin_amount = 10
        price = 1600
    elif "5 coin" in choice:
        coin_amount = 5
        price = 900
    elif "1 coin" in choice:
        coin_amount = 1
        price = 200
    else:
        await update.message.reply_text(
            "⚠️ Please choose a valid package or tap Cancel.",
            reply_markup=ReplyKeyboardMarkup(
                [
                    ["💰 1 Coin - ₦200"],
                    ["💰 5 Coins - ₦900"],
                    ["💰 10 Coins - ₦1,600"],
                    ["💰 25 Coins - ₦3,500"],
                    ["💰 50 Coins - ₦6,000"],
                    ["❌ Cancel"]
                ],
                one_time_keyboard=True,
                resize_keyboard=True
            )
        )
        return BUY_COINS
    
    # Build payment URL with params
    import urllib.parse
    user_name = update.effective_user.full_name or "Customer"
    encoded_name = urllib.parse.quote(user_name)
    
    pay_url = (
        f"{WEB_APP_URL}/pay?type=coins"
        f"&amount={price}&coins={coin_amount}"
        f"&uid={update.effective_user.id}"
        f"&name={encoded_name}"
    )
    logging.info(f"💰 Generated Coin Payment URL: {pay_url}")
    
    await update.message.reply_text(
        f"💎 *Purchase: {coin_amount} Blue Coin{'s' if coin_amount > 1 else ''}*\n\n"
        f"💰 Amount: ₦{price:,}\n\n"
        f"Tap the button below to pay securely via Paystack 🔒",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 Pay Now", url=pay_url)]
        ])
    )
    await update.message.reply_text(
        "After payment, tap Back to choose another package or Cancel.",
        reply_markup=ReplyKeyboardMarkup(
            [["⬅️ Back to Packages"],
             ["❌ Cancel"]],
            one_time_keyboard=True, resize_keyboard=True
        )
    )
    return BUY_COINS


async def coin_payment_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle coin purchase payment proof"""
    if update.message.photo or update.message.document:
        file_id = update.message.photo[-1].file_id if update.message.photo else update.message.document.file_id
        user_id = update.effective_user.id
        purchase_data = context.user_data.get('coin_purchase', {})
        
        # Store in pending registrations
        pending_data = {
            "type": "coin_purchase",
            "telegram_id": user_id,
            "timestamp": datetime.now().isoformat(),
            "coin_amount": purchase_data.get('amount', 1),
            "price": purchase_data.get('price', 200),
            "proof": file_id
        }
        
        PENDING_REGISTRATIONS[user_id] = pending_data

        try:
            await asyncio.to_thread(
                lambda: supabase.table('pending_registrations')
                    .upsert({
                        'user_id': user_id,
                        'data': json.dumps(pending_data),
                        'type': 'coin_purchase',
                        'created_at': pending_data['timestamp']
                    })
                    .execute()
            )
            logging.info(f"✅ Saved coin purchase to DB for user {user_id}")
        except Exception as e:
            logging.error(f"❌ Failed to save coin purchase: {e}")
        
        # Send to admin for approval
        keyboard = [
            [InlineKeyboardButton("✅ Approve", callback_data=f"coin_approve_{user_id}"),
             InlineKeyboardButton("❌ Reject", callback_data=f"coin_reject_{user_id}")]
        ]

        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=file_id,
            caption=(
                f"💰 *Blue Coin Purchase Request:*\n\n"
                f"🆔 User: {user_id}\n"
                f"💎 Amount: {purchase_data.get('amount')} coins\n"
                f"💵 Price: ₦{purchase_data.get('price'):,}"
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        await update.message.reply_text(
            "⏳ Your payment has been sent to admin for verification.\n\n"
            "You'll receive your coins once approved!",
            reply_markup=ReplyKeyboardMarkup([['🔁 Start Over']], one_time_keyboard=True, resize_keyboard=True)
        )
        return ConversationHandler.END

    elif update.message.text and "cancel" in update.message.text.lower():
        return await cancel(update, context)

    else:
        await update.message.reply_text(
            "⚠️ Please upload an *image or screenshot of the payment*.\n\n"
            "Or click Cancel to start over.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup([['❌ Cancel']], one_time_keyboard=True, resize_keyboard=True)
        )
        return COIN_PAYMENT_PROOF
    
# ===========================
# WEEKLY AD BOOST SYSTEM
# ===========================

async def boost_with_ads_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start weekly ad boost purchase — opens web payment UI with business selection"""
    user_id = update.effective_user.id
    
    # Check if user has businesses
    if not user_has_business(user_id):
        await update.message.reply_text(
            "⚠️ You need to register a business first!",
            reply_markup=ReplyKeyboardMarkup(
                [['🏪 Register Your Business', '🔁 Start Over']], 
                one_time_keyboard=True, 
                resize_keyboard=True
            )
        )
        return ConversationHandler.END
    
    if has_pending_ad_boost(user_id):
        await update.message.reply_text(
            "⏳ *Pending Ad Boost Request*\n\n"
            "You already have an ad boost request waiting for admin approval.\n\n"
            f"Please wait or [contact admin](https://t.me/{ADMIN_USERNAME}) for updates.",
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardMarkup([['🔁 Start Over']], one_time_keyboard=True, resize_keyboard=True),
            disable_web_page_preview=True
        )
        return ConversationHandler.END
    
    # Get user's businesses and check boost status
    businesses = get_user_businesses(user_id)
    cached_businesses = get_cached_businesses()
    
    # Build status lines for currently boosted businesses
    boosted_lines = ""
    has_available = False
    for biz in businesses:
        biz_id = biz.get('id')
        biz_name = biz.get('business_name', 'Unknown')
        is_boosted = False
        
        for b in cached_businesses:
            if b.get('id') == biz_id and b.get('is_ad_boosted'):
                is_boosted = True
                boost_expires = b.get('ad_boost_expires')
                days_left = "?"
                if boost_expires:
                    try:
                        expires_dt = datetime.fromisoformat(boost_expires.replace('Z', '+00:00'))
                        days_left = max(0, (expires_dt - datetime.now()).days)
                    except:
                        pass
                boosted_lines += f"\n  ✅ {biz_name} — {days_left} day(s) left"
                break
        
        if not is_boosted:
            has_available = True
            boosted_lines += f"\n  ⬜ {biz_name} — Not boosted"
    
    if not has_available:
        await update.message.reply_text(
            f"✨ *All Your Businesses Are Already Boosted!*\n"
            f"{boosted_lines}\n\n"
            f"You can renew after a boost expires.",
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardMarkup(
                [['🔁 Start Over']], 
                one_time_keyboard=True, 
                resize_keyboard=True
            )
        )
        return ConversationHandler.END
    
    # Store user_id for payment proof handler
    context.user_data['ad_boost_user_id'] = user_id
    
    # Build payment URL — business selection happens in the web UI
    import urllib.parse
    user_name = update.effective_user.full_name or "Customer"
    encoded_name = urllib.parse.quote(user_name)
    
    pay_url = (
        f"{WEB_APP_URL}/pay?type=boost"
        f"&amount=2000"
        f"&uid={user_id}"
        f"&name={encoded_name}"
    )
    logging.info(f"💰 Generated Boost Payment URL: {pay_url}")
    
    await update.message.reply_text(
        f"📢 *Boost Your Business with Weekly Ads!*\n\n"
        f"📋 *Your Businesses:*{boosted_lines}\n\n"
        f"✨ *What You Get:*\n"
        f"• Selected business appears FIRST in searches\n"
        f"• Priority placement in search results\n"
        f"• Get 3-5x more customer requests\n"
        f"• Active for 7 full days\n\n"
        f"💰 *Price: ₦2,000/business/week*\n\n"
        f"Tap Pay Now to select a business and pay 🔒",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("💳 Pay Now", web_app=WebAppInfo(url=pay_url))],
             ["❌ Cancel"]],
            one_time_keyboard=True, resize_keyboard=True
        )
    )
    return ConversationHandler.END

async def ad_boost_payment_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle ad boost payment proof"""
    if update.message.photo or update.message.document:
        file_id = update.message.photo[-1].file_id if update.message.photo else update.message.document.file_id
        user_id = context.user_data.get('ad_boost_user_id')
        boost_biz_id = context.user_data.get('boost_business_id')
        boost_biz_name = context.user_data.get('boost_business_name', 'Unknown')
        
        # Store in pending registrations
        pending_data = {
            "type": "ad_boost",
            "telegram_id": user_id,
            "business_id": boost_biz_id,
            "business_name": boost_biz_name,
            "timestamp": datetime.now().isoformat(),
            "price": 2000,
            "proof": file_id
        }
        
        PENDING_REGISTRATIONS[user_id] = pending_data
        
        # ✅ Persist to database
        try:
            await asyncio.to_thread(
                lambda: supabase.table('pending_registrations')
                    .upsert({
                        'user_id': user_id,
                        'data': json.dumps(pending_data),
                        'type': 'ad_boost',
                        'created_at': pending_data['timestamp']
                    })
                    .execute()
            )
            logging.info(f"✅ Saved ad boost to DB for user {user_id}, biz: {boost_biz_name}")
        except Exception as e:
            logging.error(f"❌ Failed to save ad boost: {e}")
        
        # Send to admin for approval
        keyboard = [
            [InlineKeyboardButton("✅ Approve Ad Boost", callback_data=f"adboost_approve_{user_id}"),
             InlineKeyboardButton("❌ Reject", callback_data=f"adboost_reject_{user_id}")]
        ]

        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=file_id,
            caption=(
                f"📢 *Weekly Ad Boost Request:*\n\n"
                f"🆔 User: {user_id}\n"
                f"💼 Business: {boost_biz_name}\n"
                f"💵 Price: ₦2,000/week\n\n"
                f"This business will appear FIRST in searches for 7 days."
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        await update.message.reply_text(
            "⏳ Your ad boost request has been sent to admin for verification.\n\n"
            "You'll be notified once approved!",
            reply_markup=ReplyKeyboardMarkup([['🔁 Start Over']], one_time_keyboard=True, resize_keyboard=True)
        )
        return ConversationHandler.END

    elif update.message.text and "cancel" in update.message.text.lower():
        return await cancel(update, context)

    else:
        await update.message.reply_text(
            "⚠️ Please upload an *image or screenshot of the payment*.\n\n"
            "Or click Cancel to start over.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup([['❌ Cancel']], one_time_keyboard=True, resize_keyboard=True)
        )
        return AD_BOOST_PAYMENT
    
# ===========================
# AUTO-EXPIRY BACKGROUND JOB
# ===========================

# First (basic) check_expired_ad_boosts removed — enhanced version with 1-day warning is below


async def start_expiry_check_loop(application):
    """Check expired ad boosts daily at midnight"""
    from datetime import datetime, timedelta
    
    while True:
        try:
            now = datetime.now()
            tomorrow = now + timedelta(days=1)
            midnight = datetime.combine(tomorrow.date(), datetime.min.time())
            seconds_until_midnight = (midnight - now).total_seconds()
            
            logging.info(f"⏰ Next expiry check at midnight ({seconds_until_midnight/3600:.1f} hours)")
            
            # Wait until midnight
            await asyncio.sleep(seconds_until_midnight)
            
            # Run expiry check
            await check_expired_ad_boosts(application)
            
        except Exception as e:
            logging.error(f"❌ Expiry check loop error: {e}")
            await asyncio.sleep(3600)




async def check_expired_ad_boosts(application):
    """Check and disable expired ad boosts + warn users 1 day before"""
    
    try:
        logging.info("🔍 Checking for expired ad boosts...")
        
        # Get all boosted businesses
        response = await asyncio.to_thread(
            lambda: supabase.table('businesses')
                .select('*')
                .eq('is_ad_boosted', True)
                .execute()
        )
        
        boosted_businesses = response.data
        expired_count = 0
        warned_count = 0
        
        for business in boosted_businesses:
            boost_expires = business.get('ad_boost_expires')
            
            if not boost_expires:
                await asyncio.to_thread(
                    lambda bid=business['id']: supabase.table('businesses')
                        .update({'is_ad_boosted': False})
                        .eq('id', bid)
                        .execute()
                )
                expired_count += 1
                continue
            
            try:
                expires_dt = datetime.fromisoformat(boost_expires.replace('Z', '+00:00'))
                now = datetime.now()
                time_left = expires_dt - now
                
                telegram_id = business.get('telegram_id')
                business_name = business.get('business_name', 'Your business')
                
                # Check if expired
                if now >= expires_dt:
                    # Disable the boost
                    await asyncio.to_thread(
                        lambda bid=business['id']: supabase.table('businesses')
                            .update({'is_ad_boosted': False})
                            .eq('id', bid)
                            .execute()
                    )
                    
                    # Notify expiry
                    if telegram_id:
                        try:
                            await application.bot.send_message(
                                chat_id=int(telegram_id),
                                text=(
                                    f"⏰ *Ad Boost Expired*\n\n"
                                    f"Your weekly ad boost has ended.\n\n"
                                    f"Business: {business_name}\n\n"
                                    f"💡 Want to renew? Tap '📢 Boost with Ads' anytime!"
                                ),
                                parse_mode='Markdown'
                            )
                        except Exception as notify_error:
                            logging.warning(f"Could not notify user {telegram_id}: {notify_error}")
                    
                    expired_count += 1
                    logging.info(f"⏰ Disabled expired boost: {business_name} (ID: {business['id']})")
                
                # ✅ NEW: Warn if expiring in 1 day
                elif timedelta(hours=20) <= time_left <= timedelta(hours=28):
                    if telegram_id:
                        try:
                            await application.bot.send_message(
                                chat_id=int(telegram_id),
                                text=(
                                    f"⚠️ *Ad Boost Expiring Soon*\n\n"
                                    f"Your weekly ad boost expires in ~1 day.\n\n"
                                    f"Business: {business_name}\n\n"
                                    f"💡 Renew now? Tap '📢 Boost with Ads'"
                                ),
                                parse_mode='Markdown'
                            )
                            warned_count += 1
                        except Exception as notify_error:
                            logging.warning(f"Could not warn user {telegram_id}: {notify_error}")
                
            except Exception as e:
                logging.error(f"Error processing boost expiry for business {business['id']}: {e}")
        
        if expired_count > 0 or warned_count > 0:
            # Refresh cache after changes
            await refresh_cache_from_supabase()
            logging.info(f"✅ Expired: {expired_count}, Warned: {warned_count}")
        else:
            logging.info("✅ No expired ad boosts found")
        
    except Exception as e:
        logging.error(f"❌ Auto-expiry check failed: {e}")


async def force_expiry_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to manually trigger expiry check"""
    if int(update.effective_user.id) != int(ADMIN_ID):
        await update.message.reply_text("❌ Unauthorized")
        return
    
    await update.message.reply_text("🔍 Running expiry check...")
    await check_expired_ad_boosts(context.application)
    await update.message.reply_text("✅ Expiry check complete! Check logs for details.")

# ===========================
# AUTO-APPROVE PENDING REQUESTS (24 HOURS)
# ===========================
async def start_auto_approve_loop(application):
    """Run auto-approvals every 6 hours"""
    while True:
        try:
            logging.info("🤖 Running auto-approval check...")
            await auto_approve_pending_requests(application)
            
            logging.info("⏰ Next auto-approval check in 6 hours")
            await asyncio.sleep(21600)  # 6 hours
            
        except Exception as e:
            logging.error(f"❌ Auto-approve loop error: {e}")
            await asyncio.sleep(3600)

async def force_auto_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to manually trigger auto-approve"""
    if int(update.effective_user.id) != int(ADMIN_ID):
        await update.message.reply_text("❌ Unauthorized")
        return
    
    await update.message.reply_text("🤖 Running auto-approve check...")
    await auto_approve_pending_requests(context.application)
    await update.message.reply_text("✅ Auto-approve check complete!")

async def auto_approve_pending_requests(application):
    """Auto-approve pending requests after 24 hours"""
    from datetime import datetime, timedelta
    
    try:
        logging.info("🔍 Checking for pending requests to auto-approve...")
        
        now = datetime.now()
        cutoff_time = now - timedelta(hours=24)  # ✅ FIXED: Was minutes=1
        approved_count = 0
        
        # Get all pending entries (make a copy to avoid modification during iteration)
        pending_items = list(PENDING_REGISTRATIONS.items())
        
        for key, pending_data in pending_items:
            timestamp_str = pending_data.get('timestamp')
            
            if not timestamp_str:
                # Old entry without timestamp - auto-approve it (it's definitely old)
                logging.warning(f"⚠️ Found pending entry without timestamp: {key} - auto-approving")
                pending_type = pending_data.get('type')
                
                if pending_type == 'coin_purchase':
                    await auto_approve_coin_purchase(application, key, pending_data)
                    approved_count += 1
                elif pending_type == 'ad_boost':
                    await auto_approve_ad_boost(application, key, pending_data)
                    approved_count += 1
                elif pending_type == 'registration':
                    await auto_approve_registration(application, key, pending_data)
                    approved_count += 1
                continue
            
            try:
                timestamp = datetime.fromisoformat(timestamp_str)
                
                # Check if older than 24 hours
                if timestamp < cutoff_time:
                    pending_type = pending_data.get('type')
                    user_id = pending_data.get('telegram_id')
                    
                    hours_pending = (now - timestamp).total_seconds() / 3600
                    logging.info(f"⏰ Auto-approving {pending_type} for user {user_id} (pending for {hours_pending:.1f} hours)")
                    
                    # Auto-approve based on type
                    if pending_type == 'coin_purchase':
                        await auto_approve_coin_purchase(application, key, pending_data)
                        approved_count += 1
                        
                    elif pending_type == 'ad_boost':
                        await auto_approve_ad_boost(application, key, pending_data)
                        approved_count += 1
                        
                    elif pending_type == 'registration':
                        await auto_approve_registration(application, key, pending_data)
                        approved_count += 1
                
            except Exception as e:
                logging.error(f"Error auto-approving {key}: {e}")
        
        if approved_count > 0:
            logging.info(f"✅ Auto-approved {approved_count} pending request(s)")
        else:
            logging.info("✅ No pending requests to auto-approve")
        
    except Exception as e:
        logging.error(f"❌ Auto-approval check failed: {e}")



async def auto_approve_coin_purchase(application, key, pending_data):
    """Auto-approve a coin purchase"""
    user_id = pending_data.get('telegram_id')
    coin_amount = pending_data.get('coin_amount', 1)
    price = pending_data.get('price', 200)
    
    try:
        # Add coins to database
        current_coins = get_user_coins(user_id)
        new_balance = current_coins + coin_amount
        
        await asyncio.to_thread(
            lambda: supabase.table('user_coins')
                .update({'coin_balance': new_balance})
                .eq('telegram_id', user_id)
                .execute()
        )
        
        # Update in-memory
        add_coins(user_id, coin_amount)
        
        # Record transaction
        transaction = {
            "telegram_id": user_id,
            "transaction_type": "purchase",
            "amount": coin_amount,
            "price_paid": price,
            "description": f"Auto-approved: Purchased {coin_amount} coins"
        }
        
        await asyncio.to_thread(
            lambda: supabase.table('coin_transactions').insert(transaction).execute()
        )
        
        # Notify user
        await application.bot.send_message(
            chat_id=user_id,
            text=(
                f"✅ *Coin Purchase Auto-Approved!*\n\n"
                f"💰 +{coin_amount} Blue Coin{'s' if coin_amount > 1 else ''}\n"
                f"💵 New Balance: {new_balance} coins\n\n"
                f"Your payment was automatically verified after 24 hours.\n"
                f"Thank you! 🎉"
            ),
            parse_mode="Markdown"
        )
        
        # Notify admin
        await application.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"🤖 *Auto-Approved Coin Purchase*\n\n"
                f"User: {user_id}\n"
                f"Amount: {coin_amount} coins (₦{price})\n"
                f"Reason: Pending for 24+ hours"
            ),
            parse_mode="Markdown"
        )
        
        # ✅ FIXED: Delete from database BEFORE removing from memory
        try:
            await asyncio.to_thread(
                lambda: supabase.table('pending_registrations')
                    .delete()
                    .eq('user_id', user_id)
                    .execute()
            )
            logging.info(f"✅ Deleted coin purchase from DB for user {user_id}")
        except Exception as db_error:
            logging.error(f"❌ Failed to delete from DB: {db_error}")
        
        # Remove from pending (in-memory)
        PENDING_REGISTRATIONS.pop(key, None)
        
    except Exception as e:
        logging.error(f"Failed to auto-approve coin purchase for {user_id}: {e}")


async def auto_approve_ad_boost(application, key, pending_data):
    """Auto-approve an ad boost — per-business"""
    user_id = pending_data.get('telegram_id')
    
    try:
        from datetime import timedelta, timezone
        
        # Set boost to expire in 7 days
        boost_expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        
        # Get selected business from pending data
        boost_biz_id = pending_data.get('business_id')
        boost_biz_name = pending_data.get('business_name', 'Your business')
        
        if boost_biz_id:
            # Boost only the selected business
            await asyncio.to_thread(
                lambda: supabase.table('businesses')
                    .update({
                        'is_ad_boosted': True,
                        'ad_boost_expires': boost_expires
                    })
                    .eq('id', boost_biz_id)
                    .execute()
            )
        else:
            # Legacy fallback: boost all
            await asyncio.to_thread(
                lambda: supabase.table('businesses')
                    .update({
                        'is_ad_boosted': True,
                        'ad_boost_expires': boost_expires
                    })
                    .eq('telegram_id', str(user_id))
                    .execute()
            )
            boost_biz_name = "All businesses"
        
        # Refresh cache
        await asyncio.sleep(1)
        await refresh_cache_from_supabase()
        
        # Notify user
        await application.bot.send_message(
            chat_id=user_id,
            text=(
                f"🎉 *Ad Boost Auto-Approved!*\n\n"
                f"✨ *{boost_biz_name}* now appears FIRST in searches!\n\n"
                f"⏰ Active for: 7 days\n"
                f"📈 Expires on: {boost_expires[:10]}\n\n"
                f"Your payment was automatically verified after 24 hours.\n"
                f"Thank you! 🚀"
            ),
            parse_mode="Markdown"
        )
        
        # Notify admin
        await application.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"🤖 *Auto-Approved Ad Boost*\n\n"
                f"User: {user_id}\n"
                f"Business: {boost_biz_name}\n"
                f"Reason: Pending for 24+ hours"
            ),
            parse_mode="Markdown"
        )
        
        # Remove from pending
        PENDING_REGISTRATIONS.pop(key, None)
        
    except Exception as e:
        logging.error(f"Failed to auto-approve ad boost for {user_id}: {e}")


async def auto_approve_registration(application, key, pending_data):
    """Auto-approve a business registration"""
    user_id = pending_data.get('telegram_id')
    
    try:
        # Use existing save function
        await save_to_sheet_admin(user_id, application)
        
        # Notify user
        tier_text = pending_data.get('tier', 'registration').capitalize()
        business_count = get_business_count(user_id)
        current_coins = get_user_coins(user_id)
        
        if business_count == 1 and current_coins == INITIAL_BLUE_COINS:
            await application.bot.send_message(
                chat_id=user_id,
                text=(
                    f"✅ *Registration Auto-Approved!*\n\n"
                    f"Your business is now live! 🎉\n\n"
                    f"💰 You've received {INITIAL_BLUE_COINS} Blue Coins!\n\n"
                    f"Your registration was automatically verified after 24 hours."
                ),
                parse_mode="Markdown"
            )
        else:
            await application.bot.send_message(
                chat_id=user_id,
                text=(
                    f"✅ *Registration Auto-Approved!*\n\n"
                    f"Your business is now live! 🎉\n\n"
                    f"Your registration was automatically verified after 24 hours."
                ),
                parse_mode="Markdown"
            )
        
        # Notify admin
        await application.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"🤖 *Auto-Approved Registration*\n\n"
                f"User: {user_id}\n"
                f"Business: {pending_data.get('buis_name', 'Unknown')}\n"
                f"Reason: Pending for 24+ hours"
            ),
            parse_mode="Markdown"
        )
        
        # Remove from pending
        PENDING_REGISTRATIONS.pop(key, None)
        
    except Exception as e:
        logging.error(f"Failed to auto-approve registration for {user_id}: {e}")

# ===========================
# REQUEST SERVICE HANDLER (Combined: WhatsApp + Coin Deduction)
# ===========================

async def handle_request_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle when customer clicks 'Request Service' - sends WhatsApp link AND deducts coin"""
    query = update.callback_query
    await query.answer()
    
    # Parse callback data: request_<business_id>_<phone>
    data_parts = query.data.split("_")
    business_id = int(data_parts[1])
    phone = data_parts[2]
    customer_id = query.from_user.id
    
    # Get business details
    cached_businesses = get_cached_businesses()
    business = None
    for b in cached_businesses:
        if b.get('id') == business_id:
            business = b
            break
    
    if not business:
        await query.message.reply_text("❌ Business not found")
        return
    
    business_owner_id = business.get('telegram_id')
    business_name = business.get('business_name')
    service = business.get('business_services')
    location = business.get('business_location')
    phone = business.get('phone_number', '')

    # ✅ FIX: Handle legacy businesses (registered before Telegram ID was added)
    if not business_owner_id:
        # Legacy business - just show contact info, no coin system
        logging.info(f"ℹ️ Legacy business request: {business_name} (ID: {business_id})")
        
        # Format phone for WhatsApp
        if phone:
            if phone.startswith('0'):
                phone = '234' + phone[1:] 
            elif phone.startswith('+'):
                phone = phone[1:]
            elif not phone.startswith('234'):
                phone = '234' + phone
            
            # Create pre-filled WhatsApp message
            import urllib.parse
            auto_message = (
                f"Hello! 👋\n\n"
                f"I found your business *{business_name}* on BlueLink Bot.\n\n"
                f"I'm interested in your *{service}* services in *{location}*.\n\n"
                f"Are you available to help me?\n\n"
                f"Thank you!"
            )
            
            encoded_msg = urllib.parse.quote(auto_message)
            wa_link = f"https://wa.me/{phone}?text={encoded_msg}"
            
            await query.message.reply_text(
                f"📞 *{business_name}*\n\n"
                f"This is a legacy listing (registered before our coin system).\n\n"
                f"You can contact them directly:\n\n"
                f"[Click here to chat on WhatsApp]({wa_link})\n\n"
                f"💡 New businesses use our Blue Coin system for verified leads!",
                parse_mode='Markdown',
                disable_web_page_preview=False
            )
            return
        else:
            await query.message.reply_text(
                f"⚠️ *{business_name}*\n\n"
                f"This is a legacy listing without contact information.\n\n"
                f"Please try another business or contact admin.",
                parse_mode='Markdown'
            )
            return
    try:
        owner_coins = get_user_coins(int(business_owner_id))
    except (ValueError, TypeError):
        await query.message.reply_text(
            "⚠️ This business has an invalid owner ID.\n"
            "Please contact admin or try another business."
        )
        logging.error(f"❌ Business {business_id} has invalid telegram_id: {business_owner_id}")
        return

    if owner_coins <= 0:
        await query.message.reply_text(
            "⚠️ This business is currently out of Blue Coins.\n"
            "They cannot receive new requests right now."
        )
        return
        
    # Anti-spam check (7-day cooldown per customer per business)
    can_request, message = can_request_service(customer_id, business_id)
    if not can_request:
        await query.answer(message, show_alert=True)
        return
    
    # Deduct coin from business owner
    success = deduct_coin(int(business_owner_id))
    
    if not success:
        await query.message.reply_text(
            "⚠️ Failed to process request. Please try again."
        )
        return
    
    # Update database
    try:
        # Update coin balance in database
        new_balance = get_user_coins(int(business_owner_id))
        await asyncio.to_thread(
            lambda: supabase.table('user_coins')
                .update({'coin_balance': new_balance})
                .eq('telegram_id', int(business_owner_id))
                .execute()
        )
        
        # Record the lead request
        lead_data = {
            "customer_telegram_id": customer_id,
            "business_id": business_id,
            "business_owner_id": int(business_owner_id),
            "request_type": "paid_request",
            "coin_deducted": True,
            "status": "verified"
        }
        
        await asyncio.to_thread(
            lambda: supabase.table('lead_requests').insert(lead_data).execute()
        )
        
        # Record transaction
        transaction = {
            "telegram_id": int(business_owner_id),
            "transaction_type": "deduction",
            "amount": -1,
            "description": f"Lead request for {business_name}"
        }
        
        await asyncio.to_thread(
            lambda: supabase.table('coin_transactions').insert(transaction).execute()
        )
        
        # Record for anti-spam
        record_service_request(customer_id, business_id)
        
        # ✅ CREATE PRE-FILLED WHATSAPP MESSAGE
        import urllib.parse
        auto_message = (
            f"Hello! 👋\n\n"
            f"I found your business *{business_name}* on BlueLink Bot.\n\n"
            f"I'm interested in your *{service}* services in *{location}*.\n\n"
            f"Are you available to help me?\n\n"
            f"Thank you!"
        )
        
        encoded_msg = urllib.parse.quote(auto_message)
        wa_link = f"https://wa.me/{phone}?text={encoded_msg}"
        
        # Notify business owner
        customer_name = query.from_user.first_name or "Customer"
        customer_username = f"@{query.from_user.username}" if query.from_user.username else "No username"
        
        await context.bot.send_message(
            chat_id=int(business_owner_id),
            text=(
                f"🔔 *New Verified Lead!*\n\n"
                f"Customer: {customer_name} ({customer_username})\n"
                f"Business: {business_name}\n"
                f"Service: {service}\n\n"
                f"💰 1 Blue Coin deducted\n"
                f"💵 Remaining: {new_balance} coins\n\n"
                f"📞 Contact: [Message Customer](tg://user?id={customer_id})"
            ),
            parse_mode='Markdown'
        )
        
        # ✅ SEND WHATSAPP LINK TO CUSTOMER
        photos = [business.get('photo_1'), business.get('photo_2'), business.get('photo_3')]
        photos = [p for p in photos if p]  # Remove None values

        if photos:
            # Send photos first
            media_group = []
            for i, photo in enumerate(photos):
                if i == 0:
                    # First photo gets the caption
                    caption = (
                        f"📸 *{business_name}*\n\n"
                        f"🛠️ {service}\n"
                        f"📍 {location}\n\n"
                        f"📝 {business.get('business_description', 'N/A')}"
                    )
                    await query.message.reply_photo(
                        photo=photo,
                        caption=caption,
                        parse_mode='Markdown'
                    )
                else:
                    # Additional photos without caption
                    await query.message.reply_photo(photo=photo)
            
            # Then send WhatsApp link
            await query.message.reply_text(
                f"✅ *Request Sent!*\n\n"
                f"💬 Opening WhatsApp with a pre-filled message...\n"
                f"Just tap Send when it opens!\n\n"
                f"[Click here to chat on WhatsApp]({wa_link})\n\n"
                f"⏳ You can request this business again in 7 days.",
                parse_mode='Markdown',
                disable_web_page_preview=False
            )
        else:
            # No photos - just send text and link
            await query.message.reply_text(
                f"✅ *Request Sent!*\n\n"
                f"💬 Opening WhatsApp with a pre-filled message...\n"
                f"Just tap Send when it opens!\n\n"
                f"Business: {business_name}\n"
                f"Service: {service}\n\n"
                f"[Click here to chat on WhatsApp]({wa_link})\n\n"
                f"⏳ You can request this business again in 7 days.",
                parse_mode='Markdown',
                disable_web_page_preview=False
            )
        
        logging.info(f"✅ Lead request + WhatsApp: Customer {customer_id} → Business {business_id} (Owner: {business_owner_id})")
        
    except Exception as e:
        logging.error(f"❌ Request service error: {e}")
        await query.message.reply_text(
            "❌ Something went wrong. Please try again."
        )


# ===========================
# WHATSAPP LINK CLICK HANDLER
# ===========================

async def handle_whatsapp_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle when customer clicks WhatsApp link"""
    query = update.callback_query
    await query.answer()
    
    # Parse callback data: wa_click_<business_id>_<phone>
    data_parts = query.data.split("_")
    business_id = int(data_parts[2])
    phone = data_parts[3]
    customer_id = query.from_user.id
    
    # Get business details
    cached_businesses = get_cached_businesses()
    business = None
    for b in cached_businesses:
        if b.get('id') == business_id:
            business = b
            break
    
    if not business:
        await query.message.reply_text("❌ Business not found")
        return
    
    business_owner_id = business.get('telegram_id')
    business_name = business.get('business_name')
    service = business.get('business_services')
    
    # Anti-spam check
    can_click, message = can_click_business_link(customer_id, business_id)
    if not can_click:
        await query.answer(message, show_alert=True)
        return
    
    # Record the click in database
    try:
        link_click_data = {
            "customer_telegram_id": customer_id,
            "business_id": business_id,
            "business_owner_id": int(business_owner_id),
            "link_type": "whatsapp"
        }
        
        await asyncio.to_thread(
            lambda: supabase.table('link_clicks').insert(link_click_data).execute()
        )
        
        # Record for anti-spam
        record_link_click(customer_id, business_id)
        
        # Create pre-filled WhatsApp message
        import urllib.parse
        auto_message = (
            f"Hello! 👋\n\n"
            f"I found your business *{business_name}* on BlueLink Bot.\n\n"
            f"I'm interested in your *{service}* services.\n\n"
            f"Are you available to help me?\n\n"
            f"Thank you!"
        )
        
        encoded_msg = urllib.parse.quote(auto_message)
        wa_link = f"https://wa.me/{phone}?text={encoded_msg}"
        
        # Notify business owner (NO coin deduction for link clicks)
        await context.bot.send_message(
            chat_id=int(business_owner_id),
            text=(
                f"👀 *Someone viewed your contact!*\n\n"
                f"Customer clicked your WhatsApp link.\n"
                f"Business: {business_name}\n\n"
                f"💡 No coins deducted for link clicks.\n"
                f"🔔 They may message you soon!"
            ),
            parse_mode='Markdown'
        )
        
        # Send link to customer
        await query.message.reply_text(
            f"✅ Opening WhatsApp...\n\n"
            f"💬 A message is pre-filled for you!\n"
            f"Just tap Send when WhatsApp opens.\n\n"
            f"[Click here to chat with {business_name}]({wa_link})",
            parse_mode='Markdown',
            disable_web_page_preview=False
        )
        
        logging.info(f"✅ WhatsApp link click: Customer {customer_id} → Business {business_id}")
        
    except Exception as e:
        logging.error(f"❌ WhatsApp link click error: {e}")
        await query.message.reply_text(
            "❌ Something went wrong. Please try again."
        )

async def debug_cache(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to debug cache contents"""
    if int(update.effective_user.id) != int(ADMIN_ID):
        await update.message.reply_text("❌ Unauthorized")
        return
    
    cached = get_cached_businesses()
    
    if not cached:
        await update.message.reply_text(
            "⚠️ Cache is EMPTY!\n\n"
            "Try: /refresh"
        )
        return
    
    # Get unique services
    services = set()
    for row in cached:
        service = get_row_value(row, 'Business Services') or ''
        if service:
            services.add(service.strip())
    
    # Sample data
    sample = cached[0] if cached else {}
    
    msg = (
        f"📊 *Cache Debug Info:*\n\n"
        f"Total businesses: {len(cached)}\n"
        f"Unique services: {len(services)}\n\n"
        f"*Sample Business:*\n"
        f"```\n{json.dumps(sample, indent=2)}\n```\n\n"
        f"*All Services:*\n"
        f"{chr(10).join(['• ' + s for s in sorted(services)[:20]])}"
    )
    
    await update.message.reply_text(msg, parse_mode='Markdown')

async def list_pending_registrations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to list and manage pending registrations"""
    if int(update.effective_user.id) != int(ADMIN_ID):
        await update.message.reply_text("❌ Unauthorized")
        return

    await update.message.reply_text("🔍 Fetching pending registrations...")
    
    # 1. Sync PENDING_REGISTRATIONS with Database
    try:
        result = await asyncio.to_thread(
            lambda: supabase.table('pending_registrations').select('*').execute()
        )
        if result.data:
            for item in result.data:
                u_id = int(item['user_id'])
                if u_id not in PENDING_REGISTRATIONS:
                    try:
                        PENDING_REGISTRATIONS[u_id] = json.loads(item['data'])
                        logging.info(f"📦 Recovered {u_id} from DB into memory")
                    except Exception as e:
                        logging.error(f"Failed to parse data for {u_id}: {e}")
    except Exception as e:
        logging.error(f"Failed to sync pending registrations: {e}")

    if not PENDING_REGISTRATIONS:
        await update.message.reply_text("✅ No pending registrations found.")
        return

    count = 0
    for user_id, data in PENDING_REGISTRATIONS.items():
        count += 1
        p_type = data.get('type', 'registration')
        
        # Helper to escape markdown
        def esc(text):
            if not text: return ""
            return str(text).replace("*", "\\*").replace("_", "\\_").replace("`", "\\`").replace("[", "\\[")

        # Build caption based on type
        if p_type == 'registration':
            # Truncate description if needed (Telegram caption limit is 1024)
            desc = data.get('description', '')
            if len(desc) > 500:
                desc = desc[:497] + "..."
                
            caption = (
                f"📢 *Pending Registration:*\n\n"
                f"👤 {esc(data.get('name'))}\n"
                f"🏪 {esc(data.get('buis_name'))}\n"
                f"🛠 {esc(data.get('service'))}\n"
                f"📍 {esc(data.get('location'))}\n"
                f"📞 {esc(data.get('phone'))}\n"
                f"📝 {esc(desc)}\n"
                f"🆔 Telegram ID: {user_id}"
            )
            keyboard = [
                [InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject_{user_id}")]
            ]
        elif p_type == 'upgrade':
            caption = (
                f"📢 *Pending Upgrade:*\n\n"
                f"🏪 Business: {esc(data.get('business_name'))}\n"
                f"🆔 Telegram ID: {user_id}\n"
                f"📊 Requested Tier: Premium"
            )
            keyboard = [
                [InlineKeyboardButton("✅ Approve Upgrade", callback_data=f"upgrade_approve_{user_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"upgrade_reject_{user_id}")]
            ]
        elif p_type == 'coin_purchase':
            caption = (
                f"📢 *Pending Coin Purchase:*\n\n"
                f"💰 Amount: {data.get('coin_amount')} coins\n"
                f"💵 Price: ₦{data.get('price', 0):,}\n"
                f"🆔 Telegram ID: {user_id}"
            )
            keyboard = [
                [InlineKeyboardButton("✅ Approve", callback_data=f"coin_approve_{user_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"coin_reject_{user_id}")]
            ]
        else:
            # Generic/Other
            caption = f"📢 *Pending request ({esc(p_type)})* for ID: {user_id}"
            keyboard = []

        # Send to admin
        try:
            photo_id = data.get('photo_1') or data.get('proof')
            if photo_id:
                await context.bot.send_photo(
                    chat_id=ADMIN_ID,
                    photo=photo_id,
                    caption=caption,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
                )
            else:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=caption,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
                )
        except Exception as e:
            logging.error(f"Failed to send pending item for {user_id}: {e}")
            await update.message.reply_text(f"⚠️ Error sending item for {user_id}: {e}")

    await update.message.reply_text(f"🏁 Listed {count} pending item(s).")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # ✅ FIX: Only clear context data, NOT pending registrations
    # Pending registrations should only be cleared when:
    # 1. User completes registration flow
    # 2. Admin approves/rejects it
    # NOT when user cancels unrelated flows like Find Service
    
    context.user_data.clear()

    keyboard = [['🔁 Start Over']]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)

    await update.message.reply_text(
        "❌ Cancelled. Restarting...\n\n"
        "You can tap 🔁 Start Over to begin again.",
        reply_markup=reply_markup
    )
    return ConversationHandler.END

async def warn_active_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Warn user that they are in an active conversation"""
    await update.message.reply_text(
        "⚠️ You are currently in a process.\n"
        "Please finish it or type /cancel to stop before starting a new action."
    )
    # Stay in current state (implicit return None)

async def cancel_and_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel current conversation and run /start"""
    context.user_data.clear()
    await start(update, context)
    return ConversationHandler.END

async def force_refresh_cache(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to manually refresh cache"""
    if int(update.effective_user.id) != int(ADMIN_ID):
        await update.message.reply_text("❌ Unauthorized")
        return
    
    await update.message.reply_text("🔄 Refreshing cache from Supabase...")
    await refresh_cache_from_supabase()
    await update.message.reply_text(
        f"✅ Cache refreshed!\n"
        f"📊 {len(CACHE_DATA['businesses'])} businesses\n"
        f"👥 {len(USER_REGISTRATIONS)} registered users"
    )


# NOW ADD a separate function to clear pending (only call this when needed)

# ===========================
# MANUAL CLAIM COMMAND
# ===========================
async def claim_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually verify a payment by reference"""
    args = context.args

    if not args:
        await update.message.reply_text(
            "❌ *Usage:* `/claim <reference_id>`\n\n"
            "Example: `/claim BL_coins_123...`\n"
            "Use this to claim a pending payment if the bot was offline.",
            parse_mode='Markdown'
        )
        return

    ref = args[0].strip()
    status_msg = await update.message.reply_text(f"🔍 Verifying payment reference `{ref}`...", parse_mode='Markdown')

    # Call local API
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            # We need to construct the payload.
            # Smart Verification in api_server.py only needs 'transaction_reference'.
            payload = {"transaction_reference": ref}
            
            # We pass API URL.
            # Since we are inside the bot process, we can use localhost.
            # Port is defined in env or default 8000.
            port = int(os.getenv("PORT", "8000"))
            url = f"http://127.0.0.1:{port}/api/verify-payment"
            
            try:
                # Short timeout because local
                response = await client.post(url, json=payload, timeout=20.0)
            except httpx.RequestError as e:
                await status_msg.edit_text(f"❌ Connection Error: Could not reach API Server. Is it running?")
                return

            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    # Success!
                    await status_msg.edit_text("✅ Claim processed successfully! Check your balance.")
                else:
                    await status_msg.edit_text(f"❌ Claim failed: {data.get('message', 'Unknown error')}")
            else:
                try:
                    err_data = response.json()
                    detail = err_data.get('detail', response.text)
                except:
                    detail = response.text
                
                # Check for Idempotency message (we return 200 or 400? I set 200 for idempotency)
                # Wait, I set {success: true, message: "Already processed"}
                # So it falls into 200 block above.
                
                await status_msg.edit_text(f"❌ Verification Error: {detail}")

    except Exception as e:
        logging.error(f"Claim command error: {e}")
        await status_msg.edit_text(f"❌ processing error: {e}")


async def cancel_registration_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel ONLY the registration/upgrade flow, clearing pending data"""
    user_id = update.effective_user.id
    
    # Only clear if it's actually pending registration data
    if user_id in PENDING_REGISTRATIONS:
        PENDING_REGISTRATIONS.pop(user_id, None)
        logging.info(f"✅ Cancelled registration flow for {user_id}")
    
    context.user_data.clear()

    keyboard = [['🔁 Start Over']]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)

    await update.message.reply_text(
        "❌ Registration cancelled. Let's start again.",
        reply_markup=reply_markup
    )
    return ConversationHandler.END


async def timeout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    if update.message:
        await update.message.reply_text(
            "⌛ Session timed out due to inactivity.\n\n"
            "Please tap 🔁 Start Over to begin again.",
            reply_markup=ReplyKeyboardMarkup([['🔁 Start Over']], one_time_keyboard=True, resize_keyboard=True)
        )
    return ConversationHandler.END

# Duplicate force_refresh_cache removed — original is above

async def check_supabase_columns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to check actual Supabase column names"""
    if int(update.effective_user.id) != int(ADMIN_ID):
        if update.message:
            await update.message.reply_text("❌ Unauthorized")
        return
    
    try:
        # Get one record to see column names
        response = await asyncio.to_thread(
            lambda: supabase.table('businesses').select('*').limit(1).execute()
        )
        
        if response.data:
            columns = list(response.data[0].keys())
            msg = "📋 *Supabase Column Names:*\n\n"
            msg += "```\n" + "\n".join(columns) + "\n```"
            
            if update.message:
                await update.message.reply_text(msg, parse_mode='Markdown')
            elif update.callback_query:
                await update.callback_query.message.reply_text(msg, parse_mode='Markdown')
        else:
            if update.message:
                await update.message.reply_text("⚠️ No data in table")
    except Exception as e:
        logging.error(f"Column check error: {e}")
        if update.message:
            await update.message.reply_text(f"❌ Error: {e}")

async def load_pending_registrations():
    """Load pending registrations from database on startup"""
    global PENDING_REGISTRATIONS
    
    try:
        response = await asyncio.to_thread(
            lambda: supabase.table('pending_registrations').select('*').execute()
        )
        
        loaded_count = 0
        for record in response.data:
            user_id = record['user_id']
            data = json.loads(record['data'])
            PENDING_REGISTRATIONS[user_id] = data
            loaded_count += 1
        
        logging.info(f"✅ Loaded {loaded_count} pending registrations from database")
        
    except Exception as e:
        logging.error(f"❌ Failed to load pending registrations: {e}")

async def start_over(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    return await start(update, context)  # Will reset the welcome menu

async def check_background_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to see which background tasks are running"""
    if int(update.effective_user.id) != int(ADMIN_ID):
        await update.message.reply_text("❌ Unauthorized")
        return
    
    import asyncio
    
    tasks = asyncio.all_tasks()
    task_names = []
    
    for task in tasks:
        name = task.get_name()
        coro = task.get_coro()
        if coro:
            task_names.append(f"• {coro.__name__}")
    
    msg = f"🔄 *Background Tasks Running:*\n\n"
    msg += "\n".join(sorted(set(task_names)))
    msg += f"\n\nTotal: {len(tasks)} tasks"
    
    await update.message.reply_text(msg, parse_mode='Markdown')


async def post_init(application):
    try:
        logging.info("🔧 Post-init starting...")
        
        # 1. Initial cache load
        logging.info("📦 Loading cache from Supabase...")
        await refresh_cache_from_supabase()
        
        # 2. Load pending registrations from database
        logging.info("📋 Loading pending registrations...")
        await load_pending_registrations()
        
        # ✅ NEW: Load rate limits from database
        logging.info("⏱️ Loading rate limits...")
        await load_rate_limits_from_db()

        # Load long-term user memory
        logging.info("🧠 Loading user memory...")
        await load_user_facts_from_db()

        # 3. Start background auto-refresh
        logging.info("🔄 Starting auto-refresh loop...")
        asyncio.create_task(start_cache_refresh_loop())

        # 4. Start ad boost expiry checker
        logging.info("⏰ Starting ad boost expiry checker...")
        asyncio.create_task(start_expiry_check_loop(application))

        logging.info("🤖 Starting auto-approval loop...")
        asyncio.create_task(start_auto_approve_loop(application))

        # 5. Run initial expiry check
        logging.info("🔍 Running initial expiry check...")
        await check_expired_ad_boosts(application)
        
        # ✅ NEW: Run initial auto-approval check
        logging.info("🤖 Running initial auto-approval check...")
        await auto_approve_pending_requests(application)
        
        # ✅ NEW: Start API Server and Cache Refresh
        logging.info("🚀 Launching Dashboard API Server and Cache Refresh...")
        
        # Inject bot instance into API Server
        import api_server
        api_server.bot_instance = application.bot
        logging.info("✅ Injected bot instance into API Server")


        asyncio.create_task(start_api_server())

        # Set bot menu commands
        await application.bot.set_my_commands([
            BotCommand("start", "Start the bot"),
            BotCommand("find", "Find a service"),
            BotCommand("register", "Register your business"),
            BotCommand("cancel", "Cancel current action"),
        ])
        logging.info("✅ Bot menu commands set")
        
        logging.info("✅ Bot initialized successfully!")

        
    except Exception as e:
        logging.error(f"❌ Post-init error: {e}")

async def start_api_server():
    """Run FastAPI server in the background of the bot process."""
    try:
        import uvicorn
        from api_server import app
        config = uvicorn.Config(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")), log_level="warning")
        server = uvicorn.Server(config)
        await server.serve()
    except Exception as e:
        logging.error(f"❌ API Server failed to start: {e}")


async def handle_web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle data received from WebApp"""
    if not update.message or not update.message.web_app_data:
        return
        
    data = update.message.web_app_data.data
    
    if data == "trigger_register_from_dashboard":
        # Start registration flow and RETURN the state
        return await register_start(update, context)

def main():
    # Create application
    # Bump timeouts to reduce send_photo timeouts for large uploads / slow networks.
    # (Defaults can be too aggressive for media uploads.)
    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .get_updates_read_timeout(60)
        .get_updates_write_timeout(60)
        .get_updates_connect_timeout(30)
        .get_updates_pool_timeout(60)
        .build()
    )

    application.add_handler(CommandHandler('debug', debug_cache))
    #--application.add_handler(CommandHandler('columns', check_supabase_columns))

    application.post_init = post_init

    # --- Start Over button - MUST be first, NO GROUP to ensure highest priority ---
    application.add_handler(MessageHandler(filters.Regex('^🔁 Start Over$'), start_over))
    
    application.add_handler(CommandHandler('usage', check_gemini_usage))
    application.add_handler(CommandHandler('refresh', force_refresh_cache))
    application.add_handler(CommandHandler('check_expiry', force_expiry_check))
    application.add_handler(CommandHandler('auto_approve', force_auto_approve))
    application.add_handler(CommandHandler('pending', list_pending_registrations)) # ✅ New Admin Command
    application.add_handler(CommandHandler('claim', claim_command))  # ✅ New Claim Command
    application.add_handler(CommandHandler('claimlink', generate_claim_link))  # ✅ Generate Claim Link
    # --- Register Business conversation ---
    register_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex('^🏪 Register Your Business$'), register_start),
            CommandHandler('register', register_start),
            # ✅ Handle Deep Link: /start register
            MessageHandler(filters.Regex(r'^/start\s+register'), register_start),
            # ✅ Handle WebApp Registration Trigger
            MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_web_app_data),
        ],
        states={
            REGISTER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, register_name)],
            REGISTER_BUIS_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, register_buis_name)],
            REGISTER_SERVICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, register_service)],
            REGISTER_LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, register_location)],
            REGISTER_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, register_phone)],
            REGISTER_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, register_description)],
            REGISTER_PHOTOS: [MessageHandler(filters.PHOTO | filters.Document.ALL | filters.TEXT, register_photos)],

            CONFIRM_REGISTRATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_registration)],
            WAITING_APPROVAL: [
                # Empty state - users can chat freely while waiting
            ],
        },
        fallbacks=[
            CommandHandler(['find', 'register', 'dashboard'], warn_active_conversation),
            CommandHandler('start', cancel_and_start),
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex('(?i)^cancel$'), cancel),
            MessageHandler(filters.Regex('(?i)^register via google form$'), register_google_form),
        ],
        allow_reentry=True,
        conversation_timeout=1800
    )
    application.add_handler(register_conv)

    #application.add_handler(CallbackQueryHandler(handle_whatsapp_click, pattern="^wa_click_"))
    
    application.add_handler(CallbackQueryHandler(handle_request_service, pattern="^request_"))
    # --- Admin decision callback handler ---
    application.add_handler(CallbackQueryHandler(handle_admin_decision, pattern="^(approve|reject|upgrade|coin|adboost)_"))
    application.add_handler(CallbackQueryHandler(handle_scam_flag, pattern="^flag_scam_"))
    application.add_handler(CallbackQueryHandler(handle_review_decision, pattern="^rev_(approve|reject)_"))
    # --- Find Service conversation ---
    find_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex('^🔍 Find a Service$'), find_service_start),
            CommandHandler('find', find_service_start),
            # ✅ Handle Deep Link: /start find
            MessageHandler(filters.Regex(r'^/start\s+find'), find_service_start),
        ],
        states={
            SERVICE_TYPE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_location)],
            LOCATION: [
                MessageHandler(filters.Regex('^⬅️ Back to Services$'), handle_find_back_text),
                MessageHandler(filters.TEXT & ~filters.COMMAND, search_and_reply),
            ],
        },
        fallbacks=[
            CommandHandler(['find', 'register', 'dashboard'], warn_active_conversation),
            CommandHandler('start', cancel_and_start),
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex('(?i)^cancel$'), cancel),
        ],
        allow_reentry=True,
        conversation_timeout=600
    )
    application.add_handler(find_conv)
    
    buy_coins_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^💰 Buy Blue Coins$'), buy_coins_start)],
        states={
            BUY_COINS: [MessageHandler(filters.TEXT & ~filters.COMMAND, buy_coins_choice)],
            COIN_PAYMENT_PROOF: [
                MessageHandler(filters.PHOTO | filters.Document.ALL | filters.TEXT, coin_payment_proof)
            ],
        },
        fallbacks=[
            CommandHandler(['find', 'register', 'dashboard'], warn_active_conversation),
            CommandHandler('start', cancel_and_start),
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex('(?i)^cancel$'), cancel),
        ],
        allow_reentry=False,
        conversation_timeout=600
    )
    application.add_handler(buy_coins_conv)

    # --- Weekly Ad Boost conversation ---
    ad_boost_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^📢 Boost with Ads$'), boost_with_ads_start)],
        states={
            AD_BOOST_PAYMENT: [
                MessageHandler(filters.PHOTO | filters.Document.ALL | filters.TEXT, ad_boost_payment_proof)
            ],
        },
        fallbacks=[
            CommandHandler(['find', 'register', 'dashboard'], warn_active_conversation),
            CommandHandler('start', cancel_and_start),
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex('(?i)^cancel$'), cancel),
        ],
        allow_reentry=False,
        conversation_timeout=600
    )
    application.add_handler(ad_boost_conv)

    application.add_handler(CommandHandler('dashboard', dashboard.dashboard_main))
    
    # Dashboard menu button handler
    application.add_handler(MessageHandler(filters.Regex('^📊 My Dashboard$'), dashboard.dashboard_main))
    
    # Dashboard conversation handler for editing
    dashboard_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(dashboard.edit_description_start, pattern="^editdesc_"),
            CallbackQueryHandler(dashboard.edit_photos_start, pattern="^editphotos_")
        ],
        states={
            dashboard.EDIT_DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, dashboard.save_description)
            ],
            dashboard.EDIT_PHOTOS: [
                MessageHandler(filters.PHOTO, dashboard.receive_photos),
                CallbackQueryHandler(dashboard.save_photos, pattern="^photos_done$")
            ],
        },
        fallbacks=[
            CommandHandler(['find', 'register', 'dashboard'], warn_active_conversation),
            CommandHandler('start', cancel_and_start),
            CallbackQueryHandler(dashboard.handle_dashboard_callbacks, pattern="^cancel_edit$")
        ],
        allow_reentry=True,
        conversation_timeout=600
    )
    application.add_handler(dashboard_conv)
    
    # Dashboard callback handlers (for buttons)
    application.add_handler(CallbackQueryHandler(dashboard.show_full_analytics, pattern="^analytics_"))
    application.add_handler(CallbackQueryHandler(dashboard.handle_dashboard_callbacks, pattern="^(viewbiz_|close_dashboard|goto_|cancel_edit)"))
    

    # Global Cancel Handler (for when conversation ended but user pressed Cancel)
    application.add_handler(MessageHandler(filters.Regex('^❌ Cancel$'), cancel))

    # --- Gemini/Start conversation (exactly like v13) ---
    gemini_conv = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            GEMINI_FREECHAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_after_start)],
        },
        fallbacks=[],
        allow_reentry=True
    )
    application.add_handler(gemini_conv)
    
    # --- Inline query handler ---
    application.add_handler(InlineQueryHandler(inline_search_handler))

    # ✅ Handle WebApp Data
    # (Handler moved to ConversationHandler entry points)
  
    # --- Gemini Free Chat Handler (fallback, MUST be last) ---
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_chat))

    # Start the bot
    try:
        logging.info("🚀 Bot is starting...")
        application.run_polling(drop_pending_updates=True)
    except Exception as e:
        logging.exception("Bot crashed with error: %s", e)


if __name__ == "__main__":
    logging.info("🚀 Bot is starting...")
    main()








