from logger import start_logger
from updater import Updater

#test
MANIFEST = "http://127.0.0.1:5000/latest.json"
__version__ = "1.0.0"



if __name__ == "__main__":
        Updater(__version__, MANIFEST).run()
        start_logger()
    


