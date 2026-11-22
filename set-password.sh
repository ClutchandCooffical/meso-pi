#!/bin/bash
# Set the Midwest Meso station password for this install.
# The password is read silently, never echoed, and stored in a file
# readable only by the owner (mode 0600). The package never prints it.
# Usage: ./set-password.sh
set -euo pipefail

DEST="$(cd "$(dirname "$0")" && pwd)"
PASS_FILE="${1:-$DEST/.meso-password}"

echo "This stores your Meso station password on this machine so"
echo "meso-submit.py can unlock your WXID. It is never printed"
echo "or sent anywhere except to the Meso app itself, over HTTPS."
echo ""
read -r -s -p "Station password for your WXID: " PW
echo ""
if [ -z "$PW" ]; then
  echo "ERROR: empty password, nothing written."
  exit 1
fi
read -r -s -p "Type it again to confirm: " PW2
echo ""
if [ "$PW" != "$PW2" ]; then
  echo "ERROR: the two entries do not match, nothing written."
  exit 1
fi
printf '%s' "$PW" > "$PASS_FILE"
chmod 600 "$PASS_FILE"
unset PW PW2
echo "Saved to $PASS_FILE (mode 600)."
echo "Test it with: python3 $DEST/scripts/meso-submit.py --dry-run"
echo "then a live test with: python3 $DEST/scripts/meso-submit.py --force"
