#!/usr/bin/env python3
import base64
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


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


_load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

import requests  # noqa: E402

from snippet_image import render_snippet  # noqa: E402

LINKEDIN_TOKEN = os.environ["LINKEDIN_TOKEN"]
LINKEDIN_URN   = "urn:li:person:G82eBN-mpx"
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GITHUB_TOKEN   = os.environ.get("GITHUB_TOKEN")
POSTED_FILE    = "posted_commits.txt"
LAST_POST_JSON = "last_post.json"
LAST_POST_PNG  = "last_post.png"

CODE_EXTENSIONS = {
    "py", "ts", "tsx", "js", "jsx", "go", "rs", "rb", "java", "kt",
    "c", "cpp", "h", "hpp", "cs", "swift", "scala", "php", "lua",
    "sql", "sh", "bash", "zsh", "html", "css", "scss", "vue", "svelte",
}
SKIP_FILE_PATTERNS = (
    "test", "spec", "mock", "fixture", "lock", ".min.",
    "node_modules", "dist/", "build/", ".snap",
)

EXT_TO_LANG = {
    "py": "python", "ts": "typescript", "tsx": "tsx", "js": "javascript",
    "jsx": "jsx", "go": "go", "rs": "rust", "rb": "ruby", "java": "java",
    "kt": "kotlin", "c": "c", "cpp": "cpp", "h": "c", "hpp": "cpp",
    "cs": "csharp", "swift": "swift", "scala": "scala", "php": "php",
    "lua": "lua", "sql": "sql", "sh": "bash", "bash": "bash", "zsh": "bash",
    "html": "html", "css": "css", "scss": "scss", "vue": "html", "svelte": "html",
}

MAX_SNIPPET_LINES = 22

GEMINI_MODELS  = ["gemini-2.5-flash-lite", "gemini-2.5-flash"]

REPOS = ["Velluma", "AURA", "ApplyOS"]

REPO_RULES = {
    "Velluma": {"public": False, "refer_as": "a tool I'm building", "url": None},
    "AURA":    {"public": True,  "name": "AURA",    "url": "auratriage.vercel.app"},
    "ApplyOS": {"public": True,  "name": "ApplyOS", "url": "applyos.io"},
}

SKIP_PREFIXES     = ("chore", "style", "docs", "merge", "bump", "wip")
PRIORITY_PREFIXES = ["feat", "fix", "refactor", "perf"]


def load_posted_shas():
    if not os.path.exists(POSTED_FILE):
        return set()
    with open(POSTED_FILE) as f:
        return {line.strip() for line in f if line.strip()}


def save_posted_sha(sha):
    with open(POSTED_FILE, "a") as f:
        f.write(sha + "\n")


def save_last_post(commit, post_text, alt, snippet_path, png_bytes, li_post_id):
    """Persist the latest post artifacts so downstream agents (e.g. Echo for X)
    can mirror it without rerunning the LLM pipeline."""
    payload = {
        "sha": commit["sha"],
        "repo": commit["repo"],
        "message": commit["message"],
        "post": post_text,
        "alt": alt or "",
        "snippet_path": snippet_path or "",
        "linkedin_post_id": li_post_id,
        "image": bool(png_bytes),
    }
    Path(LAST_POST_JSON).write_text(json.dumps(payload, indent=2) + "\n")
    if png_bytes:
        Path(LAST_POST_PNG).write_bytes(png_bytes)
    elif Path(LAST_POST_PNG).exists():
        # Stale image from a previous run would mismatch this post — drop it.
        Path(LAST_POST_PNG).unlink()


def _gh_headers():
    h = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return h


def fetch_commits(repo):
    url = f"https://api.github.com/repos/NajibOladosu/{repo}/commits?per_page=20"
    r = requests.get(url, headers=_gh_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def fetch_commit_detail(repo, sha):
    url = f"https://api.github.com/repos/NajibOladosu/{repo}/commits/{sha}"
    r = requests.get(url, headers=_gh_headers(), timeout=15)
    r.raise_for_status()
    return r.json()


def fetch_file_content(repo, path, ref):
    url = f"https://api.github.com/repos/NajibOladosu/{repo}/contents/{path}?ref={ref}"
    r = requests.get(url, headers=_gh_headers(), timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get("encoding") != "base64":
        raise RuntimeError(f"Unexpected encoding for {path}: {data.get('encoding')}")
    return base64.b64decode(data["content"]).decode("utf-8", errors="replace")


def relevant_changed_files(commit_detail):
    """Return list of {path, patch, ext, lang, changes} for code files in commit."""
    out = []
    for f in commit_detail.get("files", []):
        path = f.get("filename", "")
        if not path:
            continue
        low = path.lower()
        if any(p in low for p in SKIP_FILE_PATTERNS):
            continue
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        if ext not in CODE_EXTENSIONS:
            continue
        if f.get("status") == "removed":
            continue
        out.append({
            "path": path,
            "patch": f.get("patch", "") or "",
            "ext": ext,
            "lang": EXT_TO_LANG.get(ext, ext),
            "changes": f.get("changes", 0),
        })
    out.sort(key=lambda x: -x["changes"])
    return out


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


def call_llm(system_prompt, user_prompt, *, json_mode=False, max_tokens=900):
    gen_config = {"maxOutputTokens": max_tokens, "temperature": 0.7}
    if json_mode:
        gen_config["responseMimeType"] = "application/json"

    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"parts": [{"text": user_prompt}]}],
        "generationConfig": gen_config,
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


def _format_files_for_prompt(files, max_patch_chars=2400):
    """Compact patch listing for the LLM."""
    out = []
    for f in files[:6]:  # cap at 6 files
        patch = f["patch"]
        if len(patch) > max_patch_chars:
            patch = patch[:max_patch_chars] + "\n... (truncated)"
        out.append(f"FILE: {f['path']} (lang={f['lang']})\n--- patch ---\n{patch}\n")
    return "\n".join(out)


def _extract_json(text):
    """Strip markdown fences, parse JSON."""
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return json.loads(s)


def generate_post_and_slice(commit, files):
    """Single LLM call: returns dict {post, file, start_line, end_line, language, alt}.

    The chosen slice corresponds to lines in the FINAL file (post-commit), so we can
    fetch that file at the commit SHA and slice by line numbers.
    """
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

    files_blob = _format_files_for_prompt(files)

    system = (
        "You write LinkedIn posts for Najib, a software builder, AND choose a code "
        "snippet that visually accompanies the post.\n\n"
        "POST VOICE: technical, reflective, grounded. Builder in public.\n"
        "POST STRUCTURE: (1) Hook — tension or problem, (2) Challenge — what was being "
        "built and why, (3) Decision or lesson — what was figured out, (4) Reader question "
        "— genuine reflection.\n"
        "POST RULES: first person, short paragraphs (1-3 lines), use → for lists never "
        "bullet points, no buzzwords ('excited to announce', 'game-changer', 'leveraging'), "
        "no emojis, no fabrication, 150-280 words.\n\n"
        "SNIPPET RULES:\n"
        "- Pick the single most representative contiguous block from ONE of the changed files.\n"
        "- The block MUST illustrate the specific decision or lesson the post talks about.\n"
        "- Block size: between 6 and "
        f"{MAX_SNIPPET_LINES} lines.\n"
        "- Use line numbers from the NEW (post-commit) file, derivable from the patch's "
        "@@ -a,b +c,d @@ headers — start_line/end_line are 1-indexed line numbers in "
        "the file at this commit.\n"
        "- Prefer function/class bodies, key conditionals, or schema definitions over "
        "imports/boilerplate.\n"
        "- Avoid blocks containing secrets, tokens, or API keys.\n\n"
        "OUTPUT: return ONLY a JSON object with these keys:\n"
        '{"post": string, "file": string, "start_line": integer, "end_line": integer, '
        '"language": string, "alt": string}\n'
        '"alt" is a one-sentence description of the snippet for accessibility.'
    )

    user = (
        f"{context}\n\n"
        f"Commit message: {commit['message']}\n\n"
        f"Changed files and patches:\n\n{files_blob}\n\n"
        "Return the JSON now."
    )

    raw = call_llm(system, user, json_mode=True, max_tokens=1200)
    return _extract_json(raw)


def _li_headers():
    return {
        "Authorization": f"Bearer {LINKEDIN_TOKEN}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
    }


def upload_image(image_bytes: bytes) -> str:
    """Register + upload image. Returns asset URN."""
    register = requests.post(
        "https://api.linkedin.com/v2/assets?action=registerUpload",
        headers=_li_headers(),
        json={
            "registerUploadRequest": {
                "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
                "owner": LINKEDIN_URN,
                "serviceRelationships": [
                    {
                        "relationshipType": "OWNER",
                        "identifier": "urn:li:userGeneratedContent",
                    }
                ],
            }
        },
        timeout=30,
    )
    data = register.json()
    if "expired" in str(data) or "invalid_token" in str(data):
        raise RuntimeError(f"LinkedIn token invalid: {data}")
    try:
        upload_url = data["value"]["uploadMechanism"][
            "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"
        ]["uploadUrl"]
        asset = data["value"]["asset"]
    except KeyError:
        raise RuntimeError(f"registerUpload failed: {data}")

    put = requests.put(
        upload_url,
        headers={"Authorization": f"Bearer {LINKEDIN_TOKEN}"},
        data=image_bytes,
        timeout=60,
    )
    if put.status_code not in (200, 201):
        raise RuntimeError(f"image PUT failed: {put.status_code} {put.text[:200]}")
    return asset


def post_linkedin(text, image_asset_urn=None, image_alt=None):
    if image_asset_urn:
        share_content = {
            "shareCommentary": {"text": text},
            "shareMediaCategory": "IMAGE",
            "media": [
                {
                    "status": "READY",
                    "description": {"text": image_alt or ""},
                    "media": image_asset_urn,
                    "title": {"text": image_alt or ""},
                }
            ],
        }
    else:
        share_content = {
            "shareCommentary": {"text": text},
            "shareMediaCategory": "NONE",
        }

    r = requests.post(
        "https://api.linkedin.com/v2/ugcPosts",
        headers=_li_headers(),
        json={
            "author": LINKEDIN_URN,
            "lifecycleState": "PUBLISHED",
            "specificContent": {"com.linkedin.ugc.ShareContent": share_content},
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


def slice_code(code: str, start_line: int, end_line: int):
    """1-indexed inclusive slice. Clamp to file bounds."""
    lines = code.splitlines()
    s = max(1, start_line)
    e = min(len(lines), end_line)
    if e < s:
        e = s
    return "\n".join(lines[s - 1:e]), s, e


def build_post_and_image(commit):
    """Returns (post_text, png_bytes_or_None, alt_text_or_None, snippet_path_or_None)."""
    try:
        detail = fetch_commit_detail(commit["repo"], commit["sha"])
    except Exception as e:
        print(f"Warning: could not fetch commit detail: {e}")
        return None, None, None, None

    files = relevant_changed_files(detail)
    if not files:
        print("No code files in commit — text-only post.")
        post = call_llm(
            "Write a short LinkedIn post in Najib's builder-in-public voice. "
            "First person, 150-280 words, no emojis, no buzzwords, end with a "
            "reader question. Output post text only.",
            f"Commit message: {commit['message']}",
            max_tokens=600,
        )
        return post, None, None, None

    try:
        result = generate_post_and_slice(commit, files)
    except Exception as e:
        print(f"Warning: structured generation failed ({e}); falling back to text-only.")
        return None, None, None, None

    post_text = result.get("post", "").strip()
    file_path = result.get("file", "").strip()
    start_line = int(result.get("start_line", 0) or 0)
    end_line = int(result.get("end_line", 0) or 0)
    language = result.get("language") or ""
    alt = result.get("alt") or f"Code from {commit['repo']}"

    if not (post_text and file_path and start_line and end_line):
        print("LLM returned incomplete result — text-only post.")
        return post_text or None, None, None, None

    if not any(f["path"] == file_path for f in files):
        print(f"LLM picked file '{file_path}' not in changed set — text-only post.")
        return post_text, None, None, None

    try:
        code = fetch_file_content(commit["repo"], file_path, commit["sha"])
    except Exception as e:
        print(f"Warning: could not fetch file {file_path}: {e}")
        return post_text, None, None, None

    snippet, s, e = slice_code(code, start_line, end_line)
    if not snippet.strip():
        print("Empty snippet — text-only post.")
        return post_text, None, None, None

    if not language:
        ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
        language = EXT_TO_LANG.get(ext, "")

    try:
        png = render_snippet(
            code=snippet,
            filename=file_path.split("/")[-1],
            language=language,
            start_line=s,
        )
    except Exception as ex:
        print(f"Warning: render failed: {ex}")
        return post_text, None, None, None

    return post_text, png, alt, file_path


def main():
    posted_shas = load_posted_shas()
    commit = select_best_commit(posted_shas)
    if not commit:
        print("No new unposted commits in the last week. Skipping.")
        return

    print(f"Selected: [{commit['repo']}] {commit['message']}")

    post_text, png, alt, snippet_path = build_post_and_image(commit)
    if not post_text:
        raise RuntimeError("Post generation failed.")

    print(f"\n--- LinkedIn ({len(post_text.split())} words) ---\n{post_text}\n")

    asset_urn = None
    if png:
        print(f"Rendered snippet from {snippet_path} ({len(png)} bytes)")
        try:
            asset_urn = upload_image(png)
            print(f"Uploaded image asset: {asset_urn}")
        except Exception as e:
            print(f"Warning: image upload failed, posting text-only: {e}")
            asset_urn = None

    li_id = post_linkedin(post_text, image_asset_urn=asset_urn, image_alt=alt)

    save_posted_sha(commit["sha"])
    save_last_post(commit, post_text, alt, snippet_path, png, li_id)

    print("--- Summary ---")
    print(f"LinkedIn post ID : {li_id}")
    print(f"Topic            : [{commit['repo']}] {commit['message'][:60]}")
    print(f"Snippet          : {snippet_path or 'none'}")


if __name__ == "__main__":
    main()
