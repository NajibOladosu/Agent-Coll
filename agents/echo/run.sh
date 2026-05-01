#!/bin/bash
# Echo runner (launchd-friendly).
#
# Order of operations:
#   1. Wait for network connectivity (laptop may have just woken up).
#   2. Poll the Quill GitHub Actions workflow until *today's* run reports
#      status=completed AND conclusion=success (or until timeout).
#   3. git pull to pick up agents/quill/last_post.{json,png}.
#   4. Run echo.py to mirror the LinkedIn post to X.
#
# launchd handles "system was asleep at fire time" automatically — when the
# machine wakes, launchd runs missed StartCalendarInterval jobs. This script
# only needs to handle "internet might not be up yet" + "workflow might still
# be running."

set -u

REPO="/Users/najiboladosu/Documents/Projects/Agent-Coll"
ECHO_DIR="$REPO/agents/echo"
LOG="$ECHO_DIR/echo.log"
PYTHON="$ECHO_DIR/.venv/bin/python"

GH_OWNER="NajibOladosu"
GH_REPO="quill-agent"
WORKFLOW_FILE="quill.yml"

NET_WAIT_MAX=600        # 10 min waiting for network
WORKFLOW_WAIT_MAX=2700  # 45 min waiting for the Quill workflow to finish
POLL_INTERVAL=30

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

ts()  { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

mkdir -p "$ECHO_DIR"
log "Echo run starting (pid=$$)"

# ---- 1. Wait for network ----
net_ok() {
  curl -fsSL --max-time 5 https://api.github.com -o /dev/null
}

deadline=$(( $(date +%s) + NET_WAIT_MAX ))
while [ $(date +%s) -lt $deadline ]; do
  if net_ok; then
    log "Network OK"
    break
  fi
  log "No network yet — sleeping 15s"
  sleep 15
done
if ! net_ok; then
  log "Network never came up within ${NET_WAIT_MAX}s — aborting"
  exit 1
fi

# ---- 2. Wait for today's Quill workflow run to finish ----
api="https://api.github.com/repos/$GH_OWNER/$GH_REPO/actions/workflows/$WORKFLOW_FILE/runs?per_page=5"

parse_status() {
  # Returns one of:
  #   "no_run_today"
  #   "parse_error"
  #   "<status>|<conclusion>"   e.g. "completed|success", "in_progress|", "queued|"
  #
  # JSON is read from $WF_JSON env var, NOT stdin — using `python3 - <<'PY'`
  # would redirect stdin to the heredoc and clobber the piped response.
  /usr/bin/python3 -c '
import json, os, sys
from datetime import datetime, timezone
try:
    data = json.loads(os.environ.get("WF_JSON", ""))
except Exception:
    print("parse_error"); sys.exit()
today = datetime.now(timezone.utc).date().isoformat()
runs = [r for r in data.get("workflow_runs", []) if r.get("created_at", "")[:10] >= today]
if not runs:
    print("no_run_today"); sys.exit()
r = runs[0]
print("{}|{}".format(r.get("status",""), r.get("conclusion") or ""))
' 2>/dev/null
}

workflow_ok=0
deadline=$(( $(date +%s) + WORKFLOW_WAIT_MAX ))

while [ $(date +%s) -lt $deadline ]; do
  resp=$(curl -fsSL --max-time 15 "$api" 2>>"$LOG" || true)
  if [ -z "$resp" ]; then
    log "Workflow API fetch failed; retrying in ${POLL_INTERVAL}s"
    sleep $POLL_INTERVAL
    continue
  fi

  parsed=$(WF_JSON="$resp" parse_status)
  parsed=${parsed:-parse_error}
  log "Workflow status: $parsed"

  case "$parsed" in
    no_run_today|parse_error)
      sleep $POLL_INTERVAL
      continue
      ;;
  esac

  status_main=${parsed%%|*}
  status_sub=${parsed##*|}

  if [ "$status_main" = "completed" ]; then
    if [ "$status_sub" = "success" ]; then
      log "Quill workflow succeeded — proceeding"
      workflow_ok=1
      break
    else
      log "Quill workflow finished with conclusion='$status_sub' — aborting"
      exit 1
    fi
  fi

  sleep $POLL_INTERVAL
done

if [ $workflow_ok -ne 1 ]; then
  log "Timed out waiting for Quill workflow (${WORKFLOW_WAIT_MAX}s) — aborting"
  exit 1
fi

# ---- 3. Pull latest ----
cd "$REPO"
git pull --rebase --autostash >> "$LOG" 2>&1 || log "git pull failed (continuing)"

# ---- 4. Run echo ----
if [ ! -x "$PYTHON" ]; then
  log "venv missing at $PYTHON. See agents/echo/README.md"
  exit 1
fi

"$PYTHON" "$ECHO_DIR/echo.py" >> "$LOG" 2>&1
EXIT=$?
log "Echo run done (exit $EXIT)"
exit $EXIT
