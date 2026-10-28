from flask import Flask, request, send_from_directory
import os

app = Flask(__name__)
UPLOAD_FOLDER = "/upload"
RELEASES= "releases"
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

@app.route("/latest.json")
def manifest():
    return send_from_directory(RELEASES, "latest.json")

@app.route("/download/<path:filename>")
def download(filename):
    return send_from_directory(RELEASES, filename, as_attachment=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)