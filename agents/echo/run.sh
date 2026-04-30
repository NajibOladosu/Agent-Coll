#!/bin/bash
# Echo cron wrapper: pull latest repo state, then post the X version of
# Quill's latest LinkedIn post. Designed to run AFTER the GitHub Actions
# Quill workflow has committed agents/quill/last_post.{json,png}.
#
# Cron example (10:30 local, ~30 min after the 9:00 UTC Quill workflow):
#   30 10 * * * /bin/bash /Users/najiboladosu/Documents/Projects/Agent-Coll/agents/echo/run.sh

set -e

REPO="/Users/najiboladosu/Documents/Projects/Agent-Coll"
ECHO_DIR="$REPO/agents/echo"
LOG="$ECHO_DIR/echo.log"
PYTHON="$ECHO_DIR/.venv/bin/python"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

mkdir -p "$ECHO_DIR"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Echo run starting" >> "$LOG"

cd "$REPO"

# Pull latest so we see the Quill workflow's last_post.{json,png}.
# --autostash keeps any local-only state (e.g. echo.log, posted_x.txt) safe.
git pull --rebase --autostash >> "$LOG" 2>&1 || echo "git pull failed (continuing)" >> "$LOG"

if [ ! -x "$PYTHON" ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] venv missing at $PYTHON. See agents/echo/README.md" >> "$LOG"
  exit 1
fi

"$PYTHON" "$ECHO_DIR/echo.py" >> "$LOG" 2>&1
EXIT=$?

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Echo run done (exit $EXIT)" >> "$LOG"
exit $EXIT
