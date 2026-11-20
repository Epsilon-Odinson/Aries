#!/bin/bash
# Build the React web app and bundle it into the Electron shell.
# The React source of truth is production/web (in the prod staging
# dir / the repo) — this script builds FROM there, never a copy.
# Output: aries-shell/app/ (dist + static files), loaded by the
# shell from local files only. The live website is never loaded.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# The repo root: on a GitHub runner $GITHUB_WORKSPACE is the
# checkout root; otherwise ask git; otherwise aries-shell's parent.
# The repo's own production/web is checked FIRST — it is the only
# location that exists on a runner (the staging paths below are
# for local dev on this machine only).
REPO_ROOT="${GITHUB_WORKSPACE:-}"
if [ -z "$REPO_ROOT" ]; then
  REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
fi
if [ -z "$REPO_ROOT" ]; then
  REPO_ROOT="$(dirname "$SCRIPT_DIR")"
fi
# Local dev: the staging dirs sit side by side under ~/workspace,
# so the prod staging dir is the parent of the desktop staging dir.
STAGING_PARENT="$(dirname "$(dirname "$SCRIPT_DIR")")"
WEB_DIR=""
for cand in \
  "$REPO_ROOT/production/web" \
  "$SCRIPT_DIR/../production/web" \
  "$STAGING_PARENT/aries-prod-staging/production/web" \
  "/home/hatch/workspace/aries-prod-staging/production/web"; do
  if [ -f "$cand/package.json" ]; then WEB_DIR="$cand"; break; fi
done
if [ -z "$WEB_DIR" ]; then
  echo "Could not find production/web (tried $REPO_ROOT/production/web, $SCRIPT_DIR/../production/web, $STAGING_PARENT/aries-prod-staging/production/web)" >&2
  exit 1
fi
echo "Building React app from $WEB_DIR ..."
(cd "$WEB_DIR" && npm ci --no-audit --no-fund && npm run build)
rm -rf "$SCRIPT_DIR/app"
mkdir -p "$SCRIPT_DIR/app"
cp -r "$WEB_DIR/dist/." "$SCRIPT_DIR/app/"
echo "Bundled React app into $SCRIPT_DIR/app/"
ls "$SCRIPT_DIR/app" | head
