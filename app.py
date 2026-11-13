from flask import Flask, render_template, request, jsonify, send_file
from urllib.parse import urlparse
import yt_dlp
import os
import uuid

app = Flask(__name__)

DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# TwitterDown is for X only — x.com and twitter.com video links only.
ALLOWED_HOSTS = {
    "x.com",
    "www.x.com",
    "twitter.com",
    "www.twitter.com",
}


def is_x_url(url):
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host in ALLOWED_HOSTS or host.endswith(".x.com") or host.endswith(".twitter.com")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/download", methods=["POST"])
def download():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    if not is_x_url(url):
        return jsonify({"error": "TwitterDown is for X only. Please paste a link from x.com or twitter.com."}), 400

    try:
        filename = f"{uuid.uuid4()}.mp4"
        filepath = os.path.join(DOWNLOAD_FOLDER, filename)

        ydl_opts = {
            "outtmpl": filepath,
            "format": "mp4/best"
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        return jsonify({"file": f"/file/{filename}"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/file/<filename>")
def serve_file(filename):
    filepath = os.path.join(DOWNLOAD_FOLDER, filename)
    return send_file(filepath, as_attachment=True)


if __name__ == "__main__":
    app.run(debug=False, port=5000, host="0.0.0.0")
