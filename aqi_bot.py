import requests
import logging
import os
import sys
import asyncio 
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, BotCommand, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters, ConversationHandler, CallbackQueryHandler
from http import HTTPStatus
from dotenv import load_dotenv

# --- Configuration & Setup ---

# Use Environment Variables for sensitive data and configuration
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
IQAIR_API_KEY = os.getenv("IQAIR_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", "8080"))

# Fixed parameters for API calls
COUNTRY_NAME = "Uzbekistan"

# Conversation States
START_CHOOSING_STATE, CHOOSING_CITY, GETTING_AQI = range(3)

# Define the region/city data structure for Uzbekistan
# Note: 'state_param' should match the name IQAir expects (often the region name).
REGIONS_DATA = {
    # Regions with available cities
    "Andijon": {
        "state_param": "Andijon", 
        "cities": ["Andijon"]
    },
    "Bukhara": {
        "state_param": "Bukhara", 
        "cities": ["Bukhara", "Kagan"]
    },
    "Fergana": {
        "state_param": "Fergana", 
        "cities": ["Fergana", "Kirguli"]
    },
    "Jizzax": {
        "state_param": "Jizzax", 
        "cities": ["Jizzax"]
    },
    "Karakalpakstan": {
        "state_param": "Karakalpakstan", 
        "cities": ["Nukus"]
    },
    "Namangan": {
        "state_param": "Namangan", 
        "cities": ["Namangan"]
    },
    "Navoiy": {
        "state_param": "Navoiy", 
        "cities": ["Navoiy", "Zarafshan"]
    },
    "Samarqand": {
        "state_param": "Samarqand", 
        "cities": ["Samarqand"]
    },
    "Sirdaryo": {
        "state_param": "Sirdaryo", 
        "cities": ["Guliston"]
    },
    "Toshkent": {
        "state_param": "Toshkent", 
        "cities": ["Amirsoy", "Chorvoq", "G'azalkent", "Parkent", "Qibray", "Salor", "Sidzhak", "Tuytepa", "Urtaowul"]
    },
    "Toshkent Shahri": {
        "state_param": "Toshkent Shahri",
        "cities": ["Tashkent"]
    },
    "Xorazm": {
        "state_param": "Xorazm", 
        "cities": ["Pitnak", "Urganch"]
    },
    
    # Regions with NO known available cities (only shows Go Back button)
    "Qashqadaryo": {
        "state_param": "Qashqadaryo",
        "cities": []
    },
    "Surxondaryo": {
        "state_param": "Surxondaryo",
        "cities": []
    },
}

# Basic Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# --- AQI Fetching Function (UNCHANGED) ---

def fetch_air_quality(latitude=None, longitude=None, city=None, state=None, country=COUNTRY_NAME):
    """
    Fetches air quality data from the IQAir API.
    Can use coordinates (latitude/longitude) OR city/state/country.
    """
    if not IQAIR_API_KEY:
        logger.error("IQAIR_API_KEY is not set.")
        return "❌ IQAir API Key is missing. Cannot fetch data."

    try:
        if latitude is not None and longitude is not None:
            # Endpoint for coordinates: /v2/nearest_city
            api_endpoint = f"http://api.airvisual.com/v2/nearest_city"
            location_name = f"Your Location (Lat: {latitude:.2f}, Lon: {longitude:.2f})"
            params = {
                'lat': latitude,
                'lon': longitude,
                'key': IQAIR_API_KEY
            }
        elif city and state:
            # Endpoint for city/state/country: /v2/city
            api_endpoint = f"http://api.airvisual.com/v2/city"
            location_name = f"{city}, {state}"
            params = {
                'city': city,
                'state': state,
                'country': country,
                'key': IQAIR_API_KEY
            }
        else:
            return "❌ Invalid location parameters provided."
        
        # Make the GET request to the IQAir API
        response = requests.get(api_endpoint, params=params)
        response.raise_for_status() 
        data = response.json()
        
        # Check if the API returned an 'success' status
        if data.get('status') != 'success':
            error_message = data.get('data', 'Unknown API error')
            logger.error(f"IQAir API Error for {location_name}: {error_message}")
            return f"❌ Error fetching data for {location_name}: *{error_message}*"

        # --- Data Extraction ---
        city_data = data['data']
        current_data = city_data['current']
        
        # Update location name from API response for the nearest_city command
        if latitude is not None:
            city_name_from_api = city_data.get('city', 'Unknown City')
            state_name_from_api = city_data.get('state', '')
            location_name = f"Closest City: {city_name_from_api}, {state_name_from_api}"
        
        aqi_us = current_data['pollution']['aqius']
        main_pollutant = current_data['pollution']['mainus']
        temperature = current_data['weather']['tp']
        
        # Determine air quality description based on US AQI standard
        quality_info = get_aqi_description(aqi_us)
        
        # Format the final output message
        message = (
            f"**{location_name} Air Quality Report** 💨\n\n"
            f"**Current AQI (US):** {aqi_us} - {quality_info['level']}\n"
            f"**Main Pollutant:** {main_pollutant}\n"
            f"**Temperature:** {temperature}°C\n\n"
            f"ℹ️ *{quality_info['message']}*"
        )
        return message

    except requests.exceptions.RequestException as e:
        logger.error(f"HTTP Request Failed: {e}")
        return "❌ Network or API communication error."
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        return "❌ An internal error occurred while processing the data."

# --- AQI Description Logic (US Standard) (UNCHANGED) ---
def get_aqi_description(aqi):
    """Returns a dictionary with the AQI level and a health message."""
    if 0 <= aqi <= 50:
        return {'level': '🟢 Good', 'message': 'Air quality is considered satisfactory; little or no risk.'}
    elif 51 <= aqi <= 100:
        return {'level': '🟡 Moderate', 'message': 'Air quality is acceptable; sensitive individuals should consider limiting outdoor activity.'}
    elif 101 <= aqi <= 150:
        return {'level': '🟠 Unhealthy for Sensitive Groups', 'message': 'Members of sensitive groups may experience health effects. General public not likely to be affected.'}
    elif 151 <= aqi <= 200:
        return {'level': '🔴 Unhealthy', 'message': 'Everyone may begin to experience health effects; sensitive groups should limit time outdoors.'}
    elif 201 <= aqi <= 300:
        return {'level': '🟣 Very Unhealthy', 'message': 'Health warnings of emergency conditions. The entire population is more likely to be affected.'}
    else: # 301+
        return {'level': '🟤 Hazardous', 'message': 'Health alert: everyone should avoid all outdoor exertion.'}


# --- CONVERSATION HANDLERS (MODIFIED select_city) ---

async def select_region(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Sends a message with inline buttons to choose a region, or handles 'Go Back'."""
    
    if update.message:
        send_func = update.message.reply_text
    elif update.callback_query:
        query = update.callback_query
        await query.answer()
        send_func = query.edit_message_text

    keyboard = []
    for region_name in sorted(REGIONS_DATA.keys()): # Sort for alphabetical order
        callback_data = f"state:{region_name}"
        keyboard.append([InlineKeyboardButton(region_name, callback_data=callback_data)])

    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        await send_func(
            "🗺️ Please choose a region in Uzbekistan:",
            reply_markup=reply_markup
        )
    elif update.callback_query:
        await send_func(
            "🗺️ Please choose a region in Uzbekistan:",
            reply_markup=reply_markup
        )
        
    return CHOOSING_CITY

async def select_city(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processes the chosen region and sends a list of cities or just Go Back button."""
    query = update.callback_query
    await query.answer()
    
    region_name = query.data.split(":")[1]
    
    # Store the region's state_param for later use
    state_param = REGIONS_DATA[region_name]["state_param"]
    context.user_data['selected_state_param'] = state_param
    
    cities = REGIONS_DATA[region_name]["cities"]
    keyboard = []
    
    # --- NEW CONDITIONAL LOGIC ---
    if cities:
        # Region HAS cities, build city buttons
        for city_name in cities:
            callback_data = f"city:{city_name}"
            keyboard.append([InlineKeyboardButton(city_name, callback_data=callback_data)])
        
        message_text = f"🏙️ Now choose a city in {region_name}:"
        
        # The next state is GETTING_AQI
        next_state = GETTING_AQI
    else:
        # Region has NO cities, show only an informational message
        message_text = f"⚠️ Unfortunately, we do not have AQI monitoring stations listed for any city in **{region_name}**. Please choose a different region."
        
        # The next state remains GETTING_AQI to process the 'Go Back' button
        next_state = GETTING_AQI

    # Add Go Back button regardless of city list presence
    keyboard.append([InlineKeyboardButton("⬅️ Go Back", callback_data="go_back_to_regions")]) 

    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        message_text,
        reply_markup=reply_markup,
        parse_mode='Markdown' # Use Markdown for the bold warning text
    )
    
    return next_state # Move to the final state

async def get_city_aqi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processes the chosen city, the 'Go Back' action, or outputs the AQI report."""
    query = update.callback_query
    await query.answer()
    
    # Check for the 'Go Back' action first
    if query.data == 'go_back_to_regions':
        return await select_region(update, context)
        
    # --- Proceed with AQI Fetching ---
    city_name = query.data.split(":")[1]
    state_param = context.user_data.get('selected_state_param')
    
    if not state_param:
        await query.edit_message_text("❌ Error: State data lost. Please start again with /regions.")
        return ConversationHandler.END

    await query.edit_message_text(f"Fetching AQI for {city_name}...")

    # Fetch data using city/state parameters
    report_message = fetch_air_quality(
        city=city_name, 
        state=state_param, 
        country=COUNTRY_NAME
    )
    
    await query.edit_message_text(report_message, parse_mode='Markdown')
    
    # Clear user data and end the conversation
    context.user_data.clear()
    return ConversationHandler.END

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the conversation (used for fallbacks)."""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text("Conversation cancelled.")
    else:
        await update.message.reply_text("Conversation cancelled.")
        
    context.user_data.clear()
    return ConversationHandler.END


# --- STANDARD COMMAND HANDLERS (UNCHANGED) ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Greets the user and explains the bot."""
    await update.message.reply_markdown(
        "👋 Hello! I'm the **Uzbekistan AQI Bot**.\n\n"
        "Use the **Menu button** (the `/` symbol) to access my main commands:\n"
        "• `/regions` to choose a city for AQI data.\n"
        "• `/mylocation` for **your current location**'s AQI."
    )

async def mylocation_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompts the user to share their location using a custom keyboard."""
    location_button = KeyboardButton(
        text="📍 Share My Current Location",
        request_location=True
    )
    reply_markup = ReplyKeyboardMarkup(
        [[location_button]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await update.message.reply_text(
        "Please tap the button below to share your current location so I can fetch the AQI data for the nearest station.",
        reply_markup=reply_markup
    )

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receives location data and uses the coordinates to fetch AQI."""
    user_location = update.message.location
    latitude = user_location.latitude
    longitude = user_location.longitude
    
    await update.message.reply_text(
        "Location received! Searching for the nearest AQI station...",
        reply_markup=ReplyKeyboardRemove()
    )
    
    report_message = fetch_air_quality(latitude=latitude, longitude=longitude)
    
    await update.message.reply_markdown(report_message)
    
async def health_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Simple health check command for manual verification in Telegram."""
    await update.message.reply_text("✅ Bot is running and ready to fetch AQI data.")
    
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log the error and notify the user."""
    logger.error(f"Update '{update}' caused error '{context.error}'")


# --- ASYNC SETUP FUNCTION (UNCHANGED) ---
async def post_init_setup(application):
    """Sets up the bot's menu commands using set_my_commands."""
    logger.info("Setting Telegram Bot Menu Commands...")
    
    commands = [
        BotCommand("regions", "Choose a region and city for AQI data"),
        BotCommand("mylocation", "Get AQI for your current location"),
    ]
    
    await application.bot.set_my_commands(commands)
    logger.info("Bot Menu Commands successfully set.")


# --- Main Application Runner (UNCHANGED) ---
def start():
    """
    Starts the bot in either Webhook (Production) or Polling (Local) mode.
    """
    load_dotenv()

    if not TELEGRAM_TOKEN or not IQAIR_API_KEY:
        logger.error(
            "ERROR: The TELEGRAM_TOKEN or IQAIR_API_KEY environment variable is not set. Exiting."
        )
        sys.exit(1)

    application = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init_setup).build()

    # Define the Conversation Handler
    region_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("regions", select_region)], 
        
        states={
            CHOOSING_CITY: [
                CallbackQueryHandler(select_city, pattern='^state:'), 
                CallbackQueryHandler(cancel_conversation, pattern='^cancel$'),
            ],
            
            GETTING_AQI: [
                # Handles City selection OR the Go Back button
                CallbackQueryHandler(get_city_aqi), 
            ],
        },
        
        fallbacks=[CommandHandler("start", start_command), CommandHandler("cancel", cancel_conversation)],
    )
    
    # 3. Register handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("mylocation", mylocation_command))
    application.add_handler(MessageHandler(filters.LOCATION, handle_location))
    application.add_handler(CommandHandler("health", health_check))
    
    application.add_handler(region_conv_handler)
    application.add_error_handler(error_handler)

    # 4. Run based on environment
    if WEBHOOK_URL:
        logger.info(f"Setting Webhook to: {WEBHOOK_URL}/{TELEGRAM_TOKEN} on port {PORT}")
        
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TELEGRAM_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}",
        )
        logger.info(f"Bot started in **Webhook Mode** on port {PORT}. URL: {WEBHOOK_URL}")
    else:
        logger.info("Bot started in **Polling Mode**. Send /regions or /mylocation in Telegram to test.")
        application.run_polling(poll_interval=1.0)

if __name__ == '__main__':
    start()
