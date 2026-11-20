#!/bin/bash
# Build the React web app and bundle it into the Electron shell.
# The React source of truth is production/web (in the prod staging
# dir / the repo) — this script builds FROM there, never a copy.
# Output: aries-shell/app/ (dist + static files), loaded by the
# shell from local files only. The live website is never loaded.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# The repo root is the parent of the desktop staging dir. In the
# repo that is the checkout root; here (staging) it is the parent
# of aries-desktop-staging, which holds aries-prod-staging.
STAGING_PARENT="$(dirname "$SCRIPT_DIR")"
WEB_DIR=""
for cand in \
  "$STAGING_PARENT/aries-prod-staging/production/web" \
  "$SCRIPT_DIR/../../production/web" \
  "/home/hatch/workspace/aries-prod-staging/production/web"; do
  if [ -f "$cand/package.json" ]; then WEB_DIR="$cand"; break; fi
done
if [ -z "$WEB_DIR" ]; then
  echo "Could not find production/web (tried $STAGING_PARENT/aries-prod-staging/production/web)" >&2
  exit 1
fi
echo "Building React app from $WEB_DIR ..."
(cd "$WEB_DIR" && npm ci --no-audit --no-fund && npm run build)
rm -rf "$SCRIPT_DIR/app"
mkdir -p "$SCRIPT_DIR/app"
cp -r "$WEB_DIR/dist/." "$SCRIPT_DIR/app/"
echo "Bundled React app into $SCRIPT_DIR/app/"
ls "$SCRIPT_DIR/app" | head
