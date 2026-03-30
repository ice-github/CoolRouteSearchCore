# AGENTS.md

## Repo Overview
- This repository downloads JAXA G-Portal GCOM-C data from Python.
- The main local entrypoint is `uv run python main.py`.
- Python dependency management uses `uv`.
- TAKT project configuration lives in `.takt/` and is repository-local only.
- Browser automation runs in Docker with Playwright rather than Selenium.

## Requirements
- `node`
- `npm`
- `uv`
- `docker`
- `TAKT_OPENAI_API_KEY`
- `GPORTAL_USER`
- `GPORTAL_PASS`

## Standard Commands
- Install dependencies:
  - `uv sync --group dev`
  - `npm install`
- Run the example downloader:
  - `uv run python main.py`
- Run tests:
  - `uv run pytest`
- Run TAKT interactively:
  - `npm run takt`
- Execute queued TAKT tasks in a worktree:
  - `npm run takt:run`

## Automation Notes
- Host-side orchestration lives in `gcom.py`.
- Container-side browser automation lives in `playwright/download_gportal.py`.
- The host writes a temporary job file into `workspace/` and mounts it into the container.
- Downloads are written into `download/`.
- GitHub Actions workflows live in `.github/workflows/`.
- TAKT state such as task queues and run logs should stay under `.takt/` and follow `.takt/.gitignore`.

## G-Portal Assumptions
- Login URL: `https://gportal.jaxa.jp/gpr/auth?`
- Username selector: `#auth_account`
- Password selector: `#auth_password`
- Submit selector: `#auth_login_submit`
- Success condition: page title is `G-PortalTop`

## Change Guidelines
- Keep credentials in environment variables only.
- Preserve `GcomDownloader` public behavior unless callers are updated in the same change.
- Keep Docker invocation compatible with both local shells and GitHub Actions runners.
- Prefer TAKT's queued task flow and `takt run`, which executes tasks in a `git worktree`.
- If you are not using TAKT for a change, perform work on a `git worktree`, not directly on the main working tree.
- Merge a worktree back only after the agent has responded correctly to the prompt and the requested work has been validated.
- Do not use `~/.takt`; keep TAKT configuration inside this repository.
- `Execute now` in TAKT edits the current working tree directly, so treat it as non-standard and avoid it for normal implementation work.
- Do not create or edit files outside this repository unless the user explicitly asks for it.
