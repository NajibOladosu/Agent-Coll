#!/usr/bin/env python3
"""
Reddit Advocate — autonomous reply-focused traffic agent.

Subcommands:
  scout   Scan whitelist subs for pain-point threads, generate candidate replies,
          write to candidates/*.json for human review. Does NOT post.
  post    Read candidates/*.json with status='approved', post via PRAW, move to posted.
  status  Print rate-limit and queue status.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
REPO_ROOT = ROOT.parent.parent
CANDIDATES_DIR = ROOT / "candidates"
POSTED_FILE = ROOT / "posted.txt"
SUBS_FILE = ROOT / "subreddits.json"
PROMPT_FILE = ROOT / "prompts" / "reply.md"


def _load_dotenv(path):
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


_load_dotenv(REPO_ROOT / ".env")

import praw  # noqa: E402
import requests  # noqa: E402

# --- env ---
REDDIT_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET", "")
REDDIT_USERNAME = os.environ.get("REDDIT_USERNAME", "")
REDDIT_PASSWORD = os.environ.get("REDDIT_PASSWORD", "")
REDDIT_USER_AGENT = os.environ.get(
    "REDDIT_USER_AGENT", f"python:reddit_advocate:v0.1 (by /u/{REDDIT_USERNAME})"
)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
KILL = os.environ.get("REDDIT_KILL", "0") == "1"

# --- limits ---
DAILY_COMMENT_CAP = int(os.environ.get("REDDIT_DAILY_CAP", "5"))
PER_SUB_COOLDOWN_HOURS = 24
SCOUT_THREAD_AGE_MAX_HOURS = 48
DUP_COSINE_THRESHOLD = 0.85
GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite"]

PRODUCT_SUMMARY = {
    "AURA": "ER triage assist tool for nurses, helps with ESI scoring and chief-complaint workflow",
    "ApplyOS": "job application tracker that helps apply at scale and tailor resumes",
}

# Banned strings — final regex check before post
BANNED_PATTERNS = [
    r"\bAURA\b",
    r"\bApplyOS\b",
    r"auratriage\.vercel\.app",
    r"applyos\.io",
    r"https?://",
    r"\bDM me\b",
    r"\bcheck out my\b",
    r"\bI built\b",
    r"\bI made\b",
]


# ---------------- ledger ----------------

def load_posted():
    """Returns list of dicts: {timestamp, action_id, sub, thread_id, kind}."""
    if not POSTED_FILE.exists():
        return []
    out = []
    for line in POSTED_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        out.append({
            "timestamp": parts[0],
            "action_id": parts[1],
            "sub": parts[2],
            "thread_id": parts[3],
            "kind": parts[4],
            "text_hash": parts[5] if len(parts) > 5 else "",
        })
    return out


def append_posted(entry):
    POSTED_FILE.parent.mkdir(parents=True, exist_ok=True)
    line = "\t".join([
        entry["timestamp"], entry["action_id"], entry["sub"],
        entry["thread_id"], entry["kind"], entry.get("text_hash", ""),
    ])
    with POSTED_FILE.open("a") as f:
        f.write(line + "\n")


def actions_in_last(hours, posted=None):
    posted = posted or load_posted()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    n = 0
    for p in posted:
        try:
            ts = datetime.fromisoformat(p["timestamp"])
        except ValueError:
            continue
        if ts >= cutoff:
            n += 1
    return n


def last_action_in_sub(sub, posted=None):
    posted = posted or load_posted()
    sub_lower = sub.lower()
    latest = None
    for p in posted:
        if p["sub"].lower() != sub_lower:
            continue
        try:
            ts = datetime.fromisoformat(p["timestamp"])
        except ValueError:
            continue
        if latest is None or ts > latest:
            latest = ts
    return latest


def already_replied(thread_id, posted=None):
    posted = posted or load_posted()
    return any(p["thread_id"] == thread_id for p in posted)


# ---------------- dup detection ----------------

def text_fingerprint(text):
    norm = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.sha256(norm.encode()).hexdigest()[:16]


def shingles(text, k=4):
    tokens = re.findall(r"\w+", text.lower())
    return set(tuple(tokens[i:i + k]) for i in range(max(0, len(tokens) - k + 1)))


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def too_similar_to_recent(text, posted=None, n=50):
    posted = posted or load_posted()
    recent = posted[-n:]
    new_sh = shingles(text)
    for p in recent:
        # fingerprint match = exact dup
        if p.get("text_hash") == text_fingerprint(text):
            return True, "exact_match"
    # we don't store full text in ledger; jaccard check happens at scout time
    # against in-flight candidates instead
    return False, None


def too_similar_to_pending_candidates(text):
    new_sh = shingles(text)
    for cand_path in CANDIDATES_DIR.glob("*.json"):
        try:
            cand = json.loads(cand_path.read_text())
        except Exception:
            continue
        if jaccard(new_sh, shingles(cand.get("reply", ""))) > DUP_COSINE_THRESHOLD:
            return True
    return False


# ---------------- final safety check ----------------

def violates_banned(text):
    for pat in BANNED_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            return pat
    return None


# ---------------- gating ----------------

def sub_eligible(sub_cfg, account_karma, account_age_days):
    if account_karma < sub_cfg.get("min_karma", 0):
        return False, f"karma {account_karma} < {sub_cfg['min_karma']}"
    if account_age_days < sub_cfg.get("min_account_age_days", 0):
        return False, f"age {account_age_days}d < {sub_cfg['min_account_age_days']}d"
    last = last_action_in_sub(sub_cfg["name"])
    if last:
        delta_h = (datetime.now(timezone.utc) - last).total_seconds() / 3600
        if delta_h < PER_SUB_COOLDOWN_HOURS:
            return False, f"cooldown {delta_h:.1f}h < {PER_SUB_COOLDOWN_HOURS}h"
    return True, None


def relevant_product(thread, sub_cfg, pain_keywords):
    text = (thread.title + " " + (thread.selftext or "")).lower()
    candidates = sub_cfg.get("products", [])
    best = None
    best_hits = 0
    for product in candidates:
        kws = pain_keywords.get(product, [])
        hits = sum(1 for kw in kws if kw.lower() in text)
        if hits > best_hits:
            best = product
            best_hits = hits
    return best, best_hits


# ---------------- gemini ----------------

def gemini_call(system_prompt, user_payload):
    """Returns parsed JSON dict from model output."""
    last_err = None
    for model in GEMINI_MODELS:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={GEMINI_API_KEY}"
        )
        body = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": json.dumps(user_payload)}]}],
            "generationConfig": {
                "temperature": 0.7,
                "responseMimeType": "application/json",
            },
        }
        try:
            r = requests.post(url, json=body, timeout=60)
            r.raise_for_status()
            data = r.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"all gemini models failed: {last_err}")


# ---------------- reddit ----------------

def reddit_client():
    return praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        username=REDDIT_USERNAME,
        password=REDDIT_PASSWORD,
        user_agent=REDDIT_USER_AGENT,
        ratelimit_seconds=600,
    )


def account_stats(reddit):
    me = reddit.user.me()
    karma = (me.comment_karma or 0) + (me.link_karma or 0)
    age_days = (time.time() - me.created_utc) / 86400.0
    return karma, age_days, me.name


# ---------------- subcommands ----------------

def cmd_scout(args):
    if KILL:
        print("KILL=1 — abort.")
        return
    cfg = json.loads(SUBS_FILE.read_text())
    prompt = PROMPT_FILE.read_text()
    pain_kw = cfg["pain_keywords"]

    reddit = reddit_client()
    karma, age_days, uname = account_stats(reddit)
    print(f"account: u/{uname} karma={karma} age={age_days:.0f}d")

    posted = load_posted()
    today_count = actions_in_last(24, posted)
    if today_count >= DAILY_COMMENT_CAP:
        print(f"daily cap hit ({today_count}/{DAILY_COMMENT_CAP}) — skip scout")
        return

    pending = list(CANDIDATES_DIR.glob("*.json"))
    pending_count = sum(
        1 for p in pending
        if json.loads(p.read_text()).get("status") == "pending"
    )
    print(f"pending candidates: {pending_count}")

    target_new = max(0, DAILY_COMMENT_CAP - today_count - pending_count)
    if target_new == 0:
        print("queue full — skip scout")
        return

    all_subs = cfg["tiers"]["tier1_engage_only"] + cfg["tiers"]["tier2_promo_allowed"]
    found = 0

    for sub_cfg in all_subs:
        if found >= target_new:
            break
        ok, reason = sub_eligible(sub_cfg, karma, age_days)
        if not ok:
            print(f"r/{sub_cfg['name']} skip: {reason}")
            continue

        try:
            sub = reddit.subreddit(sub_cfg["name"])
            threads = list(sub.hot(limit=15)) + list(sub.new(limit=15))
        except Exception as e:
            print(f"r/{sub_cfg['name']} fetch fail: {e}")
            continue

        seen = set()
        for thread in threads:
            if found >= target_new:
                break
            if thread.id in seen:
                continue
            seen.add(thread.id)

            # age window
            age_h = (time.time() - thread.created_utc) / 3600.0
            if age_h > SCOUT_THREAD_AGE_MAX_HOURS:
                continue
            if thread.locked or thread.archived or thread.stickied:
                continue
            if already_replied(thread.id, posted):
                continue

            product, hits = relevant_product(thread, sub_cfg, pain_kw)
            if not product or hits < 1:
                continue

            payload = {
                "subreddit": sub_cfg["name"],
                "thread_title": thread.title,
                "thread_body": (thread.selftext or "")[:4000],
                "parent_comment": None,
                "product_context": product,
                "product_internal_summary": PRODUCT_SUMMARY[product],
            }
            try:
                result = gemini_call(prompt, payload)
            except Exception as e:
                print(f"gemini fail t/{thread.id}: {e}")
                continue

            if not result.get("should_reply"):
                continue
            reply = result.get("reply", "").strip()
            if not reply:
                continue
            if violates_banned(reply):
                print(f"t/{thread.id} reply violates banned pattern — drop")
                continue
            if too_similar_to_pending_candidates(reply):
                print(f"t/{thread.id} reply too similar to pending — drop")
                continue

            cand = {
                "status": "pending",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "subreddit": sub_cfg["name"],
                "thread_id": thread.id,
                "thread_url": f"https://reddit.com{thread.permalink}",
                "thread_title": thread.title,
                "thread_body_snippet": (thread.selftext or "")[:500],
                "product_context": product,
                "reply": reply,
                "confidence": result.get("confidence", 0.0),
                "self_check": result.get("self_check", {}),
            }
            out = CANDIDATES_DIR / f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{thread.id}.json"
            out.write_text(json.dumps(cand, indent=2))
            print(f"queued: r/{sub_cfg['name']} t/{thread.id} ({product}) -> {out.name}")
            found += 1

    print(f"scout done. {found} new candidates.")


def cmd_post(args):
    if KILL:
        print("KILL=1 — abort.")
        return
    posted = load_posted()
    today_count = actions_in_last(24, posted)
    if today_count >= DAILY_COMMENT_CAP:
        print(f"daily cap hit ({today_count}/{DAILY_COMMENT_CAP})")
        return

    approved = []
    for path in sorted(CANDIDATES_DIR.glob("*.json")):
        try:
            cand = json.loads(path.read_text())
        except Exception:
            continue
        if cand.get("status") == "approved":
            approved.append((path, cand))

    if not approved:
        print("no approved candidates.")
        return

    reddit = reddit_client()
    karma, age_days, uname = account_stats(reddit)
    print(f"account: u/{uname} karma={karma} age={age_days:.0f}d")

    for path, cand in approved:
        if today_count >= DAILY_COMMENT_CAP:
            print("hit daily cap mid-post — stop.")
            break

        # cooldown re-check at post time
        last = last_action_in_sub(cand["subreddit"])
        if last:
            delta_h = (datetime.now(timezone.utc) - last).total_seconds() / 3600
            if delta_h < PER_SUB_COOLDOWN_HOURS:
                print(f"r/{cand['subreddit']} cooldown {delta_h:.1f}h — skip {path.name}")
                continue

        # dup-vs-already-posted check
        if already_replied(cand["thread_id"], posted):
            print(f"already replied to {cand['thread_id']} — drop {path.name}")
            path.unlink()
            continue

        # re-run banned-pattern check (in case user edited)
        bad = violates_banned(cand["reply"])
        if bad:
            print(f"banned pattern '{bad}' in {path.name} — REJECT, mark needs_review")
            cand["status"] = "needs_review"
            cand["reject_reason"] = f"banned pattern: {bad}"
            path.write_text(json.dumps(cand, indent=2))
            continue

        try:
            submission = reddit.submission(id=cand["thread_id"])
            comment = submission.reply(cand["reply"])
        except Exception as e:
            print(f"post fail {path.name}: {e}")
            cand["status"] = "error"
            cand["last_error"] = str(e)
            path.write_text(json.dumps(cand, indent=2))
            continue

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action_id": comment.id,
            "sub": cand["subreddit"],
            "thread_id": cand["thread_id"],
            "kind": "comment",
            "text_hash": text_fingerprint(cand["reply"]),
        }
        append_posted(entry)
        posted.append(entry)
        today_count += 1

        cand["status"] = "posted"
        cand["posted_at"] = entry["timestamp"]
        cand["comment_id"] = comment.id
        cand["comment_permalink"] = f"https://reddit.com{comment.permalink}"
        path.write_text(json.dumps(cand, indent=2))
        print(f"posted: r/{cand['subreddit']} t/{cand['thread_id']} c/{comment.id}")

        # human-pace gap between posts
        time.sleep(60)

    print(f"post run done. {today_count}/{DAILY_COMMENT_CAP} today.")


def cmd_status(args):
    posted = load_posted()
    print(f"total posted ever: {len(posted)}")
    print(f"last 24h: {actions_in_last(24, posted)}/{DAILY_COMMENT_CAP}")
    print(f"last 7d:  {actions_in_last(168, posted)}")
    pending = sum(
        1 for p in CANDIDATES_DIR.glob("*.json")
        if json.loads(p.read_text()).get("status") == "pending"
    )
    approved = sum(
        1 for p in CANDIDATES_DIR.glob("*.json")
        if json.loads(p.read_text()).get("status") == "approved"
    )
    print(f"queue: pending={pending} approved={approved}")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scout")
    sub.add_parser("post")
    sub.add_parser("status")
    args = p.parse_args()

    {
        "scout": cmd_scout,
        "post": cmd_post,
        "status": cmd_status,
    }[args.cmd](args)


if __name__ == "__main__":
    main()
