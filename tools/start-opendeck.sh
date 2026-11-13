#!/bin/sh
# Start OpenDeck under XWayland. Running it as a native Wayland client makes
# the mouse stutter here, so always start it this way.
exec env -u WAYLAND_DISPLAY GDK_BACKEND=x11 /usr/bin/opendeck "$@"
