# Aries desktop 0.7.0 — release workflow (for the parent agent)

The old workflow (`.github/workflows/build-windows.yml`) built a
PyInstaller `.exe` from the PyQt app. 0.7.0 is an Electron shell +
Python sidecar, so the workflow must be REPLACED with the one below.
It cannot be edited by snapshot push — paste it via the browser
(GitHub web UI, edit `.github/workflows/build-windows.yml`, commit
to main), then create the tag `v0.7.0-desktop` (release) to trigger it.

## What the workflow does

1. Checks out the repo on `windows-latest`, sets up Node 20 and
   Python 3.11.
2. Builds the React app (`production/web`) and bundles it into
   `aries-shell/app/` — `bash aries-shell/build-web.sh` (Git Bash is
   on the Windows runner; the script finds `production/web` at the
   repo root).
3. Installs the shell's npm deps (`aries-shell`, `npm ci`).
4. Builds the Python sidecar: `pip install -r requirements.txt`
   and `pip install pyinstaller`, then
   `pyinstaller aries-shell/sidecar.spec` → `dist/aries-service.exe`,
   copied to `aries-shell/sidecar/aries-service.exe`.
5. Runs `electron-builder --win --x64` in `aries-shell`, producing
   the installer (`dist-installer/Aries-Setup.exe`) and the portable
   app (`dist-installer/Aries.exe`).
6. Publishes both to the `v0.7.0-desktop` release (idempotent: if
   the release exists it uploads with `--clobber`).

## Full workflow file (replace the whole file)

```yaml
# Build the Aries desktop app 0.7.0 (Electron shell + Python sidecar)
# and publish the installer + portable app as GitHub Release assets.
#
# Trigger it by pushing a tag like v0.7.0-desktop, or by hand from the
# Actions tab (workflow_dispatch). The installer (Aries-Setup.exe,
# electron-builder NSIS) installs Aries, offers a desktop shortcut
# and a taskbar pin. Aries.exe is the portable single file.
# The shell renders the SAME React app as the web, bundled locally —
# it never loads the live website. A local Python service on
# 127.0.0.1 handles PC control, the microphone and the wake phrase.
# No API key is needed on first run — chat uses the server like the
# website does. PC actions need the NVIDIA engine key, added in
# Settings → More → Desktop (saved to the Windows Credential Manager).
#
# The release step only runs for tag pushes. A manual dispatch just
# builds and uploads the files as workflow artifacts. The release
# step is idempotent: if the release already exists it uploads the
# assets with --clobber instead of failing.

name: Build Windows .exe

on:
  push:
    tags:
      - "v*-desktop"
  workflow_dispatch:

permissions:
  contents: write

jobs:
  build:
    runs-on: windows-latest
    steps:
      - name: Check out the repo
        uses: actions/checkout@v4

      - name: Set up Node 20
        uses: actions/setup-node@v4
        with:
          node-version: "20"

      - name: Set up Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Check Python syntax
        run: python -m compileall -q aries

      - name: Build the React app into the shell
        shell: bash
        run: bash aries-shell/build-web.sh

      - name: Install the shell's dependencies
        run: npm ci
        working-directory: aries-shell

      - name: Build the Python sidecar
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
          pip install pyinstaller
          pyinstaller aries-shell/sidecar.spec
          if (-not (Test-Path dist\aries-service.exe)) { exit 1 }
          New-Item -ItemType Directory -Force -Path aries-shell\sidecar | Out-Null
          Copy-Item dist\aries-service.exe aries-shell\sidecar\aries-service.exe

      - name: Build the installer + portable app
        run: npx electron-builder --win --x64
        working-directory: aries-shell

      - name: Check the artifacts exist
        run: |
          if (-not (Test-Path aries-shell\dist-installer\Aries-Setup.exe)) { exit 1 }
          if (-not (Test-Path aries-shell\dist-installer\Aries.exe)) { exit 1 }
          $i = (Get-Item aries-shell\dist-installer\Aries-Setup.exe).Length / 1MB
          $p = (Get-Item aries-shell\dist-installer\Aries.exe).Length / 1MB
          Write-Host "Aries-Setup.exe built: $([math]::Round($i, 1)) MB"
          Write-Host "Aries.exe built: $([math]::Round($p, 1)) MB"

      - name: Upload the installer as a workflow artifact
        uses: actions/upload-artifact@v4
        with:
          name: Aries-windows-installer
          path: aries-shell/dist-installer/Aries-Setup.exe
          if-no-files-found: error

      - name: Upload the portable app as a workflow artifact
        uses: actions/upload-artifact@v4
        with:
          name: Aries-windows-exe
          path: aries-shell/dist-installer/Aries.exe
          if-no-files-found: error

      - name: Publish GitHub Release
        if: startsWith(github.ref, 'refs/tags/')
        run: |
          $tag = "${{ github.ref_name }}"
          $notes = "Windows build of the Aries desktop app (0.7.0) — the Electron shell. The same React app as the website, bundled locally into a real installed PC program (never the live site in a window), so the desktop is pixel-identical to the web: same veil, same pointer orb, same fonts, same Settings. Aries-Setup.exe is the installer — it installs Aries, offers a desktop shortcut and a taskbar pin. Aries.exe is the portable single file — download and run it, no install needed. No API key needed on first run — chat uses the server like the website does. PC control, the microphone and the wake phrase run through a local service on your PC. PC control is off by default — turn it on in Settings, and add the NVIDIA engine key there for PC actions. Mini mode: press the face to shrink to the floating face, press it again to come back to full size."
          gh release view $tag --json tagName -q .tagName 2>$null | Out-Null
          if ($LASTEXITCODE -eq 0) {
            gh release upload $tag "aries-shell/dist-installer/Aries.exe" "aries-shell/dist-installer/Aries-Setup.exe" --clobber
          } else {
            gh release create $tag --title "Aries desktop $tag" --notes $notes "aries-shell/dist-installer/Aries.exe" "aries-shell/dist-installer/Aries-Setup.exe"
          }
        env:
          GH_TOKEN: ${{ github.token }}
```

## Notes for the release

- Tag: `v0.7.0-desktop`, target main at the 0.7.0 commit.
- The workflow needs no new hidden-imports beyond the sidecar
  spec (`aries-shell/sidecar.spec` lists them: speech_recognition,
  pyaudio, keyring, requests, dotenv, plus the aries modules).
- The `version_info.txt` (0.7.0) is stamped into aries-service.exe.
- electron-builder needs no code signing for this release
  (unsigned, as before).
- After the release succeeds, update the web download button
  (`production/web/src/App.jsx`, Settings → More → Desktop app &
  installer) from `v0.5.0-desktop` to `v0.7.0-desktop` for both
  links, and verify the live bundle.
