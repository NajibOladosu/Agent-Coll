# Echo

Local agent that posts the X (Twitter) mirror of Quill's latest LinkedIn post. Runs as a launchd LaunchAgent so it survives sleep/wake and only fires once today's Quill workflow has actually succeeded.

## How it works

1. The Quill GitHub Actions workflow runs at 09:00 UTC, posts to LinkedIn, and commits `agents/quill/last_post.json` and (if an image was used) `agents/quill/last_post.png` back to the repo.
2. Echo's launchd job fires at 10:30 local. If the laptop was asleep at that time, launchd runs it on next wake. `run.sh` then:
   1. Waits for network connectivity (up to 10 min).
   2. Polls the Quill workflow API until **today's** run reports `status=completed` and `conclusion=success` (up to 45 min).
   3. `git pull --rebase --autostash` to pick up Quill's hand-off artifacts.
   4. Runs `echo.py`.
3. `echo.py`:
   - Reads `agents/quill/last_post.json` (anchor SHA, repo, message, post text, image flag).
   - Skips if its own `posted_x.txt` already lists the SHA.
   - Asks Gemini to rewrite the LinkedIn post as a tweet (≤280 chars).
   - Posts to X via raw httpx + cookie auth (no official X API key).
   - Attaches `last_post.png` as media when present.
   - Records the SHA in `posted_x.txt`.

## Setup (one-time)

```bash
cd agents/echo
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Create `agents/echo/twitter_cookies.json` (gitignored):

```json
{
  "auth_token": "<your auth_token cookie from x.com>",
  "ct0": "<your ct0 cookie from x.com>",
  "screen_name": "YourHandle"
}
```

`GEMINI_API_KEY` is read from the repo-root `.env` (already used by Quill).

## launchd

Install and load the LaunchAgent:

```bash
bash agents/echo/install-launchd.sh install
```

This copies `com.najib.echo.plist` to `~/Library/LaunchAgents/` and `bootstrap`s it into the user GUI domain. The job fires daily at **10:30 local**; macOS reruns missed `StartCalendarInterval` jobs on wake.

Other commands:

| Command                                          | What it does                                  |
|--------------------------------------------------|-----------------------------------------------|
| `bash agents/echo/install-launchd.sh status`     | Show launchctl state                           |
| `bash agents/echo/install-launchd.sh kick`       | Run the job NOW                                |
| `bash agents/echo/install-launchd.sh uninstall`  | Unload + remove the plist                      |

The plist hardcodes Najib's home path. If you fork this, edit the paths in `com.najib.echo.plist` before installing.

## Manual run (no launchd)

```bash
.venv/bin/python echo.py
```

Skips the network/workflow gate — useful for local debugging only.

## Logs

- `agents/echo/echo.log` — `run.sh` + `echo.py` output (network wait, workflow polling, post result).
- `agents/echo/echo.launchd.log` — launchd's stdout/stderr capture (usually mirrors `echo.log`).

Both are gitignored.

## State files (all gitignored)

- `twitter_cookies.json` — X auth cookies (refresh from a logged-in browser when posts start failing).
- `posted_x.txt` — SHAs Echo has already mirrored to X.
- `*.log` — run logs.
- `.venv/` — local Python virtualenv.

## Files

- `echo.py` — main script (Gemini rewrite + httpx post to X with media).
- `run.sh` — launchd wrapper: net wait → workflow poll → git pull → echo.py.
- `com.najib.echo.plist` — launchd job definition.
- `install-launchd.sh` — install / uninstall / status / kick helper.
- `requirements.txt` — Python deps (`requests`, `httpx`).
