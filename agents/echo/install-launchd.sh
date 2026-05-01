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

print_fda_warning() {
  cat <<'WARN'

────────────────────────────────────────────────────────────────────────
  IMPORTANT — macOS Full Disk Access required
────────────────────────────────────────────────────────────────────────
  This repo lives under ~/Documents, which macOS protects via TCC.
  launchd-spawned /bin/bash has no access there by default and will fail
  with "Operation not permitted" (exit 126) when reading run.sh.

  One-time fix:
    1. System Settings → Privacy & Security → Full Disk Access
    2. Click "+", press Cmd+Shift+G, enter:  /bin/bash
    3. Add it and toggle ON.
    4. Re-run:   bash agents/echo/install-launchd.sh kick
       Then watch: tail -f agents/echo/echo.log

  Verify with:   bash agents/echo/install-launchd.sh status
  Look for `last exit code = 0` (or in-flight). 126 = TCC still blocking.
────────────────────────────────────────────────────────────────────────
WARN
}

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
    print_fda_warning
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
    sleep 2
    last_exit=$(launchctl print "$DOMAIN/$LABEL" 2>/dev/null | awk -F'= ' '/last exit code/ {print $2; exit}')
    if [ "$last_exit" = "126" ]; then
      echo "WARNING: last exit code = 126 — bash blocked from reading run.sh."
      print_fda_warning
    fi
    ;;
  *)
    echo "Usage: $0 [install|uninstall|status|kick]" >&2
    exit 2
    ;;
esac
