#!/usr/bin/env bash
# Build a self-contained OpenCorsair AppImage (Python + PyQt6 bundled).
#
#   packaging/build-appimage.sh            -> dist/OpenCorsair-<version>-x86_64.AppImage
#
# Needs: curl, rsvg-convert, FUSE, network access. The host still needs pactl/paplay (PipeWire or
# PulseAudio) for mic mute, and the udev rule for device access.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="${BUILD_DIR:-$ROOT/build/appimage}"
DIST="$ROOT/dist"
PY_TAG="python3.13"
PY_IMAGE="python3.13.17-cp313-cp313-manylinux_2_28_x86_64.AppImage"
PYQT_VERSION="${PYQT_VERSION:-6.12.0}"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/open_corsair/__init__.py")"

mkdir -p "$BUILD/downloads" "$DIST"
cd "$BUILD"

fetch() {  # url file
    [ -f "downloads/$2" ] || curl -fL --retry 3 -o "downloads/$2" "$1"
    chmod +x "downloads/$2"
}
fetch "https://github.com/niess/python-appimage/releases/download/$PY_TAG/$PY_IMAGE" python.AppImage
fetch "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage" appimagetool.AppImage
fetch "https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64" runtime-x86_64

# 1. Unpack the relocatable Python
rm -rf squashfs-root AppDir
./downloads/python.AppImage --appimage-extract >/dev/null
mv squashfs-root AppDir
PY="$(cd AppDir && ls -d opt/python3.*/bin/python3.* | grep -v config | head -1)"

# 2. Install PyQt6 and open-corsair into it
"AppDir/$PY" -m pip install --no-cache-dir --no-warn-script-location -q \
    "PyQt6==$PYQT_VERSION" "$ROOT"
# trim things we never use
SITE="$(ls -d AppDir/opt/python3.*/lib/python3.*/site-packages)"
rm -rf "$SITE"/PyQt6/Qt6/qml "$SITE"/PyQt6/Qt6/translations \
       "$SITE"/PyQt6/Qt6/lib/libQt6{Quick,Qml,Pdf,Designer,Test,OpenGLWidgets,Sql,Multimedia,Bluetooth,Nfc,Sensors,SerialPort,Positioning,WebSockets,RemoteObjects,SpatialAudio,TextToSpeech,ShaderTools}*.so* \
       2>/dev/null || true
rm -rf AppDir/usr/share/tcltk AppDir/opt/python3.*/lib/python3.*/{tkinter,idlelib,test,turtledemo} \
       AppDir/opt/python3.*/lib/python3.*/lib-dynload/_tkinter*.so

# 3. Launcher, desktop entry, icon
rm -f AppDir/AppRun AppDir/*.desktop AppDir/*.png AppDir/*.svg AppDir/.DirIcon
rm -rf AppDir/usr/share/applications AppDir/usr/share/metainfo
cat > AppDir/AppRun <<EOF
#!/bin/sh
# OpenCorsair launcher: no arguments starts the tray, otherwise CLI.
HERE="\$(dirname "\$(readlink -f "\$0")")"
export PYTHONNOUSERSITE=1
[ "\$#" -eq 0 ] && set -- tray
exec "\$HERE/$PY" -I -m open_corsair.cli "\$@"
EOF
chmod +x AppDir/AppRun
cp "$ROOT/assets/icons/opencorsair.svg" AppDir/opencorsair.svg
cp "$ROOT/assets/icons/opencorsair.svg" AppDir/.DirIcon
ICONS=AppDir/usr/share/icons/hicolor
mkdir -p "$ICONS/scalable/apps"
cp "$ROOT/assets/icons/opencorsair.svg" "$ICONS/scalable/apps/"
for s in 16 22 24 32; do
    v=small; [ "$s" -ge 32 ] && v=medium
    mkdir -p "$ICONS/${s}x${s}/apps"
    rsvg-convert -w "$s" -h "$s" "$ROOT/assets/icons/opencorsair-$v.svg" -o "$ICONS/${s}x${s}/apps/opencorsair.png"
done
cat > AppDir/opencorsair.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=OpenCorsair
Comment=Battery indicator and settings for Corsair wireless headsets
Exec=opencorsair
Icon=opencorsair
Terminal=false
Categories=Utility;
EOF

# 4. Pack
OUT="$DIST/OpenCorsair-$VERSION-x86_64.AppImage"
chmod -R a+rX AppDir
rm -f "$OUT"
# (appimagetool's extract-and-run mode is broken in current builds; needs FUSE)
TOOL=(env ARCH=x86_64 ./downloads/appimagetool.AppImage -n --runtime-file downloads/runtime-x86_64 AppDir "$OUT")
"${TOOL[@]}" >/dev/null 2>&1 || { "${TOOL[@]}"; exit 1; }
echo "$OUT"
