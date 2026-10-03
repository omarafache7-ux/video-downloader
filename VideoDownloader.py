"""Video Downloader - GUI wrapper around yt-dlp."""
import io
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
from tkinter import ttk, filedialog, messagebox
from urllib.parse import urlparse

import yt_dlp
from PIL import Image, ImageDraw, ImageOps, ImageTk
from websockets.sync.client import connect as ws_connect
from yt_dlp.networking import Request
from yt_dlp.networking.impersonate import ImpersonateTarget

# A --windowed exe has no console, so give libraries somewhere harmless to write.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")


def resource_path(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


EXE_DIR = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
TOOLS_DIR = os.path.join(EXE_DIR, "tools")

# ffmpeg and deno shipped in the "tools" folder next to the exe take priority over installed copies.
# yt-dlp finds both through PATH.
for _d in (EXE_DIR, TOOLS_DIR):
    if os.path.isdir(_d):
        os.environ["PATH"] = _d + os.pathsep + os.environ.get("PATH", "")


def find_ffmpeg():
    found = shutil.which("ffmpeg")
    return os.path.dirname(found) if found else None


FFMPEG = find_ffmpeg()
DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "Downloads")
THUMB_SIZE = (320, 180)

APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "VideoDownloader")
PROFILE_DIR = os.path.join(APP_DIR, "login-browser")
SAVED_COOKIES = os.path.join(APP_DIR, "cookies.txt")
LOG_FILE = os.path.join(APP_DIR, "log.txt")

LOGIN_BROWSER_EXES = [
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
]


def log(msg):
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
    except OSError:
        pass


def find_login_browser():
    for p in LOGIN_BROWSER_EXES:
        p = os.path.expandvars(p)
        if os.path.isfile(p):
            return p
    return None


class LoginBrowser:
    """A real Edge/Chrome window using the app's own profile, controlled over the DevTools protocol.
    Your normal browser profile locks its cookies, but this profile belongs to the app, so its
    cookies can be read back and handed to the downloader."""

    def __init__(self, url="about:blank", headless=False):
        exe = find_login_browser()
        if not exe:
            raise RuntimeError("Microsoft Edge or Google Chrome is needed for the Log in button.")
        os.makedirs(PROFILE_DIR, exist_ok=True)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        args = [exe, f"--remote-debugging-port={self.port}", f"--user-data-dir={PROFILE_DIR}",
                "--no-first-run", "--no-default-browser-check", "--disable-sync"]
        if headless:
            args.append("--headless=new")
        self.proc = subprocess.Popen(args + [url])
        self.ws_url = self._wait_for_devtools()

    def _wait_for_devtools(self):
        deadline = time.time() + 20
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("The login browser closed immediately (is its window already open?).")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/version", timeout=2) as r:
                    return json.load(r)["webSocketDebuggerUrl"]
            except OSError:
                time.sleep(0.3)
        self.proc.kill()
        raise RuntimeError("The login browser did not start in time.")

    def alive(self):
        return self.proc.poll() is None

    def call(self, method, params=None):
        with ws_connect(self.ws_url, max_size=None, open_timeout=10) as ws:
            ws.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
            while True:
                msg = json.loads(ws.recv(timeout=15))
                if msg.get("id") == 1:
                    if "error" in msg:
                        raise RuntimeError(msg["error"].get("message", "DevTools error"))
                    return msg.get("result", {})

    def cookies(self):
        return self.call("Storage.getCookies")["cookies"]

    def close(self):
        try:
            self.call("Browser.close")
        except Exception:
            pass
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            log("login browser did not close in time, killing it")
            self.proc.kill()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                pass


ACTIVE_LOGIN = None  # the visible login window, while it is open
LOGIN_LOCK = threading.Lock()


def write_cookie_file(cookies, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    session_expiry = int(time.time()) + 86400
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        if any(ch in c["name"] + c["value"] for ch in "\t\r\n"):
            continue
        expires = int(c.get("expires") or -1)
        domain = c["domain"]
        lines.append("\t".join([domain, "TRUE" if domain.startswith(".") else "FALSE", c.get("path") or "/",
                                "TRUE" if c.get("secure") else "FALSE",
                                str(expires if expires > 0 else session_expiry), c["name"], c["value"]]))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return len(lines) - 1


def has_saved_login():
    return os.path.isdir(PROFILE_DIR)


def refresh_saved_login():
    """Copy the cookies of the app's login browser into cookies.txt. Returns the file path."""
    with LOGIN_LOCK:
        browser = ACTIVE_LOGIN if ACTIVE_LOGIN and ACTIVE_LOGIN.alive() else None
        own = browser is None
        if own:
            browser = LoginBrowser(headless=True)
        try:
            cookies = browser.cookies()
        finally:
            if own:
                browser.close()
    n = write_cookie_file(cookies, SAVED_COOKIES)
    log(f"saved login refreshed: {n} cookies")
    return SAVED_COOKIES

# (yt-dlp name, display name, folder that exists when the browser is installed)
BROWSERS = [
    ("firefox", "Firefox", r"%APPDATA%\Mozilla\Firefox\Profiles"),
    ("chrome", "Chrome", r"%LOCALAPPDATA%\Google\Chrome\User Data"),
    ("edge", "Edge", r"%LOCALAPPDATA%\Microsoft\Edge\User Data"),
    ("brave", "Brave", r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data"),
    ("opera", "Opera", r"%APPDATA%\Opera Software\Opera Stable"),
    ("vivaldi", "Vivaldi", r"%LOCALAPPDATA%\Vivaldi\User Data"),
]

LOGIN_HINTS = ("sign in", "log in", "logged-in", "login", "--cookies", "not a bot", "private",
               "age-restricted", "confirm your age", "inappropriate", "registered users",
               "rate-limit", "members-only", "members only")

COOKIE_HELP = (
    "Why does a downloader need a login?\n"
    "Some videos are only shown to people who are logged in: Instagram posts, private or "
    "age-restricted videos, and sometimes YouTube when it suspects a bot.\n\n"
    "What are cookies?\n"
    "Cookies are small files your web browser keeps so websites remember that you are logged in.\n\n"
    "How to log in (one time per site):\n"
    "Click \"Log in...\". A browser window opens on the site. Log in as usual, then come back "
    "and click \"I'm logged in\". The app remembers it, so you don't have to do it again.\n"
    "(This window uses the app's own browser profile. Your normal Chrome/Edge lock their "
    "cookies so no other program can read them, which is why a separate window is needed.)\n\n"
    "What does Auto do?\n"
    "1. It first tries without any login.\n"
    "2. If the site asks for a login, it uses the login you saved with the \"Log in...\" button.\n"
    "3. Then it tries the browsers on this PC (works with Firefox; usually not with new Chrome/Edge).\n\n"
    "Is it safe?\n"
    "Everything stays on this PC. Cookies are only sent to the website they belong to, "
    "never uploaded anywhere else. Saved login: {saved}\n\n"
    "Browsers found on this PC: {browsers}"
)

ANSI = re.compile(r"\x1b\[[0-9;]*m")


class QuietLogger:
    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


def installed_browsers():
    return [(key, name) for key, name, path in BROWSERS if os.path.isdir(os.path.expandvars(path))]


def needs_login(raw_msg):
    low = raw_msg.lower()
    return any(h in low for h in LOGIN_HINTS)


def clean(raw_msg):
    msg = ANSI.sub("", raw_msg).replace("ERROR: ", "")
    msg = re.sub(r"\s*(Use --cookies|See\s+https?://|please report this issue).*", "", msg, flags=re.S | re.I)
    return msg.strip()[:400]


def human_size(n):
    if not n:
        return ""
    return f"~{n / 1024 ** 3:.1f} GB" if n >= 1024 ** 3 else f"~{max(1, round(n / 1024 ** 2))} MB"


def human_duration(sec):
    if not sec:
        return ""
    sec = int(sec)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def base_opts(extra):
    opts = {"quiet": True, "no_warnings": True, "noprogress": True, "logger": QuietLogger(),
            "noplaylist": True, "playlist_items": "1", "retries": 5, "windowsfilenames": True}
    opts.update(extra)
    return opts


def thumb_header_variants(info):
    """Header sets to try for the thumbnail. Some sites (e.g. Pornhub) refuse image requests
    that don't say which page they come from."""
    headers = dict(info.get("http_headers") or {})
    page = urlparse(info.get("webpage_url") or info.get("original_url") or "")
    if page.netloc:
        headers.setdefault("Referer", f"{page.scheme}://{page.netloc}/")
        headers.setdefault("Origin", f"{page.scheme}://{page.netloc}")
    return [
        {"headers": headers},
        {"headers": headers, "extensions": {"impersonate": ImpersonateTarget("chrome")}},
        {},
    ]


def fetch_thumb(ydl, info):
    urls = [info.get("thumbnail")] + [t.get("url") for t in reversed(info.get("thumbnails") or [])]
    urls = list(dict.fromkeys(u for u in urls if u))[:3]
    for u in urls:
        for variant in thumb_header_variants(info):
            try:
                ext = dict(variant.get("extensions") or {}, timeout=10)
                with ydl.urlopen(Request(u, headers=variant.get("headers"), extensions=ext)) as r:
                    data = r.read()
                img = Image.open(io.BytesIO(data))
                img.load()
                return img.convert("RGB")
            except Exception as e:
                log(f"thumbnail attempt failed ({u[:120]}): {clean(str(e))[:200]}")
    return None


def fit_thumb(img, text=None):
    canvas = Image.new("RGB", THUMB_SIZE, (32, 32, 32))
    if img is not None:
        img = ImageOps.contain(img, THUMB_SIZE)
        canvas.paste(img, ((THUMB_SIZE[0] - img.width) // 2, (THUMB_SIZE[1] - img.height) // 2))
    elif text:
        draw = ImageDraw.Draw(canvas)
        box = draw.textbbox((0, 0), text)
        draw.text(((THUMB_SIZE[0] - box[2]) // 2, (THUMB_SIZE[1] - box[3]) // 2), text, fill=(170, 170, 170))
    return canvas


def extract(url, extra):
    """Look up a video without downloading it. Returns (info, thumbnail image or None)."""
    with yt_dlp.YoutubeDL(base_opts(extra)) as ydl:
        info = ydl.extract_info(url, download=False)
        if info.get("_type") == "playlist" or info.get("entries") is not None:
            entries = [e for e in (info.get("entries") or []) if e]
            if not entries:
                raise yt_dlp.utils.DownloadError("No video found at this link.")
            info = entries[0]
        return info, fetch_thumb(ydl, info)


def build_choices(info):
    """Turn the formats a video actually offers into download options, best first."""
    fmts = [f for f in (info.get("formats") or [info]) if not f.get("has_drm")]
    duration = info.get("duration") or 0

    def size(f):
        if not f:
            return 0
        s = f.get("filesize") or f.get("filesize_approx")
        if not s and f.get("tbr") and duration:
            s = f["tbr"] * 125 * duration  # kbit/s -> bytes
        return s or 0

    audios = [f for f in fmts if f.get("vcodec") == "none" and f.get("acodec") != "none"]
    best_audio = max(audios, key=lambda f: (f.get("ext") == "m4a", f.get("abr") or f.get("tbr") or 0), default=None)
    can_merge = bool(FFMPEG and best_audio)

    groups = {}
    for f in fmts:
        if f.get("vcodec") == "none" or not (f.get("height") or f.get("width")):
            continue
        video_only = f.get("acodec") == "none"
        if video_only and not can_merge:
            continue
        h, w = f.get("height") or 0, f.get("width") or 0
        p = min(h, w) if h and w else (h or w)  # short side, so vertical 1080x1920 reads "1080p"
        key = (p, (f.get("fps") or 0) > 32)
        rank = ((f.get("vcodec") or "").startswith(("avc1", "h264")), f.get("tbr") or 0)
        if key not in groups or rank > groups[key][0]:
            groups[key] = (rank, f)

    choices = []
    for (p, high_fps), (_, f) in sorted(groups.items(), key=lambda kv: kv[0], reverse=True):
        video_only = f.get("acodec") == "none"
        sel = f"{f['format_id']}+{best_audio['format_id']}" if video_only else f["format_id"]
        h = f.get("height")
        if FFMPEG:
            fallback = f"bv*[height={h}]+ba/b[height={h}]/bv*+ba/b" if h else "bv*+ba/b"
        else:
            fallback = f"b[height={h}]/b" if h else "b"
        fps = f"{int(f['fps'])}" if high_fps else ""
        sz = human_size(size(f) + (size(best_audio) if video_only else 0))
        label = f"{p}p{fps}" + (f"   ({sz})" if sz else "")
        choices.append({"label": label, "format": f"{sel}/{fallback}", "kind": "video"})

    if not choices:
        choices.append({"label": "Best available", "format": "bv*+ba/b" if FFMPEG else "b", "kind": "video"})
    if choices[0]["kind"] == "video":
        choices[0]["label"] += "   - best"

    a_sel = f"{best_audio['format_id']}/ba/b" if best_audio else "ba/b"
    a_sz = human_size(size(best_audio))
    if FFMPEG:
        choices.append({"label": "Audio only - MP3" + (f"   ({a_sz})" if a_sz else ""), "format": a_sel, "kind": "audio"})
    elif best_audio:
        choices.append({"label": f"Audio only - {best_audio.get('ext', 'original')}" + (f"   ({a_sz})" if a_sz else ""),
                        "format": a_sel, "kind": "audio"})
    return choices


def download(url, choice, extra, out, hook=None, done_hook=None):
    opts = base_opts(extra)
    # audio gets its own name so converting it never deletes an earlier video download of the same item
    name = "%(title).150B [%(id)s] (audio).%(ext)s" if choice["kind"] == "audio" else "%(title).150B [%(id)s].%(ext)s"
    opts.update({"outtmpl": os.path.join(out, name), "format": choice["format"]})
    if hook:
        opts["progress_hooks"] = [hook]
    if done_hook:
        opts["post_hooks"] = [done_hook]
    if FFMPEG:
        opts["ffmpeg_location"] = FFMPEG
        if choice["kind"] == "audio":
            opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
        else:
            opts["merge_output_format"] = "mp4"
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


class App:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.req = 0
        self.busy = False
        self.loading = False
        self.info = None
        self.choices = []
        self.fetched_url = None
        self.cookie_extra = {}
        self.cookie_file = None
        self.last_file = None
        self.debounce = None
        self.browsers = installed_browsers()

        root.title("Video Downloader")
        root.geometry("720x560")
        root.minsize(660, 540)
        try:
            root.iconbitmap(resource_path("icon.ico"))
        except tk.TclError:
            pass

        frm = ttk.Frame(root, padding=14)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        # link row
        ttk.Label(frm, text="Video link:").grid(row=0, column=0, sticky="w")
        self.url = tk.StringVar()
        self.url.trace_add("write", self.on_url_change)
        entry = ttk.Entry(frm, textvariable=self.url, font=("Segoe UI", 10))
        entry.grid(row=0, column=1, sticky="ew", padx=6)
        entry.bind("<Return>", lambda _e: self.fetch(force=True))
        entry.focus_set()
        ttk.Button(frm, text="Paste", command=self.paste).grid(row=0, column=2)
        ttk.Button(frm, text="Load", command=lambda: self.fetch(force=True)).grid(row=0, column=3, padx=(6, 0))

        # preview
        preview = ttk.Frame(frm)
        preview.grid(row=1, column=0, columnspan=4, sticky="ew", pady=14)
        self.blank = ImageTk.PhotoImage(fit_thumb(None))
        self.photo = None
        self.thumb_lbl = ttk.Label(preview, image=self.blank)
        self.thumb_lbl.pack(side="left", anchor="n")
        meta = ttk.Frame(preview)
        meta.pack(side="left", fill="both", expand=True, padx=(14, 0))
        self.title_var = tk.StringVar(value="Paste a video link above.\nThe preview and qualities will appear here.")
        ttk.Label(meta, textvariable=self.title_var, font=("Segoe UI", 11, "bold"),
                  wraplength=330, justify="left").pack(anchor="w")
        self.meta_var = tk.StringVar()
        ttk.Label(meta, textvariable=self.meta_var, wraplength=330, justify="left",
                  foreground="#555555").pack(anchor="w", pady=(8, 0))

        # options
        ttk.Label(frm, text="Quality:").grid(row=2, column=0, sticky="w", pady=4)
        self.quality = ttk.Combobox(frm, state="disabled", width=40)
        self.quality.grid(row=2, column=1, sticky="w", padx=6)

        ttk.Label(frm, text="Login:").grid(row=3, column=0, sticky="w", pady=4)
        login_values = (["Auto (recommended)", "No login", "My saved login (Log in button)"]
                        + [f"Use my {n} login" for _, n in self.browsers] + ["cookies.txt file..."])
        self.login = ttk.Combobox(frm, values=login_values, state="readonly", width=40)
        self.login.current(0)
        self.login.grid(row=3, column=1, sticky="w", padx=6)
        self.login.bind("<<ComboboxSelected>>", self.on_login)
        self.login_btn = ttk.Button(frm, text="Log in...", command=self.login_clicked)
        self.login_btn.grid(row=3, column=2, sticky="w")
        ttk.Button(frm, text="What's this?", command=self.explain_cookies).grid(row=3, column=3, sticky="w", padx=(6, 0))

        ttk.Label(frm, text="Save to:").grid(row=4, column=0, sticky="w", pady=4)
        self.folder = tk.StringVar(value=DEFAULT_DIR)
        ttk.Entry(frm, textvariable=self.folder).grid(row=4, column=1, sticky="ew", padx=6)
        ttk.Button(frm, text="Browse...", command=self.browse).grid(row=4, column=2, columnspan=2, sticky="w")

        self.progress = ttk.Progressbar(frm, maximum=100)
        self.progress.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(14, 4))
        self.status = tk.StringVar(value="Ready." if FFMPEG else "Ready. (ffmpeg not found: some qualities unavailable)")
        ttk.Label(frm, textvariable=self.status, wraplength=680).grid(row=6, column=0, columnspan=4, sticky="w")

        bottom = ttk.Frame(frm)
        bottom.grid(row=7, column=0, columnspan=4, sticky="e", pady=(12, 0))
        ttk.Button(bottom, text="Open folder", command=self.open_folder).pack(side="left", padx=6)
        self.dl_btn = ttk.Button(bottom, text="Download", command=self.start, state="disabled")
        self.dl_btn.pack(side="left")
        frm.rowconfigure(8, weight=1)

        root.after(100, self.poll)

    # ---------- input ----------
    def paste(self):
        try:
            text = self.root.clipboard_get().strip()
        except tk.TclError:
            return
        self.url.set(text.split()[0] if text else "")
        self.fetch(force=True)

    def on_url_change(self, *_):
        if self.debounce:
            self.root.after_cancel(self.debounce)
        self.debounce = self.root.after(700, self.fetch)

    def on_login(self, _event=None):
        if self.login.get().startswith("cookies.txt"):
            f = filedialog.askopenfilename(title="Select cookies.txt",
                                           filetypes=[("Cookies file", "*.txt"), ("All files", "*.*")])
            if not f:
                self.login.current(0)
            self.cookie_file = f or None
        if self.url.get().strip():
            self.fetch(force=True)

    def explain_cookies(self):
        names = ", ".join(n for _, n in self.browsers) or "none found"
        saved = "yes" if has_saved_login() else "not yet"
        messagebox.showinfo("Login & cookies", COOKIE_HELP.format(browsers=names, saved=saved))

    # ---------- built-in login window ----------
    def login_clicked(self):
        if ACTIVE_LOGIN is not None:
            self.login_btn.config(state="disabled")
            self.status.set("Saving your login...")
            threading.Thread(target=self.finish_login_worker, daemon=True).start()
            return
        page = urlparse(self.url.get().strip())
        site = f"{page.scheme}://{page.netloc}/" if page.scheme in ("http", "https") and page.netloc else "about:blank"
        self.login_btn.config(state="disabled")
        self.status.set("Opening the login window...")
        threading.Thread(target=self.open_login_worker, args=(site,), daemon=True).start()

    def open_login_worker(self, site):
        global ACTIVE_LOGIN
        try:
            ACTIVE_LOGIN = LoginBrowser(site)
            self.q.put(("login_open", None))
        except Exception as e:
            log(f"login window failed: {e}")
            self.q.put(("login_open", str(e)))

    def finish_login_worker(self):
        global ACTIVE_LOGIN
        try:
            refresh_saved_login()
            err = None
        except Exception as e:
            log(f"saving login failed: {e}")
            err = str(e)
        browser, ACTIVE_LOGIN = ACTIVE_LOGIN, None
        if browser and browser.alive():
            browser.close()
        self.q.put(("login_saved", err))

    def browse(self):
        d = filedialog.askdirectory(initialdir=self.folder.get() or DEFAULT_DIR)
        if d:
            self.folder.set(d)

    def open_folder(self):
        if self.last_file and os.path.isfile(self.last_file):
            subprocess.Popen(f'explorer /select,"{self.last_file}"')
            return
        d = self.folder.get()
        os.makedirs(d, exist_ok=True)
        os.startfile(d)

    def attempts(self):
        # extra=None means "saved login": its cookies are refreshed in the worker thread when needed
        idx = self.login.current()
        browsers = [(n, {"cookiesfrombrowser": (k,)}) for k, n in self.browsers]
        saved = [("saved", None)]
        if idx == 0:
            return [("none", {})] + (saved if has_saved_login() else []) + browsers
        if idx == 1:
            return [("none", {})]
        if idx == 2:
            return saved
        if idx - 3 < len(browsers):
            return [browsers[idx - 3]]
        return [("cookies.txt", {"cookiefile": self.cookie_file})] if self.cookie_file else [("none", {})]

    # ---------- look up ----------
    def fetch(self, force=False):
        self.debounce = None
        url = self.url.get().strip()
        if self.busy:
            return
        if not url.lower().startswith(("http://", "https://")):
            if force and url:
                self.status.set("That doesn't look like a link. It should start with http:// or https://")
            return
        if url == self.fetched_url and not force:
            return
        self.fetched_url = url
        self.req += 1
        self.info = None
        self.loading = True
        self.thumb_lbl.config(image=self.blank)
        self.title_var.set("Loading video info...")
        self.meta_var.set("")
        self.quality.set("")
        self.quality.config(state="disabled")
        self.dl_btn.config(state="disabled")
        self.progress.config(mode="indeterminate")
        self.progress.start(12)
        self.status.set("Looking up video...")
        threading.Thread(target=self.fetch_worker, args=(self.req, url, self.attempts()), daemon=True).start()

    def fetch_worker(self, req, url, attempts):
        errors = []
        for label, extra in attempts:
            if req != self.req:
                return
            if label != "none":
                self.q.put(("status", req, f"This site wants a login. Trying your {label} login..."))
            try:
                if extra is None:
                    if not has_saved_login():
                        raise RuntimeError("No saved login yet. Click \"Log in...\" first.")
                    extra = {"cookiefile": refresh_saved_login()}
                info, thumb = extract(url, extra)
            except Exception as e:
                raw = str(e)
                errors.append((label, clean(raw), needs_login(raw)))
                if label == "none" and not needs_login(raw):
                    break  # not a login problem, other logins won't help
                continue
            self.q.put(("info", req, info, thumb, label, extra))
            return
        self.q.put(("fetch_error", req, errors))

    def show_info(self, info, thumb, label, extra):
        self.info = info
        self.cookie_extra = extra
        self.photo = ImageTk.PhotoImage(fit_thumb(thumb, "No preview available"))
        self.thumb_lbl.config(image=self.photo)
        self.title_var.set(info.get("title") or info.get("id") or "Untitled video")
        lines = []
        who = info.get("uploader") or info.get("channel") or info.get("creator")
        if who:
            lines.append(f"By {who}")
        details = [x for x in (human_duration(info.get("duration")), info.get("extractor_key")) if x]
        if details:
            lines.append("   |   ".join(details))
        self.meta_var.set("\n".join(lines))
        self.choices = build_choices(info)
        self.quality.config(values=[c["label"] for c in self.choices], state="readonly")
        self.quality.current(0)
        self.dl_btn.config(state="normal")
        n_video = sum(c["kind"] == "video" for c in self.choices)
        used = f" (used your {label} login)" if label != "none" else ""
        self.status.set(f"Ready to download. {n_video} video quality option(s) available{used}.")

    def show_fetch_error(self, errors):
        self.title_var.set("Couldn't load this video.")
        # show the site's own message, not a browser-cookie read error
        site_errors = [e for e in errors if not re.search(r"decrypt|could not copy|cookie database|cookies? (file|from)", e[1], re.I)]
        msg = (site_errors or errors)[0][1] if errors else "Unknown error."
        login = any(e[2] for e in errors)
        tried = [e[0] for e in errors if e[0] != "none"]
        unreadable = [e[0] for e in errors if e[0] != "none" and e not in site_errors]
        fix = ("Click \"Log in...\", log into the site in the window that opens, "
               "then click \"I'm logged in\". The video will load by itself.")
        if login and self.login.current() == 1:
            hint = "\n\nThe site wants a login. Set Login to Auto and click Load again."
        elif login and "saved" in tried:
            hint = f"\n\nThe site wants a login, and your saved login didn't work here (not logged into this site yet?). {fix}"
        elif login:
            hint = f"\n\nThe site wants a login. {fix}"
        else:
            hint = ""
        self.meta_var.set(msg + hint)
        self.status.set("Couldn't load the video. See the message above.")

    # ---------- download ----------
    def start(self):
        if self.busy or not self.info:
            return
        out = self.folder.get().strip() or DEFAULT_DIR
        try:
            os.makedirs(out, exist_ok=True)
        except OSError as e:
            messagebox.showerror("Video Downloader", f"Cannot use folder:\n{e}")
            return
        choice = self.choices[self.quality.current()]
        self.busy = True
        self.last_file = None
        self.dl_btn.config(state="disabled")
        self.progress.stop()
        self.progress.config(mode="determinate", value=0)
        threading.Thread(target=self.download_worker, args=(self.fetched_url, choice, self.cookie_extra, out),
                         daemon=True).start()

    def hook(self, d):
        part = "audio" if d.get("info_dict", {}).get("vcodec") == "none" else "video"
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            pct = d["downloaded_bytes"] * 100 / total if total else 0
            speed = ANSI.sub("", d.get("_speed_str", "")).strip()
            eta = ANSI.sub("", d.get("_eta_str", "")).strip()
            self.q.put(("progress", pct, f"Downloading {part}... {pct:.1f}%   {speed}   ETA {eta}"))
        elif d["status"] == "finished":
            self.q.put(("progress", 100, "Finishing up (merging / converting)..."))

    def download_worker(self, url, choice, extra, out):
        def done_hook(path):
            self.last_file = path
        try:
            download(url, choice, extra, out, self.hook, done_hook)
            self.q.put(("done", None))
        except Exception as e:
            self.q.put(("done", clean(str(e))))

    # ---------- UI loop ----------
    def poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                kind = item[0]
                if kind in ("status", "info", "fetch_error") and item[1] != self.req:
                    continue  # result of an old lookup
                if kind == "status":
                    self.status.set(item[2])
                elif kind == "info":
                    self.stop_loading()
                    self.show_info(*item[2:])
                elif kind == "fetch_error":
                    self.stop_loading()
                    self.show_fetch_error(item[2])
                elif kind == "progress":
                    self.progress["value"] = item[1]
                    self.status.set(item[2])
                elif kind == "login_open":
                    self.login_btn.config(state="normal")
                    if item[1]:
                        self.login_btn.config(text="Log in...")
                        self.status.set("Couldn't open the login window.")
                        messagebox.showerror("Log in", item[1])
                    else:
                        self.login_btn.config(text="I'm logged in")
                        self.status.set("A browser window opened. Log into the site there, "
                                        "then come back and click \"I'm logged in\".")
                elif kind == "login_saved":
                    self.login_btn.config(text="Log in...", state="normal")
                    if item[1]:
                        self.status.set("Couldn't save the login.")
                        messagebox.showerror("Log in", f"Couldn't save the login:\n{item[1]}")
                    else:
                        self.status.set("Login saved! It will be used automatically when a site asks for it.")
                        if self.login.current() == 1:
                            self.login.current(0)
                        if self.url.get().strip():
                            self.fetch(force=True)
                elif kind == "done":
                    self.busy = False
                    self.dl_btn.config(state="normal")
                    if item[1]:
                        self.status.set("Download failed.")
                        messagebox.showerror("Download failed", item[1])
                    else:
                        self.progress["value"] = 100
                        where = self.last_file or self.folder.get()
                        self.status.set(f"Done! Saved: {where}")
                        if messagebox.askyesno("Video Downloader", f"Download finished!\n\n{where}\n\nShow the file?"):
                            self.open_folder()
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def stop_loading(self):
        self.loading = False
        self.progress.stop()
        self.progress.config(mode="determinate", value=0)


def selftest(url, out):
    """Headless check of the packaged exe: VideoDownloader.exe --selftest URL OUTDIR"""
    with open(os.path.join(out, "selftest.log"), "w", encoding="utf-8") as log:
        log.write(f"ffmpeg: {shutil.which('ffmpeg')}\nffprobe: {shutil.which('ffprobe')}\ndeno: {shutil.which('deno')}\n")
        try:
            info, thumb = extract(url, {})
            log.write(f"title: {info.get('title')}\nthumb: {thumb.size if thumb else None}\n")
            choices = build_choices(info)
            for c in choices:
                log.write(f"choice: {c['label']}  [{c['format']}]\n")
            videos = [c for c in choices if c["kind"] == "video"]
            files = []
            download(url, videos[-1], {}, out, done_hook=files.append)
            log.write(f"video OK: {files}\n")
            files.clear()
            download(url, choices[-1], {}, out, done_hook=files.append)
            log.write(f"{choices[-1]['kind']} OK: {files}\n")
        except Exception as e:
            log.write(f"FAIL {clean(str(e))}\n")
        log.write(f"browsers: {installed_browsers()}\n")


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--selftest":
        selftest(sys.argv[2], sys.argv[3])
        sys.exit(0)
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
