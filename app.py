import os
import json
from flask import Flask, request, jsonify, render_template, Response, stream_with_context

from extract_invoice import process_files_stream

app = Flask(__name__)

UPLOAD_DIR = "uploaded_files"
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_EXTENSIONS = {".pdf", ".txt"}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/process", methods=["POST"])
def process():
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "No files uploaded."}), 400

    saved_paths = []
    for file in files:
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            continue  # unsupported files are reported inside process_files_stream via get_raw_text
        save_path = os.path.join(UPLOAD_DIR, file.filename)
        file.save(save_path)
        saved_paths.append(save_path)

    def generate():
        # Each yielded line is one complete JSON object, so the browser
        # can parse and render results as they arrive, not all at once.
        for result in process_files_stream(saved_paths):
            if result["status"] == "success":
                payload = {
                    "filename": result["filename"],
                    "status": "success",
                    "invoice": result["invoice"].model_dump(),
                    "index": result["index"],
                    "total": result["total"],
                }
            else:
                payload = {
                    "filename": result["filename"],
                    "status": "error",
                    "error_type": result["error_type"],
                    "message": result["message"],
                    "index": result["index"],
                    "total": result["total"],
                }
            yield json.dumps(payload) + "\n"

    return Response(stream_with_context(generate()), mimetype="application/x-ndjson")


if __name__ == "__main__":
    app.run(debug=True, port=5001, threaded=True)
