# Quill

Autonomous LinkedIn posting agent. Runs daily on GitHub Actions, fetches recent commits from the active repos, picks an anchor commit, looks at the surrounding commit thread to find the broader theme, and posts a single LinkedIn update with a code snippet image.

Also produces the hand-off artifacts that **Echo** (`agents/echo/`) consumes to mirror the post on X.

## How it works

1. Fetches commits from the last 168 hours (1 week) across three repos: Velluma, AURA, ApplyOS.
2. Filters out noise commits (chore, style, docs, merge, bump, wip).
3. Picks the highest-priority unposted commit (`feat` > `fix` > `refactor`/`perf` > other) as the **anchor**.
4. Pulls the **last 10 non-noise commits** in the anchor's repo (oldest → newest, anchor included) — the *thread*. The LLM uses this thread to infer the broader effort, not just the anchor's one-liner.
5. Sends the thread + the anchor's diff to Gemini 2.5 Flash. A single structured call returns:
   - the LinkedIn post text (synthesized across the thread),
   - the file + line range of the snippet that best illustrates what the post discusses.
6. Fetches that file at the anchor SHA, slices the chosen lines, renders a Carbon-style snippet image (Pillow + Pygments, Cascadia Mono, gradient backdrop).
7. Uploads the image to LinkedIn and posts with the image attached.
8. Records **every SHA in the 10-commit thread** in `posted_commits.txt` so the next run starts on a fresh window.
9. Writes the hand-off artifacts for Echo:
   - `last_post.json` — anchor SHA, repo, message, post text, alt text, image flag, LinkedIn post id
   - `last_post.png` — the same snippet image (omitted/removed for text-only posts)
10. The workflow commits `posted_commits.txt`, `last_post.json`, and `last_post.png` back to the repo.

## Schedule

Runs automatically at **09:00 UTC** every day. Can also be triggered manually from the Actions tab. Echo runs ~30 min later locally (gated on this workflow finishing — see `agents/echo/`).

## Setup

### 1. GitHub Secrets

Add these in `Settings → Secrets → Actions`:

| Secret           | Description                                            |
|------------------|--------------------------------------------------------|
| `LINKEDIN_TOKEN` | LinkedIn OAuth Bearer token                            |
| `GEMINI_API_KEY` | Google AI Studio API key (free tier)                   |

### 2. Get a Gemini API key

[aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey). No billing required.

### 3. Enable Actions

Make sure GitHub Actions is enabled. The workflow needs `contents: write` (already set) to commit `posted_commits.txt`, `last_post.json`, and `last_post.png` back after each run.

## Repo rules

| Repo    | Visibility | Notes                                                                   |
|---------|------------|-------------------------------------------------------------------------|
| Velluma | Private    | Never named or linked — referred to as "a tool I'm building"            |
| AURA    | Public     | Named freely, links to `auratriage.vercel.app`                          |
| ApplyOS | Public     | Named freely, links to `applyos.io`                                     |

## Token expiry

LinkedIn tokens expire after ~60 days. When the run logs `LinkedIn token invalid`, refresh the token and update the `LINKEDIN_TOKEN` secret.

## Tunables

Constants at the top of `quill.py`:

| Name              | Default | Meaning                                                          |
|-------------------|---------|------------------------------------------------------------------|
| `THREAD_SIZE`     | 10      | Commits of context the LLM sees around the anchor                |
| `MAX_SNIPPET_LINES` | 22    | Upper bound on the rendered code-snippet height                  |
| `REPOS`           | 3 repos | Sources the agent scans for commits                              |

## Files

- `quill.py` — main script
- `snippet_image.py` — Carbon-style code snippet renderer
- `fonts/` — Cascadia Mono TTFs bundled for consistent rendering on CI
- `posted_commits.txt` — log of consumed commit SHAs (auto-updated by the workflow)
- `last_post.json` — hand-off metadata for Echo (auto-updated)
- `last_post.png` — hand-off image for Echo (auto-updated; absent for text-only posts)

The workflow lives at `.github/workflows/quill.yml` in the repo root.
