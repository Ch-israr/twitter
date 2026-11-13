# TwitterDown — Twitter / X Video Downloader

Download videos and GIFs from X (Twitter) as MP4. Paste a link, pick a quality, save the file. Fast, free, no watermark, no sign-up.

**Live site:** https://twitter-7vlf.onrender.com

## How it works

1. Paste a link from `x.com` or `twitter.com` into the box.
2. The server reads the tweet's video links with [yt-dlp](https://github.com/yt-dlp/yt-dlp) (metadata only — nothing is downloaded to the server).
3. You get up to three download buttons (for example 320x568, 480x852, 720x1280) served through TwitterDown as real file downloads, plus a thumbnail preview.

If no direct link is found for a tweet, the server falls back to downloading the video itself and serving the file.

**X only.** Links from TikTok, YouTube, Instagram or anywhere else are rejected, on the site and on the server.

## Run locally

```bash
pip install -r requirements.txt
python app.py
```

Then open http://localhost:5000.

## Deploy

The repo includes a `render.yaml` Blueprint and a `Dockerfile`, so it deploys on Render as a free Docker web service. Pushing to `main` auto-deploys.

## Disclaimer

TwitterDown is not affiliated with X Corp. or Twitter, Inc. It does not host any pirated or copyright content on its server — videos are downloaded to your system directly from their respective CDN servers.
