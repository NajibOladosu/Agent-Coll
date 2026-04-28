# Reddit Advocate

Reply-focused Reddit traffic agent for AURA + ApplyOS. Plants value comments in pain-point threads. Profile bio is the trapdoor — comments stay link-free.

## Mental model

Reddit is a traffic system, not a social network. Comments are curiosity trails to your profile. The product is never named in a comment.

## Flow

```
scout (cron)        post (cron)
    │                   │
    ▼                   ▼
 hot+new threads    candidates/*.json
 → score by pain    where status=approved
 → gen reply via    → post via PRAW
   Gemini           → log to posted.txt
 → write candidate  → set status=posted
   JSON (pending)
        │
        ▼
   YOU REVIEW
   set status=approved
```

## Subcommands

```
python advocate.py scout    # find threads, queue candidates (no posting)
python advocate.py post     # post approved candidates
python advocate.py status   # rate-limit + queue counts
```

## Approval workflow (you)

1. `scout` writes `candidates/YYYYMMDD-HHMMSS-<thread_id>.json` with `status: "pending"`.
2. Open the file. Read the thread (URL in file). Read the reply.
3. Edit if needed. Set `"status": "approved"` and commit.
4. Next `post` run sends it.

To reject: delete the file or set `"status": "rejected"`.

## Hard ban-prevention rules (in code)

- No links, no product names in comments. Regex blocks before post.
- Per-sub cooldown: 24h between any actions in same sub.
- Per-account daily cap: 5 comments (`REDDIT_DAILY_CAP` env).
- Per-sub karma + account-age gates from `subreddits.json`.
- Dup detection: jaccard shingle similarity vs pending candidates + exact hash vs ledger.
- Final regex banned-pattern check at post time (catches user edits).
- 60s gap between posts in a single run.
- `REDDIT_KILL=1` env stops all writes.

## Setup

### 1. Reddit OAuth

`https://www.reddit.com/prefs/apps` → create app → type **script** → set redirect to `http://localhost:8080`. Get `client_id` (under app name) and `client_secret`.

### 2. Account hygiene

Use an account with:
- 30+ days old
- 50+ combined karma
- Real comment history (not 0-karma fresh egg)

Fresh accounts get automod-nuked.

### 3. Profile bio (the trapdoor)

Edit your Reddit profile bio to:
```
building tools for [job hunters / ER nurses]. links in comments below.
```

Pin a self-post on your profile that links to your free guide → guide CTAs link to AURA / ApplyOS landing pages with UTM tags.

UTM template:
```
?utm_source=reddit&utm_medium=profile&utm_campaign=guide_v1
```

### 4. Free guides (do this first)

Before running the agent, ship the trapdoor content:
- **AURA**: "ER Triage Cheat Sheet — 10 cases, 5 min decisions" (10-page PDF)
- **ApplyOS**: "200 Applications, 12 Offers — full system" (10-page PDF)

Host on Gumroad (free) or simple landing page. Last page links to product.

### 5. Local run

Env lives in repo root `.env` (shared across all agents). Copy `.env.example` from repo root if missing:

```
cp ../../.env.example ../../.env
# fill secrets in ../../.env
pip install praw requests
python advocate.py status
python advocate.py scout
# review candidates/, set approved
python advocate.py post
```

### 6. GitHub Actions

Add secrets in `Settings → Secrets → Actions`:

| Secret | |
|---|---|
| `REDDIT_CLIENT_ID` | |
| `REDDIT_CLIENT_SECRET` | |
| `REDDIT_USERNAME` | |
| `REDDIT_PASSWORD` | |
| `REDDIT_USER_AGENT` | optional, default OK |
| `GEMINI_API_KEY` | reuse from Quill |

Two workflows:
- `reddit-advocate-scout.yml` — every 4h, generates candidates, commits to repo
- `reddit-advocate-post.yml` — every 2h, posts approved candidates, commits ledger

## Subreddit tiers

`subreddits.json` defines two tiers:
- **Tier 1 — engage only**: cscareerquestions, jobs, recruitinghell, ExperiencedDevs, jobsearchhacks, nursing, medicine, emergencymedicine
- **Tier 2 — promo allowed in megathread**: SaaS (Sat), SideProject, indiehackers, Entrepreneur (Sun), startups (Sat), EntrepreneurRideAlong

This v1 focuses on **comments only**. Promo posting is separate work.

## Pain keywords

`subreddits.json → pain_keywords` per product. Threads are scored on keyword hit count. Tune these weekly based on which threads convert.

## Tracking conversion (do this manually for now)

When a candidate gets posted, watch profile clicks:
- Reddit profile insights (mod-only on subs you mod)
- Track guide downloads via UTM in your landing analytics
- Manual log: which sub → most profile clicks → most guide signups → most product signups

After ~2 weeks of data, kill subs that don't convert and double down on winners.

## What this agent will NOT do

- Post submissions (top-level posts) — comments only in v1
- Reply with links
- Send DMs
- Vote
- Operate without your approval on each comment

## Debugging

If you see "all gemini models failed" — check `GEMINI_API_KEY`.

If `praw` raises 401 — recheck `REDDIT_USERNAME`/`REDDIT_PASSWORD`. Reddit script-app auth uses password grant; it does not work for accounts with 2FA. Use an app-specific password or a second account.

If automod removes your comment within seconds — check sub rules for required karma/age/flair. Update `min_karma` / `min_account_age_days` in `subreddits.json`.
