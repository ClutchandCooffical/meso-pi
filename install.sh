#!/bin/bash
# meso-pi installer. Run on the Raspberry Pi (or any Linux box):
#   tar xzf meso-pi-v1.0.0.tar.gz
#   cd meso-pi-v1.0.0
#   ./install.sh            (installs to /home/pi/meso)
#   ./install.sh /opt/meso  (or another directory)
# Then: edit meso.conf, run ./set-password.sh, test in shadow mode,
# and read the README for cutover.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="${1:-/home/pi/meso}"

echo "Installing meso-pi v1.0.0 to $DEST"

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 not found. Install it first (sudo apt install python3)."
  exit 1
fi
PYVER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "python3 $PYVER found."
if python3 -c 'import sys; assert sys.version_info >= (3, 8)' 2>/dev/null; then
  echo "Python 3.8+ OK (stdlib only, no extra packages needed)."
else
  echo "ERROR: Python 3.8 or newer is required."
  exit 1
fi

mkdir -p "$DEST"/{scripts,state,shadow,log}
cp "$SRC"/scripts/*.py "$DEST/scripts/"
chmod +x "$DEST"/scripts/*.py
cp "$SRC"/set-password.sh "$DEST/"
chmod +x "$DEST/set-password.sh"

if [ ! -f "$DEST/meso.conf" ]; then
  sed "s|__HOME__|$DEST|g" "$SRC/meso.conf.example" > "$DEST/meso.conf"
  echo "Created $DEST/meso.conf from the example (LIVE_SUBMIT=false)."
else
  echo "Keeping existing $DEST/meso.conf (not overwritten)."
fi

# Install cron entries, preserving any other cron jobs.
TMP_CRON="$(mktemp)"
sed "s|__HOME__|$DEST|g" "$SRC/meso.cron" > "$TMP_CRON"
if command -v crontab >/dev/null 2>&1; then
  if crontab -l 2>/dev/null | grep -v "meso-submit.py" > "$TMP_CRON.existing"; then
    cat "$TMP_CRON.existing" "$TMP_CRON" | crontab -
  else
    crontab "$TMP_CRON"
  fi
  rm -f "$TMP_CRON" "$TMP_CRON.existing"
  echo "Cron installed:"
  crontab -l | grep -E "meso-submit" || true
else
  cp "$TMP_CRON" "$DEST/meso.cron.installed"
  rm -f "$TMP_CRON"
  echo "NOTE: the 'crontab' command is not on this machine, so the schedule"
  echo "was not installed. Install cron (sudo apt install cron) and run:"
  echo "  crontab $DEST/meso.cron.installed"
  echo "or add that line to your scheduler by hand."
fi

echo ""
echo "Installed. Next steps:"
echo "  1. Edit $DEST/meso.conf"
echo "     (WXID, STATION_URL or STATION_JSON, LATITUDE, LONGITUDE, NOTES)"
echo "  2. ./set-password.sh            (your Meso station password)"
echo "  3. python3 $DEST/scripts/meso-check.py"
echo "  4. python3 $DEST/scripts/meso-submit.py --dry-run"
echo "     (shadow mode: builds the payload, submits nothing)"
echo "  5. When the dry runs look right, set LIVE_SUBMIT=true in meso.conf"
echo "     and run one live test: python3 $DEST/scripts/meso-submit.py --force"
echo "  6. Logs: $DEST/log/  State: $DEST/state/meso-last.json"
