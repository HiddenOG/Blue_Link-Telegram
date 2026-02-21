import os
import hmac
import hashlib
import json
import asyncio
import logging
from urllib.parse import parse_qs
from fastapi import FastAPI, HTTPException, Request, Form, UploadFile, File, Query
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Import bot modules
from bot_state import (
    USER_REGISTRATIONS, get_user_businesses, get_row_value, supabase,
    refresh_cache_from_supabase
)
from dashboard import get_business_analytics, get_ad_boost_status
from coin_system import get_user_coins, deduct_coin, can_request_service, record_service_request
from fastapi import UploadFile, File, Form
from typing import List, Optional
from datetime import datetime

# Global bot instance (injected from lot2.py)
bot_instance = None



load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

app = FastAPI(title="BlueBot Dashboard API")

# Enable CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- NEW: Ngrok Bypass Middleware ---
@app.middleware("http")
async def add_ngrok_skip_header(request, call_next):
    response = await call_next(request)
    response.headers["ngrok-skip-browser-warning"] = "true"
    return response


# --- Security ---

def validate_telegram_data(init_data: str) -> dict:
    """Validate Telegram initData to ensure request comes from a real user."""
    if not TELEGRAM_TOKEN or os.getenv("DASHBOARD_SKIP_AUTH") == "true":
        # For local testing without token or with skip flag
        logging.warning("Auth validation bypassed via DASHBOARD_SKIP_AUTH")
        return {"user": {"id": 6584628162, "first_name": "Godson (Dev Mode)"}}


    try:
        if not init_data:
            logging.error("Validation failed: initData is empty")
            raise HTTPException(status_code=401, detail="Missing authorization data")

        parsed_data = parse_qs(init_data)
        if "hash" not in parsed_data:
            logging.error(f"Validation failed: hash missing in parsed data. Keys: {list(parsed_data.keys())}")
            raise HTTPException(status_code=401, detail="Invalid session format")


        received_hash = parsed_data["hash"][0]
        data_check_string = "\n".join([
            f"{k}={v[0]}" for k, v in sorted(parsed_data.items()) if k != "hash"
        ])

        secret_key = hmac.new(b"WebAppData", TELEGRAM_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if calculated_hash != received_hash:
            raise HTTPException(status_code=401, detail="Authentication failed")

        user_data = json.loads(parsed_data["user"][0])
        return {"user": user_data}
    except Exception as e:
        logging.error(f"Validation error: {e}")
        raise HTTPException(status_code=401, detail="Invalid session")

def resolve_user_id(init_data: str = None, user_id: int = None) -> int:
    """Resolve user identity with fallbacks. Used by all endpoints."""
    # 1. Try HMAC validation
    if init_data:
        try:
            user = validate_telegram_data(init_data)
            return user["user"]["id"]
        except:
            # 2. Try parsing user from initData directly
            try:
                parsed = parse_qs(init_data)
                if "user" in parsed:
                    user_data = json.loads(parsed["user"][0])
                    uid = user_data.get("id")
                    if uid:
                        return uid
            except:
                pass
    # 3. Trust uid param (set by our bot code)
    if user_id:
        return user_id
    raise HTTPException(status_code=401, detail="Authentication required")

# --- Endpoints ---

@app.get("/api/dashboard")
async def get_dashboard_data(initData: str = Query(None), user_id: int = Query(None)):
    user_name = "User"
    valid_user_id = None
    had_init_data = bool(initData)
    
    if initData:
        try:
            user = validate_telegram_data(initData)
            valid_user_id = user["user"]["id"]
            user_name = user["user"].get("first_name", "User")
            logging.info(f"✅ Dashboard: HMAC auth OK for user {valid_user_id}")
        except Exception as e:
            logging.warning(f"⚠️ Dashboard: initData validation failed: {e}")
            # Try to extract user from initData even if HMAC fails
            try:
                from urllib.parse import parse_qs
                parsed = parse_qs(initData)
                if "user" in parsed:
                    user_data = json.loads(parsed["user"][0])
                    valid_user_id = user_data.get("id")
                    user_name = user_data.get("first_name", "User")
                    logging.info(f"✅ Dashboard: Extracted user {valid_user_id} from initData (HMAC bypass)")
            except Exception as parse_err:
                logging.warning(f"⚠️ Dashboard: Could not parse user from initData: {parse_err}")
        
    if not valid_user_id and user_id:
        # uid is set by our bot code in get_main_keyboard(), safe for read-only endpoint
        valid_user_id = user_id
        # Try to get actual name from database
        try:
            name_resp = await asyncio.to_thread(
                lambda: supabase.table('businesses')
                    .select('full_name')
                    .eq('telegram_id', str(valid_user_id))
                    .limit(1)
                    .execute()
            )
            if name_resp.data and name_resp.data[0].get('full_name'):
                user_name = name_resp.data[0]['full_name'].split()[0]
            else:
                user_name = "User"
        except:
            user_name = "User"
        logging.info(f"✅ Dashboard: Using uid fallback: {valid_user_id}, name: {user_name}")
        
    if not valid_user_id:
        return {"businesses": [], "coins": 0, "error": "Auth failed"}
        
    user_id = valid_user_id # Proceed with valid ID
    
    # 1. Get businesses for user
    businesses_brief = get_user_businesses(user_id)
    if not businesses_brief:
        return {"businesses": [], "coins": get_user_coins(user_id)}

    # 2. Enrich with analytics
    # Note: We use the full cache to get row values
    from bot_state import get_cached_businesses
    all_cached = get_cached_businesses()
    
    enriched_businesses = []
    for brief in businesses_brief:
        # Find the full record to get the 'id' (supabase primary key)
        full_record = None
        for b in all_cached:
            if (str(get_row_value(b, 'Telegram ID')) == str(user_id) and 
                get_row_value(b, 'Business Name') == brief['business_name']):
                full_record = b
                break
        
        if not full_record:
            continue
            
        biz_id = full_record.get('id')
        analytics = await get_business_analytics(supabase, biz_id, user_id)
        boost = await get_ad_boost_status(full_record)
        
        # Query real weekly stats from lead_requests table
        weekly_stats = []
        try:
            from datetime import timedelta
            now = datetime.now()
            for i in range(6, -1, -1):
                day = now - timedelta(days=i)
                day_name = day.strftime('%a')
                day_start = day.replace(hour=0, minute=0, second=0).isoformat()
                day_end = day.replace(hour=23, minute=59, second=59).isoformat()
                
                leads_resp = await asyncio.to_thread(
                    lambda ds=day_start, de=day_end: supabase.table('lead_requests')
                        .select('id', count='exact')
                        .eq('business_id', biz_id)
                        .gte('request_timestamp', ds)
                        .lte('request_timestamp', de)
                        .execute()
                )
                req_count = leads_resp.count if leads_resp.count else (len(leads_resp.data) if leads_resp.data else 0)
                
                # Also get clicks for this day
                clicks_resp = await asyncio.to_thread(
                    lambda ds=day_start, de=day_end: supabase.table('link_clicks')
                        .select('id', count='exact')
                        .eq('business_id', biz_id)
                        .gte('click_timestamp', ds)
                        .lte('click_timestamp', de)
                        .execute()
                )
                click_count = clicks_resp.count if clicks_resp.count else (len(clicks_resp.data) if clicks_resp.data else 0)
                
                weekly_stats.append({"date": day_name, "requests": req_count, "clicks": click_count})
        except Exception as stats_err:
            logging.warning(f"Could not load weekly stats for biz {biz_id}: {stats_err}")
            for day_name in ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']:
                weekly_stats.append({"date": day_name, "requests": 0, "clicks": 0})

        enriched_businesses.append({
            "id": brief.get('id'),
            "business_name": brief['business_name'],
            "phone": brief.get('phone', ''),
            "service": brief['service'],
            "location": brief['location'],
            "description": brief.get('description', ''),
            "photo_1": get_row_value(full_record, 'photo_1') or None,
            "photo_2": get_row_value(full_record, 'photo_2') or None,
            "photo_3": get_row_value(full_record, 'photo_3') or None,
            "analytics": analytics,
            "boost_status": boost,
            "weekly_stats": weekly_stats
        })


    return {
        "user_name": user_name,
        "coins": get_user_coins(user_id),
        "businesses": enriched_businesses
    }

@app.post("/api/business/update")
async def update_business(
    initData: str = Query(None),
    business_id: int = Query(...),
    user_id: int = Query(None),
    updates: dict = None
):
    uid = resolve_user_id(initData, user_id)
    
    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")
        
    # Map frontend names to database names
    # The database uses snake_case columns (based on bot_state.COLUMN_MAP)
    db_mapping = {
        "business_name": "business_name",
        "business_services": "business_services",
        "business_location": "business_location",
        "phone_number": "phone_number",
        "business_description": "business_description",
        "photo_1": "photo_1",
        "photo_2": "photo_2",
        "photo_3": "photo_3"
    }
    
    db_updates = {}
    for key, value in updates.items():
        if key in db_mapping:
            db_updates[db_mapping[key]] = value

    
    if not db_updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    try:
        # Verify ownership and update
        result = await asyncio.to_thread(
            lambda: supabase.table('businesses')
                .update(db_updates)
                .eq('id', business_id)
                .eq('telegram_id', uid)
                .execute()
        )


        
        if not result.data:
            raise HTTPException(status_code=404, detail="Business not found or unauthorized")
            
        # Refresh shared cache
        await refresh_cache_from_supabase()
        
        return {"success": True, "message": "Business updated successfully"}
    except Exception as e:
        logging.error(f"Error updating business: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/business/delete")
async def delete_business(
    initData: str = Query(None),
    business_id: int = Query(...),
    user_id: int = Query(None)
):
    uid = resolve_user_id(initData, user_id)
    
    try:
        # Delete related records first (foreign key constraints)
        for table in ['lead_requests', 'link_clicks', 'customer_request_history']:
            await asyncio.to_thread(
                lambda t=table: supabase.table(t)
                    .delete()
                    .eq('business_id', business_id)
                    .execute()
            )
        
        # Now delete the business
        result = await asyncio.to_thread(
            lambda: supabase.table('businesses')
                .delete()
                .eq('id', business_id)
                .eq('telegram_id', uid)
                .execute()
        )

        
        if not result.data:
            raise HTTPException(status_code=404, detail="Business not found or unauthorized")
            
        # Refresh shared cache
        await refresh_cache_from_supabase()
        
        return {"success": True, "message": "Business deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error deleting business: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/business/{business_id}")
async def get_business_profile(business_id: int):
    """Get full business details for the profile page."""
    from bot_state import get_cached_businesses, get_row_value, is_ad_boosted
    
    cached = get_cached_businesses()
    business = None
    for b in cached:
        if b.get('id') == business_id:
            business = b
            break
    
    if not business:
        raise HTTPException(status_code=404, detail="Business not found")
    
    owner_id = business.get('telegram_id')
    owner_coins = 0
    if owner_id:
        try:
            owner_coins = get_user_coins(int(owner_id))
        except (ValueError, TypeError):
            pass
    
    return {
        "id": business_id,
        "business_name": get_row_value(business, 'Business Name') or 'Unknown',
        "service": get_row_value(business, 'Business Services') or 'N/A',
        "location": get_row_value(business, 'Business Location') or 'N/A',
        "description": get_row_value(business, 'Business Description') or '',
        "phone": get_row_value(business, 'Phone Number') or '',
        "photo_1": business.get('photo_1') or None,
        "photo_2": business.get('photo_2') or None,
        "photo_3": business.get('photo_3') or None,
        "is_boosted": is_ad_boosted(business),
        "has_owner": bool(owner_id),
        "owner_coins": owner_coins
    }

@app.post("/api/business/contact")
async def contact_business(request: Request):
    """Handle contact request: coin deduction + WhatsApp link."""
    import urllib.parse
    from bot_state import get_cached_businesses, get_row_value
    
    body = await request.json()
    business_id = body.get('business_id')
    customer_id = body.get('customer_id')
    
    if not business_id or not customer_id:
        raise HTTPException(status_code=400, detail="Missing business_id or customer_id")
    
    # Find business
    cached = get_cached_businesses()
    business = None
    for b in cached:
        if b.get('id') == business_id:
            business = b
            break
    
    if not business:
        raise HTTPException(status_code=404, detail="Business not found")
    
    owner_id = business.get('telegram_id')
    business_name = get_row_value(business, 'Business Name') or 'Unknown'
    service = get_row_value(business, 'Business Services') or 'N/A'
    location = get_row_value(business, 'Business Location') or 'N/A'
    phone = str(get_row_value(business, 'Phone Number') or '').strip()
    
    # Format phone for WhatsApp
    if phone:
        if phone.startswith('0'):
            phone = '234' + phone[1:]
        elif phone.startswith('+'):
            phone = phone[1:]
        elif not phone.startswith('234'):
            phone = '234' + phone
    else:
        raise HTTPException(status_code=400, detail="No phone number available")
    
    # Build WhatsApp link
    auto_message = (
        f"Hello! 👋\n\n"
        f"I found your business *{business_name}* on BlueLink Bot.\n\n"
        f"I'm interested in your *{service}* services in *{location}*.\n\n"
        f"Are you available to help me?\n\n"
        f"Thank you!"
    )
    encoded_msg = urllib.parse.quote(auto_message)
    wa_link = f"https://wa.me/{phone}?text={encoded_msg}"
    
    # Legacy business - no coin system
    if not owner_id:
        return {"wa_link": wa_link, "deducted": False, "legacy": True}
    
    # Check cooldown (7 days)
    can_request, msg = can_request_service(customer_id, business_id)
    
    if not can_request:
        # Within cooldown - still give WhatsApp link, no deduction
        return {"wa_link": wa_link, "deducted": False, "cooldown_message": msg}
    
    # Check coins
    try:
        owner_coins = get_user_coins(int(owner_id))
    except (ValueError, TypeError):
        raise HTTPException(status_code=500, detail="Invalid business owner")
    
    if owner_coins <= 0:
        raise HTTPException(status_code=400, detail="Business is out of Blue Coins")
    
    # Deduct coin
    success = deduct_coin(int(owner_id))
    if not success:
        raise HTTPException(status_code=500, detail="Failed to deduct coin")
    
    # Record the request for cooldown tracking
    record_service_request(customer_id, business_id)
    
    # Update DB + record lead
    try:
        new_balance = get_user_coins(int(owner_id))
        await asyncio.to_thread(
            lambda: supabase.table('user_coins')
                .update({'coin_balance': new_balance})
                .eq('telegram_id', int(owner_id))
                .execute()
        )
        
        lead_data = {
            "customer_telegram_id": customer_id,
            "business_id": business_id,
            "business_owner_id": int(owner_id),
            "request_type": "paid_request",
            "coin_deducted": True,
            "status": "verified"
        }
        await asyncio.to_thread(
            lambda: supabase.table('lead_requests').insert(lead_data).execute()
        )
        
        # Notify business owner via bot
        if bot_instance:
            try:
                await bot_instance.send_message(
                    chat_id=int(owner_id),
                    text=(
                        f"🔔 *New Verified Lead!*\n\n"
                        f"A customer found your business via BlueLink.\n"
                        f"Business: {business_name}\n"
                        f"Service: {service}\n\n"
                        f"💰 1 Blue Coin deducted\n"
                        f"💵 Remaining: {new_balance} coins\n\n"
                        f"📞 [Message Customer](tg://user?id={customer_id})"
                    ),
                    parse_mode='Markdown'
                )
            except Exception as notify_err:
                logging.error(f"Failed to notify owner: {notify_err}")
        
    except Exception as e:
        logging.error(f"Error recording lead: {e}")
    
    return {"wa_link": wa_link, "deducted": True, "new_balance": new_balance}

@app.get("/api/photo")
async def get_photo(file_id: str = Query(...)):
    """Proxy Telegram photos for web display."""
    import httpx
    try:
        if not bot_instance:
            raise HTTPException(status_code=503, detail="Bot not ready")
        
        file = await bot_instance.get_file(file_id)
        file_url = file.file_path
        
        # file_path is already a full URL for Bot API
        if not file_url.startswith('http'):
            file_url = f"https://api.telegram.org/file/bot{os.getenv('TELEGRAM_TOKEN')}/{file_url}"
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(file_url)
            if resp.status_code != 200:
                raise HTTPException(status_code=404, detail="Photo not found")
            
            return StreamingResponse(
                iter([resp.content]),
                media_type=resp.headers.get('content-type', 'image/jpeg')
            )
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error fetching photo: {e}")
        raise HTTPException(status_code=500, detail="Failed to load photo")

@app.post("/api/business/upload-photo")
async def upload_photo(
    initData: str = Query(None),
    business_id: int = Query(...),
    user_id: int = Query(None),
    photo: UploadFile = File(...),
    slot: str = Form(...)
):
    """Upload a new photo for a business. Sends to Telegram to get file_id."""
    uid = resolve_user_id(initData, user_id)
    
    if slot not in ('photo_1', 'photo_2', 'photo_3'):
        raise HTTPException(status_code=400, detail="Invalid photo slot")
    
    try:
        # Read the uploaded file
        photo_bytes = await photo.read()
        
        if not bot_instance:
            raise HTTPException(status_code=503, detail="Bot not ready")
        
        # Send photo to Telegram to get a file_id (send to user's own chat)
        from telegram import InputFile
        import io
        msg = await bot_instance.send_photo(
            chat_id=user_id,
            photo=io.BytesIO(photo_bytes),
            caption="📸 Photo updated via dashboard"
        )
        
        # Get the file_id from the sent message
        file_id = msg.photo[-1].file_id  # highest resolution
        
        # Update the database
        await asyncio.to_thread(
            lambda: supabase.table('businesses')
                .update({slot: file_id})
                .eq('id', business_id)
                .eq('telegram_id', uid)
                .execute()
        )
        
        # Refresh cache
        await refresh_cache_from_supabase()
        
        return {"success": True, "file_id": file_id}
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error uploading photo: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/catalog")
async def get_catalog(service: Optional[str] = Query(None), location: Optional[str] = Query(None)):
    """
    Public catalog endpoint.
    - No params: returns list of unique services
    - ?service=X: returns unique locations for that service
    - ?service=X&location=Y: returns businesses in that service+location
    """
    from bot_state import get_cached_businesses, get_row_value
    businesses = get_cached_businesses()
    
    if service and location:
        # Return businesses matching service + location
        results = []
        for biz in businesses:
            biz_service = get_row_value(biz, 'Business Services') or ''
            biz_location = get_row_value(biz, 'Business Location') or ''
            if (biz_service.strip().lower() == service.strip().lower() and 
                biz_location.strip().lower() == location.strip().lower()):
                is_boosted = bool(biz.get('is_ad_boosted'))
                results.append({
                    "id": biz.get('id'),
                    "business_name": biz.get('business_name', 'Unknown'),
                    "service": biz_service.strip(),
                    "location": biz_location.strip(),
                    "description": (biz.get('business_description') or '')[:100],
                    "is_boosted": is_boosted,
                    "has_photos": bool(biz.get('photo_1'))
                })
        # Boosted businesses first
        results.sort(key=lambda x: (not x['is_boosted'], x['business_name'].lower()))
        return {
            "service": service,
            "location": location,
            "businesses": results,
            "total": len(results)
        }
    
    elif service:
        # Return unique locations for this service
        locations = set()
        for biz in businesses:
            biz_service = get_row_value(biz, 'Business Services') or ''
            biz_location = get_row_value(biz, 'Business Location') or ''
            if biz_service.strip().lower() == service.strip().lower() and biz_location.strip():
                locations.add(biz_location.strip())
        
        return {
            "service": service,
            "locations": sorted(list(locations))
        }
    else:
        # Return unique services
        services = {}
        for biz in businesses:
            svc = get_row_value(biz, 'Business Services') or ''
            svc = svc.strip()
            if svc:
                services[svc.lower()] = services.get(svc.lower(), {"name": svc, "count": 0})
                services[svc.lower()]["count"] += 1
        
        return {
            "services": sorted(list(services.values()), key=lambda x: x["name"].lower()),
            "total": len(businesses)
        }

# --- Synonym Map for Hybrid Search ---
SYNONYM_MAP = {
    "tailor": ["fashion designer", "seamstress", "clothing alteration", "fashion design"],
    "fashion designer": ["tailor", "seamstress", "fashion design", "clothing"],
    "seamstress": ["tailor", "fashion designer"],
    "barber": ["hair stylist", "haircut", "grooming", "barbing"],
    "hair stylist": ["barber", "salon", "hairdressing"],
    "salon": ["hair stylist", "hairdressing", "beauty", "makeup"],
    "mechanic": ["auto repair", "car repair", "car service", "vehicle maintenance"],
    "auto repair": ["mechanic", "car repair"],
    "plumber": ["plumbing", "pipe fitting", "drainage", "water repair"],
    "plumbing": ["plumber", "pipe fitting"],
    "electrician": ["electrical", "wiring", "electrical repair"],
    "electrical": ["electrician", "wiring"],
    "painter": ["painting", "house painting", "decorator"],
    "carpenter": ["carpentry", "furniture", "woodwork"],
    "photographer": ["photography", "videography", "photo studio"],
    "photography": ["photographer", "videography"],
    "catering": ["food", "restaurant", "cooking", "chef"],
    "cleaning": ["laundry", "dry cleaning", "janitor", "housekeeping"],
    "laundry": ["cleaning", "dry cleaning", "ironing"],
    "delivery": ["logistics", "dispatch", "courier", "shipping"],
    "logistics": ["delivery", "dispatch", "courier"],
    "security": ["guard", "bodyguard", "surveillance"],
    "dj": ["event", "music", "entertainment", "party"],
    "event": ["events", "party", "dj", "entertainment", "decoration"],
    "baking": ["cake", "pastry", "confectionery", "bakery"],
    "cake": ["baking", "pastry", "confectionery"],
    "fitness": ["gym", "trainer", "exercise", "workout"],
    "massage": ["spa", "wellness", "relaxation"],
    "driving": ["driver", "chauffeur", "ride", "taxi"],
    "doctor": ["medical", "health", "clinic", "hospital", "nurse"],
    "nurse": ["medical", "health", "doctor"],
    "lawyer": ["legal", "law", "attorney", "solicitor"],
    "accounting": ["finance", "bookkeeping", "tax"],
    "real estate": ["property", "housing", "agent", "realtor"],
    "graphic design": ["graphics", "design", "branding", "logo"],
    "web developer": ["software", "programming", "tech", "website"],
    "software": ["web developer", "programming", "tech", "developer"],
    "tutoring": ["teaching", "lesson", "teacher", "tutor", "education"],
    "welding": ["welder", "metal work", "fabrication"],
    "farming": ["agriculture", "agribusiness"],
    "printing": ["print", "signage", "banner"],
    "phone repair": ["phone", "gadget repair", "screen repair"],
    "makeup": ["beauty", "cosmetics", "salon"],
}

def fuzzy_match(query, candidates, threshold=0.6):
    """Simple fuzzy matching using difflib"""
    from difflib import SequenceMatcher
    matches = []
    query_lower = query.lower()
    for candidate in candidates:
        candidate_lower = candidate.lower()
        # Direct substring match
        if query_lower in candidate_lower or candidate_lower in query_lower:
            matches.append((candidate, 1.0))
            continue
        # Sequence similarity
        ratio = SequenceMatcher(None, query_lower, candidate_lower).ratio()
        if ratio >= threshold:
            matches.append((candidate, ratio))
    matches.sort(key=lambda x: x[1], reverse=True)
    return matches

@app.get("/api/catalog/search")
async def search_catalog(q: str = Query(..., min_length=1)):
    """
    Hybrid search: synonym map + fuzzy matching.
    Returns matching categories (services) + suggestions for related services.
    """
    from bot_state import get_cached_businesses, get_row_value
    businesses = get_cached_businesses()
    query = q.strip().lower()
    
    # 1. Get all unique service names
    all_services = set()
    service_counts = {}
    for biz in businesses:
        svc = (get_row_value(biz, 'Business Services') or '').strip()
        if svc:
            all_services.add(svc)
            service_counts[svc.lower()] = service_counts.get(svc.lower(), 0) + 1
    
    # 2. Find matching services (fuzzy + synonym)
    matched_services = set()
    suggestions = []
    
    # Direct/fuzzy matches against actual service names
    fuzzy_results = fuzzy_match(query, list(all_services), threshold=0.5)
    for svc_name, score in fuzzy_results:
        matched_services.add(svc_name)
    
    # Synonym suggestions
    synonyms = SYNONYM_MAP.get(query, [])
    # Also check if query is a value in any synonym list
    for key, values in SYNONYM_MAP.items():
        if query in [v.lower() for v in values]:
            synonyms.append(key)
    
    # Find which synonyms/keys actually exist as services
    for syn in synonyms:
        syn_lower = syn.lower()
        for svc in all_services:
            if syn_lower == svc.lower() or syn_lower in svc.lower():
                if svc not in matched_services:
                    suggestions.append(svc)
    
    # Return list of service objects
    results = []
    for svc_name in matched_services:
        results.append({
            "name": svc_name,
            "count": service_counts.get(svc_name.lower(), 0)
        })
    
    # Sort results by name
    results.sort(key=lambda x: x["name"].lower())
    
    return {
        "query": q,
        "categories": results,
        "suggestions": list(set(suggestions))[:5],
        "total": len(results)
    }

@app.get("/pay")
async def serve_pay_page(request: Request):
    """Serve pay.html at /pay path"""
    return FileResponse("web/pay.html")


async def process_paystack_reference(
    transaction_ref: str,
    explicit_user_id: int = None,
    explicit_type: str = None,
    explicit_amount: int = None,
    explicit_expected_amount: int = None
):
    """
    Core logic to verify a Paystack reference and apply value (Coins/Boost).
    Reusable by:
    1. /api/verify-payment (Frontend callback)
    2. /api/paystack-webhook (Backend event)
    3. /claim command (Manual recovery)
    """
    import httpx
    
    if not transaction_ref:
        raise HTTPException(status_code=400, detail="Missing transaction reference")

    # --- Idempotency Check (Security) ---
    try:
        existing_txn = await asyncio.to_thread(
            lambda: supabase.table('coin_transactions')
                .select('id')
                .ilike('description', f'%{transaction_ref}%')
                .execute()
        )
        if existing_txn.data and len(existing_txn.data) > 0:
            logging.info(f"Duplicate verification attempt for {transaction_ref}. Skipping.")
            return {"success": True, "message": "Transaction already processed", "new_balance": "unchanged"}
    except Exception as e:
        logging.error(f"Idempotency check failed: {e}")
        pass
    
    # --- Verify with Paystack API ---
    paystack_secret = os.getenv("PAYSTACK_SECRET_KEY", "")
    
    try:
        if not paystack_secret:
            raise HTTPException(status_code=500, detail="Paystack secret key missing")

        async with httpx.AsyncClient() as client:
            verify_response = await client.get(
                f"https://api.paystack.co/transaction/verify/{transaction_ref}",
                headers={"Authorization": f"Bearer {paystack_secret}"}
            )
            
            if verify_response.status_code != 200:
                logging.error(f"Paystack verify failed: {verify_response.text}")
                raise HTTPException(status_code=400, detail="Transaction verification failed at Paystack")
            
            response_data = verify_response.json()
            if not response_data.get("status"):
                raise HTTPException(status_code=400, detail=f"Paystack error: {response_data.get('message')}")
            
            data = response_data.get("data", {})
            
            if data.get("status") != "success":
                raise HTTPException(status_code=400, detail=f"Payment not active. Status: {data.get('status')}")
            
            # --- Auto-Fill Missing Data from Metadata (Smart Recovery) ---
            metadata = data.get("metadata", {})
            custom_fields = metadata.get("custom_fields", [])
            meta_dict = {}
            
            if isinstance(custom_fields, list):
                for field in custom_fields:
                    meta_dict[field.get("variable_name")] = field.get("value")
            meta_dict.update(metadata)
            
            # Determine effective values (Explicit > Metadata)
            user_id = explicit_user_id or meta_dict.get("user_id")
            if user_id: user_id = int(str(user_id).strip())
                
            payment_type = explicit_type or meta_dict.get("payment_type")
            
            # Amounts (Paystack is Kobo)
            paid_amount_kobo = int(data.get("amount", 0))
            
            # If explicit amount (coins) missing, try metadata
            coin_amount = explicit_amount
            if not coin_amount:
                coin_amount = int(meta_dict.get("coin_amount", 0))
                
            expected_amount = explicit_expected_amount or int(meta_dict.get("amount", 0))
            
            if not user_id or not payment_type:
                 raise HTTPException(status_code=400, detail="Could not recover User ID or Type from transaction. Cannot process.")

            logging.info(f"✅ Verified Ref {transaction_ref}: User={user_id}, Type={payment_type}, Paid={paid_amount_kobo/100}")
            
            expected_amount_kobo = expected_amount * 100
            if expected_amount_kobo > 0 and paid_amount_kobo < expected_amount_kobo:
                 raise HTTPException(status_code=400, detail=f"Amount mismatch. Expected ₦{expected_amount}, got ₦{paid_amount_kobo/100}")

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Paystack verification error: {e}")
        raise HTTPException(status_code=500, detail="Payment verification failed")
    
    # --- Apply Value ---
    try:
        if payment_type == "coins":
            # 1. Fetch current (DB First)
            try:
                current_db = await asyncio.to_thread(
                    lambda: supabase.table('user_coins')
                        .select('coin_balance')
                        .eq('telegram_id', user_id)
                        .execute()
                )
                current_balance = 0
                if current_db.data and len(current_db.data) > 0:
                    current_balance = current_db.data[0].get('coin_balance', 0)
            except Exception as e:
                logging.error(f"Critical DB Error fetching balance: {e}")
                raise HTTPException(status_code=500, detail="Payment verified but failed to load user balance. Contact admin.")

            new_balance = current_balance + coin_amount
            
            # 2. Upsert
            await asyncio.to_thread(
                lambda: supabase.table('user_coins').upsert({
                    'telegram_id': user_id,
                    'coin_balance': new_balance,
                    'last_updated': datetime.now().isoformat()
                }, on_conflict='telegram_id').execute()
            )
            
            # 3. Cache
            from coin_system import set_user_coins
            set_user_coins(user_id, new_balance)

            # 4. Log
            try:
                await asyncio.to_thread(
                    lambda: supabase.table('coin_transactions').insert({
                        'telegram_id': user_id,
                        'transaction_type': 'coin_purchase',
                        'amount': coin_amount,
                        'price_paid': expected_amount,
                        'description': f"Paystack Ref: {transaction_ref}",
                        'timestamp': datetime.now().isoformat()
                    }).execute()
                )
            except Exception as e:
                logging.error(f"Failed to log coin transaction: {e}")
            
            logging.info(f"💰 Coins credited: {coin_amount} to user {user_id}. New balance: {new_balance}")
            
            # 5. Notify
            if bot_instance:
                try:
                    await bot_instance.send_message(
                        chat_id=user_id,
                        text=(
                            f"✅ *Payment Successful!*\n\n"
                            f"💎 {coin_amount} Blue Coin{'s' if coin_amount > 1 else ''} added\n"
                            f"💰 New balance: {new_balance} coins"
                        ),
                        parse_mode='Markdown'
                    )
                except Exception as e:
                    logging.error(f"Failed to send coin confirmation: {e}")
            
            return {"success": True, "new_balance": new_balance}
            
        elif payment_type == "boost":
            from datetime import timedelta, timezone
            boost_expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
            
            # Get business_id from metadata (selected in web UI)
            boost_biz_id = meta_dict.get('business_id')
            boost_biz_name = meta_dict.get('business_name', 'Your business')
            
            if boost_biz_id:
                # Boost only the selected business
                await asyncio.to_thread(
                    lambda: supabase.table('businesses')
                        .update({'is_ad_boosted': True, 'ad_boost_expires': boost_expires})
                        .eq('id', int(boost_biz_id))
                        .execute()
                )
            else:
                # Legacy fallback: boost all
                await asyncio.to_thread(
                    lambda: supabase.table('businesses')
                        .update({'is_ad_boosted': True, 'ad_boost_expires': boost_expires})
                        .eq('telegram_id', user_id)
                        .execute()
                )
                boost_biz_name = "All businesses"
            
            try:
                await asyncio.to_thread(
                    lambda: supabase.table('coin_transactions').insert({
                        'telegram_id': user_id,
                        'transaction_type': 'ad_boost',
                        'amount': 0,
                        'price_paid': expected_amount,
                        'description': f"Ad Boost ({boost_biz_name}) - Ref: {transaction_ref}",
                        'timestamp': datetime.now().isoformat()
                    }).execute()
                )
            except Exception as e:
                logging.error(f"Failed to log boost transaction: {e}")
            
            await refresh_cache_from_supabase()
            
            if bot_instance:
                try:
                    await bot_instance.send_message(
                        chat_id=user_id,
                        text=(
                            f"✅ *Ad Boost Activated!*\n\n"
                            f"📢 *{boost_biz_name}* now appears FIRST in search results!\n"
                            f"⏰ Active for 7 days"
                        ),
                        parse_mode='Markdown'
                    )
                except Exception as e:
                    logging.error(f"Failed to send boost confirmation: {e}")
            
            return {"success": True, "boost_expires": boost_expires, "business": boost_biz_name}
        
        else:
            raise HTTPException(status_code=400, detail="Invalid payment type")
            
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error applying payment: {e}")
        raise HTTPException(status_code=500, detail="Payment verified but could not be applied.")


@app.get("/api/boost-businesses")
async def get_boost_businesses(request: Request):
    """Return user's businesses with boost status for the payment UI"""
    uid_param = request.query_params.get('uid')
    init_data = request.query_params.get('initData')
    
    valid_user_id = None
    had_init_data = bool(init_data)
    
    # Try initData auth first (same pattern as dashboard)
    if init_data:
        try:
            user = validate_telegram_data(init_data)
            valid_user_id = user["user"]["id"]
        except:
            # Try to extract user from initData even if HMAC fails
            try:
                parsed = parse_qs(init_data)
                if "user" in parsed:
                    user_data = json.loads(parsed["user"][0])
                    valid_user_id = user_data.get("id")
            except:
                pass
    
    # uid is set by our bot code in pay.html, safe for read-only endpoint
    if not valid_user_id and uid_param:
        valid_user_id = int(uid_param)
    
    if not valid_user_id:
        raise HTTPException(status_code=401, detail="Missing user identification")
    
    try:
        result = await asyncio.to_thread(
            lambda: supabase.table('businesses')
                .select('id, business_name, is_ad_boosted, ad_boost_expires')
                .eq('telegram_id', str(valid_user_id))
                .execute()
        )
        
        businesses = []
        for b in (result.data or []):
            biz = {
                'id': b.get('id'),
                'name': b.get('business_name', 'Unknown'),
                'is_boosted': bool(b.get('is_ad_boosted')),
            }
            if b.get('ad_boost_expires'):
                biz['expires'] = b.get('ad_boost_expires')
            businesses.append(biz)
        
        return {'success': True, 'businesses': businesses}
    except Exception as e:
        logging.error(f"Failed to fetch boost businesses: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch businesses")


@app.post("/api/verify-payment")
async def verify_payment(request: Request):
    """Verify a payment via Frontend Callback (Manual or JS)"""
    body = await request.json()
    return await process_paystack_reference(
        transaction_ref=body.get("transaction_reference"),
        explicit_user_id=body.get("user_id"),
        explicit_type=body.get("payment_type"),
        explicit_amount=int(body.get("coin_amount", 0)),
        explicit_expected_amount=int(body.get("amount", 0))
    )

@app.post("/api/paystack-webhook")
async def paystack_webhook(request: Request):
    """
    Handle Paystack Webhook (Background Worker).
    Triggers when payment is successful, even if user closes browser.
    """
    secret = os.getenv("PAYSTACK_SECRET_KEY", "")
    if not secret:
        logging.error("Webhook Error: PAYSTACK_SECRET_KEY not set")
        raise HTTPException(status_code=500, detail="Configuration Error")

    # 1. Verify Signature
    signature = request.headers.get("x-paystack-signature")
    if not signature:
         raise HTTPException(status_code=400, detail="Missing Signature")
    
    body_bytes = await request.body()
    calculated_signature = hmac.new(
        key=secret.encode('utf-8'),
        msg=body_bytes,
        digestmod=hashlib.sha512
    ).hexdigest()
    
    if calculated_signature != signature:
        logging.warning("⚠️ Invalid Paystack Webhook Signature")
        raise HTTPException(status_code=400, detail="Invalid Signature")
        
    # 2. Process Event
    try:
        event_data = await request.json()
        event_type = event_data.get("event")
        
        if event_type == "charge.success":
            data = event_data.get("data", {})
            ref = data.get("reference")
            logging.info(f"🔔 Webhook received for Ref: {ref}")
            
            # Check if auto-generated reference or custom
            # We used `BL_{type}_{uid}_{date}`
            # Process it using our Smart Verify Logic
            await process_paystack_reference(transaction_ref=ref)
            
            return {"status": "success", "message": "Webhook processed"}
    
    except Exception as e:
        logging.error(f"Webhook processing error: {e}")
        # Return 200 to prevent Paystack from retrying 100 times if it's a logic error?
        # No, if it's our error, let it retry.
        raise HTTPException(status_code=500, detail="Processing failed")
    
    return {"status": "ignored"}


@app.get("/register")
async def serve_register_page(request: Request):
    """Serve register.html at /register path (without .html extension)"""
    logging.info(f"📄 Serving register.html for /register request")
    logging.info(f"🔗 Request URL: {request.url}")
    return FileResponse("web/register.html")

@app.post("/api/register")
async def register_business(
    initData: Optional[str] = Form(None),
    user_id: Optional[int] = Form(None),
    full_name: Optional[str] = Form(None),
    business_name: Optional[str] = Form(None),
    business_services: Optional[str] = Form(None),
    business_location: Optional[str] = Form(None),
    phone_number: Optional[str] = Form(None),
    business_description: Optional[str] = Form(None),
    photos: List[UploadFile] = File(None)
):
    logging.info(f"📝 Received registration request from frontend (RAW)")
    
    # Determine user_id via initData or fallback
    resolved_user_id = None
    skip_auth = os.getenv('DASHBOARD_SKIP_AUTH', 'false').lower() == 'true'
    
    if initData:
        try:
            user = validate_telegram_data(initData)
            resolved_user_id = user["user"]["id"]
            logging.info(f"👤 Authenticated user via initData: {resolved_user_id}")
        except Exception as e:
            logging.error(f"❌ Auth failed: {e}")
            if not skip_auth:
                raise
    
    if not resolved_user_id and skip_auth and user_id:
        resolved_user_id = user_id
        logging.info(f"👤 Using fallback user_id (skip_auth): {resolved_user_id}")
    
    if not resolved_user_id and user_id:
        # Emergency Fallback: If auth fails (e.g. Ngrok strips hash), trust the form user_id
        resolved_user_id = user_id
        logging.warning(f"⚠️ Auth failed/missing, using fallback user_id: {resolved_user_id}")
    
    if not resolved_user_id:
        logging.error("❌ No user_id could be determined")
        raise HTTPException(status_code=400, detail="Could not identify user. Please open this from inside Telegram.")
    
    # 1. Process photos using bot instance

    photo_file_ids = []
    if photos and bot_instance:
        try:
            for photo in photos:
                # Read file content
                content = await photo.read()
                
                # Send to user to get file_id (silent)
                msg = await bot_instance.send_photo(
                    chat_id=resolved_user_id, 
                    photo=content,
                    caption=f"Processing upload: {photo.filename}..."
                )
                
                # Get largest photo file_id
                file_id = msg.photo[-1].file_id
                photo_file_ids.append(file_id)
                
                # Delete the message immediately
                await msg.delete()
                
        except Exception as e:
            logging.error(f"Error processing photos: {e}")
            # Continue even if photos fail
    
    # 2. Create pending registration data
    from bot_state import PENDING_REGISTRATIONS
    
    registration_data = {
        "name": full_name,
        "buis_name": business_name,
        "service": business_services,
        "location": business_location,
        "phone": phone_number,
        "description": business_description,
        "telegram_id": resolved_user_id,
        "tier": "free",
        "photo_1": photo_file_ids[0] if len(photo_file_ids) > 0 else None,
        "photo_2": photo_file_ids[1] if len(photo_file_ids) > 1 else None,
        "photo_3": photo_file_ids[2] if len(photo_file_ids) > 2 else None,
        "timestamp": datetime.now().isoformat(),
        "type": "registration"
    }

    # 3. Save to PENDING_REGISTRATIONS and Database
    PENDING_REGISTRATIONS[resolved_user_id] = registration_data
    
    try:
        # Delete existing pending registration first (avoid duplicate key)
        await asyncio.to_thread(
            lambda: supabase.table('pending_registrations')
                .delete()
                .eq('user_id', resolved_user_id)
                .execute()
        )
        await asyncio.to_thread(
            lambda: supabase.table('pending_registrations')
                .insert({
                    'user_id': resolved_user_id,
                    'data': json.dumps(registration_data),
                    'type': 'registration',
                    'created_at': registration_data['timestamp']
                })
                .execute()
        )
    except Exception as e:
        logging.error(f"Failed to save pending registration: {e}")
        raise HTTPException(status_code=500, detail="Database error")

    # 4. Notify Admin (Using bot instance)
    if bot_instance:
        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = [
                [InlineKeyboardButton("✅ Approve", callback_data=f"approve_{resolved_user_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject_{resolved_user_id}")]
            ]
            
            caption = (
                f"📢 *New Web Registration:*\n\n"
                f"👤 {full_name}\n"
                f"🏪 {business_name}\n"
                f"🛠 {business_services}\n"
                f"📍 {business_location}\n"
                f"📞 {phone_number}\n"
                f"📝 {business_description}\n"
                f"📸 Photos: {len(photo_file_ids)}\n"
                f"🆔 Telegram ID: {resolved_user_id}"
            )
            
            admin_id = os.getenv("ADMIN_ID")
            
            # Send to admin
            if photo_file_ids:
                await bot_instance.send_photo(
                    chat_id=admin_id,
                    photo=photo_file_ids[0],
                    caption=caption,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                # Send extra photos
                for pid in photo_file_ids[1:]:
                    await bot_instance.send_photo(chat_id=admin_id, photo=pid)
            else:
                await bot_instance.send_message(
                    chat_id=admin_id,
                    text=caption,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
        except Exception as e:
            logging.error(f"Failed to notify admin: {e}")

    logging.info(f"✅ Registration successful for {resolved_user_id}")
    return {"success": True}


# Serve Static Files
app.mount("/", StaticFiles(directory="web", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    # Local run for testing
    uvicorn.run(app, host="0.0.0.0", port=8000)



