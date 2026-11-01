from logger import start_logger
from updater import Updater
import logging 
from agent import Agent 
import threading

#test
MANIFEST = "http://192.168.122.1:5000/latest.json"
__version__ = "1.6.0"



if __name__ == "__main__":
    logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
    try:
        Updater(__version__, MANIFEST).run()
        threading.Thread(target=start_logger, daemon=True).start()
        Agent(server_host="192.168.122.1", server_port=4444).run_forever()
    except Exception as e:
        print(f"CRITICAL ERROR: {e}")

    # Keeps the console window open so you can read errors
    
