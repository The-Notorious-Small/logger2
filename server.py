from flask import Flask, request
import os

app = flask(__name__)
Upload_Folder="./logs"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

@app.route("/upload", methods=["post"])
UPLOAD_FOLDER = "./recieved_logs"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

@app.route("/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return "No file part", 400
    file = request.files["file"]
    if file.filename == "":
        return "No selected file", 400

    #save the file use secure filename in production
    save_path = os.path.join(UPLOAD_FOLDER, file.filename)
    file.save(save_path)
    return f"file saved as {file.filename}", 200

if __name__ = "__main__":
    app.run(host="0.0.0.0", port =5000)