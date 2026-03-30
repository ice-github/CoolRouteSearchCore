import json
import os
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlparse

import requests


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
        return response.json()

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

        for start, end in intervals:
            url = self._create_query_url(
                dataset_id,
                self._get_string_from_date(start),
                self._get_string_from_date(end),
                ",".join(str(v) for v in bbox),
            )
            data = self._fetch_data(url)
            for feature in data.get("features", []):
                product = feature.get("properties", {}).get("product", {})
                filename = product.get("fileName")
                if filename:
                    h5_urls.append(filename)

        return h5_urls


class JPortalLogin:
    def __init__(self) -> None:
        self.login_url = "https://gportal.jaxa.jp/gpr/auth?"
        self.username_selector = "#auth_account"
        self.password_selector = "#auth_password"
        self.submit_selector = "#auth_login_submit"
        self.success_title = "G-PortalTop"


class GcomDownloader:
    _image_name = "mcr.microsoft.com/playwright/python:v1.55.0-noble"

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

    def _create_job_file(self, urls: list[str]) -> Path:
        payload = {
            "login": {
                "url": self._login.login_url,
                "username_selector": self._login.username_selector,
                "password_selector": self._login.password_selector,
                "submit_selector": self._login.submit_selector,
                "success_title": self._login.success_title,
            },
            "download_dir": "/downloads",
            "urls": urls,
        }
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            prefix="gportal_job_",
            dir=self._workspace_dir,
            delete=False,
        ) as handle:
            json.dump(payload, handle)
            return Path(handle.name)

    def _run_playwright_download(self, job_file: Path) -> None:
        command = [
            "docker",
            "run",
            "--rm",
            "-e",
            f"GPORTAL_USER={self._username}",
            "-e",
            f"GPORTAL_PASS={self._password}",
            "-e",
            f"JOB_PATH=/jobs/{job_file.name}",
            "-v",
            f"{self._download_dir.resolve()}:/downloads",
            "-v",
            f"{self._workspace_dir.resolve()}:/jobs",
            "-v",
            f"{(self._repo_root / 'playwright').resolve()}:/work/playwright:ro",
            self._image_name,
            "python3",
            "/work/playwright/download_gportal.py",
        ]
        result = subprocess.run(command, cwd=self._repo_root, check=False)
        if result.returncode != 0:
            raise RuntimeError("Playwright download container failed")

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

        if not missing_urls:
            return resolved_paths

        job_file = self._create_job_file(missing_urls)
        try:
            self._run_playwright_download(job_file)
        finally:
            if job_file.exists():
                job_file.unlink()

        for path in resolved_paths:
            if not os.path.exists(path):
                raise FileNotFoundError(f"downloaded file not found: {path}")

        return resolved_paths
