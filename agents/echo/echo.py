#!/usr/bin/env python3
"""Echo: post the X (Twitter) version of Quill's latest LinkedIn post.

Reads agents/quill/last_post.json (committed by the Quill workflow),
asks Gemini to rewrite the post for X, and posts via twikit using
cookie auth (no official X API key).
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent


def _load_dotenv(path):
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv(REPO / ".env")

import requests  # noqa: E402
from twikit import Client  # noqa: E402

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GEMINI_MODELS = ["gemini-2.5-flash-lite", "gemini-2.5-flash"]

QUILL_DIR      = REPO / "agents" / "quill"
LAST_POST_JSON = QUILL_DIR / "last_post.json"
LAST_POST_PNG  = QUILL_DIR / "last_post.png"

POSTED_FILE   = ROOT / "posted_x.txt"
COOKIES_FILE  = ROOT / "twitter_cookies.json"

X_HARD_LIMIT  = 280


def load_posted():
    if not POSTED_FILE.exists():
        return set()
    return {l.strip() for l in POSTED_FILE.read_text().splitlines() if l.strip()}


def save_posted(sha):
    with POSTED_FILE.open("a") as f:
        f.write(sha + "\n")


def call_llm(system, user, max_tokens=400):
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"parts": [{"text": user}]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.7},
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
                    time.sleep(8 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            except Exception as e:
                last_err = e
                if attempt < 2:
                    time.sleep(6)
        print(f"{model} failed, trying next", file=sys.stderr)
    raise RuntimeError(f"All Gemini models failed: {last_err}")


def generate_tweet(linkedin_text, repo, message):
    system = (
        "You rewrite a LinkedIn post into a single X (Twitter) post in Najib's "
        "builder-in-public voice.\n"
        "RULES:\n"
        f"- Hard maximum {X_HARD_LIMIT} characters. Aim for 240-270.\n"
        "- First person. Technical, grounded, reflective.\n"
        "- Keep ONE concrete detail or insight from the source post.\n"
        "- No hashtags. No emojis. No buzzwords.\n"
        "- No 'thread', '1/', '(1/n)', 'continued', 'cont.'\n"
        "- No surrounding quotes. No preamble. Output ONLY the tweet text."
    )
    user = (
        f"Repo: {repo}\nCommit: {message}\n\n"
        f"LinkedIn post:\n{linkedin_text}\n\n"
        "Write the tweet now."
    )
    text = call_llm(system, user).strip()
    if (text.startswith('"') and text.endswith('"')) or (
        text.startswith("'") and text.endswith("'")
    ):
        text = text[1:-1].strip()
    if len(text) > X_HARD_LIMIT:
        cut = text[: X_HARD_LIMIT - 1]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        text = cut + "…"
    return text


async def post_tweet(text, image_path):
    cookies = json.loads(COOKIES_FILE.read_text())
    client = Client("en-US")
    client.set_cookies({"auth_token": cookies["auth_token"], "ct0": cookies["ct0"]})

    media_ids = []
    if image_path and image_path.exists():
        media_id = await client.upload_media(str(image_path))
        media_ids.append(media_id)

    tweet = await client.create_tweet(
        text=text, media_ids=media_ids if media_ids else None
    )
    return getattr(tweet, "id", None) or getattr(tweet, "rest_id", None)


async def amain():
    if not LAST_POST_JSON.exists():
        print("No agents/quill/last_post.json found. Nothing to post.")
        return

    data = json.loads(LAST_POST_JSON.read_text())
    sha = data.get("sha")
    if not sha:
        print("last_post.json missing 'sha'. Aborting.")
        return

    posted = load_posted()
    if sha in posted:
        print(f"Already posted X version of {sha}. Skipping.")
        return

    repo          = data.get("repo", "")
    message       = data.get("message", "")
    linkedin_text = data.get("post", "")
    has_image     = bool(data.get("image"))

    if not linkedin_text:
        print("last_post.json has empty 'post'. Aborting.")
        return

    image_path = LAST_POST_PNG if has_image and LAST_POST_PNG.exists() else None

    tweet_text = generate_tweet(linkedin_text, repo, message)
    print(f"--- Tweet ({len(tweet_text)} chars) ---\n{tweet_text}\n")

    tweet_id = await post_tweet(tweet_text, image_path)
    if tweet_id:
        print(f"Posted: https://x.com/i/web/status/{tweet_id}")
    else:
        print("Posted (no id returned).")

    save_posted(sha)
    print("--- Summary ---")
    print(f"Source SHA   : {sha}")
    print(f"Repo         : {repo}")
    print(f"With image   : {bool(image_path)}")


if __name__ == "__main__":
    asyncio.run(amain())
