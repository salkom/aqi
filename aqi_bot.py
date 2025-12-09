import requests
import logging
import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes


# --- Configuration (Load from Environment Variables) ---
# NOTE: These variables must be set on the Render dashboard
TELEGRAM_TOKEN = os.environ.get("8575554544:AAGdIiaZei1Fjnbtvt6VVEcBQ4IlVdvVTi8")
IQAIR_API_KEY = os.environ.get("a6dcc4d7-06a2-481b-81ca-a72132034752")

# Render environment variables
WEBHOOK_URL = os.environ.get("RENDER_EXTERNAL_URL") # This is automatically set by Render
PORT = int(os.environ.get("PORT", 5000)) # Render often uses PORT 10000, but 5000 is a common default

# Tashkent location parameters for the IQAir API (Uzbekistan is the country)
CITY = "Tashkent"
STATE = "Toshkent Shahri" # IQAir often uses the region/state name
COUNTRY = "Uzbekistan"
BASE_URL = "http://api.airvisual.com/v2/city"

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# --- AQI Fetching Function ---
def fetch_air_quality():
    """Fetches air quality data for Tashkent from the IQAir API."""
    # ... (Keep your existing fetch_air_quality function here) ...
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
            logging.error(f"IQAir API Error: {data.get('data', 'Unknown API error')}")
            return "❌ Error fetching data from the IQAir service."

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
        logging.error(f"HTTP Request Failed: {e}")
        return "❌ Network or API communication error."
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
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
async def aqi_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends the air quality report when the /aqi command is issued."""
    # Send a temporary message while fetching data
    await update.message.reply_text("Fetching the latest air quality data for Tashkent...")
    
    # Get the data
    report_message = fetch_air_quality()
    
    # Send the final report
    await update.message.reply_markdown(report_message)


# --- Main Application Runner ---
def main():
    """Builds the Application and returns it for Gunicorn to run."""
    if not TELEGRAM_TOKEN or not IQAIR_API_KEY or not WEBHOOK_URL:
        logger.error("Required environment variables are missing. Deployment will fail.")
        # Return a dummy app or raise error, depending on production environment needs
        return None
    
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Register the command handler
    application.add_handler(CommandHandler("aqi", aqi_command))

    # --- Webhook Configuration ---
    
    # 1. Start the internal webserver on the specified port
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TELEGRAM_TOKEN, # Use the token as a secret path (e.g., /123456:ABC-DEF/ for updates)
        webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}" # The full URL Telegram needs to hit
    )
    
    # In a typical Gunicorn setup, you would return the WSGI/ASGI app object here,
    # but for simple PTB Webhook server, we let run_webhook handle the execution.
    # We define 'app' for Gunicorn to target.
    return application.updater.webserver


# Create a variable 'app' which Gunicorn will look for.
# In this specific case, we'll use a wrapper function that returns the result of main
# to cleanly initialize the app when Gunicorn starts.
def run_app():
    # We call main to initialize and run the webhook server
    return main()


# Gunicorn expects an application object, but for this simple PTB setup, 
# we let the script execute the webhook server directly via a runner script/Procfile. 
# We'll rely on the Procfile for the actual execution command.

# Define the application object for the Gunicorn worker process to use
# We wrap the ApplicationBuilder in a simple callable target for Gunicorn

def init_webhook_app():
    """Initializes and returns the PTB Application instance for Gunicorn."""
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN environment variable is not set.")
        return None
        
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("aqi", aqi_command))

    return application

# The actual entry point for Gunicorn will be a separate script or the Procfile
# which is why the main logic is slightly different from the polling setup.

# Export the application object for external webserver integration (like gunicorn)
application_instance = init_webhook_app()

# --- Application Initialization for Gunicorn ---

# Function to build and configure the PTB Application
def build_application():
    """Builds and returns the PTB Application instance."""
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN is not set. Cannot build application.")
        return None

    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("aqi", aqi_command))
    
    # We do NOT run run_webhook() here. We just return the application.
    # The actual Webhook serving logic will be handled by the Runner below.
    return application

# The Application object Gunicorn is looking for (aqi_bot:app)
application_instance = build_application()


# Function to handle the actual running of the webhook
# This will be used in the Procfile/Start Command instead of gunicorn targeting 'app'.
def run_webhook_server():
    """Runs the webhook server using the application instance."""
    if not application_instance:
        logger.error("Application instance is None. Exiting.")
        return

    # Set the webhook URL (This must be the public URL provided by Render)
    webhook_url = f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}"

    logger.info(f"Starting webhook server on port {PORT}. Webhook URL: {webhook_url}")

    # Use the application_instance to start the webhook server
    application_instance.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TELEGRAM_TOKEN,
        webhook_url=webhook_url
    )
    
# We won't use 'app' directly as the WSGI callable since PTB's webhook setup is custom.
# Instead, we will tell Render to execute the script directly.

if __name__ == '__main__':
    # This block is only for local testing, not for Render
    print("Running Webhook bot locally. This requires a public URL service like ngrok to work.")
    # For local Webhook testing, you'd typically set the webhook_url manually
    # application_instance.run_webhook(...)
    
    # For deployment, we use the Gunicorn/Procfile approach.
    pass

