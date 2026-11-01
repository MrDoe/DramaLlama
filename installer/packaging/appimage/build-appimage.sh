#!/usr/bin/env bash
# Wrap the PyInstaller onedir wizard in an AppImage.
#
# Usage: build-appimage.sh <onedir> <version> <output-dir>
# Requires appimagetool on PATH or in $APPIMAGETOOL. Without it the AppDir and
# a tarball are produced instead, which is the documented fallback.
set -euo pipefail

ONEDIR=${1:?onedir (PyInstaller DramaLlama-Setup folder)}
VERSION=${2:?version}
OUTDIR=${3:?output directory}

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
APPDIR="$OUTDIR/AppDir"
ICON_SRC="$REPO_ROOT/web/public/llogo.png"

rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" \
         "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor/512x512/apps"

cp -a "$ONEDIR/." "$APPDIR/usr/bin/"
chmod +x "$APPDIR/usr/bin/DramaLlama-Setup"

cat > "$APPDIR/drama-llama-installer.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Drama Llama Installer
Comment=Install, repair or remove Drama Llama
Exec=DramaLlama-Setup
Icon=drama-llama
Terminal=false
Categories=Utility;AudioVideo;
StartupNotify=true
DESKTOP
# appimagetool requires the .desktop file at the AppDir root and also copies it
# into usr/share/applications, so keep a copy for the extracted fallback.
cp "$APPDIR/drama-llama-installer.desktop" \
   "$APPDIR/usr/share/applications/drama-llama-installer.desktop"

if [ -f "$ICON_SRC" ]; then
  # appimagetool looks for the Icon= name at the AppDir root.
  cp "$ICON_SRC" "$APPDIR/drama-llama.png"
  cp "$ICON_SRC" "$APPDIR/.DirIcon"
  cp "$ICON_SRC" "$APPDIR/usr/share/icons/hicolor/512x512/apps/drama-llama.png"
fi

cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
# Resolve symlinks so the AppImage can be launched from anywhere.
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/DramaLlama-Setup" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

TOOL="${APPIMAGETOOL:-$(command -v appimagetool || true)}"
if [ -z "$TOOL" ]; then
  echo "appimagetool not found; producing a tarball fallback instead." >&2
  tar -czf "$OUTDIR/DramaLlama-$VERSION-linux-x86_64.AppDir.tar.gz" -C "$OUTDIR" AppDir
  echo "Install appimagetool (https://github.com/AppImage/appimagetool) for a real .AppImage." >&2
  exit 0
fi

# APPIMAGE_EXTRACT_AND_RUN avoids a FUSE requirement in containers/CI.
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" \
  "$APPDIR" "$OUTDIR/DramaLlama-$VERSION-x86_64.AppImage"

echo "AppImage written to $OUTDIR/DramaLlama-$VERSION-x86_64.AppImage"
