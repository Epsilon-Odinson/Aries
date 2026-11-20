# PyInstaller spec for the Aries local service (sidecar), 0.8.0.
# Builds aries-service.exe — the Python service the Electron shell
# spawns on 127.0.0.1. Since 0.8.0 it is the WHOLE backend on the PC:
# the production FastAPI app (chat, memory, tasks, voice, face,
# screenshot — the same app Render runs) plus the desktop-only routes
# (PC control, microphone, wake phrase). Local data lives in
# ~/.aries/ (SQLite), never in the install dir and never in the cloud.
#
# IMPORTANT: console=True. The sidecar prints ARIES_SERVICE_PORT=<port>
# on stdout so the shell can find it; a windowed build would discard
# stdout. The shell spawns it with windowsHide so no console window
# shows on the laptop.
#
# The production server is bundled as DATA files (prod_server/), not
# as analysed code: at runtime the sidecar puts that directory on
# sys.path and imports app.main from it. Its dependencies (FastAPI,
# uvicorn, SQLAlchemy, bcrypt, ...) are collected below, because
# PyInstaller cannot see imports inside data files. Face login needs
# OpenCV with the contrib modules (cv2.face) — see requirements.txt.
#
# Build (from the repo root, on the Windows runner):
#   pyinstaller aries-shell/sidecar.spec
# Output: dist/aries-service.exe  (copied to aries-shell/sidecar/ next)
import os

from PyInstaller.utils.hooks import collect_all

SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))
STAGING = os.path.dirname(SPEC_DIR)  # aries-shell's parent == repo root

# Server dependencies PyInstaller cannot trace (they are imported by
# the production server code, which ships as data files below).
_collected_datas = []
_collected_binaries = []
_collected_hidden = []
for _pkg in (
    "uvicorn",
    "fastapi",
    "starlette",
    "sqlalchemy",
    "bcrypt",
    "pydantic",
    "multipart",
    "anyio",
    "httpx",
    "h11",
    "dotenv",
    "PIL",
    "bs4",
    "cv2",
):
    _d, _b, _h = collect_all(_pkg)
    _collected_datas += _d
    _collected_binaries += _b
    _collected_hidden += _h

# The production server as data files. The Tree excludes the local
# data/ dir (a stray test database must never ship) and bytecode
# caches. SOUL.md / MEMORY.md ride along so the personality loader
# finds them next to the server code, exactly like the Docker image.
_server_tree = Tree(
    os.path.join(STAGING, "production", "server"),
    prefix="prod_server",
    excludes=["data", "__pycache__", "*.pyc"],
)
_server_files = [
    (os.path.join(STAGING, "production", "SOUL.md"), "prod_server"),
    (os.path.join(STAGING, "production", "MEMORY.md"), "prod_server"),
]

a = Analysis(
    [os.path.join(STAGING, "aries", "service.py")],
    pathex=[STAGING],
    binaries=_collected_binaries,
    datas=_collected_datas + _server_files,
    hiddenimports=[
        "aries",
        "aries.service",
        "aries.local_backend",
        "aries.desktop_api",
        "aries.pc_control",
        "aries.voice",
        "aries.llm",
        "aries.config",
        "aries.mic_worker",
        "aries.api_client",
        "aries.main",
        "speech_recognition",
        "pyaudio",
        "keyring",
        "requests",
        "dotenv",
    ]
    + _collected_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
a.datas += _server_tree
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="aries-service",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=os.path.join(STAGING, "version_info.txt"),
    icon=os.path.join(STAGING, "aries.ico"),
)
