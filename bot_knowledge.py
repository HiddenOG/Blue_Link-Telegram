# BlueLink Bot — Complete Knowledge Base
# This document is injected as RAG context into Gemini's system prompt
# so the AI assistant "Blue" can answer any question about the bot.

BOT_KNOWLEDGE = """

=== BLUELINK BOT OVERVIEW ===
BlueLink Bot is a Nigerian business directory on Telegram. It connects customers looking for services with registered businesses. The bot is called "Blue" and the currency is "Blue Coins".

=== COMMANDS & BUTTONS ===
/start - Main menu with all buttons. Clears chat memory for a fresh conversation.
/find - Start the "Find a Service" flow (type service → choose location → see results).
/register - Start the "Register Your Business" flow.
/cancel - Cancel any ongoing operation and return to main menu.

Main keyboard buttons:
- 🔍 Find a Service → starts the service search flow
- 🏪 Register Your Business → starts registration
- 💰 Buy Blue Coins → opens coin purchase menu
- 📊 My Dashboard → opens the web-based dashboard
- 📋 Business Catalog → browse all available services
- 📩 Message Admin → chat with the admin directly
- 📢 Join Our Channel → link to the BlueLink channel

=== FINDING A SERVICE ===
1. User taps "🔍 Find a Service" or types /find
2. User types the service they need (e.g., "plumber", "electrician")
3. Bot shows available locations where that service exists
4. User picks a location
5. Bot shows matching businesses with inline "📞 Request Service" buttons
6. User taps the button → a Business Profile page opens
7. Profile shows: business photos (gallery), name, service, location, description
8. User taps "💬 Contact on WhatsApp" → 1 Blue Coin is deducted from the business owner → WhatsApp opens with a pre-filled message
   - Featured/Ad Boosted businesses appear first in results with a ⭐ FEATURED AD badge
   - If user already contacted this business within 7 days, no extra coin is deducted but WhatsApp still opens

=== REGISTERING A BUSINESS ===
1. User taps "🏪 Register Your Business" or types /register
2. User enters: Business Name, Service, Location, Phone Number, Description
3. User can upload up to 3 photos (optional)
4. Registration is submitted for admin approval
5. Admin reviews and approves or rejects
6. Once approved, the business appears in search results
7. New businesses get 5 free Blue Coins upon approval
8. Users can register unlimited businesses (no limit)

=== BLUE COINS SYSTEM ===
Blue Coins are the in-app currency. They work like credits for receiving customer leads.

How it works:
- Every new registered business gets 5 FREE Blue Coins to start
- When a customer taps "Contact on WhatsApp" on your business profile, 1 coin is deducted from you (the business owner)
- If your business has 0 coins, customers cannot contact you (your listing shows as "unavailable")
- You need to buy more coins to keep receiving leads and customer requests

Coin Prices (Nigerian Naira):
- 1 coin = ₦200
- 5 coins = ₦900 (save 10%)
- 10 coins = ₦1,600 (save 20%)
- 25 coins = ₦3,500 (save 30%)
- 50 coins = ₦6,000 (save 40%)

How to buy coins:
1. Tap "💰 Buy Blue Coins"
2. Choose a package (1, 5, 10, 25, or 50 coins)
3. Transfer the amount to the provided bank account details
4. Upload a screenshot of your payment proof
5. Admin verifies and credits your coins (usually within a few hours)

Fair use protections:
- A customer can only contact the SAME business once every 7 days. If they contact you again within 7 days, no extra coin is deducted — you are not charged twice for the same customer.
- This protects business owners from unnecessary coin deduction.

=== AD BOOST / FEATURED ADS ===
Business owners can pay for an "Ad Boost" to get premium visibility.

What Ad Boost does:
- Your business appears FIRST in search results (above all non-boosted businesses)
- Your listing gets a ⭐ FEATURED AD badge
- More visibility = more customer leads

Ad Boost details:
- Cost: ₦3,500 for 7 days
- Duration: Lasts exactly 7 days, then expires automatically
- How to get it: Purchase via the bot → upload payment proof → admin approves → boost activated immediately
- You can re-purchase after it expires

=== DASHBOARD ===
The "📊 My Dashboard" button opens your personal business dashboard where you can:
- See your business performance stats (total requests, WhatsApp clicks, conversion rate)
- View a 7-day activity chart
- See your current Blue Coin balance
- View your business details (service, location, boost status)
- Edit your business info (name, service, location, phone, description)
- Manage your photos (view, remove, or add new photos — max 3 per business)
- Delete a business if needed
- Switch between multiple businesses if you have more than one

=== BUSINESS CATALOG ===
The "📋 Business Catalog" opens a page showing all available services in the directory.
- Browse all service categories at a glance
- See which locations each service is available in
- Great for discovering what services exist before searching

=== BUSINESS PROFILE PAGE ===
When a customer taps "📞 Request Service" on a search result, a profile page opens showing:
- Photo gallery at the top (swipe to see all photos, tap to view full size)
- Business name, service category, and location
- Full business description
- "💬 Contact on WhatsApp" button
- Tapping Contact opens WhatsApp with a pre-filled message so the customer can easily reach you
- The business owner gets a notification with a link to message the customer back

=== LISTING TIERS ===
Free Listing:
- No cost to register
- Requires admin approval before going live
- Standard visibility in search results

Premium Listing:
- Costs ₦300 one-time
- Upload payment proof during registration
- Gets priority placement in search results
- Faster approval process

=== WAITING FOR APPROVAL ===
After registering your business, it goes to admin for review. This is normal!
- You will receive a notification when your business is approved or rejected
- If rejected, you can try registering again with corrected information
- If you need it faster, you can message the admin via "📩 Message Admin"
- You cannot register another business while one is pending approval

=== LEGACY BUSINESSES ===
Some businesses were registered before the coin system existed. These are called "Legacy Listings":
- Customers can contact them directly via WhatsApp for free (no coin involved)
- They show a 📜 Legacy Listing badge
- They do not use the Blue Coin system

=== FREQUENTLY ASKED QUESTIONS ===
Q: How do I find a service?
A: Tap "🔍 Find a Service" or type /find, then tell me what service you need.

Q: How much are Blue Coins?
A: Starting from ₦200 for 1 coin, up to ₦6,000 for 50 coins. Buy more, save more!

Q: What happens when I run out of coins?
A: Customers won't be able to contact you until you buy more coins. Tap "💰 Buy Blue Coins" to top up.

Q: Can I register more than one business?
A: Yes! You can register unlimited businesses.

Q: How long does approval take?
A: Usually within a few hours. You can message the admin if you need it faster.

Q: How does Ad Boost work?
A: Pay ₦3,500 and your business appears at the top of search results with a ⭐ badge for 7 days.

Q: Will I be charged twice if the same customer contacts me again?
A: No! There is a 7-day protection. The same customer contacting you again within 7 days does NOT deduct another coin.

Q: How do I edit my business info?
A: Open your Dashboard (tap "📊 My Dashboard") and use the edit feature there.

Q: How do I add photos to my business?
A: Go to your Dashboard, tap Edit, and use the photo manager to add up to 3 photos.
"""
