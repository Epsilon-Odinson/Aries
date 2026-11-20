# PyInstaller spec for the Aries local service (sidecar), 0.7.0.
# Builds aries-service.exe — the Python service the Electron shell
# spawns on 127.0.0.1 for PC control, microphone and wake phrase.
#
# IMPORTANT: console=True. The sidecar prints ARIES_SERVICE_PORT=<port>
# on stdout so the shell can find it; a windowed build would discard
# stdout. The shell spawns it with windowsHide so no console window
# shows on the laptop.
#
# Build (from the repo root, on the Windows runner):
#   pyinstaller aries-shell/sidecar.spec
# Output: dist/aries-service.exe  (copied to aries-shell/sidecar/ next)
import os

SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))
STAGING = os.path.dirname(SPEC_DIR)  # aries-desktop-staging == repo root

a = Analysis(
    [os.path.join(STAGING, "aries", "service.py")],
    pathex=[STAGING],
    binaries=[],
    datas=[],
    hiddenimports=[
        "aries",
        "aries.service",
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
    ],
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
