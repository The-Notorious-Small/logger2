from logger import start_logger
from updater import Updater

#test
MANIFEST = "http://192.168.122.1:5000/latest.json"
__version__ = "1.2.0"



if __name__ == "__main__":
    try:
        Updater(__version__, MANIFEST).run()
        start_logger()
    except Exception as e:
        print(f"CRITICAL ERROR: {e}")

    # Keeps the console window open so you can read errors
    input("\nPress Enter to exit...")