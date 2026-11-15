#!/usr/bin/env bash
# Install (or refresh) the systemd *user* service that runs the TV Caster server.
#   tools/install_service.sh            write the unit, reload systemd, enable autostart
#   tools/install_service.sh --start    ... and (re)start the server now
#   tools/install_service.sh --print    only print the rendered unit
# Autostart at boot without a login needs lingering: loginctl enable-linger "$USER" (checked below).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_SRC="$ROOT_DIR/deploy/tv-caster.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_DST="$UNIT_DIR/tv-caster.service"

render() { sed "s|@PROJECT_DIR@|$ROOT_DIR|g" "$UNIT_SRC"; }

if [ "${1:-}" = "--print" ]; then render; exit 0; fi

mkdir -p "$UNIT_DIR"
if [ -f "$UNIT_DST" ] && ! diff -q <(render) "$UNIT_DST" >/dev/null; then
  cp "$UNIT_DST" "$UNIT_DST.bak"
  echo "previous unit saved as $UNIT_DST.bak"
fi
render > "$UNIT_DST"
systemctl --user daemon-reload
systemctl --user enable tv-caster.service >/dev/null
echo "installed $UNIT_DST (enabled)"

if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
  echo "note: lingering is off, the server will only start after you log in. Enable: loginctl enable-linger $USER"
fi

if [ "${1:-}" = "--start" ]; then
  systemctl --user restart tv-caster.service
  sleep 2
  systemctl --user --no-pager --lines=0 status tv-caster.service | head -4
fi
