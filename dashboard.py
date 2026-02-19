"""
Dashboard module for BlueLink Bot
Handles user dashboard, analytics, and business management
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from telegram.ext import ContextTypes, ConversationHandler
from datetime import datetime, timedelta, timezone
import logging
import asyncio

# Import from main bot
from coin_system import get_user_coins
import os
WEB_APP_URL = os.getenv("WEB_APP_URL", "https://your-ngrok-url.ngrok-free.app")
if "ngrok-free.app" in WEB_APP_URL and "ngrok-skip-browser-warning" not in WEB_APP_URL:
    base_url = WEB_APP_URL.rstrip('/')
    sep = '&' if '?' in base_url else '?'
    WEB_APP_URL = f"{base_url}/{sep}ngrok-skip-browser-warning=true"



from bot_state import (
    USER_REGISTRATIONS, PENDING_REGISTRATIONS, get_cached_businesses,
    get_user_businesses, get_business_count, get_row_value, is_ad_boosted
)


# Conversation states
EDIT_DESCRIPTION, EDIT_PHOTOS, SELECTING_BUSINESS = range(100, 103)

async def get_business_analytics(supabase, business_id, telegram_id):
    """Get analytics for a specific business"""
    try:
        # Get last 7 days of data
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        
        # Get request clicks (paid leads)
        requests = await asyncio.to_thread(
            lambda: supabase.table('lead_requests')
                .select('*')
                .eq('business_id', business_id)
                .gte('request_timestamp', week_ago)
                .execute()
        )
        
        # Get WhatsApp link clicks
        whatsapp_clicks = await asyncio.to_thread(
            lambda: supabase.table('link_clicks')
                .select('*')
                .eq('business_id', business_id)
                .eq('link_type', 'whatsapp')
                .gte('click_timestamp', week_ago)
                .execute()
        )
        
        request_count = len(requests.data) if requests.data else 0
        whatsapp_count = len(whatsapp_clicks.data) if whatsapp_clicks.data else 0
        
        # Calculate conversion rate
        conversion = 0
        if request_count > 0:
            conversion = int((whatsapp_count / request_count) * 100)
        
        return {
            'requests': request_count,
            'whatsapp_clicks': whatsapp_count,
            'conversion': conversion
        }
        
    except Exception as e:
        logging.error(f"Analytics error: {e}")
        return {
            'requests': 0,
            'whatsapp_clicks': 0,
            'conversion': 0
        }


async def get_ad_boost_status(business):
    """Get ad boost status and time remaining"""
    if not business.get('is_ad_boosted'):
        return {
            'active': False,
            'message': '⚠️ Not Boosted'
        }
    
    boost_expires = business.get('ad_boost_expires')
    if not boost_expires:
        return {
            'active': False,
            'message': '⚠️ Not Boosted'
        }
    
    try:
        expires_dt = datetime.fromisoformat(boost_expires.replace('Z', '+00:00'))
        if expires_dt.tzinfo is None:
            expires_dt = expires_dt.replace(tzinfo=timezone.utc)
        
        now = datetime.now(timezone.utc)
        
        if now >= expires_dt:
            return {
                'active': False,
                'message': '⚠️ Boost Expired'
            }
        
        time_left = expires_dt - now
        days_left = time_left.days
        hours_left = time_left.seconds // 3600
        
        if days_left > 0:
            time_msg = f"{days_left} day(s)"
        else:
            time_msg = f"{hours_left} hour(s)"
        
        return {
            'active': True,
            'message': f'✨ Active - {time_msg} left'
        }
        
    except Exception as e:
        logging.error(f"Ad boost status error: {e}")
        return {
            'active': False,
            'message': '⚠️ Status Unknown'
        }


async def dashboard_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main dashboard command - shows all user's businesses"""
    user_id = update.effective_user.id
    
    # Import here to avoid circular import
    from bot_state import supabase



    
    # Check if user has businesses
    business_count = get_business_count(user_id)
    
    if business_count == 0:
        # ✅ NEW: Check for pending registrations
        pending = PENDING_REGISTRATIONS.get(user_id)

        
        if pending:
            await update.message.reply_text(
                "⏳ *Registration Pending*\n\n"
                f"Your business *{pending.get('buis_name', 'Unknown')}* is currently waiting for admin approval.\n\n"
                "You'll receive a notification once it's live! 🎉",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                "📈 *Your Business Dashboard*\n\n"
                "View detailed analytics, manage your businesses, and track lead performance in our new Web UI!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📊 Open Web Dashboard", web_app=WebAppInfo(url=WEB_APP_URL))],
                    [InlineKeyboardButton("🏪 Register New Business", callback_data="goto_register")]
                ]),
                parse_mode='Markdown'
            )
        return ConversationHandler.END




    
    # Get user's businesses
    user_businesses = get_user_businesses(user_id)
    
    if business_count == 1:
        # Single business - show full dashboard
        business_name = user_businesses[0].get('business_name', 'Unknown')
        
        # Get full business data from cache
        cached_businesses = get_cached_businesses()
        business_data = None
        for b in cached_businesses:
            b_tid = get_row_value(b, 'Telegram ID')
            b_name = get_row_value(b, 'Business Name')
            
            if (str(b_tid) == str(user_id) and 
                b_name == business_name):
                business_data = b
                break

        
        if not business_data:
            await update.message.reply_text("❌ Business not found in database.")
            return ConversationHandler.END
        
        # Show single business dashboard
        await show_business_dashboard(update, context, business_data, user_id)
        
    else:
        # Multiple businesses - show selector
        await show_business_selector(update, context, user_businesses)
    
    return ConversationHandler.END


async def show_business_selector(update, context, businesses):
    """Show list of businesses for user to select"""
    message = (
        "📊 *Your Businesses*\n\n"
        f"You have {len(businesses)} registered business(es).\n"
        "Select one to view details:\n"
    )
    
    keyboard = []
    for biz in businesses:
        biz_name = biz.get('business_name', 'Unknown')
        keyboard.append([
            InlineKeyboardButton(
                f"📊 {biz_name}", 
                callback_data=f"viewbiz_{biz_name}"
            )
        ])
    
    keyboard.append([InlineKeyboardButton("🔙 Close", callback_data="close_dashboard")])
    
    await update.message.reply_text(
        message,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_business_dashboard(update, context, business_data, user_id):
    """Show detailed dashboard for a single business"""
    from bot_state import supabase


    
    business_id = get_row_value(business_data, 'id') # Standard 'id' usually, but safe
    business_name = get_row_value(business_data, 'Business Name') or 'Unknown'
    service = get_row_value(business_data, 'Business Services') or 'N/A'
    location = get_row_value(business_data, 'Business Location') or 'N/A'
    description = get_row_value(business_data, 'Business Description') or 'No description'

    
    # Get analytics
    analytics = await get_business_analytics(supabase, business_id, user_id)
    
    # Get coin balance
    coins = get_user_coins(user_id)
    
    # Get ad boost status
    boost_status = await get_ad_boost_status(business_data)
    
    # Build dashboard message
    dashboard_text = (
        f"━━━━━━━━━━━━━━━━\n"
        f"📊 *{business_name}*\n"
        f"━━━━━━━━━━━━━━━━\n\n"
        f"💰 *Blue Coins:* {coins}\n"
        f"📞 *Requests (7 days):* {analytics['requests']}\n"
        f"🔗 *WhatsApp Clicks:* {analytics['whatsapp_clicks']}\n"
        f"📈 *Conversion:* {analytics['conversion']}%\n\n"
        f"🛠️ *Service:* {service}\n"
        f"📍 *Location:* {location}\n\n"
        f"📢 *Ad Boost:* {boost_status['message']}\n"
        f"━━━━━━━━━━━━━━━━"
    )
    
    # Build inline buttons
    keyboard = [
        [
            InlineKeyboardButton("📝 Edit Description", callback_data=f"editdesc_{business_id}"),
            InlineKeyboardButton("📸 Change Photos", callback_data=f"editphotos_{business_id}")
        ],
        [
            InlineKeyboardButton("💵 Buy Coins", callback_data="goto_buycoins"),
            InlineKeyboardButton("📢 Boost Ads", callback_data="goto_adboost")
        ],
        [
            InlineKeyboardButton("📊 Full Analytics", callback_data=f"analytics_{business_id}")
        ],
        [
            InlineKeyboardButton("🔙 Close", callback_data="close_dashboard")
        ]
    ]
    
    # Send or edit message
    if update.callback_query:
        await update.callback_query.edit_message_text(
            dashboard_text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.message.reply_text(
            dashboard_text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def show_full_analytics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show detailed analytics for a business"""
    query = update.callback_query
    await query.answer()
    
    business_id = int(query.data.split("_")[1])
    user_id = query.from_user.id
    
    from bot_state import supabase, get_cached_businesses

    
    # Get business data
    cached_businesses = get_cached_businesses()
    business = None
    for b in cached_businesses:
        if b.get('id') == business_id:
            business = b
            break
    
    if not business or str(business.get('telegram_id')) != str(user_id):
        await query.edit_message_text("❌ Business not found or unauthorized.")
        return
    
    business_name = business.get('business_name', 'Unknown')
    
    # Get all-time analytics
    try:
        # Total requests
        all_requests = await asyncio.to_thread(
            lambda: supabase.table('lead_requests')
                .select('*')
                .eq('business_id', business_id)
                .execute()
        )
        
        # Total WhatsApp clicks
        all_clicks = await asyncio.to_thread(
            lambda: supabase.table('link_clicks')
                .select('*')
                .eq('business_id', business_id)
                .execute()
        )
        
        # Recent 7 days
        week_analytics = await get_business_analytics(supabase, business_id, user_id)
        
        # Recent 30 days
        month_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        
        month_requests = await asyncio.to_thread(
            lambda: supabase.table('lead_requests')
                .select('*')
                .eq('business_id', business_id)
                .gte('request_timestamp', month_ago)
                .execute()
        )
        
        month_clicks = await asyncio.to_thread(
            lambda: supabase.table('link_clicks')
                .select('*')
                .eq('business_id', business_id)
                .gte('click_timestamp', month_ago)
                .execute()
        )
        
        total_requests = len(all_requests.data) if all_requests.data else 0
        total_clicks = len(all_clicks.data) if all_clicks.data else 0
        month_req = len(month_requests.data) if month_requests.data else 0
        month_clk = len(month_clicks.data) if month_clicks.data else 0
        
        # Calculate overall conversion
        overall_conversion = 0
        if total_requests > 0:
            overall_conversion = int((total_clicks / total_requests) * 100)
        
        analytics_text = (
            f"📊 *Analytics - {business_name}*\n"
            f"━━━━━━━━━━━━━━━━\n\n"
            f"📈 *All Time Stats:*\n"
            f"📞 Total Requests: {total_requests}\n"
            f"🔗 Total Clicks: {total_clicks}\n"
            f"📊 Conversion: {overall_conversion}%\n\n"
            f"📅 *Last 30 Days:*\n"
            f"📞 Requests: {month_req}\n"
            f"🔗 Clicks: {month_clk}\n\n"
            f"📅 *Last 7 Days:*\n"
            f"📞 Requests: {week_analytics['requests']}\n"
            f"🔗 Clicks: {week_analytics['whatsapp_clicks']}\n"
            f"📈 Conversion: {week_analytics['conversion']}%\n"
            f"━━━━━━━━━━━━━━━━"
        )
        
        keyboard = [
            [InlineKeyboardButton("🔙 Back to Dashboard", callback_data=f"viewbiz_{business_name}")]
        ]
        
        await query.edit_message_text(
            analytics_text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        logging.error(f"Full analytics error: {e}")
        await query.edit_message_text(
            "❌ Error loading analytics. Please try again.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data=f"viewbiz_{business_name}")]
            ])
        )


async def edit_description_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start editing business description"""
    query = update.callback_query
    await query.answer()
    
    business_id = int(query.data.split("_")[1])
    user_id = query.from_user.id
    


    
    # Verify ownership
    cached_businesses = get_cached_businesses()
    business = None
    for b in cached_businesses:
        if b.get('id') == business_id and str(b.get('telegram_id')) == str(user_id):
            business = b
            break
    
    if not business:
        await query.edit_message_text("❌ Business not found or unauthorized.")
        return ConversationHandler.END
    
    # Store business_id in context
    context.user_data['editing_business_id'] = business_id
    context.user_data['business_name'] = business.get('business_name')
    
    current_desc = business.get('business_description', 'No description')
    
    await query.edit_message_text(
        f"📝 *Edit Description*\n\n"
        f"Current description:\n_{current_desc}_\n\n"
        f"Send your new description (max 1000 characters):",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_edit")]
        ])
    )
    
    return EDIT_DESCRIPTION


async def save_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save the new description"""
    new_description = update.message.text.strip()[:1000]
    business_id = context.user_data.get('editing_business_id')
    business_name = context.user_data.get('business_name', 'Unknown')
    
    from bot_state import supabase, refresh_cache_from_supabase


    
    try:
        # Update in database
        await asyncio.to_thread(
            lambda: supabase.table('businesses')
                .update({'business_description': new_description})
                .eq('id', business_id)
                .execute()
        )
        
        # Refresh cache
        await asyncio.sleep(1)
        await refresh_cache_from_supabase()
        
        await update.message.reply_text(
            f"✅ Description updated successfully!\n\n"
            f"New description:\n_{new_description}_",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 Back to Dashboard", callback_data=f"viewbiz_{business_name}")]
            ])
        )
        
        context.user_data.clear()
        return ConversationHandler.END
        
    except Exception as e:
        logging.error(f"Save description error: {e}")
        await update.message.reply_text(
            "❌ Error updating description. Please try again.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data=f"viewbiz_{business_name}")]
            ])
        )
        return ConversationHandler.END


async def edit_photos_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start editing business photos"""
    query = update.callback_query
    await query.answer()
    
    business_id = int(query.data.split("_")[1])
    user_id = query.from_user.id
    


    
    # Verify ownership
    cached_businesses = get_cached_businesses()
    business = None
    for b in cached_businesses:
        if b.get('id') == business_id and str(b.get('telegram_id')) == str(user_id):
            business = b
            break
    
    if not business:
        await query.edit_message_text("❌ Business not found or unauthorized.")
        return ConversationHandler.END
    
    # Store business_id in context
    context.user_data['editing_business_id'] = business_id
    context.user_data['business_name'] = business.get('business_name')
    context.user_data['new_photos'] = []
    
    # Show current photos
    current_photos = [
        business.get('photo_1'),
        business.get('photo_2'),
        business.get('photo_3')
    ]
    current_photos = [p for p in current_photos if p]
    
    await query.edit_message_text(
        f"📸 *Change Photos*\n\n"
        f"Current photos: {len(current_photos)}/3\n\n"
        f"Send up to 3 new photos (one by one).\n"
        f"Tap 'Done' when finished.",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Done (Keep Current)", callback_data="photos_done")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_edit")]
        ])
    )
    
    return EDIT_PHOTOS


async def receive_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive new photos from user"""
    if not update.message.photo:
        await update.message.reply_text(
            "⚠️ Please send a photo, or tap 'Done' to finish.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Done", callback_data="photos_done")],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel_edit")]
            ])
        )
        return EDIT_PHOTOS
    
    new_photos = context.user_data.get('new_photos', [])
    
    if len(new_photos) >= 3:
        await update.message.reply_text(
            "⚠️ Maximum 3 photos allowed. Tap 'Done' to save.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Done", callback_data="photos_done")],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel_edit")]
            ])
        )
        return EDIT_PHOTOS
    
    # Get photo file_id
    photo_file_id = update.message.photo[-1].file_id
    new_photos.append(photo_file_id)
    context.user_data['new_photos'] = new_photos
    
    remaining = 3 - len(new_photos)
    
    if remaining > 0:
        await update.message.reply_text(
            f"✅ Photo {len(new_photos)}/3 received!\n\n"
            f"You can send {remaining} more, or tap 'Done'.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Done", callback_data="photos_done")],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel_edit")]
            ])
        )
    else:
        await update.message.reply_text(
            "✅ All 3 photos received! Tap 'Done' to save.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Done & Save", callback_data="photos_done")],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel_edit")]
            ])
        )
    
    return EDIT_PHOTOS


async def save_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save the new photos"""
    query = update.callback_query
    await query.answer()
    
    business_id = context.user_data.get('editing_business_id')
    business_name = context.user_data.get('business_name', 'Unknown')
    new_photos = context.user_data.get('new_photos', [])
    
    from bot_state import supabase, refresh_cache_from_supabase


    
    if not new_photos:
        await query.edit_message_text(
            "⚠️ No new photos received. Photos unchanged.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 Back to Dashboard", callback_data=f"viewbiz_{business_name}")]
            ])
        )
        context.user_data.clear()
        return ConversationHandler.END
    
    try:
        # Update photos in database
        update_data = {
            'photo_1': new_photos[0] if len(new_photos) > 0 else None,
            'photo_2': new_photos[1] if len(new_photos) > 1 else None,
            'photo_3': new_photos[2] if len(new_photos) > 2 else None
        }
        
        await asyncio.to_thread(
            lambda: supabase.table('businesses')
                .update(update_data)
                .eq('id', business_id)
                .execute()
        )
        
        # Refresh cache
        await asyncio.sleep(1)
        await refresh_cache_from_supabase()
        
        await query.edit_message_text(
            f"✅ Photos updated successfully!\n\n"
            f"New photos: {len(new_photos)}/3 uploaded",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 Back to Dashboard", callback_data=f"viewbiz_{business_name}")]
            ])
        )
        
        context.user_data.clear()
        return ConversationHandler.END
        
    except Exception as e:
        logging.error(f"Save photos error: {e}")
        await query.edit_message_text(
            "❌ Error updating photos. Please try again.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data=f"viewbiz_{business_name}")]
            ])
        )
        context.user_data.clear()
        return ConversationHandler.END


async def handle_dashboard_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all dashboard-related callback queries"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    



    
    # Close dashboard
    if data == "close_dashboard":
        await query.delete_message()
        return
    
    # Cancel edit
    if data == "cancel_edit":
        business_name = context.user_data.get('business_name', 'Unknown')
        context.user_data.clear()
        
        # Go back to dashboard
        cached_businesses = get_cached_businesses()
        business = None
        for b in cached_businesses:
            b_tid = get_row_value(b, 'Telegram ID')
            b_name = get_row_value(b, 'Business Name')
            
            if (str(b_tid) == str(user_id) and 
                b_name == business_name):
                business = b
                break

        
        if business:
            await show_business_dashboard(update, context, business, user_id)
        else:
            await query.edit_message_text("Dashboard closed.")
        return ConversationHandler.END
    
    # View specific business
    if data.startswith("viewbiz_"):
        business_name = data.replace("viewbiz_", "")
        
        # Get business data
        cached_businesses = get_cached_businesses()
        business = None
        for b in cached_businesses:
            b_tid = get_row_value(b, 'Telegram ID')
            b_name = get_row_value(b, 'Business Name')
            
            if (str(b_tid) == str(user_id) and 
                b_name == business_name):
                business = b
                break

        
        if business:
            await show_business_dashboard(update, context, business, user_id)
        else:
            await query.edit_message_text("❌ Business not found.")
        return
    
    # Navigation to other sections
    if data == "goto_register":
        await query.edit_message_text(
            "To register a business, tap the menu button and select:\n"
            "🏪 Register Your Business"
        )
        return
    
    if data == "goto_buycoins":
        await query.edit_message_text(
            "To buy Blue Coins, tap the menu button and select:\n"
            "💰 Buy Blue Coins"
        )
        return
    
    if data == "goto_adboost":
        await query.edit_message_text(
            "To boost with ads, tap the menu button and select:\n"
            "📢 Boost with Ads"
        )
        return