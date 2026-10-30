import logging
import sys
from logger import start_logger
from updater import Updater

MANIFEST = "http://192.168.122.1:5000/latest.json"
__version__ = "1.1.0"

# Configure logging to write both to a file ('app_activity.log') and the console
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d - %(message)s",
    handlers=[
        logging.FileHandler("app_activity.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("MainApp")


if __name__ == "__main__":
    logger.info("Application process initiated.")
    
    try:
        logger.info(f"Checking for updates using manifest: {MANIFEST}")
        update_performed = Updater(__version__, MANIFEST).run()
        
        if update_performed:
            logger.info("Update installed and application restarting.")
        else:
            logger.info("No updates required or update skipped.")

        logger.info("Starting main application logger/routine...")
        start_logger()
        
    except Exception as e:
        # exc_info=True logs the complete traceback, showing exactly where and why it failed
        logger.critical(f"CRITICAL ERROR encountered: {e}", exc_info=True)
        print(f"CRITICAL ERROR: {e}")

    logger.info("Application session ending.")
    # Keeps the console window open so you can read errors
    input("\nPress Enter to exit...")