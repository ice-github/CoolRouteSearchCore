from datetime import datetime
from pathlib import Path

import pytest

import gcom
from gcom import CSWWrapper, GcomDownloader, _PlaywrightServer


def test_split_intervals_chunks_by_day_window() -> None:
    wrapper = CSWWrapper()

    intervals = wrapper._split_intervals(
        datetime(2024, 1, 1, 0, 0, 0),
        datetime(2024, 1, 8, 0, 0, 0),
        3,
    )

    assert intervals == [
        (datetime(2024, 1, 1, 0, 0, 0), datetime(2024, 1, 4, 0, 0, 0)),
        (datetime(2024, 1, 4, 0, 0, 0), datetime(2024, 1, 7, 0, 0, 0)),
        (datetime(2024, 1, 7, 0, 0, 0), datetime(2024, 1, 8, 0, 0, 0)),
    ]


def test_get_string_from_date_formats_milliseconds() -> None:
    wrapper = CSWWrapper()

    assert wrapper._get_string_from_date(datetime(2024, 1, 2, 3, 4, 5, 678900)) == "2024-01-02T03:04:05.678Z"


def test_fetch_data_raises_clear_error_for_non_json_response(monkeypatch: pytest.MonkeyPatch) -> None:
    wrapper = CSWWrapper()

    class FakeResponse:
        status_code = 200
        text = "<html>bad gateway</html>"
        headers = {"content-type": "text/html"}

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            raise ValueError("no json")

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: FakeResponse())

    with pytest.raises(RuntimeError, match="CSW request did not return JSON"):
        wrapper._fetch_data("https://example.com")


def test_get_filename_from_url_returns_basename(tmp_path: Path) -> None:
    downloader = GcomDownloader(str(tmp_path / "download"), str(tmp_path / "workspace"), "user", "pass")

    assert downloader._get_filename_from_url("https://example.com/files/test-data.h5") == "test-data.h5"


def test_get_filename_from_url_raises_for_invalid_url(tmp_path: Path) -> None:
    downloader = GcomDownloader(str(tmp_path / "download"), str(tmp_path / "workspace"), "user", "pass")

    with pytest.raises(ValueError):
        downloader._get_filename_from_url("https://example.com/")


def test_build_playwright_server_image_command_uses_public_base_image(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "docker" / "playwright-server").mkdir(parents=True, exist_ok=True)

    command = GcomDownloader._build_playwright_server_image_command(repo_root)

    assert command[:3] == ["docker", "build", "--pull"]
    assert "--build-arg" in command
    assert f"PLAYWRIGHT_BASE_IMAGE={GcomDownloader._playwright_base_image}" in command
    assert f"PLAYWRIGHT_VERSION={GcomDownloader._playwright_version}" in command
    assert GcomDownloader._server_image_name in command
    assert str(repo_root / "docker" / "playwright-server" / "Dockerfile") in command
    assert str(repo_root / "docker" / "playwright-server") in command


def test_build_playwright_server_image_raises_clear_error_for_public_pull_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)

    def fake_run(command: list[str], cwd: Path, capture_output: bool, text: bool, check: bool) -> object:
        class Result:
            returncode = 1
            stdout = ""
            stderr = (
                f'failed to resolve reference "{GcomDownloader._playwright_base_image}": '
                "dial tcp: lookup mcr.microsoft.com: i/o timeout"
            )

        return Result()

    monkeypatch.setattr("subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="Failed to pull public Playwright base image"):
        GcomDownloader.build_playwright_server_image(repo_root)


def test_build_playwright_server_image_raises_clear_error_for_build_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)

    def fake_run(command: list[str], cwd: Path, capture_output: bool, text: bool, check: bool) -> object:
        class Result:
            returncode = 1
            stdout = "Step 2/2 : RUN npm install -g playwright@1.58.0"
            stderr = "npm ERR! code ETARGET"

        return Result()

    monkeypatch.setattr("subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="Failed to build Playwright server image"):
        GcomDownloader.build_playwright_server_image(repo_root)


def test_build_playwright_server_command_uses_built_server_image(tmp_path: Path) -> None:
    downloader = GcomDownloader(str(tmp_path / "download"), str(tmp_path / "workspace"), "user", "pass")

    command = downloader._build_playwright_server_command(3000, "gportal-playwright-test")

    assert command[:4] == ["docker", "run", "-d", "--rm"]
    assert "--init" in command
    assert "--ipc=host" in command
    assert "--name" in command
    assert "gportal-playwright-test" in command
    assert GcomDownloader._server_image_name in command
    assert command[-6:] == ["playwright", "run-server", "--port", "3000", "--host", "0.0.0.0"]


def test_start_playwright_server_launches_container_and_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    downloader = GcomDownloader(str(tmp_path / "download"), str(tmp_path / "workspace"), "user", "pass")
    captured: dict[str, object] = {}

    class FakeUuid:
        hex = "abcdef1234567890"

    def fake_run(command: list[str], cwd: Path, capture_output: bool, text: bool, check: bool) -> object:
        captured["command"] = command
        captured["cwd"] = cwd
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["check"] = check

        class Result:
            returncode = 0
            stdout = "container-id\n"
            stderr = ""

        return Result()

    waited_for: dict[str, object] = {}

    monkeypatch.setattr(downloader, "_allocate_server_port", lambda: 4567)
    monkeypatch.setattr(gcom.uuid, "uuid4", lambda: FakeUuid())
    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(downloader, "_ensure_playwright_server_image", lambda: captured.setdefault("built", True))
    monkeypatch.setattr(downloader, "_wait_for_playwright_server", lambda server: waited_for.setdefault("server", server))

    server = downloader._start_playwright_server()

    assert server == _PlaywrightServer("gportal-playwright-abcdef12", "ws://127.0.0.1:4567/")
    assert waited_for["server"] == server
    assert captured["built"] is True
    assert captured["cwd"] == downloader._repo_root
    assert captured["capture_output"] is True
    assert captured["text"] is True
    assert captured["check"] is False

    command = captured["command"]
    assert isinstance(command, list)
    assert GcomDownloader._server_image_name in command
    assert "gportal-playwright-abcdef12" in command


def test_download_missing_urls_uses_host_playwright_client_and_saves_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    download_dir = tmp_path / "download"
    workspace_dir = tmp_path / "workspace"
    downloader = GcomDownloader(str(download_dir), str(workspace_dir), "user", "pass")
    server = _PlaywrightServer("gportal-playwright-test", "ws://127.0.0.1:3000/")
    stopped: list[_PlaywrightServer] = []

    class FakeLocator:
        def fill(self, value: str) -> None:
            return None

    class FakeDownload:
        def save_as(self, path: str) -> None:
            Path(path).write_text("downloaded", encoding="utf-8")

    class FakeDownloadInfo:
        value = FakeDownload()

    class FakeExpectDownload:
        def __enter__(self) -> FakeDownloadInfo:
            return FakeDownloadInfo()

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    class FakePage:
        def goto(self, url: str, wait_until: str | None = None) -> None:
            return None

        def locator(self, selector: str) -> FakeLocator:
            return FakeLocator()

        def evaluate(self, script: str, args: dict[str, str]) -> dict[str, int]:
            return {"status": 1}

        def wait_for_function(self, script: str, arg: str, timeout: int) -> None:
            return None

        def wait_for_load_state(self, state: str, timeout: int) -> None:
            return None

        def title(self) -> str:
            return "G-PortalTop"

        def expect_download(self, timeout: int) -> FakeExpectDownload:
            return FakeExpectDownload()

    class FakeContext:
        def new_page(self) -> FakePage:
            return FakePage()

        def close(self) -> None:
            return None

    class FakeBrowser:
        def new_context(self, accept_downloads: bool) -> FakeContext:
            assert accept_downloads is True
            return FakeContext()

        def close(self) -> None:
            return None

    class FakeChromium:
        def connect(self, ws_endpoint: str) -> FakeBrowser:
            assert ws_endpoint == server.ws_endpoint
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakePlaywrightManager:
        def __enter__(self) -> FakePlaywright:
            return FakePlaywright()

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    monkeypatch.setattr(downloader, "_start_playwright_server", lambda: server)
    monkeypatch.setattr(downloader, "_stop_playwright_server", lambda actual: stopped.append(actual))
    monkeypatch.setattr(gcom, "sync_playwright", lambda: FakePlaywrightManager())

    downloader._download_missing_urls(["https://example.com/files/result.h5"])

    assert stopped == [server]
    assert (download_dir / "result.h5").read_text(encoding="utf-8") == "downloaded"


def test_download_missing_urls_wraps_error_and_stops_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    downloader = GcomDownloader(str(tmp_path / "download"), str(tmp_path / "workspace"), "user", "pass")
    server = _PlaywrightServer("gportal-playwright-test", "ws://127.0.0.1:3000/")
    stopped: list[_PlaywrightServer] = []

    class FakeChromium:
        def connect(self, ws_endpoint: str):
            raise RuntimeError("boom")

    class FakePlaywright:
        chromium = FakeChromium()

    class FakePlaywrightManager:
        def __enter__(self) -> FakePlaywright:
            return FakePlaywright()

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    monkeypatch.setattr(downloader, "_start_playwright_server", lambda: server)
    monkeypatch.setattr(downloader, "_stop_playwright_server", lambda actual: stopped.append(actual))
    monkeypatch.setattr(downloader, "_docker_logs", lambda name: "server logs")
    monkeypatch.setattr(gcom, "sync_playwright", lambda: FakePlaywrightManager())

    with pytest.raises(RuntimeError, match="Playwright download failed during host-controlled browser automation: boom"):
        downloader._download_missing_urls(["https://example.com/files/result.h5"])

    assert stopped == [server]


def test_get_downloaded_file_paths_logs_progress_and_skips_existing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    download_dir = tmp_path / "download"
    workspace_dir = tmp_path / "workspace"
    download_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    existing_url = "https://example.com/files/existing.h5"
    missing_url = "https://example.com/files/missing.h5"
    (download_dir / "existing.h5").write_text("present", encoding="utf-8")

    downloader = GcomDownloader(str(download_dir), str(workspace_dir), "user", "pass")

    monkeypatch.setattr(downloader, "_ensure_docker", lambda: None)

    def fake_download_missing_urls(urls: list[str]) -> None:
        assert urls == [missing_url]
        (download_dir / "missing.h5").write_text("downloaded", encoding="utf-8")

    monkeypatch.setattr(downloader, "_download_missing_urls", fake_download_missing_urls)

    paths = downloader.get_downloaded_file_paths([existing_url, missing_url])

    assert paths == [str(download_dir / "existing.h5"), str(download_dir / "missing.h5")]
    stdout = capsys.readouterr().out
    assert "[download] resolved 2 file path(s); 1 missing, 1 already present" in stdout
    assert "[download] downloading 1 missing file(s)" in stdout
    assert "[download] verified 2 downloaded file(s)" in stdout
