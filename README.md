# Agents

Monorepo for Najib's autonomous agents. Each agent lives under `agents/<name>/` with its own code, config, and docs. Workflows under `.github/workflows/` schedule and run them.

## Agents

| Name | Path | Purpose |
|---|---|---|
| Quill | `agents/quill/` | Daily LinkedIn post from recent commits (runs in GitHub Actions) |
| Echo  | `agents/echo/`  | Mirrors Quill's latest LinkedIn post to X (runs locally via cron) |

## Layout

```
.
├── agents/
│   ├── quill/              # LinkedIn poster (GitHub Actions)
│   │   ├── quill.py
│   │   ├── posted_commits.txt
│   │   ├── last_post.{json,png}   # written by quill, consumed by echo
│   │   └── README.md
│   └── echo/               # X mirror of quill (local cron)
│       ├── echo.py
│       ├── run.sh
│       └── README.md
├── .github/workflows/      # one workflow per agent
│   └── quill.yml
└── README.md
```

## Adding a new agent

1. Create `agents/<name>/` with the agent's entrypoint and any state files.
2. Add a workflow at `.github/workflows/<name>.yml`. Set `defaults.run.working-directory: agents/<name>` so relative paths resolve.
3. Add required secrets under `Settings → Secrets → Actions`.
4. Document the agent in its own `agents/<name>/README.md` and add a row to the table above.

## Conventions

- Each agent owns its own state files (e.g. `posted_commits.txt`). Never share state across agents.
- Workflows that commit state back to the repo must scope `git add` to the agent's own files.
- Secrets are global to the repo; namespace them with the agent prefix when collisions are possible (e.g. `QUILL_LINKEDIN_TOKEN`).
