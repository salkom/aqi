import logging
import os
import sys
import asyncio 
import httpx
from http import HTTPStatus
from dotenv import load_dotenv

# --- DATABASE IMPORTS ---
from sqlalchemy import create_engine, Column, Integer, String, DateTime, text, BigInteger
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Mapped, mapped_column
from datetime import datetime
# -------------------------

from telegram import (
    Update, 
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    ReplyKeyboardRemove, 
    BotCommand, 
    constants
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

DATABASE_URL = os.getenv("DATABASE_URL") 

COUNTRY_NAME = "Uzbekistan"

# Conversation States
CHOOSING_REGION, CHOOSING_CITY = range(2) 

# Define the region/city data structure
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
    "Qashqadaryo": { "state_param": "Qashqadaryo", "cities": [] },
    "Surxondaryo": { "state_param": "Surxondaryo", "cities": [] },
}

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# --- DATABASE MODEL AND UTILITIES ---

class Base(AsyncAttrs, DeclarativeBase):
    pass

class UsageLog(Base):
    __tablename__ = "usage_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    user_id: Mapped[int] = mapped_column(BigInteger) 
    username: Mapped[str] = mapped_column(String(50), nullable=True)
    first_name: Mapped[str] = mapped_column(String(50))
    action: Mapped[str] = mapped_column(String(100))
    location_details: Mapped[str] = mapped_column(String(255), nullable=True)

    def __repr__(self) -> str:
        return f"UsageLog(id={self.id}, user_id={self.user_id}, action='{self.action}')"

# Configure the async engine using the DATABASE_URL environment variable
if DATABASE_URL and "+asyncpg" in DATABASE_URL:
    ASYNC_DATABASE_URL = DATABASE_URL
elif DATABASE_URL and DATABASE_URL.startswith("postgresql://"):
    ASYNC_DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")
else:
    if DATABASE_URL:
        print("DATABASE_URL provided but format is unrecognizable. Falling back to local SQLite.")
    else:
        print("DATABASE_URL not set. Falling back to local SQLite.")
    ASYNC_DATABASE_URL = "sqlite+aiosqlite:///temp_local_db.db"

async_engine = create_async_engine(
    ASYNC_DATABASE_URL,
    echo=False
)

AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False)


async def init_db():
    """Initializes the database: creates the table if it doesn't exist."""
    if not DATABASE_URL and ASYNC_DATABASE_URL == "sqlite+aiosqlite:///temp_local_db.db":
        logger.warning("Database initialization skipped due to missing DATABASE_URL. Using ephemeral local file.")
        return

    try:
        async with async_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database connection and tables initialized successfully.")
    except Exception as e:
        logger.error(f"FATAL: Database initialization failed. Check connection string/permissions. Error: {e}")


async def save_usage_log(user, action: str, details: str = None):
    """Saves a usage record to the database asynchronously."""
    if ASYNC_DATABASE_URL == "sqlite+aiosqlite:///temp_local_db.db":
        logger.debug(f"Skipping DB log for {user.id}. Action: {action}")
        return

    try:
        new_log = UsageLog(
            user_id=user.id,
            username=user.username or 'N/A',
            first_name=user.first_name,
            action=action,
            location_details=details
        )
        async with AsyncSessionLocal() as session:
            session.add(new_log)
            await session.commit()
    except Exception as e:
        logger.error(f"Failed to save usage log for user {user.id}. Error: {e}")

# Safe Background Logging Helper (Fire and Forget)
async def log_and_ignore_errors(user, action, details=None):
    """
    Wraps save_usage_log to handle exceptions cleanly.
    Used by asyncio.create_task() to prevent blocking the main thread.
    """
    try:
        await save_usage_log(user, action, details)
    except Exception as e:
        logger.error(f"Background logging failed for user {user.id} action '{action}': {e}") 

# --- REPLY KEYBOARD CONFIGURATION (UNCHANGED) ---

BUTTON_REGIONS = "🌍 Select Region"
BUTTON_MY_LOCATION = "📍 My Location"
BUTTON_BACK_MAIN = "⬅️ Back to Main Menu" 
BUTTON_BACK_REGION = "⬅️ Back to Regions" 

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [BUTTON_REGIONS], 
        [KeyboardButton(BUTTON_MY_LOCATION, request_location=True)]
    ],
    resize_keyboard=True,       
    one_time_keyboard=False     
)

def get_region_reply_keyboard():
    region_names = sorted(REGIONS_DATA.keys())
    keyboard_rows = []
    current_row = []
    
    for region_name in region_names:
        current_row.append(region_name)
        if len(current_row) == 2:
            keyboard_rows.append(current_row)
            current_row = []
            
    if current_row:
        keyboard_rows.append(current_row)
        
    keyboard_rows.append([BUTTON_BACK_MAIN]) 
    
    return ReplyKeyboardMarkup(
        keyboard_rows,
        resize_keyboard=True,
        one_time_keyboard=False
    )

REGION_REPLY_KEYBOARD = get_region_reply_keyboard()

def get_city_reply_keyboard(region_name):
    cities = REGIONS_DATA.get(region_name, {}).get("cities", [])
    keyboard_rows = []
    current_row = []
    
    for city_name in cities:
        current_row.append(city_name)
        if len(current_row) == 2:
            keyboard_rows.append(current_row)
            current_row = []
            
    if current_row:
        keyboard_rows.append(current_row)
        
    keyboard_rows.append([BUTTON_BACK_REGION]) 
    
    return ReplyKeyboardMarkup(
        keyboard_rows,
        resize_keyboard=True,
        one_time_keyboard=False
    )


# --- AQI Fetching Logic (UNCHANGED) ---

async def fetch_air_quality(latitude=None, longitude=None, city=None, state=None, country=COUNTRY_NAME):
    """ASYNCHRONOUS function using httpx for non-blocking API calls."""
    if not IQAIR_API_KEY:
        logger.error("IQAIR_API_KEY is not set.")
        return "❌ IQAir API Key is missing. Cannot fetch data."

    async with httpx.AsyncClient() as client:
        try:
            # Determine Endpoint
            if latitude is not None and longitude is not None:
                api_endpoint = "http://api.airvisual.com/v2/nearest_city"
                location_name = f"Location (Lat: {latitude:.2f}, Lon {longitude:.2f})"
                params = {
                    'lat': latitude,
                    'lon': longitude,
                    'key': IQAIR_API_KEY
                }
            elif city and state:
                api_endpoint = "http://api.airvisual.com/v2/city"
                location_name = f"{city}, {state}"
                params = {
                    'city': city,
                    'state': state,
                    'country': country,
                    'key': IQAIR_API_KEY
                }
            else:
                return "❌ Invalid location parameters provided."
            
            # Make the ASYNCHRONOUS request
            response = await client.get(api_endpoint, params=params)
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
            
            if latitude is not None:
                location_name = f"{city_data.get('city', 'Unknown City')}, {city_data.get('state', '')}"
            
            aqi_us = current_data['pollution']['aqius']
            main_pollutant = current_data['pollution']['mainus']
            temperature = current_data['weather']['tp']
            
            quality_info = get_aqi_description(aqi_us)
            
            message = (
                f"**{location_name} Air Quality** 💨\n\n"
                f"**Current AQI (US):** {aqi_us} - {quality_info['level']}\n"
                f"**Main Pollutant:** {main_pollutant}\n"
                f"**Temperature:** {temperature}°C\n\n"
                f"ℹ️ *{quality_info['message']}*"
            )
            return message

        except httpx.RequestError as e:
            logger.error(f"HTTP Request Failed (httpx): {e}")
            return "❌ Network or API communication error."
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return "❌ An internal error occurred."

def get_aqi_description(aqi):
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


# --- CONVERSATION HANDLERS (UPDATED WITH BACKGROUND LOGGING) ---

async def start_region_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Level 1 -> Level 2 Transition: Displays the Region Reply Keyboard."""
    
    # LOGGING: Fire and Forget (Clean multi-line format)
    asyncio.create_task(
        log_and_ignore_errors(
            update.effective_user, 
            "Start Region Selection"
        )
    )
    
    await update.message.reply_text(
        "🗺️ Please choose a region:",
        reply_markup=REGION_REPLY_KEYBOARD
    )
    return CHOOSING_REGION

async def select_city(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Level 2 -> Level 3 Transition: 
    Receives the Region NAME (text) and displays the City Reply Keyboard.
    """
    
    region_name = update.message.text
    
    if region_name == BUTTON_BACK_MAIN:
        # LOGGING: Fire and Forget
        asyncio.create_task(
            log_and_ignore_errors(
                update.effective_user, 
                "Back to Main Menu"
            )
        )
        await update.message.reply_text("Main menu restored.", reply_markup=MAIN_KEYBOARD)
        context.user_data.clear()
        return ConversationHandler.END 
        
    if region_name not in REGIONS_DATA:
        await update.message.reply_text(
            "❌ Invalid region. Please choose a button from the keyboard below.",
            reply_markup=REGION_REPLY_KEYBOARD 
        )
        return CHOOSING_REGION

    # LOGGING: Fire and Forget
    asyncio.create_task(
        log_and_ignore_errors(
            update.effective_user, 
            "Select Region", 
            details=region_name
        )
    )
    
    # Save data for the next step
    state_param = REGIONS_DATA[region_name]["state_param"]
    context.user_data['selected_state_param'] = state_param
    context.user_data['selected_region_name'] = region_name 
    
    city_keyboard = get_city_reply_keyboard(region_name)
    
    cities_message = f"🏙️ Now choose a city in **{region_name}**:"
    if not REGIONS_DATA[region_name]["cities"]:
        cities_message = f"⚠️ No monitoring stations listed for **{region_name}**. Use the back button."

    await update.message.reply_text(
        cities_message, 
        reply_markup=city_keyboard,
        parse_mode='Markdown'
    )
    return CHOOSING_CITY

async def get_aqi_by_city_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Level 3 -> Data Fetch: 
    Receives the City NAME (text) and fetches data, then resets to Level 1.
    """
    city_name = update.message.text
    
    if city_name == BUTTON_BACK_REGION:
        # LOGGING: Fire and Forget
        asyncio.create_task(
            log_and_ignore_errors(
                update.effective_user, 
                "Back to Regions"
            )
        )
        await update.message.reply_text("Returning to region selection...", reply_markup=REGION_REPLY_KEYBOARD)
        return CHOOSING_REGION 
        
    state_param = context.user_data.get('selected_state_param')
    region_name = context.user_data.get('selected_region_name')
    
    if not state_param or city_name not in REGIONS_DATA.get(region_name, {}).get('cities', []):
        await update.message.reply_text(
            "❌ Invalid city selection. Please use the buttons.", 
            reply_markup=get_city_reply_keyboard(region_name)
        )
        return CHOOSING_CITY

    # FIX: Pre-format the string AND use clean multi-line format
    details_string = f"{city_name}, {state_param}"
    # LOGGING: Fire and Forget
    asyncio.create_task(
        log_and_ignore_errors(
            update.effective_user, 
            "AQI by City", 
            details=details_string
        )
    )
    
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.TYPING)
    await update.message.reply_text(f"🔎 Fetching AQI for **{city_name}**...", 
                                   parse_mode='Markdown', 
                                   reply_markup=ReplyKeyboardRemove())

    report_message = await fetch_air_quality(
        city=city_name, 
        state=state_param, 
        country=COUNTRY_NAME
    )
    
    await update.message.reply_markdown(report_message)
    await update.message.reply_text("Select another option:", reply_markup=MAIN_KEYBOARD)
    
    context.user_data.clear()
    return ConversationHandler.END


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the flow and restores the main menu (Level 1)."""
    msg_text = "Conversation cancelled. Main menu restored."
    
    # LOGGING: Fire and Forget
    asyncio.create_task(
        log_and_ignore_errors(
            update.effective_user, 
            "Conversation Cancelled"
        )
    )

    if update.message:
        await update.message.reply_text(msg_text, reply_markup=MAIN_KEYBOARD)
    else:
        await context.bot.send_message(update.effective_chat.id, msg_text, reply_markup=MAIN_KEYBOARD)
            
    context.user_data.clear()
    return ConversationHandler.END


# --- STANDARD HANDLERS (UPDATED WITH BACKGROUND LOGGING) ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a welcome message and displays the Main Reply Keyboard (Level 1)."""
    
    # LOGGING: Fire and Forget
    asyncio.create_task(
        log_and_ignore_errors(
            update.effective_user, 
            "Start Command"
        )
    )
    
    await update.message.reply_markdown(
        "👋 Hello! I'm the **Uzbekistan AQI Bot**.\n\n"
        "Please use the buttons below to check air quality.",
        reply_markup=MAIN_KEYBOARD 
    )
    context.user_data.clear() 
    return ConversationHandler.END

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the location object sent by the user (triggered by request_location=True)."""
    user_location = update.message.location
    
    # FIX: Pre-format the string AND use clean multi-line format
    details_string = f"Lat {user_location.latitude:.4f}, Lon {user_location.longitude:.4f}"
    
    # LOGGING: Fire and Forget
    asyncio.create_task(
        log_and_ignore_errors(
            update.effective_user, 
            "AQI by Location", 
            details=details_string
        )
    )
    
    await update.message.reply_text(
        "📍 Location received! Searching for the nearest station...",
        reply_markup=ReplyKeyboardRemove() 
    )
    
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.TYPING)
    
    report_message = await fetch_air_quality(
        latitude=user_location.latitude, 
        longitude=user_location.longitude
    )
    
    await update.message.reply_markdown(report_message)
    await update.message.reply_text("Select another option:", reply_markup=MAIN_KEYBOARD)
    
    return ConversationHandler.END 

async def health_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Responds with a simple message to confirm the bot is active."""
    await update.message.reply_text("✅ Bot is running.")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update caused error: {context.error}")


# --- SETUP & MAIN ---

async def post_init_setup(application):
    # Only keep /start visible in the menu.
    await application.bot.set_my_commands([
        BotCommand("start", "Display Main Menu"),
    ])
    # Run database initialization
    await init_db()


def main():
    if not TELEGRAM_TOKEN or not IQAIR_API_KEY:
        logger.error("❌ ERROR: Tokens missing in environment variables.")
        sys.exit(1)

    # Initial checks for the database setup
    if not os.getenv("DATABASE_URL") and ASYNC_DATABASE_URL != "sqlite+aiosqlite:///temp_local_db.db":
        logger.error("❌ ERROR: DATABASE_URL environment variable is missing. Check your Render setup.")
        # We don't exit here, but the DB logging will default to the temporary local file.

    application = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init_setup).build()

    region_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{BUTTON_REGIONS}$"), start_region_selection)], 
        states={
            CHOOSING_REGION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, select_city),
            ],
            CHOOSING_CITY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_aqi_by_city_name), 
            ],
        },
        fallbacks=[
            CommandHandler("start", start_command), 
            CommandHandler("cancel", cancel_conversation),
            MessageHandler(filters.TEXT & ~filters.COMMAND, cancel_conversation), 
        ],
    )
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.LOCATION, handle_location))
    application.add_handler(CommandHandler("health", health_check))
    
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
