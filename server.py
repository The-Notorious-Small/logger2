from flask import Flask, request, send_from_directory
import os

app = Flask(__name__)
UPLOAD_FOLDER = "."
# Point this to the folder where PyUpdater stores built updates (e.g., pyu-data/releases/)
UPDATES_FOLDER = os.path.abspath("pyu-data/releases")

@app.route("/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return "No file part", 400
    file = request.files["file"]
    if file.filename == "":
        return "No selected file", 400
    if file:
        file.save(os.path.join(UPLOAD_FOLDER, "received_" + file.filename))
        return "File uploaded successfully", 200

# Endpoint to serve update files to the client application
@app.route("/updates/<path:filename>", methods=["GET"])
def download_update(filename):
    return send_from_directory(UPDATES_FOLDER, filename)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)