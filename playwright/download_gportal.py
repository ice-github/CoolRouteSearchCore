import json
import os
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


def get_filename_from_url(url: str) -> str:
    filename = Path(urlparse(url).path).name
    if not filename:
        raise ValueError(f"could not determine filename from url: {url}")
    return filename


def load_job() -> dict:
    job_path = os.environ.get("JOB_PATH")
    if not job_path:
        raise RuntimeError("JOB_PATH is required")
    with open(job_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def login(page, job: dict, username: str, password: str) -> None:
    login_config = job["login"]
    print("[playwright] opening login page", flush=True)
    page.goto(login_config["url"], wait_until="domcontentloaded")
    page.locator(login_config["username_selector"]).fill(username)
    page.locator(login_config["password_selector"]).fill(password)
    print("[playwright] submitting credentials", flush=True)
    auth_result = page.evaluate(
        """async ({user, password}) => {
            const response = await fetch('/gpr/auth/authenticate.json', {
                method: 'POST',
                headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'},
                body: new URLSearchParams({
                    account: user,
                    password,
                    fuel_csrf_token: window.fuel_csrf_token(),
                }),
                credentials: 'same-origin',
            });
            return await response.json();
        }""",
        {"user": username, "password": password},
    )
    if auth_result.get("status") != 1:
        raise RuntimeError(f"login failed: {auth_result}")

    print("[playwright] waiting for authenticated session", flush=True)
    page.goto("https://gportal.jaxa.jp/gpr/index", wait_until="domcontentloaded")
    try:
        page.wait_for_function(
            """expectedTitle => document.title === expectedTitle""",
            arg=login_config["success_title"],
            timeout=15000,
        )
    except PlaywrightTimeoutError:
        page.wait_for_load_state("networkidle", timeout=15000)
        if page.title() != login_config["success_title"]:
            raise RuntimeError(f"login failed after auth: current title is {page.title()!r}")
    print("[playwright] login complete", flush=True)


def download_files(page, job: dict) -> None:
    download_dir = Path(job["download_dir"])
    download_dir.mkdir(parents=True, exist_ok=True)
    total = len(job["urls"])
    print(f"[playwright] processing {total} URL(s)", flush=True)

    for index, url in enumerate(job["urls"], start=1):
        target_path = download_dir / get_filename_from_url(url)
        if target_path.exists():
            print(f"[playwright] skip existing {index}/{total}: {target_path}", flush=True)
            continue

        print(f"[playwright] downloading {index}/{total}: {url}", flush=True)
        with page.expect_download(timeout=120000) as download_info:
            try:
                page.goto(url, wait_until="commit")
            except PlaywrightError as exc:
                if "Download is starting" not in str(exc):
                    raise

        download = download_info.value
        download.save_as(str(target_path))
        print(f"[playwright] saved {index}/{total}: {target_path}", flush=True)

    print("[playwright] download loop complete", flush=True)


def main() -> None:
    username = os.environ.get("GPORTAL_USER")
    password = os.environ.get("GPORTAL_PASS")
    if not username or not password:
        raise RuntimeError("GPORTAL_USER and GPORTAL_PASS are required")

    job = load_job()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        login(page, job, username, password)
        download_files(page, job)

        context.close()
        browser.close()


if __name__ == "__main__":
    main()
