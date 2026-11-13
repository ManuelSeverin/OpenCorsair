#!/usr/bin/env bash
# Build the Stream Deck plugin (OpenDeck on Linux, Elgato app on Windows).
#
#   packaging/build-streamdeck-plugin.sh            -> dist/io.github.manuelseverin.opencorsair.sdPlugin
#                                                    dist/OpenCorsair-<version>.streamDeckPlugin
#   packaging/build-streamdeck-plugin.sh --install  also copies it to ~/.config/opendeck/plugins
#   packaging/build-streamdeck-plugin.sh --windows  -> dist/OpenCorsair-<version>-windows.streamDeckPlugin
#                                                    for the Elgato Stream Deck app on Windows
#
# The Linux plugin needs only python3 (3.10+) on the host. The device code of
# open_corsair is copied into the plugin, so OpenCorsair itself does not have
# to be installed. The udev rule is still needed for device access.
#
# The Windows plugin brings its own Python (the official embeddable package
# from python.org, downloaded once into dist/cache) and is started by the
# Stream Deck app's Node.js through launcher.js. Building it needs curl,
# unzip and rsvg-convert (for the PNG key images).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ID="io.github.manuelseverin.opencorsair"
DIST="$ROOT/dist"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/open_corsair/__init__.py")"

PY_VERSION="3.13.9"
PY_ZIP="python-$PY_VERSION-embed-amd64.zip"
PY_SHA256="91d828c2da3a029b41699e918674a0cb379c02cf20dab9c501306885f837402a"

# copy the files both builds share into $1
common() {
    local out="$1"; shift
    rm -rf "$out"
    mkdir -p "$out/open_corsair"
    cp -r "$ROOT/streamdeck/pi" "$out/"
    install -m 755 "$ROOT/streamdeck/plugin.py" "$out/plugin.py"
    # only the modules without Qt dependencies
    for m in __init__ bragi hidraw headset mic config "$@"; do
        cp "$ROOT/open_corsair/$m.py" "$out/open_corsair/"
    done
}

package() {
    local dir="$1" pkg="$2"
    rm -f "$pkg"
    (cd "$dir" && python3 -m zipfile -c "$pkg" "$ID.sdPlugin")
    echo "built $dir/$ID.sdPlugin"
    echo "built $pkg"
}

build_linux() {
    local out="$DIST/$ID.sdPlugin"
    common "$out"
    cp -r "$ROOT/streamdeck/icons" "$out/"
    sed "s/\"Version\": \"[^\"]*\"/\"Version\": \"$VERSION\"/" "$ROOT/streamdeck/manifest.json" > "$out/manifest.json"
    package "$DIST" "$DIST/OpenCorsair-$VERSION.streamDeckPlugin"
}

build_windows() {
    local stage="$DIST/windows" out="$DIST/windows/$ID.sdPlugin" cache="$DIST/cache"
    common "$out" winhid winmic
    install -m 644 "$ROOT/streamdeck/launcher.js" "$out/launcher.js"

    # key images as PNG (1x and @2x); the Stream Deck app is pickier about SVG
    command -v rsvg-convert >/dev/null || { echo "rsvg-convert is needed (librsvg)" >&2; exit 1; }
    mkdir -p "$out/icons"
    for svg in "$ROOT"/streamdeck/icons/*.svg; do
        local name size
        name="$(basename "$svg" .svg)"
        case "$name" in
            plugin) size=256 ;;
            category) size=28 ;;
            *) size=72 ;;
        esac
        rsvg-convert -w "$size" -h "$size" "$svg" -o "$out/icons/$name.png"
        rsvg-convert -w $((size * 2)) -h $((size * 2)) "$svg" -o "$out/icons/$name@2x.png"
    done

    # embeddable Python, checked against the pinned hash
    mkdir -p "$cache"
    if [ ! -f "$cache/$PY_ZIP" ]; then
        curl -sSfL "https://www.python.org/ftp/python/$PY_VERSION/$PY_ZIP" -o "$cache/$PY_ZIP.part"
        mv "$cache/$PY_ZIP.part" "$cache/$PY_ZIP"
    fi
    if ! echo "$PY_SHA256  $cache/$PY_ZIP" | sha256sum -c --quiet -; then
        echo "checksum mismatch for $cache/$PY_ZIP, delete it and try again" >&2
        exit 1
    fi
    unzip -q "$cache/$PY_ZIP" -d "$out/python"

    python3 - "$ROOT/streamdeck/manifest.json" "$out/manifest.json" "$VERSION" <<'EOF'
import json, sys
src, dst, version = sys.argv[1:]
m = json.load(open(src))
m["Version"] = version
# the Stream Deck app runs launcher.js with its bundled Node.js
for key in ("CodePaths", "CodePathLin"):
    m.pop(key, None)
m["CodePath"] = m["CodePathWin"] = "launcher.js"
m["Nodejs"] = {"Version": "20", "Debug": "disabled"}
m["OS"] = [{"Platform": "windows", "MinimumVersion": "10"}]
json.dump(m, open(dst, "w"), indent="\t", ensure_ascii=False)
open(dst, "a").write("\n")
EOF
    package "$stage" "$DIST/OpenCorsair-$VERSION-windows.streamDeckPlugin"
}

case "${1:-}" in
    --windows)
        build_windows
        ;;
    --install)
        build_linux
        PLUGINS="${XDG_CONFIG_HOME:-$HOME/.config}/opendeck/plugins"
        mkdir -p "$PLUGINS"
        rm -rf "$PLUGINS/$ID.sdPlugin"
        cp -r "$DIST/$ID.sdPlugin" "$PLUGINS/"
        echo "installed to $PLUGINS/$ID.sdPlugin, restart OpenDeck (tools/start-opendeck.sh) to load it"
        ;;
    "")
        build_linux
        ;;
    *)
        echo "usage: $0 [--install | --windows]" >&2
        exit 2
        ;;
esac
