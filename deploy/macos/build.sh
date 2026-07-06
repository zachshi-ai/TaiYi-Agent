#!/usr/bin/env bash
# Build the unsigned Taiyi macOS .app (and an optional .dmg).
#
#   bash deploy/macos/build.sh          # → dist/Taiyi.app
#   bash deploy/macos/build.sh --dmg    # → dist/Taiyi.app + dist/Taiyi.dmg
#
# Unsigned: on another Mac, first open needs right-click → Open (Gatekeeper).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

BUILD_VENV="$ROOT/.build-venv"
echo "==> Preparing build venv at $BUILD_VENV"
python3 -m venv "$BUILD_VENV"
# shellcheck disable=SC1091
source "$BUILD_VENV/bin/activate"
python -m pip install --quiet --upgrade pip
# Install taiyi (with live extra so a configured model works) + PyInstaller.
python -m pip install --quiet ".[live]" pyinstaller

echo "==> Building Taiyi.app with PyInstaller"
pyinstaller deploy/macos/Taiyi.spec --noconfirm --clean

echo "==> Built: $ROOT/dist/Taiyi.app"

if [[ "${1:-}" == "--dmg" ]]; then
  DMG="$ROOT/dist/Taiyi.dmg"
  echo "==> Packaging $DMG"
  rm -f "$DMG"
  STAGE="$(mktemp -d)"
  cp -R "$ROOT/dist/Taiyi.app" "$STAGE/"
  ln -s /Applications "$STAGE/Applications"
  hdiutil create -volname "Taiyi" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
  rm -rf "$STAGE"
  echo "==> Built: $DMG"
fi

echo "Done. Launch with: open dist/Taiyi.app"
