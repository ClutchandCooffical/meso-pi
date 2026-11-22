#!/bin/bash
# meso-pi remote installer. Run on the Raspberry Pi (or any Linux box):
#   curl -fsSL https://raw.githubusercontent.com/ClutchandCooffical/meso-pi/main/install-remote.sh | bash
# Or with a custom install directory:
#   curl -fsSL https://raw.githubusercontent.com/ClutchandCooffical/meso-pi/main/install-remote.sh | bash -s -- /opt/meso
#
# Downloads the latest meso-pi from GitHub, extracts it, and runs
# install.sh. Uses bash explicitly to run install.sh because the
# executable bit does not survive the GitHub archive download.
# install.sh itself makes the installed scripts executable.
set -euo pipefail

REPO="ClutchandCooffical/meso-pi"
DEST="${1:-/home/pi/meso}"
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

echo "Downloading meso-pi (latest main) from GitHub..."
curl -fsSL "https://github.com/$REPO/archive/refs/heads/main.tar.gz" \
  -o "$TMPDIR/meso-pi.tar.gz"
tar xzf "$TMPDIR/meso-pi.tar.gz" -C "$TMPDIR"
# The archive extracts to meso-pi-main/
bash "$TMPDIR/meso-pi-main/install.sh" "$DEST"
