# /root/polymarket-clob-server/scripts/resolution_cron.py
import requests
import logging
from logging.handlers import RotatingFileHandler
import sys
import os
from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv

# Load environment variables first
load_dotenv()

# Basic logging setup first - so we can log any startup errors
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger('resolution_cron')

def trigger_resolution():
    """Trigger the market resolution process via API endpoint"""
    try:
        logger.info("Initiating market resolution trigger")
        api_url = os.getenv('API_BASE_URL', 'http://localhost:8000')
        response = requests.post(f"{api_url}/api/resolve-markets", timeout=30)
        
        if response.status_code == 200:
            logger.info("Resolution trigger successful")
        else:
            logger.error(f"Resolution trigger failed with status {response.status_code}")
            
    except requests.exceptions.RequestException as e:
        logger.error(f"HTTP Request failed: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)

def main():
    logger.info("Starting resolution cron service")
    
    # Initialize scheduler
    scheduler = BlockingScheduler()
    scheduler.add_job(
        trigger_resolution,
        'interval',
        minutes=5,
        id='resolution_job'
    )
    
    try:
        # Execute once immediately
        logger.info("Executing initial resolution check")
        trigger_resolution()
        
        # Start the scheduler
        logger.info("Starting scheduler")
        scheduler.start()
        
    except (KeyboardInterrupt, SystemExit):
        logger.info("Service shutdown requested")
    except Exception as e:
        logger.error(f"Fatal error in scheduler: {str(e)}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()