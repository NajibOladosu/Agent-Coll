# Agents

Monorepo for Najib's autonomous agents. Each agent lives under `agents/<name>/` with its own code, config, and docs. Some agents run on GitHub Actions; others run locally and read state the cloud agents commit back to the repo.

## Agents

| Name           | Path                       | Purpose                                                                                  | Runs on                                |
|----------------|----------------------------|------------------------------------------------------------------------------------------|----------------------------------------|
| Quill          | `agents/quill/`            | Daily LinkedIn post from recent commits (synthesizes a 10-commit thread → 1 post + image) | GitHub Actions (`quill.yml`)            |
| Echo           | `agents/echo/`             | Mirrors Quill's latest LinkedIn post to X                                                 | Local launchd agent (`com.najib.echo`) |
| Reddit Advocate| `agents/reddit_advocate/`  | Scouts and replies to relevant Reddit threads                                              | GitHub Actions                          |

## Pipeline

```
GitHub Actions (Quill, 09:00 UTC)
    │
    ├─ posts to LinkedIn
    └─ commits agents/quill/last_post.{json,png} back to the repo
            │
            ▼
Local launchd (Echo, fires 10:30 local)
    ├─ waits for network
    ├─ polls Quill workflow until today's run = completed|success
    ├─ git pull
    └─ rewrites the LinkedIn post for X (≤280 chars), posts with the same image
```

This split lets the cloud half run unattended on a schedule while the local half handles the X poster cookie auth that doesn't survive on a CI runner.

## Layout

```
.
├── agents/
│   ├── quill/                    # LinkedIn poster (GitHub Actions)
│   │   ├── quill.py
│   │   ├── snippet_image.py
│   │   ├── fonts/
│   │   ├── posted_commits.txt    # consumed-SHA log (committed)
│   │   ├── last_post.json        # written by quill, consumed by echo (committed)
│   │   ├── last_post.png         # snippet image used in the LinkedIn post (committed)
│   │   └── README.md
│   ├── echo/                     # X mirror of quill (local launchd)
│   │   ├── echo.py
│   │   ├── run.sh                # network + workflow-completion gate
│   │   ├── com.najib.echo.plist  # launchd job definition
│   │   ├── install-launchd.sh    # install / uninstall / status / kick
│   │   ├── requirements.txt
│   │   └── README.md
│   └── reddit_advocate/
├── .github/workflows/            # one workflow per cloud agent
│   ├── quill.yml
│   ├── reddit-advocate-scout.yml
│   └── reddit-advocate-post.yml
└── README.md
```

## Adding a new agent

1. Create `agents/<name>/` with the agent's entrypoint and any state files.
2. If it runs in CI, add a workflow at `.github/workflows/<name>.yml`. Set `defaults.run.working-directory: agents/<name>` so relative paths resolve.
3. If it runs locally, ship a launchd plist + an install script alongside the agent (see `agents/echo/`).
4. Add required secrets under `Settings → Secrets → Actions` for cloud agents, or a `.env` entry at the repo root for local agents.
5. Document the agent in its own `agents/<name>/README.md` and add a row to the table above.

## Conventions

- Each agent owns its own state files (e.g. `posted_commits.txt`, `posted_x.txt`). Never share state across agents.
- Cloud agents that need to feed local agents commit their hand-off artifacts back to the repo (see `agents/quill/last_post.{json,png}`).
- Workflows that commit state back must scope `git add` to the agent's own files.
- Secrets and env vars are namespaced by agent when collisions are possible (e.g. `QUILL_LINKEDIN_TOKEN`).
- All local-only state — venvs, cookies, logs, dedupe lists — is gitignored. See `.gitignore`.
