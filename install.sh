#!/bin/sh
# Install open-corsair for the current user and enable the tray at login.
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
install -m 644 data/open-corsair.desktop "$HOME/.local/share/applications/"
install -m 644 data/open-corsair.desktop "$HOME/.config/autostart/"
echo "Done. Start now with: open-corsair tray"
