import os
import socket
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlparse

import requests
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


def _log(message: str) -> None:
    print(message, flush=True)


class CSWWrapper:
    def __init__(self) -> None:
        self._base_url = "https://gportal.jaxa.jp/csw/csw"

    def _create_query_url(self, dataset_id: str, start_time: str, end_time: str, bbox: str) -> str:
        params = {
            "service": "CSW",
            "version": "3.0.0",
            "request": "GetRecords",
            "outputFormat": "application/json",
            "datasetId": dataset_id,
            "startTime": start_time,
            "endTime": end_time,
            "bbox": bbox,
        }
        return f"{self._base_url}?{urlencode(params)}"

    def _fetch_data(self, url: str) -> dict:
        response = requests.get(url, timeout=120)
        response.raise_for_status()
        try:
            return response.json()
        except ValueError as error:
            body = response.text.strip()
            snippet = body[:500]
            raise RuntimeError(
                "CSW request did not return JSON "
                f"(status={response.status_code}, content_type={response.headers.get('content-type', 'unknown')}, url={url!r}, body={snippet!r})"
            ) from error

    def _get_string_from_date(self, utc_date: datetime) -> str:
        return utc_date.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def _split_intervals(self, start: datetime, end: datetime, days: int) -> list[tuple[datetime, datetime]]:
        intervals = []
        delta = timedelta(days=days)
        current = start
        while current < end:
            next_end = min(current + delta, end)
            intervals.append((current, next_end))
            current = next_end
        return intervals

    def get_hdf5_urls(self, dataset_id: str, utc_start: datetime, utc_end: datetime, bbox: list[float]) -> list[str]:
        if len(bbox) != 4:
            raise ValueError("bbox must be [min_lon, min_lat, max_lon, max_lat]")

        intervals = self._split_intervals(utc_start, utc_end, 3)
        h5_urls: list[str] = []
        _log(
            f"[csw] querying dataset={dataset_id} intervals={len(intervals)} "
            f"start={utc_start.isoformat()} end={utc_end.isoformat()} bbox={bbox}"
        )

        for index, (start, end) in enumerate(intervals, start=1):
            _log(
                f"[csw] interval {index}/{len(intervals)} "
                f"start={start.isoformat()} end={end.isoformat()}"
            )
            url = self._create_query_url(
                dataset_id,
                self._get_string_from_date(start),
                self._get_string_from_date(end),
                ",".join(str(v) for v in bbox),
            )
            data = self._fetch_data(url)
            interval_urls = 0
            for feature in data.get("features", []):
                product = feature.get("properties", {}).get("product", {})
                filename = product.get("fileName")
                if filename:
                    h5_urls.append(filename)
                    interval_urls += 1
            _log(f"[csw] interval {index}/{len(intervals)} yielded {interval_urls} URL(s)")

        _log(f"[csw] found {len(h5_urls)} URL(s) total")
        return h5_urls


class JPortalLogin:
    def __init__(self) -> None:
        self.login_url = "https://gportal.jaxa.jp/gpr/auth?"
        self.username_selector = "#auth_account"
        self.password_selector = "#auth_password"
        self.submit_selector = "#auth_login_submit"
        self.success_title = "G-PortalTop"


@dataclass(frozen=True)
class _PlaywrightServer:
    container_name: str
    ws_endpoint: str


class GcomDownloader:
    # Keep the host client package, server image, and Docker base image aligned.
    _playwright_version = "1.58.0"
    _playwright_base_image = f"mcr.microsoft.com/playwright:v{_playwright_version}-noble"
    _server_image_name = f"coolroutesearchcore-playwright-server:v{_playwright_version}"
    _server_start_timeout_seconds = 30.0
    _server_shm_size = "512m"

    def __init__(self, download_dir: str, workspace_dir: str, username: str, password: str) -> None:
        self._repo_root = Path(__file__).resolve().parent
        self._download_dir = self._repo_root / download_dir
        self._workspace_dir = self._repo_root / workspace_dir
        self._download_dir.mkdir(parents=True, exist_ok=True)
        self._workspace_dir.mkdir(parents=True, exist_ok=True)

        self._username = username
        self._password = password
        self._login = JPortalLogin()

    def _require_credentials(self) -> None:
        if not self._username or not self._password:
            raise ValueError("G-Portal credentials are required. Set GPORTAL_USER and GPORTAL_PASS.")

    def _ensure_docker(self) -> None:
        _log("[download] checking docker availability")
        result = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError("docker is required to download G-Portal files")

    def _get_filename_from_url(self, url: str) -> str:
        filename = os.path.basename(urlparse(url).path)
        if not filename:
            raise ValueError(f"could not determine filename from url: {url}")
        return filename

    def _allocate_server_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
            handle.bind(("127.0.0.1", 0))
            return int(handle.getsockname()[1])

    @classmethod
    def _server_dockerfile_path(cls, repo_root: Path) -> Path:
        return repo_root / "docker" / "playwright-server" / "Dockerfile"

    @classmethod
    def _server_build_context_path(cls, repo_root: Path) -> Path:
        return repo_root / "docker" / "playwright-server"

    @classmethod
    def _build_playwright_server_image_command(cls, repo_root: Path) -> list[str]:
        return [
            "docker",
            "build",
            "--pull",
            "--build-arg",
            f"PLAYWRIGHT_BASE_IMAGE={cls._playwright_base_image}",
            "--build-arg",
            f"PLAYWRIGHT_VERSION={cls._playwright_version}",
            "-t",
            cls._server_image_name,
            "-f",
            str(cls._server_dockerfile_path(repo_root)),
            str(cls._server_build_context_path(repo_root)),
        ]

    @classmethod
    def build_playwright_server_image(cls, repo_root: Path | None = None) -> None:
        resolved_repo_root = repo_root or Path(__file__).resolve().parent
        _log(
            f"[download] building Playwright server image {cls._server_image_name} "
            f"from public base {cls._playwright_base_image}"
        )
        result = subprocess.run(
            cls._build_playwright_server_image_command(resolved_repo_root),
            cwd=resolved_repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            _log(f"[download] Playwright server image {cls._server_image_name} is ready")
            return

        combined_output = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part).strip()
        if (
            cls._playwright_base_image in combined_output
            and (
                "failed to resolve reference" in combined_output
                or "failed to resolve source metadata" in combined_output
                or "load metadata for" in combined_output
            )
        ):
            raise RuntimeError(
                f"Failed to pull public Playwright base image {cls._playwright_base_image}: {combined_output}"
            )
        raise RuntimeError(
            f"Failed to build Playwright server image {cls._server_image_name}: "
            f"{combined_output or 'unknown docker build error'}"
        )

    def _ensure_playwright_server_image(self) -> None:
        self.build_playwright_server_image(self._repo_root)

    def _build_playwright_server_command(self, port: int, container_name: str) -> list[str]:
        return [
            "docker",
            "run",
            "-d",
            "--rm",
            "--init",
            "--shm-size",
            self._server_shm_size,
            "--name",
            container_name,
            "-p",
            f"{port}:{port}",
            "--workdir",
            "/home/pwuser",
            "--user",
            "pwuser",
            self._server_image_name,
            "playwright",
            "run-server",
            "--port",
            str(port),
            "--host",
            "0.0.0.0",
        ]

    def _docker_logs(self, container_name: str) -> str:
        result = subprocess.run(
            ["docker", "logs", container_name],
            cwd=self._repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        output = (result.stdout or "") + (result.stderr or "")
        return output.strip()

    def _stop_playwright_server(self, server: _PlaywrightServer) -> None:
        _log(f"[download] stopping Playwright server container {server.container_name}")
        subprocess.run(
            ["docker", "rm", "-f", server.container_name],
            cwd=self._repo_root,
            capture_output=True,
            text=True,
            check=False,
        )

    def _wait_for_playwright_server(self, server: _PlaywrightServer) -> None:
        deadline = time.monotonic() + self._server_start_timeout_seconds
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.connect(server.ws_endpoint)
                    browser.close()
                _log(f"[download] Playwright server is ready at {server.ws_endpoint}")
                return
            except PlaywrightError as error:
                last_error = error
                time.sleep(0.5)

        logs = self._docker_logs(server.container_name)
        message = f"Playwright server did not become ready at {server.ws_endpoint}"
        if logs:
            message = f"{message}\n[docker logs]\n{logs}"
        raise RuntimeError(message) from last_error

    def _start_playwright_server(self) -> _PlaywrightServer:
        self._ensure_playwright_server_image()
        port = self._allocate_server_port()
        container_name = f"gportal-playwright-{uuid.uuid4().hex[:8]}"
        _log(
            f"[download] starting Playwright server container {container_name} "
            f"from {self._server_image_name} on port {port}"
        )
        result = subprocess.run(
            self._build_playwright_server_command(port, container_name),
            cwd=self._repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            raise RuntimeError(
                f"Failed to start Playwright server container {container_name}: {stderr or 'unknown docker run error'}"
            )
        server = _PlaywrightServer(container_name=container_name, ws_endpoint=f"ws://127.0.0.1:{port}/")
        try:
            self._wait_for_playwright_server(server)
        except Exception:
            self._stop_playwright_server(server)
            raise
        return server

    def _login_to_gportal(self, page) -> None:
        _log("[download] opening G-Portal login page")
        page.goto(self._login.login_url, wait_until="domcontentloaded")
        page.locator(self._login.username_selector).fill(self._username)
        page.locator(self._login.password_selector).fill(self._password)

        _log("[download] submitting G-Portal credentials")
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
            {"user": self._username, "password": self._password},
        )
        if auth_result.get("status") != 1:
            raise RuntimeError(f"G-Portal login failed: {auth_result}")

        _log("[download] waiting for authenticated G-Portal session")
        page.goto("https://gportal.jaxa.jp/gpr/index", wait_until="domcontentloaded")
        try:
            page.wait_for_function(
                """expectedTitle => document.title === expectedTitle""",
                arg=self._login.success_title,
                timeout=15000,
            )
        except PlaywrightTimeoutError:
            page.wait_for_load_state("networkidle", timeout=15000)
            if page.title() != self._login.success_title:
                raise RuntimeError(f"G-Portal login failed after auth: current title is {page.title()!r}")
        _log("[download] G-Portal login complete")

    def _download_missing_urls(self, urls: list[str]) -> None:
        server = self._start_playwright_server()
        try:
            _log(f"[download] connecting Playwright client to {server.ws_endpoint}")
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect(server.ws_endpoint)
                context = browser.new_context(accept_downloads=True)
                page = context.new_page()
                try:
                    self._login_to_gportal(page)
                    total = len(urls)
                    for index, url in enumerate(urls, start=1):
                        target_path = self._download_dir / self._get_filename_from_url(url)
                        if target_path.exists():
                            _log(f"[download] skip existing {index}/{total}: {target_path}")
                            continue

                        _log(f"[download] downloading {index}/{total}: {url}")
                        try:
                            with page.expect_download(timeout=120000) as download_info:
                                try:
                                    page.goto(url, wait_until="commit")
                                except PlaywrightError as error:
                                    if "Download is starting" not in str(error):
                                        raise
                        except Exception as error:
                            raise RuntimeError(f"failed to start download for {url}") from error

                        download = download_info.value
                        download.save_as(str(target_path))
                        _log(f"[download] saved {index}/{total}: {target_path}")
                finally:
                    context.close()
                    browser.close()
        except Exception as error:
            logs = self._docker_logs(server.container_name)
            message = f"Playwright download failed during host-controlled browser automation: {error}"
            if logs:
                message = f"{message}\n[docker logs]\n{logs}"
            raise RuntimeError(message) from error
        finally:
            self._stop_playwright_server(server)

    def get_downloaded_file_paths(self, urls: list[str]) -> list[str]:
        self._require_credentials()
        self._ensure_docker()

        resolved_paths: list[str] = []
        missing_urls: list[str] = []
        for url in urls:
            path = self._download_dir / self._get_filename_from_url(url)
            resolved_paths.append(str(path))
            if not path.exists():
                missing_urls.append(url)

        _log(
            f"[download] resolved {len(resolved_paths)} file path(s); "
            f"{len(missing_urls)} missing, {len(resolved_paths) - len(missing_urls)} already present"
        )

        if not missing_urls:
            _log("[download] all requested files already exist; skipping Playwright download")
            return resolved_paths

        _log(f"[download] downloading {len(missing_urls)} missing file(s)")
        self._download_missing_urls(missing_urls)

        for path in resolved_paths:
            if not os.path.exists(path):
                raise FileNotFoundError(f"downloaded file not found: {path}")

        _log(f"[download] verified {len(resolved_paths)} downloaded file(s)")
        return resolved_paths
