# Builds the one-folder package: dist\VideoDownloader\ and dist\VideoDownloader.zip
# Usage:  powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$work = Join-Path $root "build\tools-download"
$pkg = Join-Path $root "dist\VideoDownloader"
$zip = Join-Path $root "dist\VideoDownloader.zip"
$ffmpegUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-lgpl-shared.zip"
$denoUrl = "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"

Write-Host "==> Installing Python packages"
python -m pip install --upgrade -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host "==> Building VideoDownloader.exe"
if (-not (Test-Path icon.ico)) { python make_icon.py }
python -m PyInstaller --noconfirm --onefile --windowed --name VideoDownloader `
    --icon "$root\icon.ico" --add-data "$root\icon.ico;." `
    --collect-submodules yt_dlp --collect-submodules websockets `
    --collect-all curl_cffi --collect-all yt_dlp_ejs `
    --distpath build\exe --workpath build\pyinstaller --specpath build `
    VideoDownloader.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Write-Host "==> Downloading ffmpeg and deno"
if (Test-Path $work) { Remove-Item $work -Recurse -Force }
New-Item -ItemType Directory $work | Out-Null
$ProgressPreference = "SilentlyContinue"
Invoke-WebRequest $ffmpegUrl -OutFile "$work\ffmpeg.zip"
Invoke-WebRequest $denoUrl -OutFile "$work\deno.zip"
Expand-Archive "$work\ffmpeg.zip" "$work\ffmpeg"
Expand-Archive "$work\deno.zip" "$work\deno"
$ff = (Get-ChildItem "$work\ffmpeg" -Directory | Select-Object -First 1).FullName

Write-Host "==> Assembling package"
if (Test-Path $pkg) { Remove-Item $pkg -Recurse -Force }
New-Item -ItemType Directory "$pkg\tools" -Force | Out-Null
Copy-Item "build\exe\VideoDownloader.exe" $pkg
Copy-Item "packaging\README.txt" $pkg
Get-ChildItem "$ff\bin" | Where-Object Name -ne "ffplay.exe" | Copy-Item -Destination "$pkg\tools"
Copy-Item "$ff\LICENSE.txt" "$pkg\tools\FFMPEG-LICENSE.txt"
Copy-Item "$work\deno\deno.exe" "$pkg\tools"

if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path $pkg -DestinationPath $zip -CompressionLevel Optimal

$mb = [math]::Round((Get-ChildItem $pkg -Recurse -File | Measure-Object Length -Sum).Sum / 1MB)
Write-Host "==> Done: $pkg ($mb MB)"
Write-Host "          $zip ($([math]::Round((Get-Item $zip).Length / 1MB)) MB)"
