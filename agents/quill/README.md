# Quill

Autonomous LinkedIn posting agent for Najib. Runs daily via GitHub Actions — fetches recent commits from active repos, picks the most significant one, and posts a LinkedIn update. No interaction needed.

## How it works

1. Fetches commits from the last 168 hours (1 week) across three repos: Velluma, AURA, ApplyOS
2. Filters out noise commits (chore, style, docs, merge, bump, wip)
3. Picks the highest-priority commit (feat > fix > refactor/perf > other)
4. Skips any commit SHA already in `posted_commits.txt` (deduplication)
5. Generates a LinkedIn post using Gemini 2.5 Flash Lite
6. Posts to LinkedIn and records the commit SHA

## Schedule

Runs automatically at **9:00 AM UTC** every day. Can also be triggered manually from the Actions tab.

## Setup

### 1. GitHub Secrets

Add these in `Settings → Secrets → Actions`:

| Secret | Description |
|---|---|
| `LINKEDIN_TOKEN` | LinkedIn OAuth Bearer token |
| `GEMINI_API_KEY` | Google AI Studio API key (free tier) |

### 2. Get a Gemini API key

Go to [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) and create a key. No billing required.

### 3. Enable Actions

Make sure GitHub Actions is enabled in the repo's Actions tab. The workflow needs `contents: write` permission (already set in the workflow file) to commit `posted_commits.txt` back after each run.

## Repo rules

| Repo | Visibility | Notes |
|---|---|---|
| Velluma | Private | Never named or linked — referred to as "a tool I'm building" |
| AURA | Public | Named freely, links to `auratriage.vercel.app` |
| ApplyOS | Public | Named freely, links to `applyos.io` |

## Token expiry

LinkedIn tokens expire after ~60 days. When the run logs `LinkedIn token invalid`, refresh the token and update the `LINKEDIN_TOKEN` secret.

## Files

- `quill.py` — main script
- `posted_commits.txt` — log of posted commit SHAs (auto-updated by the workflow)
- `INSTRUCTIONS.md` — voice/style guide for generated posts

The workflow lives at `.github/workflows/quill.yml` in the repo root.
