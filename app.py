from flask import Flask, render_template, request, jsonify, send_file, send_from_directory, Response, stream_with_context
from urllib.parse import urlparse, urlencode
from urllib.request import Request, urlopen
from translations import TRANSLATIONS, LANGS
import yt_dlp
import json
import math
import os
import re
import secrets
import tempfile
import time
import uuid

app = Flask(__name__)

# Canonical home of the site. Every canonical URL, hreflang alternate,
# Open Graph URL, JSON-LD URL and sitemap entry is built from this.
SITE_URL = "https://twitterdown.app"

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


# Friendly message shown when a post has no downloadable video (for
# example a photo-only post). The raw yt-dlp error is technical and is
# never shown to the user for this case.
NO_VIDEO_MESSAGE = "No video or photo could be found in this post — it may be private or deleted."


# ---------------------------------------------------------------------------
# Download tokens + rate limiting (abuse protection)
#
# The browser never sees a raw video.twimg.com address any more: /download
# hands back a short-lived, unguessable token per quality, and /get only
# accepts that token. Tokens are small files in a shared temp directory,
# so both gunicorn workers (same container, same filesystem) can mint and
# read them. Nothing here is a secret that belongs in the repo — tokens
# are random per download and expire after TOKEN_TTL_SECONDS.
# ---------------------------------------------------------------------------
TOKEN_TTL_SECONDS = 600  # 10 minutes: long enough to click, short enough to be useless later
TOKEN_DIR = os.path.join(tempfile.gettempdir(), "twitterdown-tokens")
TOKEN_MAX_FILES = 2000
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{24,64}$")

try:
    os.makedirs(TOKEN_DIR, exist_ok=True)
except Exception:
    # If the temp dir cannot be created, token minting fails per request
    # instead of crashing the app at import time.
    pass


def _sweep_expired_tokens(now=None):
    """Delete expired token files. Best-effort; never raises."""
    now = now if now is not None else int(time.time())
    try:
        names = os.listdir(TOKEN_DIR)
    except Exception:
        return
    for name in names[:500]:  # bound the work per call
        if not _TOKEN_RE.match(name):
            continue
        path = os.path.join(TOKEN_DIR, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if int(payload.get("exp") or 0) <= now:
                os.unlink(path)
        except Exception:
            try:
                os.unlink(path)
            except Exception:
                pass


def mint_download_token(url, label, kind="video", mime="video/mp4", ext="mp4"):
    """Create a short-lived token for one video or photo URL. Returns the
    token string, or None when the store is full (caller should answer 429)."""
    now = int(time.time())
    _sweep_expired_tokens(now)
    try:
        if len(os.listdir(TOKEN_DIR)) >= TOKEN_MAX_FILES:
            return None
    except Exception:
        return None
    token = secrets.token_urlsafe(24)
    payload = {"url": url, "label": label, "exp": now + TOKEN_TTL_SECONDS,
               "kind": kind, "mime": mime, "ext": ext}
    tmp_path = os.path.join(TOKEN_DIR, f".tmp-{token}-{os.getpid()}")
    path = os.path.join(TOKEN_DIR, token)
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        return None
    return token


def lookup_download_token(token):
    """Return the token's payload dict {"url", "label", "kind", "mime",
    "ext"} for a valid token, the string "expired" when the token existed
    but is past its expiry, or None for an unknown or malformed token.
    The token is used as a filename, so the strict format check is what
    keeps path traversal out."""
    if not token or not _TOKEN_RE.match(token):
        return None
    path = os.path.join(TOKEN_DIR, token)
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return None
    if int(payload.get("exp") or 0) <= int(time.time()):
        try:
            os.unlink(path)
        except Exception:
            pass
        return "expired"
    url = payload.get("url") or ""
    if not url:
        return None
    return {
        "url": url,
        "label": payload.get("label") or "video",
        "kind": payload.get("kind") or "video",
        "mime": payload.get("mime") or "video/mp4",
        "ext": payload.get("ext") or "mp4",
    }


# Per-IP rate limits, in memory. Each gunicorn worker keeps its own
# counters, so the effective limit is roughly 2x the nominal one with two
# workers — still far below what a human does, and far above it is abuse.
# Limits are per route so a burst of downloads cannot crowd out lookups
# (or vice versa).
_RATE_LIMITS = {}
_RATE_LIMIT_MAX_KEYS = 2000


def _client_ip():
    forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return forwarded or (request.remote_addr or "unknown")


def _check_rate_limit(route, limit, window_seconds=60):
    """True when this IP may proceed on `route`; False when over the limit."""
    now = time.time()
    key = (_client_ip(), route)
    hits = _RATE_LIMITS.get(key)
    if hits is None:
        hits = _RATE_LIMITS[key] = []
    hits[:] = [t for t in hits if t > now - window_seconds]
    if len(hits) >= limit:
        return False
    hits.append(now)
    if len(_RATE_LIMITS) > _RATE_LIMIT_MAX_KEYS:
        for k in list(_RATE_LIMITS):
            if not _RATE_LIMITS[k] or _RATE_LIMITS[k][-1] <= now - window_seconds:
                del _RATE_LIMITS[k]
    return True


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


# Photos: yt-dlp raises before returning any info for a photo-only tweet,
# so the photos cannot come from yt-dlp. They are read from X's public
# syndication endpoint instead (no auth, no account, no key — the same
# endpoint yt-dlp itself uses under the hood).
_SYNDICATION_URL = "https://cdn.syndication.twimg.com/tweet-result"
_PHOTO_URL_RE = re.compile(r"^https://pbs\.twimg\.com/media/([A-Za-z0-9_-]+)\.(jpg|jpeg|png|webp)$")
_IMAGE_MIME = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


def syndication_token(twid):
    """The syndication token is a pure function of the tweet id:
    ((id / 1e15) * pi) written in base 36, with '0' and '.' removed."""
    n = (int(twid) / 1e15) * math.pi
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    whole = int(n)
    out = ""
    while whole > 0:
        whole, r = divmod(whole, 36)
        out = digits[r] + out
    out = out or "0"
    frac = n - int(n)
    tail = ""
    for _ in range(12):
        frac *= 36
        d = int(frac)
        frac -= d
        tail += digits[d]
    return (out + "." + tail).replace("0", "").replace(".", "")


def extract_photos(url):
    """Photos of a tweet at original quality, via the syndication endpoint.

    Returns (photos, title) where photos is a list of
    {"label", "url", "ext"} (at most 4, one per photo, original size) or
    ([], None) when the tweet has no photos or the endpoint fails. Never
    raises — a failure here must never break the video flow.
    """
    try:
        m = re.search(r"/status/(\d+)", url or "")
        if not m:
            return [], None
        twid = m.group(1)
        req_url = _SYNDICATION_URL + "?" + urlencode({
            "id": twid,
            "token": syndication_token(twid),
        })
        req = Request(req_url, headers={"User-Agent": "Googlebot"})
        with urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return [], None
    if not isinstance(body, dict) or not body:
        return [], None
    # The tweet's own photos first; a retweet carries the original tweet's
    # photos under retweeted_status. A quoted tweet is a different post —
    # its photos are not included (paste that post's link instead).
    media = list(body.get("mediaDetails") or [])
    if not media:
        media = list((body.get("retweeted_status") or {}).get("mediaDetails") or [])
    photos = []
    seen = set()
    for detail in media:
        if not isinstance(detail, dict) or detail.get("type") != "photo":
            continue
        m = _PHOTO_URL_RE.match(detail.get("media_url_https") or "")
        if not m:
            continue
        media_id, ext = m.group(1), m.group(2).lower()
        if ext == "jpeg":
            ext = "jpg"
        if media_id in seen:
            continue
        seen.add(media_id)
        photos.append({
            "label": f"Image {len(photos) + 1}",
            "url": f"https://pbs.twimg.com/media/{media_id}?format={ext}&name=orig",
            "ext": ext,
        })
        if len(photos) >= 4:
            break
    title = (body.get("text") or "").strip() or None
    return photos, title


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
    ctx = page_context(lang, "404")
    # A 404 has no canonical page of its own: point at the homepage
    # and offer no language alternates.
    ctx["canonical"] = SITE_URL + "/"
    ctx["hreflangs"] = []
    return render_template("404.html", **ctx), 404


# Pages with a translated version in every language. Privacy and Terms
# keep their legal text in English, but they have a URL in every language
# (/es/privacy/, /zh/terms/, ...) so the language switcher keeps the user
# on the same page with translated navigation instead of dropping them
# back to the homepage. Pages with no per-language URL at all (e.g. the
# 404 page) fall back to the language homepage.
TRANSLATED_PAGES = {"home", "about", "contact", "faq"}
ROUTED_PAGES = TRANSLATED_PAGES | {"privacy", "terms"}


def lang_url(page, code):
    """URL of `page` in language `code`, preserving the language prefix.

    English info pages keep their existing slash-less URLs (/about);
    language versions use a trailing slash (/zh/about/). Pages with no
    per-language URL fall back to the language homepage.
    """
    if page not in ROUTED_PAGES:
        return "/" if code == "en" else f"/{code}/"
    if page == "home":
        return "/" if code == "en" else f"/{code}/"
    return f"/{page}" if code == "en" else f"/{code}/{page}/"


def page_context(lang, page):
    """Shared template context for every page: translations, the current
    language, and every internal URL in that language."""
    t = TRANSLATIONS[lang]
    base = SITE_URL + "/"
    # Pages with a per-language URL keep their own URL for canonical /
    # hreflang purposes; pages without one fall back to the homepage.
    if page in ROUTED_PAGES:
        this_url = lang_url(page, lang)
        en_url = lang_url(page, "en")
    else:
        this_url = "/"
        en_url = "/"
    canonical = base + this_url.lstrip("/")
    if page in ROUTED_PAGES:
        hreflangs = [
            (code, base + lang_url(page, code).lstrip("/"))
            for code, _, _ in LANGS
        ]
    else:
        hreflangs = [("en", canonical)]
    return {
        "t": t,
        "lang": lang,
        "langs": LANGS,
        "lang_urls": {code: lang_url(page, code) for code, _, _ in LANGS},
        "home": lang_url("home", lang),
        "about_url": lang_url("about", lang),
        "contact_url": lang_url("contact", lang),
        "faq_url": lang_url("faq", lang),
        "canonical": canonical,
        "hreflangs": hreflangs,
        "x_default": base + en_url.lstrip("/"),
    }


def render_info(template, lang, page):
    """Render an info page (about/contact/faq/privacy/terms) in `lang`."""
    ctx = page_context(lang, page)
    if page == "faq":
        ctx["faq_json"] = json.dumps({
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": f["q"],
                 "acceptedAnswer": {"@type": "Answer", "text": f["a"]}}
                for f in ctx["t"]["faqs"]
            ],
        }, ensure_ascii=False)
    return render_template(template, **ctx)


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
    return render_info("privacy.html", "en", "privacy")


@app.route("/<any(es, zh, fr, tr, id):lang>/privacy/")
def privacy_lang(lang):
    # Legal text stays English; header, footer and navigation render in
    # the selected language so switching languages keeps the user here.
    return render_info("privacy.html", lang, "privacy")


@app.route("/terms")
def terms():
    return render_info("terms.html", "en", "terms")


@app.route("/<any(es, zh, fr, tr, id):lang>/terms/")
def terms_lang(lang):
    # Legal text stays English; header, footer and navigation render in
    # the selected language so switching languages keeps the user here.
    return render_info("terms.html", lang, "terms")


@app.route("/about")
def about():
    return render_info("about.html", "en", "about")


@app.route("/contact")
def contact():
    return render_info("contact.html", "en", "contact")


@app.route("/faq")
def faq():
    return render_info("faq.html", "en", "faq")


@app.route("/<any(es, zh, fr, tr, id):lang>/about/")
def about_lang(lang):
    return render_info("about.html", lang, "about")


@app.route("/<any(es, zh, fr, tr, id):lang>/contact/")
def contact_lang(lang):
    return render_info("contact.html", lang, "contact")


@app.route("/<any(es, zh, fr, tr, id):lang>/faq/")
def faq_lang(lang):
    return render_info("faq.html", lang, "faq")


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
    base = SITE_URL + "/"
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
        lang_urls={code: lang_url("home", code) for code, _, _ in LANGS},
        about_url=lang_url("about", lang),
        contact_url=lang_url("contact", lang),
        faq_url=lang_url("faq", lang),
        canonical=canonical,
        hreflangs=hreflangs,
        faq_json=faq_json,
        js_json=json.dumps(t["js"], ensure_ascii=False),
    )


@app.route("/download", methods=["POST"])
def download():
    if not _check_rate_limit("download", 10, 60):
        return jsonify({"error": "Too many requests. Please wait a minute and try again."}), 429

    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    if not is_x_url(url):
        return jsonify({"error": "TwitterDown is for X only. Please paste a link from x.com or twitter.com."}), 400

    try:
        videos, thumbnail, title = extract_direct_videos(url)
    except Exception as e:
        if "no video could be found" in str(e).lower():
            # Photo-only post: yt-dlp gives up before returning any info,
            # so read the photos from X's public syndication endpoint.
            resp = _photos_response(url)
            if resp is not None:
                return resp
            return jsonify({"error": NO_VIDEO_MESSAGE}), 404
        return jsonify({"error": str(e)}), 500

    if videos:
        out = []
        for v in videos:
            token = mint_download_token(v["url"], v["label"])
            if token is None:
                return jsonify({"error": "The service is busy. Please try again in a minute."}), 429
            out.append({
                "label": v["label"],
                "width": v["width"],
                "height": v["height"],
                "token": token,
            })
        return jsonify({
            "videos": out,
            "images": [],
            "thumbnail": thumbnail,
            "title": title,
            "count": len(out),
        })

    # No direct video link — try photos before the server-side fallback.
    resp = _photos_response(url)
    if resp is not None:
        return resp

    # No direct link found — fall back to downloading the file server-side.
    try:
        filename = download_file_fallback(url)
        return jsonify({"file": f"/file/{filename}"})
    except Exception as e:
        if "no video could be found" in str(e).lower():
            return jsonify({"error": NO_VIDEO_MESSAGE}), 404
        return jsonify({"error": str(e)}), 500


def _photos_response(url):
    """A /download JSON response for a photo post, or None when the post
    has no downloadable photos."""
    photos, title = extract_photos(url)
    if not photos:
        return None
    out = []
    for p in photos:
        token = mint_download_token(p["url"], p["label"], kind="image",
                                    mime=_IMAGE_MIME[p["ext"]], ext=p["ext"])
        if token is None:
            return jsonify({"error": "The service is busy. Please try again in a minute."}), 429
        out.append({"label": p["label"], "ext": p["ext"], "token": token})
    return jsonify({
        "videos": [],
        "images": out,
        "thumbnail": photos[0]["url"],
        "title": title,
        "count": 0,
    }), 200


@app.route("/get")
def stream_video():
    """Stream a video or photo from X's CDN through this server as a file
    download.

    Accepts only a short-lived token minted by /download — the raw
    video.twimg.com or pbs.twimg.com address is never exposed to the
    browser any more, so download links cannot be hotlinked or forged, and
    they stop working after 10 minutes. Direct CDN links are blocked for
    some users' browsers or networks (HTTP 403), and browsers open them in
    a player instead of downloading. This route fetches the file on the
    server and hands it to the user as an attachment, so the download
    always works. Only video.twimg.com video links and pbs.twimg.com
    photo links are allowed — this is not an open proxy. The file is
    streamed in chunks and never saved to disk.
    """
    if not _check_rate_limit("get", 30, 60):
        return jsonify({"error": "Too many requests. Please wait a minute and try again."}), 429

    token = (request.args.get("token") or "").strip()
    payload = lookup_download_token(token)
    if payload == "expired":
        return jsonify({"error": "This download link has expired. Please fetch it again."}), 410
    if not payload:
        return jsonify({"error": "Invalid download link. Please fetch it again."}), 400

    cdn_url = payload["url"]
    label = payload["label"]
    kind = payload["kind"]
    mime = payload["mime"]
    ext = payload["ext"]

    try:
        parsed = urlparse(cdn_url)
    except Exception:
        return jsonify({"error": "Invalid URL"}), 400

    if kind == "image":
        if parsed.scheme != "https" or parsed.hostname != "pbs.twimg.com":
            return jsonify({"error": "Only pbs.twimg.com links are allowed"}), 400
        if not re.fullmatch(r"/media/[A-Za-z0-9_-]+", parsed.path or ""):
            return jsonify({"error": "Invalid image link"}), 400
        if ext not in _IMAGE_MIME or mime != _IMAGE_MIME[ext]:
            return jsonify({"error": "Invalid image link"}), 400
    else:
        if parsed.scheme != "https" or parsed.hostname != "video.twimg.com":
            return jsonify({"error": "Only video.twimg.com links are allowed"}), 400
        if not (parsed.path.startswith("/amplify_video/") or parsed.path.startswith("/ext_tw_video/") or parsed.path.startswith("/tweet_video/")):
            return jsonify({"error": "Invalid video link"}), 400

    safe_label = "".join(c for c in label if c.isalnum()) or "video"
    filename = f"twitterdown-{safe_label}.{ext}"

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
        "Content-Type": mime,
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
