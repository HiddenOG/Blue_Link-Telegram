import threading
import os
from datetime import datetime, timezone
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

# --- Database ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    # We'll fail later if needed, but don't crash on import if just for testing
    supabase = None
    print(f"⚠️ Supabase init failed in bot_state: {e}")

# --- Shared State ---

CACHE_LOCK = threading.Lock()
CACHE_DATA = {
    "businesses": [],
    "last_updated": None,
    "is_updating": False
}

CACHE_REFRESH_INTERVAL = 1000  # ~16.7 minutes in seconds

USER_REGISTRATIONS = {}

PENDING_REGISTRATIONS = {}

# --- Column Mapping ---
COLUMN_MAP = {
    'Full Name': 'full_name',
    'Business Name': 'business_name',
    'Business Services': 'business_services',
    'Business Location': 'business_location',
    'Phone Number': 'phone_number',
    'Business Description': 'business_description',
    'Preferred Listing Package': 'preferred_listing_package',
    'Telegram ID': 'telegram_id',
    'Phone Number(Encrypted': 'phone_number_encrypted',
    'Payment Confirmation': 'payment_confirmation',
    'Online Business Category': 'online_business_category',
    'Blue Collar Skillset': 'blue_collar_skillset',
    'Tech Skillset': 'tech_skillset',
    'Business State Location': 'business_state_location',
    'Service Radius ': 'service_radius'
}

# --- Shared Helpers ---

def get_row_value(row, key):
    """Helper to get value using either old or new column names"""
    if not row:
        return None
        
    # Try new snake_case name first
    snake_case_key = COLUMN_MAP.get(key, key.lower().replace(' ', '_'))
    value = row.get(snake_case_key)
    
    # Fallback to old name if not found
    if value is None:
        value = row.get(key)
    
    # If still None, try case-insensitive matches for the key
    if value is None:
        row_keys_lower = {k.lower(): k for k in row.keys()}
        if key.lower() in row_keys_lower:
            value = row.get(row_keys_lower[key.lower()])
        elif snake_case_key.lower() in row_keys_lower:
            value = row.get(row_keys_lower[snake_case_key.lower()])
            
    return value

def _normalize_text(x):
    return (x or "").strip().lower()

def get_user_businesses(telegram_id):
    """Get all businesses - SAFE"""
    if telegram_id not in USER_REGISTRATIONS:
        return []
    
    user_data = USER_REGISTRATIONS[telegram_id]
    
    if isinstance(user_data, dict) and "businesses" in user_data:
        return user_data["businesses"]
    elif isinstance(user_data, dict):
        # Old structure: return single business as list
        return [{
            "business_name": user_data.get("business_name", ""),
            "phone": user_data.get("phone", ""),
            "tier": user_data.get("tier", "free")
        }]
    return []

def get_business_count(telegram_id):
    """Get business count - SAFE"""
    return len(get_user_businesses(telegram_id))

def user_has_business(telegram_id):
    """Check if user has any registered business"""
    return get_business_count(telegram_id) > 0

def user_is_premium(telegram_id):
    """Check if user's account is Premium"""
    if telegram_id not in USER_REGISTRATIONS:
        return False
    
    user_data = USER_REGISTRATIONS[telegram_id]
    
    if isinstance(user_data, dict) and "account_tier" in user_data:
        return user_data["account_tier"] == "premium"
    elif isinstance(user_data, dict):
        tier = user_data.get('tier', 'free').lower()
        return 'premium' in tier
    return False

def user_is_free_tier(telegram_id):
    """Check if user is free tier"""
    if telegram_id not in USER_REGISTRATIONS:
        return False
    
    user_data = USER_REGISTRATIONS[telegram_id]
    
    if isinstance(user_data, dict) and "account_tier" in user_data:
        return user_data["account_tier"] == "free"
    elif isinstance(user_data, dict):
        tier = user_data.get('tier', 'free').lower()
        return 'free' in tier and 'premium' not in tier
    return True # Default to free if not found

def row_is_premium(row):
    """Check if business row is premium - SAFE"""
    if not row:
        return False
    tier = str(get_row_value(row, 'Preferred Listing Package') or 'free').lower()
    return 'premium' in tier or 'paid' in tier

def get_cached_businesses():

    """Get businesses from cache (instant, no API call)."""
    with CACHE_LOCK:
        return CACHE_DATA["businesses"].copy()

def is_ad_boosted(row):
    """Check if business is currently ad boosted"""
    if not row:
        return False
        
    is_boosted = row.get('is_ad_boosted')
    if not is_boosted:
        return False
    
    boost_expires = row.get('ad_boost_expires')
    if not boost_expires:
        return False
    
    try:
        # Simple string compare if already isoformat or try parse
        now = datetime.now(timezone.utc).isoformat()
        return boost_expires > now
    except:
        return False

async def refresh_cache_from_supabase():
    """Fetch all data from Supabase and store in memory cache."""
    # Importing here to avoid circular imports
    from coin_system import set_user_coins
    import asyncio
    import logging
    
    try:
        logging.info("🔄 Refreshing cache from Supabase...")
        
        # ✅ Fetch businesses
        response = await asyncio.to_thread(
            lambda: supabase.table('businesses').select('*').execute()
        )
        records = response.data
        
        with CACHE_LOCK:
            CACHE_DATA["businesses"] = records
            CACHE_DATA["last_updated"] = datetime.now(timezone.utc)
            CACHE_DATA["is_updating"] = False
        
        # ✅ Build user registrations using a temporary dict for thread-safety
        temp_registrations = {}
        
        for record in records:
            telegram_id = get_row_value(record, 'Telegram ID')
            if telegram_id:
                try:
                    telegram_id = int(str(telegram_id).strip())
                    if not telegram_id:        
                        continue
                    
                    tier_raw = get_row_value(record, 'Preferred Listing Package') or 'free'
                    tier_raw = str(tier_raw).strip().lower()
                    
                    if telegram_id not in temp_registrations:
                        temp_registrations[telegram_id] = {
                            "account_tier": "free",
                            "businesses": []
                        }
                    
                    if 'premium' in tier_raw or 'paid' in tier_raw:
                        temp_registrations[telegram_id]["account_tier"] = "premium"
                    
                    business_entry = {
                        "id": get_row_value(record, 'id'),
                        "business_name": get_row_value(record, 'Business Name') or '',
                        "phone": get_row_value(record, 'Phone Number') or '',
                        "service": get_row_value(record, 'Business Services') or '',
                        "location": get_row_value(record, 'Business Location') or '',
                        "description": get_row_value(record, 'Business Description') or '',
                        "tier": tier_raw
                    }

                    
                    existing = [b for b in temp_registrations[telegram_id]["businesses"] 
                               if b["business_name"] == business_entry["business_name"]]
                    
                    if not existing:
                        temp_registrations[telegram_id]["businesses"].append(business_entry)
                    
                except Exception as entry_error:
                    logging.error(f"Error processing business record: {entry_error}")
                    continue
        
        # Atomically update the global dict
        with CACHE_LOCK:
            USER_REGISTRATIONS.clear()
            USER_REGISTRATIONS.update(temp_registrations)
        
        # ✅ ✅ ✅ NEW: Load user coins from database
        coin_response = await asyncio.to_thread(
            lambda: supabase.table('user_coins').select('*').execute()
        )
        
        for coin_record in coin_response.data:
            tid = coin_record.get('telegram_id')
            balance = coin_record.get('coin_balance', 0)
            if tid:
                set_user_coins(int(tid), balance)

        # ✅ NEW: Load historical leads for cooldown persistence (last 7 days)
        from coin_system import load_cooldowns_from_db
        seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        lead_response = await asyncio.to_thread(
            lambda: supabase.table('lead_requests')
                .select('*')
                .gte('created_at', seven_days_ago)
                .execute()
        )
        if lead_response.data:
            load_cooldowns_from_db(lead_response.data)
        
        logging.info(f"✅ Cache refreshed! {len(records)} businesses, {len(USER_REGISTRATIONS)} users, {len(coin_response.data)} coin accounts, {len(lead_response.data)} active leads")
        
    except Exception as e:
        logging.error(f"❌ Cache refresh failed: {e}")
        with CACHE_LOCK:
            CACHE_DATA["is_updating"] = False

async def start_cache_refresh_loop():
    """Background task that refreshes cache periodically."""
    import asyncio
    import logging
    while True:
        try:
            await refresh_cache_from_supabase()
            await asyncio.sleep(CACHE_REFRESH_INTERVAL)
        except Exception as e:
            logging.error(f"Cache refresh loop error: {e}")
            await asyncio.sleep(60)



