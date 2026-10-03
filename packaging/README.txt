VIDEO DOWNLOADER
================

How to use
----------
1. Double-click VideoDownloader.exe (keep it inside this folder).
2. Paste a video link. The preview and the available qualities appear by themselves.
3. Pick a quality and click Download. Files go to your Downloads folder by default.

Sites that need a login (Instagram, private videos, ...): click "Log in...",
log in inside the window that opens, then click "I'm logged in". This is only
needed once per site. Click "What's this?" in the app for details.

Nothing needs to be installed. Everything the app needs is in this folder.
Don't delete the "tools" folder: without it, high qualities, MP3 and YouTube may not work.

Windows may show "Windows protected your PC" the first time, because the app is
not signed. Click "More info" -> "Run anyway".

Only download videos you have the right to save.

What's inside / licenses
------------------------
- VideoDownloader.exe: the app, built on yt-dlp (Unlicense, https://github.com/yt-dlp/yt-dlp)
- tools\ffmpeg.exe, tools\ffprobe.exe, tools\*.dll: FFmpeg LGPL build from
  https://github.com/BtbN/FFmpeg-Builds (license in tools\FFMPEG-LICENSE.txt,
  source code at https://ffmpeg.org)
- tools\deno.exe: Deno JavaScript runtime (MIT license, https://github.com/denoland/deno),
  used to pass YouTube's checks
