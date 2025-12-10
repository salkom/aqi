import requests
import logging
import os
import sys
import asyncio
import functools
from http import HTTPStatus
from dotenv import load_dotenv

from telegram import (
    Update, 
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    ReplyKeyboardRemove, 
    BotCommand, 
    constants # For ChatAction.TYPING
)
from telegram.ext import (
    ApplicationBuilder, 
    CommandHandler, 
    ContextTypes, 
    MessageHandler, 
    filters, 
    ConversationHandler
)

# --- Configuration & Setup ---

# Load environment variables once at the top
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
IQAIR_API_KEY = os.getenv("IQAIR_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", "8080"))

# Fixed parameters for API calls
COUNTRY_NAME = "Uzbekistan"

# Conversation States
# CHOOSING_REGION is the state where the Region Reply Keyboard (Level 2) is visible
CHOOSING_REGION, CHOOSING_CITY = range(2) 

# Define the region/city data structure
# NOTE: 'state_param' must match the IQAir English spelling exactly.
REGIONS_DATA = {
    "Andijon": { "state_param": "Andijon", "cities": ["Andijon"] },
    "Bukhara": { "state_param": "Bukhara", "cities": ["Bukhara", "Kagan"] },
    "Fergana": { "state_param": "Fergana", "cities": ["Fergana", "Kirguli"] },
    "Jizzax": { "state_param": "Jizzax", "cities": ["Jizzax"] },
    "Karakalpakstan": { "state_param": "Karakalpakstan", "cities": ["Nukus"] },
    "Namangan": { "state_param": "Namangan", "cities": ["Namangan"] },
    "Navoiy": { "state_param": "Navoiy", "cities": ["Navoiy", "Zarafshan"] },
    "Samarqand": { "state_param": "Samarqand", "cities": ["Samarqand"] },
    "Sirdaryo": { "state_param": "Sirdaryo", "cities": ["Guliston"] },
    "Toshkent": { 
        "state_param": "Toshkent", 
        "cities": ["Amirsoy", "Chorvoq", "G'azalkent", "Parkent", "Qibray", "Salor", "Sidzhak", "Tuytepa", "Urtaowul"] 
    },
    "Toshkent Shahri": { 
        "state_param": "Toshkent Shahri", 
        "cities": ["Tashkent"] 
    },
    "Xorazm": { "state_param": "Xorazm", "cities": ["Pitnak", "Urganch"] },
    
    # Regions with NO known available cities (now handled to show only 'Back' button)
    "Qashqadaryo": { "state_param": "Qashqadaryo", "cities": [] },
    "Surxondaryo": { "state_param": "Surxondaryo", "cities": [] },
}

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# --- REPLY KEYBOARD CONFIGURATION ---

# Define button texts
BUTTON_REGIONS = "🌍 Select Region"
BUTTON_MY_LOCATION = "📍 My Location"
BUTTON_BACK_MAIN = "⬅️ Back to Main Menu" 
BUTTON_BACK_REGION = "⬅️ Back to Regions" 

# Level 1: Main Menu Keyboard
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [BUTTON_REGIONS], 
        [KeyboardButton(BUTTON_MY_LOCATION, request_location=True)]
    ],
    resize_keyboard=True,      
    one_time_keyboard=False    
)

# Level 2: Region Selection Keyboard
def get_region_reply_keyboard():
    """Dynamically creates the Reply Keyboard for regions."""
    region_names = sorted(REGIONS_DATA.keys())
    keyboard_rows = []
    current_row = []
    
    for region_name in region_names:
        current_row.append(region_name)
        if len(current_row) == 2: # 2 buttons per row
            keyboard_rows.append(current_row)
            current_row = []
            
    if current_row:
        keyboard_rows.append(current_row)
        
    # Add the 'Back' button on the last row
    keyboard_rows.append([BUTTON_BACK_MAIN]) 
    
    return ReplyKeyboardMarkup(
        keyboard_rows,
        resize_keyboard=True,
        one_time_keyboard=False
    )

REGION_REPLY_KEYBOARD = get_region_reply_keyboard()

# Level 3: City Selection Keyboard
def get_city_reply_keyboard(region_name):
    """Dynamically creates the Reply Keyboard for cities in a selected region. 
       Always includes a 'Back to Regions' button, even if cities list is empty."""
    cities = REGIONS_DATA.get(region_name, {}).get("cities", [])
    keyboard_rows = []
    current_row = []
    
    for city_name in cities:
        current_row.append(city_name)
        if len(current_row) == 2: # 2 buttons per row
            keyboard_rows.append(current_row)
            current_row = []
            
    if current_row:
        keyboard_rows.append(current_row)
        
    # Always add the 'Back to Regions' button on the last row
    keyboard_rows.append([BUTTON_BACK_REGION]) 
    
    return ReplyKeyboardMarkup(
        keyboard_rows,
        resize_keyboard=True,
        one_time_keyboard=False
    )


# --- AQI Fetching Logic (UNCHANGED) ---

def _fetch_air_quality_sync(latitude=None, longitude=None, city=None, state=None, country=COUNTRY_NAME):
    """Internal synchronous function to fetch data."""
    if not IQAIR_API_KEY:
        logger.error("IQAIR_API_KEY is not set.")
        return "❌ IQAir API Key is missing. Cannot fetch data."

    try:
        # Determine Endpoint
        if latitude is not None and longitude is not None:
            api_endpoint = f"http://api.airvisual.com/v2/nearest_city"
            location_name = f"Location (Lat: {latitude:.2f}, Lon: {longitude:.2f})"
            params = {
                'lat': latitude,
                'lon': longitude,
                'key': IQAIR_API_KEY
            }
        elif city and state:
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
        
        # Make request
        response = requests.get(api_endpoint, params=params)
        response.raise_for_status()
        data = response.json()
        
        if data.get('status') != 'success':
            error_data = data.get('data', 'Unknown API error')
            error_message = error_data['message'] if isinstance(error_data, dict) and 'message' in error_data else str(error_data)
            logger.error(f"IQAir API Error for {location_name}: {error_message}")
            return f"❌ Error fetching data for {location_name}: *{error_message}*"

        # Extract Data
        city_data = data['data']
        current_data = city_data['current']
        
        # If coordinates were used, get the actual city name found
        if latitude is not None:
            location_name = f"{city_data.get('city', 'Unknown City')}, {city_data.get('state', '')}"
        
        aqi_us = current_data['pollution']['aqius']
        main_pollutant = current_data['pollution']['mainus']
        temperature = current_data['weather']['tp']
        
        quality_info = get_aqi_description(aqi_us)
        
        # Format Message
        message = (
            f"**{location_name} Air Quality** 💨\n\n"
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
        logger.error(f"Unexpected error: {e}")
        return "❌ An internal error occurred."

async def fetch_air_quality(latitude=None, longitude=None, city=None, state=None, country=COUNTRY_NAME):
    """Async wrapper that runs the synchronous _fetch_air_quality_sync in a separate thread."""
    loop = asyncio.get_running_loop()
    func = functools.partial(
        _fetch_air_quality_sync, 
        latitude=latitude, longitude=longitude, city=city, state=state, country=country
    )
    return await loop.run_in_executor(None, func)

def get_aqi_description(aqi):
    """Returns AQI level and health message."""
    if 0 <= aqi <= 50:
        return {'level': '🟢 Good', 'message': 'Air quality is satisfactory.'}
    elif 51 <= aqi <= 100:
        return {'level': '🟡 Moderate', 'message': 'Sensitive individuals should limit outdoor activity.'}
    elif 101 <= aqi <= 150:
        return {'level': '🟠 Unhealthy for Sensitive Groups', 'message': 'Sensitive groups may experience health effects.'}
    elif 151 <= aqi <= 200:
        return {'level': '🔴 Unhealthy', 'message': 'Everyone may begin to experience health effects.'}
    elif 201 <= aqi <= 300:
        return {'level': '🟣 Very Unhealthy', 'message': 'Health warnings of emergency conditions.'}
    else:
        return {'level': '🟤 Hazardous', 'message': 'Health alert: avoid all outdoor exertion.'}


# --- CONVERSATION HANDLERS ---

async def start_region_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Level 1 -> Level 2 Transition: Displays the Region Reply Keyboard."""
    
    # Send the Region Reply Keyboard (Level 2)
    await update.message.reply_text(
        "🗺️ Please choose a region:",
        reply_markup=REGION_REPLY_KEYBOARD
    )
        
    # The next state waits for a region NAME as text
    return CHOOSING_REGION

async def select_city(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Level 2 -> Level 3 Transition: 
    Receives the Region NAME (text) and displays the City Reply Keyboard.
    """
    
    region_name = update.message.text
    
    # 1. Handle the 'Back to Main' button
    if region_name == BUTTON_BACK_MAIN:
        # Jump back to start_command to reset the keyboard to Level 1
        await update.message.reply_text("Main menu restored.", reply_markup=MAIN_KEYBOARD)
        context.user_data.clear()
        return ConversationHandler.END 
        
    if region_name not in REGIONS_DATA:
        await update.message.reply_text(
            "❌ Invalid region. Please choose a button from the keyboard below.",
            reply_markup=REGION_REPLY_KEYBOARD # Restore Level 2 Keyboard
        )
        return CHOOSING_REGION

    # Save data for the next step
    state_param = REGIONS_DATA[region_name]["state_param"]
    context.user_data['selected_state_param'] = state_param
    context.user_data['selected_region_name'] = region_name 
    
    # Generate and send the City Reply Keyboard (Level 3)
    # This automatically handles regions with no cities (only 'Back' button will appear)
    city_keyboard = get_city_reply_keyboard(region_name)
    
    cities_message = "🏙️ Now choose a city in **{region_name}**:"
    if not REGIONS_DATA[region_name]["cities"]:
        cities_message = f"⚠️ No monitoring stations listed for **{region_name}**. Use the back button."

    await update.message.reply_text(
        cities_message.format(region_name=region_name), 
        reply_markup=city_keyboard,
        parse_mode='Markdown'
    )
    
    # The next state waits for a city NAME as text
    return CHOOSING_CITY

async def get_aqi_by_city_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Level 3 -> Data Fetch: 
    Receives the City NAME (text) and fetches data, then resets to Level 1.
    """
    city_name = update.message.text
    
    # 1. Handle the 'Back to Regions' button
    if city_name == BUTTON_BACK_REGION:
        # Redisplay the region selection keyboard (Level 2)
        await update.message.reply_text("Returning to region selection...", reply_markup=REGION_REPLY_KEYBOARD)
        return CHOOSING_REGION 
        
    state_param = context.user_data.get('selected_state_param')
    region_name = context.user_data.get('selected_region_name')
    
    # Basic validation (Check if the city name is one of the valid cities for the region)
    if not state_param or city_name not in REGIONS_DATA.get(region_name, {}).get('cities', []):
        await update.message.reply_text(
            "❌ Invalid city selection. Please use the buttons.", 
            reply_markup=get_city_reply_keyboard(region_name)
        )
        return CHOOSING_CITY

    # UX: Show typing status and remove the Level 3 keyboard
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.TYPING)
    await update.message.reply_text(f"🔎 Fetching AQI for **{city_name}**...", 
                                    parse_mode='Markdown', 
                                    reply_markup=ReplyKeyboardRemove())

    # Call the ASYNC wrapper
    report_message = await fetch_air_quality(
        city=city_name, 
        state=state_param, 
        country=COUNTRY_NAME
    )
    
    await update.message.reply_markdown(report_message)
    
    # Final step: Restore the main menu keyboard (Level 1)
    await update.message.reply_text("Select another option:", reply_markup=MAIN_KEYBOARD)
    
    context.user_data.clear()
    return ConversationHandler.END


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the flow and restores the main menu (Level 1)."""
    msg_text = "Conversation cancelled. Main menu restored."
    
    if update.message:
        await update.message.reply_text(msg_text, reply_markup=MAIN_KEYBOARD)
    else:
         # Fallback for unexpected update types during conversation
        await context.bot.send_message(update.effective_chat.id, msg_text, reply_markup=MAIN_KEYBOARD)
            
    context.user_data.clear()
    return ConversationHandler.END


# --- STANDARD HANDLERS ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a welcome message and displays the Main Reply Keyboard (Level 1)."""
    await update.message.reply_markdown(
        "👋 Hello! I'm the **Uzbekistan AQI Bot**.\n\n"
        "Please use the buttons below to check air quality.",
        reply_markup=MAIN_KEYBOARD 
    )
    context.user_data.clear() # Clear state on restart
    return ConversationHandler.END

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the location object sent by the user (triggered by request_location=True)."""
    user_location = update.message.location
    
    await update.message.reply_text(
        "📍 Location received! Searching for the nearest station...",
        reply_markup=ReplyKeyboardRemove() 
    )
    
    # UX: Show typing status
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.TYPING)
    
    # Call the ASYNC wrapper
    report_message = await fetch_air_quality(
        latitude=user_location.latitude, 
        longitude=user_location.longitude
    )
    
    await update.message.reply_markdown(report_message)
    
    # Restore the main menu keyboard (Level 1)
    await update.message.reply_text("Select another option:", reply_markup=MAIN_KEYBOARD)
    
    return ConversationHandler.END 

async def health_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Responds with a simple message to confirm the bot is active."""
    await update.message.reply_text("✅ Bot is running.")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update caused error: {context.error}")


# --- SETUP & MAIN ---

async def post_init_setup(application):
    # Only keep /start visible in the menu. /health is removed as requested.
    await application.bot.set_my_commands([
        BotCommand("start", "Display Main Menu"),
        # BotCommand("health", "Check bot status"), <-- Removed from menu
    ])

def main():
    if not TELEGRAM_TOKEN or not IQAIR_API_KEY:
        logger.error("❌ ERROR: Tokens missing in environment variables.")
        sys.exit(1)

    application = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init_setup).build()

    # The conversation handler's entry point is the text from the 'Select Region' button (Level 1)
    region_conv_handler = ConversationHandler(
        # Entry point: Catch the text message from the 'Select Region' button
        entry_points=[MessageHandler(filters.Regex(f"^{BUTTON_REGIONS}$"), start_region_selection)], 
        states={
            # CHOOSING_REGION (Level 2) waits for a REGION NAME or 'Back to Main Menu'
            CHOOSING_REGION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, select_city),
            ],
            # CHOOSING_CITY (Level 3) waits for a CITY NAME or 'Back to Regions'
            CHOOSING_CITY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_aqi_by_city_name), 
            ],
        },
        fallbacks=[
            # Handles /start command and general command cancellation at any point
            CommandHandler("start", start_command), 
            CommandHandler("cancel", cancel_conversation),
            # General text fallback to cancel the conversation (outside of expected button text)
            MessageHandler(filters.TEXT & ~filters.COMMAND, cancel_conversation), 
        ],
    )
    
    # General Handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.LOCATION, handle_location))
    application.add_handler(CommandHandler("health", health_check)) # Keeps command functional
    
    # Add the Conversation Handler last (except for error handler)
    application.add_handler(region_conv_handler)
    application.add_error_handler(error_handler)

    if WEBHOOK_URL:
        logger.info(f"Starting Webhook on port {PORT}")
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TELEGRAM_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}",
        )
    else:
        logger.info("Starting Polling...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
