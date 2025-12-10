import requests
import logging
import os
import sys
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from http import HTTPStatus
from dotenv import load_dotenv

# --- Configuration & Setup ---

# Use Environment Variables for sensitive data and configuration
# Get the Telegram Bot Token (required)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Get the IQAir API Key (required)
IQAIR_API_KEY = os.getenv("IQAIR_API_KEY")

# Get the Webhook URL for production (optional, if present, webhook mode is used)
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# Get the port for the webhook server (Render usually sets this)
PORT = int(os.getenv("PORT", "8080")) # Default to 8080 for local testing if not set

# Tashkent location parameters for the IQAir API (Uzbekistan is the country)
CITY = "Tashkent"
STATE = "Toshkent Shahri" # IQAir often uses the region/state name
COUNTRY = "Uzbekistan"

# IQAir API Endpoint (Get city-specific data)
BASE_URL = "http://api.airvisual.com/v2/city"

# Basic Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# --- AQI Fetching Function ---
def fetch_air_quality():
    """Fetches air quality data for Tashkent from the IQAir API."""
    if not IQAIR_API_KEY:
        logger.error("IQAIR_API_KEY is not set.")
        return "❌ IQAir API Key is missing. Cannot fetch data."

    try:
        # Define the query parameters for the API call
        params = {
            'city': CITY,
            'state': STATE,
            'country': COUNTRY,
            'key': IQAIR_API_KEY
        }
        
        # Make the GET request to the IQAir API
        response = requests.get(BASE_URL, params=params)
        response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)
        
        data = response.json()
        
        # Check if the API returned an 'ok' status
        if data.get('status') != 'success':
            error_message = data.get('data', 'Unknown API error')
            logger.error(f"IQAir API Error: {error_message}")
            return f"❌ Error fetching data from the IQAir service: *{error_message}*"

        # Extract relevant information
        current_data = data['data']['current']
        aqi_us = current_data['pollution']['aqius']
        main_pollutant = current_data['pollution']['mainus']
        temperature = current_data['weather']['tp']
        
        # Determine air quality description based on US AQI standard
        quality_info = get_aqi_description(aqi_us)
        
        # Format the final output message
        message = (
            f"**Tashkent Air Quality Report** 💨\n\n"
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

# --- AQI Description Logic (US Standard) ---
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

# --- Telegram Command Handler ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Greets the user and explains the bot."""
    await update.message.reply_markdown(
        "👋 Hello! I'm the **Tashkent AQI Bot**.\n\n"
        "Send the command `/aqi` to get the latest Air Quality Index (AQI) report for Tashkent."
    )

async def aqi_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends the air quality report when the /aqi command is issued."""
    # Send a temporary message while fetching data
    await update.message.reply_text("Fetching the latest air quality data for Tashkent...")
    
    # Get the data
    report_message = fetch_air_quality()
    
    # Send the final report
    await update.message.reply_markdown(report_message)

async def health_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Simple health check endpoint for Render."""
    await update.message.reply_text(str(HTTPStatus.OK))
    
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log the error and notify the user."""
    logger.error(f"Update '{update}' caused error '{context.error}'")


# --- Main Application Runner ---
def start():
    """
    Starts the bot in either Webhook (Production) or Polling (Local) mode.
    The mode is determined by the presence of the WEBHOOK_URL environment variable.
    """
    # Load environment variables from .env file for local development
    load_dotenv()

    # 1. Check for required keys
    if not TELEGRAM_TOKEN or not IQAIR_API_KEY:
        logger.error(
            "ERROR: The TELEGRAM_TOKEN or IQAIR_API_KEY environment variable is not set. Exiting."
        )
        sys.exit(1)

    # 2. Build the Application
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # 3. Register handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("aqi", aqi_command))
    application.add_handler(CommandHandler("health", health_check)) # Optional: useful for external checks
    application.add_error_handler(error_handler)

    # 4. Run based on environment
    if WEBHOOK_URL:
        # --- Production Mode (Render/Webhook) ---
        # Set the webhook to the external URL
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TELEGRAM_TOKEN, # Use the token as the path for security
            webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}",
            on_startup=lambda app: logger.info(f"Webhook set to {WEBHOOK_URL}/{TELEGRAM_TOKEN} on port {PORT}"),
        )
        logger.info(f"Bot started in **Webhook Mode** on port {PORT}. URL: {WEBHOOK_URL}")
    else:
        # --- Local Development Mode (Polling) ---
        logger.info("Bot started in **Polling Mode**. Send /aqi in Telegram to test.")
        application.run_polling(poll_interval=1.0) # Poll every 1 second

if __name__ == '__main__':
    start()
