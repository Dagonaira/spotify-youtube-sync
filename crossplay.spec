# PyInstaller spec for the Crossplay desktop app.
# Build with: pyinstaller crossplay.spec
#
# Bundles our shared app identity (.env holding the Spotify client id/secret,
# and client_secret.json for Google) into the exe - intentional: this is the
# accepted distribution model for "Desktop app" / "Installed app" OAuth
# clients (Google/Spotify don't treat these as confidential the way a server
# secret would be), and it's what makes the app plug-and-play for anyone we
# send it to - they just click "Connect Spotify" / "Connect YouTube" and log
# into their own account, no setup of their own required.

from PyInstaller.utils.hooks import collect_all

datas = [
    ("app/static", "app/static"),
    (".env", "."),
    ("client_secret.json", "."),
]
binaries = []
hiddenimports = []

for pkg in [
    "uvicorn",
    "fastapi",
    "webview",
    "googleapiclient",
    "google_auth_httplib2",
    "google_auth_oauthlib",
    "spotipy",
]:
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["desktop.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Crossplay",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
)
