#!/bin/bash
# Make the Raspberry Pi start recording automatically (no window) every time it turns on.
#
#   Install:      bash install_autostart.sh
#   Remove:       bash install_autostart.sh --remove
#   Is it on?     systemctl status nest-camera
#   Live log:     journalctl -u nest-camera -f        (Ctrl+C to leave the log)
#   Stop for now: sudo systemctl stop nest-camera     (e.g. before opening the camera window)
#   Start again:  sudo systemctl start nest-camera
#
# It uses the settings in camera_settings.json (set them once with the window version:
# python3 camera_ui.py --gui -> Settings -> Save).

set -e
SERVICE=nest-camera
UNIT=/etc/systemd/system/$SERVICE.service

if [ "$1" = "--remove" ]; then
    sudo systemctl disable --now "$SERVICE" 2>/dev/null || true
    sudo rm -f "$UNIT"
    sudo systemctl daemon-reload
    echo "Auto-start removed. The camera will no longer record when the Pi turns on."
    exit 0
fi

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$(command -v python3)"
USER_NAME="$(id -un)"

if [ ! -f "$DIR/camera_settings.json" ]; then
    echo "Note: no camera_settings.json found yet, so the default settings (camera 0) will be used."
    echo "      To choose the camera and settings, run:  python3 camera_ui.py --gui"
    echo "      then open Settings and press Save, and run this script again."
    echo
fi

sudo tee "$UNIT" > /dev/null <<EOF
[Unit]
Description=Nest camera recorder (no window)
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$DIR
Environment=PYTHONUNBUFFERED=1
ExecStart=$PY $DIR/camera_ui.py --headless
Restart=always
RestartSec=10
KillSignal=SIGTERM
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE"
sleep 3
echo
echo "Auto-start is ON. The camera now records every time the Pi turns on."
echo "Check it with:   systemctl status $SERVICE"
echo "Watch the log:   journalctl -u $SERVICE -f"
echo "Turn it off:     bash $DIR/install_autostart.sh --remove"
echo
systemctl --no-pager --lines=5 status "$SERVICE" || true
