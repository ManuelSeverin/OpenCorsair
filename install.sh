#!/bin/sh
# Install OpenCorsair for the current user and enable the tray at login.
set -e
cd "$(dirname "$0")"

if [ ! -f /etc/udev/rules.d/70-open-corsair.rules ]; then
    echo "Installing udev rule (needs sudo)…"
    sudo install -m 644 udev/70-open-corsair.rules /etc/udev/rules.d/
    sudo udevadm control --reload
    sudo udevadm trigger --subsystem-match=hidraw
fi

if command -v pipx >/dev/null; then
    pipx install --force --system-site-packages .
else
    python3 -m pip install --user --break-system-packages .
fi

mkdir -p "$HOME/.local/share/applications" "$HOME/.config/autostart"
install -m 644 data/opencorsair.desktop "$HOME/.local/share/applications/"
install -m 644 data/opencorsair.desktop "$HOME/.config/autostart/"
ICONS="$HOME/.local/share/icons/hicolor"
mkdir -p "$ICONS/scalable/apps"
install -m 644 assets/icons/opencorsair.svg "$ICONS/scalable/apps/opencorsair.svg"
if command -v rsvg-convert >/dev/null; then
    for s in 16 22 24 32; do
        v=small; [ "$s" -ge 32 ] && v=medium
        mkdir -p "$ICONS/${s}x${s}/apps"
        rsvg-convert -w "$s" -h "$s" "assets/icons/opencorsair-$v.svg" -o "$ICONS/${s}x${s}/apps/opencorsair.png"
    done
fi
echo "Done. Start now with: open-corsair tray"
