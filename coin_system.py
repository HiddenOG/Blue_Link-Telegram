# coin_system.py
from datetime import datetime, timedelta
from supabase import Client
import logging

# This file will contain all coin-related functions
# We'll populate it step by step

# Coin constants
INITIAL_BLUE_COINS = 5
FREE_TIER_MAX_BUSINESSES = 5
COIN_COST_PER_LEAD = 1

# Coin prices
COIN_PRICES = {
    1: 200,
    5: 900,
    10: 1600,
    25: 3500,
    50: 6000
}

# Anti-spam settings
REQUEST_COOLDOWN_DAYS = 2
LINK_CLICK_COOLDOWN_DAYS = 2
MAX_REQUESTS_PER_DAY = 10
MAX_LINK_CLICKS_PER_DAY = 20
MIN_ACCOUNT_AGE_DAYS = 30

# In-memory tracking (will be populated from DB on startup)
USER_COINS = {}  # {telegram_id: coin_count}
CUSTOMER_REQUESTS = {}  # {customer_id: [(business_id, timestamp), ...]}
CUSTOMER_LINK_CLICKS = {}  # {customer_id: [(business_id, timestamp), ...]}

def get_user_coins(telegram_id):
    """Get user's current blue coin balance"""
    return USER_COINS.get(telegram_id, 0)

def set_user_coins(telegram_id, amount):
    """Set user's coin balance (used when loading from DB)"""
    USER_COINS[telegram_id] = amount

def add_coins(telegram_id, amount):
    """Add blue coins to user's balance"""
    current = USER_COINS.get(telegram_id, 0)
    USER_COINS[telegram_id] = current + amount
    logging.info(f"💰 Added {amount} coins to user {telegram_id}. New balance: {current + amount}")
    return current + amount


def deduct_coin(telegram_id):
    """Deduct 1 blue coin from user (for lead request)"""
    current = USER_COINS.get(telegram_id, 0)
    if current > 0:
        USER_COINS[telegram_id] = current - 1
        logging.info(f"💰 Deducted 1 coin from user {telegram_id}. New balance: {current - 1}")
        return True
    logging.warning(f"⚠️ User {telegram_id} has no coins to deduct")
    return False

# Anti-spam tracking (uses CUSTOMER_REQUESTS declared above)
DAILY_REQUEST_COUNT = {}  # {customer_id: {date: count}}

def can_request_service(customer_id, business_id):
    """Check if customer can request service (anti-spam)"""
    from datetime import datetime, timedelta
    
    now = datetime.now()
    today = now.date()
    
    # CHECK 1: Daily request limit (10 max)
    if customer_id not in DAILY_REQUEST_COUNT:
        DAILY_REQUEST_COUNT[customer_id] = {}
    
    today_count = DAILY_REQUEST_COUNT[customer_id].get(today, 0)
    if today_count >= MAX_REQUESTS_PER_DAY:
        return False, f"⚠️ Daily limit reached ({MAX_REQUESTS_PER_DAY} requests/day)"
    
    # CHECK 2: Business-specific cooldown (2 days)
    if customer_id in CUSTOMER_REQUESTS:
        for biz_id, timestamp in CUSTOMER_REQUESTS[customer_id]:
            if biz_id == business_id:
                days_since = (now - timestamp).days
                if days_since < REQUEST_COOLDOWN_DAYS:
                    days_left = REQUEST_COOLDOWN_DAYS - days_since
                    return False, f"⏳ You requested this business {days_since} day(s) ago. Wait {days_left} more day(s)."
    
    return True, "OK"


def record_service_request(customer_id, business_id):
    """Record a service request (for anti-spam tracking)"""
    from datetime import datetime
    
    now = datetime.now()
    today = now.date()
    
    # Record request
    if customer_id not in CUSTOMER_REQUESTS:
        CUSTOMER_REQUESTS[customer_id] = []
    CUSTOMER_REQUESTS[customer_id].append((business_id, now))
    
    # Update daily count
    if customer_id not in DAILY_REQUEST_COUNT:
        DAILY_REQUEST_COUNT[customer_id] = {}
    DAILY_REQUEST_COUNT[customer_id][today] = DAILY_REQUEST_COUNT[customer_id].get(today, 0) + 1
    
    logging.info(f"📞 Recorded request: Customer {customer_id} → Business {business_id}")

# Link click tracking (uses CUSTOMER_LINK_CLICKS declared above)
DAILY_LINK_CLICKS = {}  # {customer_id: {date: count}}

def can_click_business_link(customer_id, business_id):
    """Check if customer can click WhatsApp link (anti-spam)"""
    from datetime import datetime, timedelta
    
    now = datetime.now()
    today = now.date()
    
    # CHECK 1: Daily link click limit (20 max)
    if customer_id not in DAILY_LINK_CLICKS:
        DAILY_LINK_CLICKS[customer_id] = {}
    
    today_count = DAILY_LINK_CLICKS[customer_id].get(today, 0)
    if today_count >= MAX_LINK_CLICKS_PER_DAY:
        return False, f"⚠️ Daily contact limit reached ({MAX_LINK_CLICKS_PER_DAY} businesses/day)"
    
    # CHECK 2: Business-specific cooldown (7 days)
    if customer_id in CUSTOMER_LINK_CLICKS:
        for biz_id, timestamp in CUSTOMER_LINK_CLICKS[customer_id]:
            if biz_id == business_id:
                days_since = (now - timestamp).days
                if days_since < LINK_CLICK_COOLDOWN_DAYS:
                    days_left = LINK_CLICK_COOLDOWN_DAYS - days_since
                    return False, f"⏳ You contacted this business {days_since} day(s) ago. Wait {days_left} more day(s)."
    
    return True, "OK"


def record_link_click(customer_id, business_id):
    """Record a link click (for anti-spam tracking)"""
    from datetime import datetime
    
    now = datetime.now()
    today = now.date()
    
    # Record click
    if customer_id not in CUSTOMER_LINK_CLICKS:
        CUSTOMER_LINK_CLICKS[customer_id] = []
    CUSTOMER_LINK_CLICKS[customer_id].append((business_id, now))
    
    # Update daily count
    if customer_id not in DAILY_LINK_CLICKS:
        DAILY_LINK_CLICKS[customer_id] = {}
    DAILY_LINK_CLICKS[customer_id][today] = DAILY_LINK_CLICKS[customer_id].get(today, 0) + 1
    
    logging.info(f"🔗 Recorded link click: Customer {customer_id} → Business {business_id}")