import platform
import subprocess
import time


def start_screen_recording(output_filename="output.mp4", duration=10):
  system = platform.system()

  # Base command
  cmd = ["ffmpeg", "-y"]  # -y overwrites output file without asking

  if system == "Windows":
    # Windows screen capture using gdigrab
    # framerate (r), desktop source (i)
    cmd += ["-f", "gdigrab", "-framerate", "30", "-i", "desktop"]

  elif system == "Darwin":  # macOS
    # macOS screen capture using avfoundation
    # List devices using `ffmpeg -f avfoundation -list_devices true -i ""` to find screen index (usually "1" or "0")
    cmd += ["-f", "avfoundation", "-framerate", "30", "-i", "1:none"]

  elif system == "Linux":
    # Linux X11 display capture
    display = ":0.0"  # Adjust if using a different display
    cmd += [
        "-f",
        "x11grab",
        "-framerate",
        "30",
        "-video_size",
        "1920x1080",  # Set your screen resolution
        "-i",
        display,
    ]
  else:
    raise UnsupportedPlatformError(f"Unsupported operating system: {system}")

  # Encoding settings for good quality and compatibility
  cmd += ["-c:v", "libx264", "-preset", "ultrafast", output_filename]

  print(
      f"Starting recording for {duration} seconds... Saved to"
      f" {output_filename}"
  )

  # Start the FFmpeg process
  process = subprocess.Popen(
      cmd,
      stdin=subprocess.PIPE,
      stdout=subprocess.DEVNULL,
      stderr=subprocess.DEVNULL,
  )

  try:
    # Record for the specified duration
    time.sleep(duration)
  finally:
    # Gracefully stop the recording by sending 'q' to FFmpeg's stdin
    if process.poll() is None:
      print("Stopping recording...")
      process.communicate(input=b"q")

    print("Recording finished.")


if __name__ == "__main__":
  # Record for 10 seconds as a test
  start_screen_recording(output_filename="my_recording.mp4", duration=10)