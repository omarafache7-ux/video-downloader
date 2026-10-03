<p align="center">
  <img src="icon.png" width="96" alt="Video Downloader icon">
</p>

<h1 align="center">Video Downloader</h1>

<p align="center">
  A simple Windows app to download videos from YouTube, Instagram, TikTok, X/Twitter, Facebook, Reddit and 1000+ other sites.<br>
  Paste a link, see the preview, pick a quality, done.
</p>

---

## Download

1. Go to [**Releases**](../../releases/latest) and download `VideoDownloader.zip`.
2. **Unzip it** (it won't run from inside the zip).
3. Open the folder and double-click `VideoDownloader.exe`.

Nothing else needs to be installed. Everything the app needs is in the folder.

> **"Windows protected your PC"?** The app is not code-signed, so Windows SmartScreen warns about it the first time.
> Click **More info → Run anyway**.

Requires 64-bit Windows 10 or 11.

## Features

- **Instant preview:** paste a link and the thumbnail, title, uploader and length appear automatically.
- **Real quality options:** shows only the qualities the video actually has, with file sizes
  (for example `1080p60 (~219 MB)`, `720p (~54 MB)`, `Audio only - MP3`).
- **Plays everywhere:** prefers H.264 + AAC, saved as MP4.
- **MP3 audio:** extract just the audio from any video.
- **Automatic login:** handles sites that require you to be logged in (see below).
- **Works on vertical videos:** TikTok and Reels are labeled correctly (1080p, not 1920p).

## How to use

1. Paste a video link into the box (or click **Paste**).
2. Choose a quality.
3. Click **Download**. Files are saved to your **Downloads** folder (change it with **Browse...**).

### Sites that need a login

Some videos are only shown to logged-in users: Instagram posts, private or age-restricted videos,
and sometimes YouTube when it suspects a bot.

1. Click **Log in...**. A browser window opens on that site.
2. Log in as usual.
3. Go back to the app and click **I'm logged in**.

You only do this once per site. With **Login** set to **Auto**, the app first tries without a login and uses
your saved login only when a site asks for it.

<details>
<summary>How does this work, and is it safe?</summary>

Websites remember that you are logged in with *cookies*. Newer versions of Chrome and Edge encrypt their cookies so
no other program can read them. So the app opens its own separate Edge/Chrome profile, stored in
`%LOCALAPPDATA%\VideoDownloader`, and reads the cookies from that profile instead. Your normal browser is never touched.

Cookies are only sent to the website they belong to and never leave your PC otherwise. Delete the folder above to
log out of everything.

Other options under **Login**: a Firefox login (read directly), or a `cookies.txt` file exported with a browser extension.
</details>

## Build from source

Requirements: Windows, Python 3.12+ and an internet connection.

```powershell
git clone https://github.com/omarafache7-ux/video-downloader.git
cd video-downloader
powershell -ExecutionPolicy Bypass -File build.ps1
```

The script installs the Python packages, builds the exe with PyInstaller, downloads ffmpeg and deno, and puts
the finished app in `dist\VideoDownloader\` and `dist\VideoDownloader.zip`.

To run without building: `pip install -r requirements.txt`, then `python VideoDownloader.py`
(this uses ffmpeg and deno from your `PATH`).

**When downloads from a site stop working**, it is usually because the site changed. Rebuild with `build.ps1`
to pick up the latest yt-dlp.

### Project layout

| File | Purpose |
|---|---|
| `VideoDownloader.py` | The whole app (Tkinter GUI + yt-dlp) |
| `build.ps1` | One-command build of the shareable folder and zip |
| `make_icon.py` | Generates `icon.ico` / `icon.png` |
| `packaging/README.txt` | Plain-text guide shipped inside the zip |
| `requirements.txt` | Python dependencies |

The packaged app also supports a headless check:
`VideoDownloader.exe --selftest <url> <output-folder>` writes `selftest.log` to that folder.

## Troubleshooting

| Problem | Fix |
|---|---|
| "The site wants a login" | Use **Log in...** (see above). |
| No preview image | The video still downloads. Details are logged in `%LOCALAPPDATA%\VideoDownloader\log.txt`. |
| A site worked before but now fails | Rebuild to update yt-dlp, or download the latest release. |
| Google won't let you sign in in the login window | Google sometimes blocks sign-in in browser windows controlled by another app. Use a `cookies.txt` file instead (**Login → cookies.txt file...**). |

## Credits

- [yt-dlp](https://github.com/yt-dlp/yt-dlp): the download engine (Unlicense)
- [FFmpeg](https://ffmpeg.org): merging and audio conversion. LGPL build from [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds)
- [Deno](https://deno.com): JavaScript runtime used to pass YouTube's checks (MIT)
- [curl_cffi](https://github.com/lexiforest/curl_cffi), [Pillow](https://python-pillow.org), [websockets](https://github.com/python-websockets/websockets), [PyInstaller](https://pyinstaller.org)

## License

This project is released under the [MIT License](LICENSE). The bundled tools keep their own licenses (see Credits).

## Disclaimer

Only download content you have the right to download. Respect copyright and each website's terms of service.
