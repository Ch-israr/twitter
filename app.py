from flask import Flask, render_template, request, jsonify, send_file, send_from_directory, Response, stream_with_context
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from translations import TRANSLATIONS, LANGS
import yt_dlp
import json
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


def extract_direct_videos(url):
    """Return direct video.twimg.com MP4 links for a tweet, best per quality.

    Uses yt-dlp only to read the tweet's metadata — no video file is
    downloaded to this server. Returns (videos, thumbnail, title) where
    videos is a list of {"label", "width", "height", "url"} sorted by
    quality, or ([], None, None) when no direct MP4 format was found.
    """
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 60,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    formats = info.get("formats") or []
    # Direct file downloads only. yt-dlp lists the same qualities twice:
    # once as direct files (no protocol field, plain https URL) and once as
    # HLS playlists (protocol "m3u8_native", "/pl/" URLs) that return a
    # playlist instead of a video file. The protocol is the reliable way to
    # tell them apart — X's direct video URLs do not always end in .mp4,
    # so filtering on the URL text alone misses real files.
    # NOTE: do NOT filter on vcodec/acodec here — yt-dlp's direct file
    # formats for X have no codec fields at all (only the playlist formats
    # get codecs filled in), so requiring a codec would drop every real
    # direct link.
    candidates = [
        f for f in formats
        if f.get("url")
        and (f.get("protocol") or "https") in ("http", "https")
    ]
    # Prefer formats that include audio; fall back to video-only ones.
    with_audio = [
        f for f in candidates
        if f.get("acodec") not in (None, "none")
    ]
    pool = with_audio or candidates

    best_per_height = {}
    for f in pool:
        height = f.get("height") or 0
        current = best_per_height.get(height)
        if current is None or (f.get("tbr") or 0) > (current.get("tbr") or 0):
            best_per_height[height] = f

    videos = []
    for height in sorted(best_per_height):
        f = best_per_height[height]
        width = f.get("width")
        label = f"{width}x{height}" if width else f"{height}p"
        videos.append({
            "label": label,
            "width": width,
            "height": height,
            "url": f["url"],
        })
    return videos, info.get("thumbnail"), info.get("title")


def download_file_fallback(url):
    """Old behaviour: download the video to this server and serve it.

    Kept as a fallback for tweets where no direct MP4 link was found.
    Returns the saved filename.
    """
    filename = f"{uuid.uuid4()}.mp4"
    filepath = os.path.join(DOWNLOAD_FOLDER, filename)
    ydl_opts = {
        "outtmpl": filepath,
        "format": "mp4/best",
        "socket_timeout": 60,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    return filename


@app.errorhandler(404)
def page_not_found(e):
    """Friendly branded 404 page, in the visitor's language when the
    bad URL starts with a language code (e.g. /es/nope).

    Still returns HTTP 404 so search engines don't index bad URLs.
    """
    lang = "en"
    parts = request.path.strip("/").split("/")
    if parts and parts[0] in TRANSLATIONS:
        lang = parts[0]
    t = TRANSLATIONS[lang]
    home = "/" if lang == "en" else f"/{lang}/"
    return render_template("404.html", t=t, lang=lang, home=home), 404


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(app.static_folder, "favicon.svg", mimetype="image/svg+xml")


@app.route("/robots.txt")
def robots_txt():
    return send_from_directory(app.static_folder, "robots.txt", mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap_xml():
    return send_from_directory(app.static_folder, "sitemap.xml", mimetype="application/xml")


@app.route("/llms.txt")
def llms_txt():
    return send_from_directory(app.static_folder, "llms.txt", mimetype="text/plain")


@app.route("/llms-full.txt")
def llms_full_txt():
    return send_from_directory(app.static_folder, "llms-full.txt", mimetype="text/plain")


@app.route("/cfe4e974576948388be18b4402adf3e1.txt")
def indexnow_key():
    return send_from_directory(
        app.static_folder,
        "cfe4e974576948388be18b4402adf3e1.txt",
        mimetype="text/plain",
    )


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/contact")
def contact():
    return render_template("contact.html")


@app.route("/")
def index():
    return render_home("en")


@app.route("/<any(es, zh, fr, tr, id):lang>/")
def index_lang(lang):
    return render_home(lang)


def render_home(lang):
    """Render the homepage in the requested language.

    The page layout is one template; all text comes from translations.py.
    JSON-LD is built from the same strings so the schema always matches
    what the visitor sees.
    """
    t = TRANSLATIONS[lang]
    base = "https://twitter-7vlf.onrender.com/"
    canonical = base if lang == "en" else base + lang + "/"
    hreflangs = [
        ("en", base),
        *[(code, base + code + "/") for code, _, _ in LANGS if code != "en"],
    ]
    faq_json = json.dumps({
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "WebApplication",
                "name": "TwitterDown",
                "alternateName": "Twitter / X Video Downloader",
                "url": base,
                "applicationCategory": "MultimediaApplication",
                "operatingSystem": "Any",
                "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
                "description": t["description"] + " X videos only.",
            },
            {
                "@type": "FAQPage",
                "mainEntity": [
                    {"@type": "Question", "name": f["q"],
                     "acceptedAnswer": {"@type": "Answer", "text": f["a"]}}
                    for f in t["faqs"]
                ],
            },
        ],
    }, ensure_ascii=False)
    return render_template(
        "index.html",
        t=t,
        lang=lang,
        langs=LANGS,
        canonical=canonical,
        hreflangs=hreflangs,
        faq_json=faq_json,
        js_json=json.dumps(t["js"], ensure_ascii=False),
    )


@app.route("/download", methods=["POST"])
def download():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    if not is_x_url(url):
        return jsonify({"error": "TwitterDown is for X only. Please paste a link from x.com or twitter.com."}), 400

    try:
        videos, thumbnail, title = extract_direct_videos(url)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if videos:
        return jsonify({
            "videos": videos,
            "thumbnail": thumbnail,
            "title": title,
            "count": len(videos),
        })

    # No direct link found — fall back to downloading the file server-side.
    try:
        filename = download_file_fallback(url)
        return jsonify({"file": f"/file/{filename}"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/get")
def stream_video():
    """Stream a video from X's CDN through this server as a file download.

    Direct video.twimg.com links are blocked for some users' browsers or
    networks (HTTP 403), and browsers open them in a player instead of
    downloading. This route fetches the file on the server and hands it to
    the user as an attachment, so the download always works. Only
    video.twimg.com video links are allowed — this is not an open proxy.
    The file is streamed in chunks and never saved to disk.
    """
    cdn_url = (request.args.get("url") or "").strip()
    label = (request.args.get("label") or "video").strip()

    try:
        parsed = urlparse(cdn_url)
    except Exception:
        return jsonify({"error": "Invalid URL"}), 400

    if parsed.scheme != "https" or parsed.hostname != "video.twimg.com":
        return jsonify({"error": "Only video.twimg.com links are allowed"}), 400
    if not (parsed.path.startswith("/amplify_video/") or parsed.path.startswith("/ext_tw_video/")):
        return jsonify({"error": "Invalid video link"}), 400

    safe_label = "".join(c for c in label if c.isalnum()) or "video"
    filename = f"twitterdown-{safe_label}.mp4"

    try:
        upstream = urlopen(Request(cdn_url, headers={"User-Agent": "Mozilla/5.0"}), timeout=60)
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    def generate():
        try:
            while True:
                chunk = upstream.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            upstream.close()

    headers = {
        "Content-Type": "video/mp4",
        "Content-Disposition": f'attachment; filename="{filename}"',
    }
    length = upstream.headers.get("Content-Length")
    if length:
        headers["Content-Length"] = length
    return Response(stream_with_context(generate()), headers=headers)


@app.route("/file/<filename>")
def serve_file(filename):
    filepath = os.path.join(DOWNLOAD_FOLDER, filename)
    return send_file(filepath, as_attachment=True)


if __name__ == "__main__":
    app.run(debug=False, port=5000, host="0.0.0.0")
