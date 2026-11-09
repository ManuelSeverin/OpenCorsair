#!/usr/bin/env bash
# Build the OpenDeck plugin.
#
#   packaging/build-opendeck-plugin.sh            -> dist/io.github.manuelseverin.opencorsair.sdPlugin
#                                                    dist/OpenCorsair-<version>.streamDeckPlugin
#   packaging/build-opendeck-plugin.sh --install  also copies it to ~/.config/opendeck/plugins
#
# The plugin needs only python3 (3.10+) on the host. The device code of
# open_corsair is copied into the plugin, so OpenCorsair itself does not have
# to be installed. The udev rule is still needed for device access.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ID="io.github.manuelseverin.opencorsair"
DIST="$ROOT/dist"
OUT="$DIST/$ID.sdPlugin"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/open_corsair/__init__.py")"

rm -rf "$OUT"
mkdir -p "$OUT/open_corsair"
cp -r "$ROOT/opendeck/icons" "$ROOT/opendeck/pi" "$OUT/"
install -m 755 "$ROOT/opendeck/plugin.py" "$OUT/plugin.py"
# only the modules without Qt dependencies
for m in __init__ bragi hidraw headset mic config; do
    cp "$ROOT/open_corsair/$m.py" "$OUT/open_corsair/"
done
sed "s/\"Version\": \"[^\"]*\"/\"Version\": \"$VERSION\"/" "$ROOT/opendeck/manifest.json" > "$OUT/manifest.json"

PKG="$DIST/OpenCorsair-$VERSION.streamDeckPlugin"
rm -f "$PKG"
(cd "$DIST" && python3 -m zipfile -c "$PKG" "$ID.sdPlugin")
echo "built $OUT"
echo "built $PKG"

if [ "${1:-}" = "--install" ]; then
    PLUGINS="${XDG_CONFIG_HOME:-$HOME/.config}/opendeck/plugins"
    mkdir -p "$PLUGINS"
    rm -rf "$PLUGINS/$ID.sdPlugin"
    cp -r "$OUT" "$PLUGINS/"
    echo "installed to $PLUGINS/$ID.sdPlugin, restart OpenDeck to load it"
fi
