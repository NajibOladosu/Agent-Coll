#!/usr/bin/env python3
import os
import time
import requests
from datetime import datetime, timedelta, timezone
from requests_oauthlib import OAuth1Session

LINKEDIN_TOKEN = os.environ["LINKEDIN_TOKEN"]
LINKEDIN_URN   = "urn:li:person:G82eBN-mpx"
X_CONSUMER_KEY        = os.environ["X_CONSUMER_KEY"]
X_CONSUMER_SECRET     = os.environ["X_CONSUMER_SECRET"]
X_ACCESS_TOKEN        = os.environ["X_ACCESS_TOKEN"]
X_ACCESS_TOKEN_SECRET = os.environ["X_ACCESS_TOKEN_SECRET"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
POSTED_FILE    = "posted_commits.txt"

GEMINI_MODELS  = ["gemini-2.5-flash-lite", "gemini-2.5-flash"]

REPOS = ["Velluma", "AURA", "ApplyOS"]

REPO_RULES = {
    "Velluma": {"public": False, "refer_as": "a tool I'm building", "url": None},
    "AURA":    {"public": True,  "name": "AURA",    "url": "auratriage.vercel.app"},
    "ApplyOS": {"public": True,  "name": "ApplyOS", "url": "applyos.io"},
}

SKIP_PREFIXES     = ("chore", "style", "docs", "merge", "bump", "wip")
PRIORITY_PREFIXES = ["feat", "fix", "refactor", "perf"]


# --- Deduplication ---

def load_posted_shas():
    if not os.path.exists(POSTED_FILE):
        return set()
    with open(POSTED_FILE) as f:
        return {line.strip() for line in f if line.strip()}


def save_posted_sha(sha):
    with open(POSTED_FILE, "a") as f:
        f.write(sha + "\n")


# --- Commit selection ---

def fetch_commits(repo):
    url = f"https://api.github.com/repos/NajibOladosu/{repo}/commits?per_page=20"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return r.json()


def is_recent(date_str):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=168)
    dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    return dt >= cutoff


def commit_priority(message):
    msg = message.lower()
    for i, prefix in enumerate(PRIORITY_PREFIXES):
        if msg.startswith(prefix):
            return i
    return len(PRIORITY_PREFIXES)


def select_best_commit(posted_shas):
    candidates = []
    for repo in REPOS:
        try:
            commits = fetch_commits(repo)
        except Exception as e:
            print(f"Warning: could not fetch {repo}: {e}")
            continue

        for c in commits:
            sha = c["sha"]
            if sha in posted_shas:
                continue
            date    = c["commit"]["author"]["date"]
            message = c["commit"]["message"].split("\n")[0].strip()
            if not is_recent(date):
                continue
            if any(message.lower().startswith(p) for p in SKIP_PREFIXES):
                continue
            ts = datetime.fromisoformat(date.replace("Z", "+00:00")).timestamp()
            candidates.append({
                "sha":      sha,
                "repo":     repo,
                "message":  message,
                "priority": commit_priority(message),
                "ts":       ts,
            })

    if not candidates:
        return None

    candidates.sort(key=lambda x: (x["priority"], -x["ts"]))
    return candidates[0]


# --- Content generation (Gemini) ---

def call_llm(system_prompt, user_prompt):
    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"parts": [{"text": user_prompt}]}],
        "generationConfig": {"maxOutputTokens": 600, "temperature": 0.7},
    }
    last_err = None
    for model in GEMINI_MODELS:
        url = (
            "https://generativelanguage.googleapis.com/v1beta"
            f"/models/{model}:generateContent?key={GEMINI_API_KEY}"
        )
        for attempt in range(3):
            try:
                r = requests.post(url, json=payload, timeout=30)
                if r.status_code in (500, 503):
                    wait = 10 * (attempt + 1)
                    print(f"{model} returned {r.status_code}, retrying in {wait}s...")
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            except Exception as e:
                last_err = e
                if attempt < 2:
                    time.sleep(10)
        print(f"{model} failed, trying next model...")
    raise RuntimeError(f"All Gemini models failed: {last_err}")


def generate_linkedin(commit):
    rules = REPO_RULES[commit["repo"]]

    if rules["public"]:
        context = (
            f"This post is about {rules['name']} (live at {rules['url']}). "
            f"You MAY name it and link to {rules['url']}."
        )
    else:
        context = (
            f"This post is about {rules['refer_as']}. "
            "NEVER name it or imply it is publicly available. "
            "NEVER mention a URL. "
            "Write ONLY about lessons learned, technical decisions, or architectural challenges."
        )

    system = (
        "You write LinkedIn posts for Najib, a software builder. "
        "Voice: technical, reflective, grounded. Builder in public. "
        "Structure: (1) Hook — a tension or problem, (2) Challenge — what was being built and why, "
        "(3) Decision or lesson — what was figured out, (4) Reader question — genuine reflection. "
        "Rules: first person, short paragraphs (1-3 lines), use → for lists never bullet points, "
        "no buzzwords (no 'excited to announce', 'game-changer', 'leveraging'), "
        "no emojis, no fabrication, 150-280 words. "
        "Output the post text only, no preamble."
    )

    return call_llm(system, f"{context}\n\nCommit message: {commit['message']}\n\nWrite the LinkedIn post:")


def generate_x(commit):
    rules = REPO_RULES[commit["repo"]]

    if rules["public"]:
        context = (
            f"This is about {rules['name']}. "
            f"Include the URL {rules['url']} (counts as exactly 23 characters toward the limit)."
        )
    else:
        context = "This is about a private tool in development. Do NOT name it. Do NOT include a URL."

    system = (
        "You write punchy single tweets for X (Twitter). Hard limit: 280 characters total. "
        "Every URL counts as exactly 23 characters regardless of actual length. "
        "No markdown, no bullet points, 1-2 hashtags max, single tweet only. "
        "Opener pattern: 'Just shipped: [thing]. [one-line why]. #buildinpublic'. "
        "Target under 260 characters. Output only the tweet text, nothing else."
    )

    text = call_llm(system, f"{context}\n\nCommit message: {commit['message']}\n\nWrite the tweet:")
    if len(text) > 280:
        text = text[:277].rsplit(" ", 1)[0] + "..."
    return text


# --- Posting ---

def post_linkedin(text):
    r = requests.post(
        "https://api.linkedin.com/v2/ugcPosts",
        headers={
            "Authorization": f"Bearer {LINKEDIN_TOKEN}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
        },
        json={
            "author": LINKEDIN_URN,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": text},
                    "shareMediaCategory": "NONE",
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        },
        timeout=30,
    )
    data = r.json()
    if "expired" in str(data) or "invalid_token" in str(data):
        raise RuntimeError(f"LinkedIn token invalid: {data}")
    post_id = data.get("id")
    if not post_id:
        raise RuntimeError(f"LinkedIn post failed: {data}")
    return post_id


def post_x(text):
    twitter = OAuth1Session(
        X_CONSUMER_KEY,
        client_secret=X_CONSUMER_SECRET,
        resource_owner_key=X_ACCESS_TOKEN,
        resource_owner_secret=X_ACCESS_TOKEN_SECRET,
    )

    r = twitter.post(
        "https://api.twitter.com/2/tweets",
        json={"text": text},
        timeout=30,
    )
    
    print(f"X response status: {r.status_code}")
    print(f"X response body: {r.text[:500]}")

    if r.status_code != 201:
        # 403 Forbidden might still happen if the app doesn't have Write permission
        print(f"Warning: X API returned status {r.status_code}")
        # Return None so the run still saves the SHA and doesn't retry the same content.
        return None

    try:
        data = r.json()
        tweet_id = data["data"]["id"]
        return f"https://x.com/i/web/status/{tweet_id}"
    except (KeyError, TypeError) as e:
        print(f"Warning: X accepted the request but returned unexpected format: {data}")
        return None


# --- Main ---

def main():
    posted_shas = load_posted_shas()
    commit = select_best_commit(posted_shas)
    if not commit:
        print("No new unposted commits in the last 48 hours. Skipping.")
        return

    print(f"Selected: [{commit['repo']}] {commit['message']}")

    li_text = generate_linkedin(commit)
    x_text  = generate_x(commit)

    print(f"\n--- LinkedIn ({len(li_text.split())} words) ---\n{li_text}")
    print(f"\n--- X ({len(x_text)} chars) ---\n{x_text}\n")

    li_id = post_linkedin(li_text)
    x_url = post_x(x_text)

    save_posted_sha(commit["sha"])

    print("--- Summary ---")
    print(f"LinkedIn post ID : {li_id}")
    print(f"X tweet URL      : {x_url if x_url else 'blocked (duplicate or restricted)'}")
    print(f"Topic            : [{commit['repo']}] {commit['message'][:60]}")


if __name__ == "__main__":
    main()
