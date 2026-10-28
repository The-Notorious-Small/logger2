from logger import start_logger
from updater import Updater


MANIFEST = ""
__version__ = "1.0.0"



if __name__ == "__main__":
        Updater(__version__, Manifest).run()
        start_logger()
    


