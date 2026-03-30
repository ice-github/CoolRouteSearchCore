# AGENTS.md

## Repo Overview
- This repository downloads JAXA G-Portal GCOM-C data from Python.
- The main local entrypoint is `uv run python main.py`.
- Python dependency management uses `uv`.
- Browser automation runs in Docker with Playwright rather than Selenium.

## Requirements
- `uv`
- `docker`
- `GPORTAL_USER`
- `GPORTAL_PASS`

## Standard Commands
- Install dependencies:
  - `uv sync --group dev`
- Run the example downloader:
  - `uv run python main.py`
- Run tests:
  - `uv run pytest`

## Automation Notes
- Host-side orchestration lives in `gcom.py`.
- Container-side browser automation lives in `playwright/download_gportal.py`.
- Playwright and Chromium run in Docker only; do not rely on host-installed browser binaries for automation or screenshots.
- The host writes a temporary job file into `workspace/` and mounts it into the container.
- Downloads are written into `download/`.
- GitHub Actions workflows live in `.github/workflows/`.
- For LST analysis visual checks, the human-facing comparison image must compare the 2D preview against the 3D sampling points rendered as sphere-like markers.
- Do not use the 3D surface as the right-hand comparison image for that visual check.
- When rendering that comparison from 3D to 2D, use a camera perpendicular to the XY plane with orthographic projection.
- Render the final 3D surface output only after the point-cloud comparison passes.

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
- Do not create or edit files outside this repository unless the user explicitly asks for it.
- Before starting implementation work, create and use a dedicated worktree for the task.
- When the prompt requirements are satisfied, sync the finished changes back to `main`.
