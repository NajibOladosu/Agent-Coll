#!/bin/bash
# Install the Echo launchd agent. Idempotent.
#
# Usage:
#   bash agents/echo/install-launchd.sh           # install + load
#   bash agents/echo/install-launchd.sh uninstall # unload + remove
#   bash agents/echo/install-launchd.sh status    # show launchctl status
#   bash agents/echo/install-launchd.sh kick      # run the job NOW

set -e

LABEL="com.najib.echo"
SRC_PLIST="$(cd "$(dirname "$0")" && pwd)/com.najib.echo.plist"
DEST_PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

cmd="${1:-install}"

case "$cmd" in
  install)
    mkdir -p "$HOME/Library/LaunchAgents"
    # Unload any prior version first (ignore failure on first install).
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    cp "$SRC_PLIST" "$DEST_PLIST"
    launchctl bootstrap "$DOMAIN" "$DEST_PLIST"
    launchctl enable "$DOMAIN/$LABEL"
    echo "Installed $LABEL → $DEST_PLIST"
    launchctl print "$DOMAIN/$LABEL" | head -20
    ;;
  uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$DEST_PLIST"
    echo "Uninstalled $LABEL"
    ;;
  status)
    launchctl print "$DOMAIN/$LABEL" 2>/dev/null | head -40 \
      || echo "$LABEL not loaded"
    ;;
  kick)
    launchctl kickstart -k "$DOMAIN/$LABEL"
    echo "Kicked $LABEL — tail agents/echo/echo.log to watch"
    ;;
  *)
    echo "Usage: $0 [install|uninstall|status|kick]" >&2
    exit 2
    ;;
esac
