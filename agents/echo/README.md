# Echo

Local agent that posts the X (Twitter) mirror of Quill's latest LinkedIn post.

## How it works

1. The Quill GitHub Actions workflow runs daily, posts to LinkedIn, and commits
   `agents/quill/last_post.json` and (if an image was used) `agents/quill/last_post.png`
   back to the repo.
2. Echo runs locally via cron a short while later. It:
   - `git pull`s the repo to pick up Quill's artifacts.
   - Reads `last_post.json` (sha, repo, message, post text, image flag).
   - Skips if its own `posted_x.txt` already lists the SHA.
   - Asks Gemini to rewrite the LinkedIn post as a tweet (≤280 chars).
   - Posts to X via [`twikit`](https://github.com/d60/twikit) using the cookies
     in `twitter_cookies.json` — no official X API key needed.
   - Attaches `last_post.png` as media when present.
   - Records the SHA in `posted_x.txt`.

This keeps LinkedIn and X in sync without re-running the LLM pipeline.

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

## Cron

Add to your local crontab (`crontab -e`). 10:30 local time runs ~30 min after
the Quill workflow finishes at 09:00 UTC:

```
30 10 * * * /bin/bash /Users/najiboladosu/Documents/Projects/Agent-Coll/agents/echo/run.sh
```

Logs land in `agents/echo/echo.log`.

## State files (all gitignored)

- `twitter_cookies.json` — X auth cookies.
- `posted_x.txt` — SHAs Echo has already posted to X.
- `echo.log` — run log.

## Manual run

```bash
.venv/bin/python echo.py
```
