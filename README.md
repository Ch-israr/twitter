# TwitterDown — Twitter / X Video & Photo Downloader

Download videos, GIFs and photos from X (Twitter). Videos save as MP4 — paste a link, pick a quality, save the file. Photos save at original quality as JPG or PNG — one button per photo, up to four per post. Fast, free, no watermark, no sign-up.

**Live site:** https://twitterdown.app

## How it works

1. Paste a link from `x.com` or `twitter.com` into the box.
2. The server reads the tweet's video links with [yt-dlp](https://github.com/yt-dlp/yt-dlp) (metadata only — nothing is downloaded to the server). For photo posts, yt-dlp gives up before returning any info, so the server reads the photos from X's public syndication endpoint instead (no auth).
3. You get up to five download buttons for videos (for example 480x270, 1280x720, 1920x1080, 3840x2160) served through TwitterDown as real file downloads, plus a thumbnail preview — or one download button per photo (up to four), each with its own preview, at original quality.

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
