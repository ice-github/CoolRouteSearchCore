import argparse
import os
from datetime import datetime

from gcom import CSWWrapper, GcomDownloader
from lst_analysis import compute_lst_point_means
from prefecture_bbox import get_prefecture_bbox


DEFAULT_DATASET_ID = "10002019"
DEFAULT_DOWNLOAD_PREFECTURE = "愛知"
DEFAULT_DOWNLOAD_START = datetime(2024, 1, 1)
DEFAULT_DOWNLOAD_END = datetime(2024, 1, 2)
DEFAULT_ANALYSIS_AREA = "愛知県名古屋市"
DEFAULT_ANALYSIS_START = datetime(2025, 7, 1)
DEFAULT_ANALYSIS_END = datetime(2025, 8, 31)
DEFAULT_ANALYSIS_SPACING_M = 1000


def gportal_username_and_password_from_env() -> tuple[str | None, str | None]:
    return os.environ.get("GPORTAL_USER"), os.environ.get("GPORTAL_PASS")


def _parse_datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"invalid datetime value: {value}") from error


def _log(message: str) -> None:
    print(message, flush=True)


def run_download(
    prefecture_keyword: str = DEFAULT_DOWNLOAD_PREFECTURE,
    dataset_id: str = DEFAULT_DATASET_ID,
    utc_start: datetime = DEFAULT_DOWNLOAD_START,
    utc_end: datetime = DEFAULT_DOWNLOAD_END,
    limit: int | None = 1,
    download_dir: str = "download",
    workspace_dir: str = "workspace",
) -> list[str]:
    _log(
        f"[download] prefecture={prefecture_keyword} dataset={dataset_id} "
        f"start={utc_start.isoformat()} end={utc_end.isoformat()}"
    )
    bbox = get_prefecture_bbox(prefecture_keyword)

    csw_wrapper = CSWWrapper()
    hdf5_urls = csw_wrapper.get_hdf5_urls(dataset_id, utc_start, utc_end, bbox)
    if not hdf5_urls:
        raise RuntimeError("no HDF5 URLs were found for the query")

    if limit is not None:
        hdf5_urls = hdf5_urls[:limit]

    _log(f"[download] found {len(hdf5_urls)} URL(s); starting Playwright download")

    username, password = gportal_username_and_password_from_env()
    downloader = GcomDownloader(download_dir, workspace_dir, username or "", password or "")
    paths = downloader.get_downloaded_file_paths(hdf5_urls)
    _log(f"[download] wrote {len(paths)} file(s)")
    return paths


def run_analysis(
    area_name: str = DEFAULT_ANALYSIS_AREA,
    start: datetime = DEFAULT_ANALYSIS_START,
    end: datetime = DEFAULT_ANALYSIS_END,
    spacing_m: int = DEFAULT_ANALYSIS_SPACING_M,
    output_path: str | None = None,
) -> str:
    if output_path is None:
        safe_area_name = area_name.replace("/", "_")
        output_path = f"workspace/analysis/{safe_area_name}/lst_mean_local_{spacing_m}m.csv"
    _log(
        f"[analyze] area={area_name} spacing={spacing_m}m "
        f"start={start.isoformat()} end={end.isoformat()}"
    )
    csv_path = compute_lst_point_means(area_name, start, end, spacing_m, output_path)
    _log(f"[analyze] wrote {csv_path}")
    return csv_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download GCOM-C HDF5 data or run LST analysis.")
    subparsers = parser.add_subparsers(dest="command")

    download_parser = subparsers.add_parser("download", help="Download HDF5 data for a prefecture.")
    download_parser.add_argument("--prefecture", default=DEFAULT_DOWNLOAD_PREFECTURE, help="Prefecture keyword used to find the bbox.")
    download_parser.add_argument("--dataset-id", default=DEFAULT_DATASET_ID, help="CSW dataset ID.")
    download_parser.add_argument("--start", type=_parse_datetime, default=DEFAULT_DOWNLOAD_START, help="UTC start datetime in ISO format.")
    download_parser.add_argument("--end", type=_parse_datetime, default=DEFAULT_DOWNLOAD_END, help="UTC end datetime in ISO format.")
    download_parser.add_argument("--limit", type=int, default=1, help="Maximum number of URLs to download. Use 0 for all.")
    download_parser.add_argument("--download-dir", default="download", help="Directory where downloaded files are written.")
    download_parser.add_argument("--workspace-dir", default="workspace", help="Workspace directory mounted into the container.")

    analysis_parser = subparsers.add_parser("analyze", help="Run LST analysis for an area.")
    analysis_parser.add_argument("--area-name", default=DEFAULT_ANALYSIS_AREA, help="Administrative area name such as 愛知県名古屋市.")
    analysis_parser.add_argument("--start", type=_parse_datetime, default=DEFAULT_ANALYSIS_START, help="UTC start datetime in ISO format.")
    analysis_parser.add_argument("--end", type=_parse_datetime, default=DEFAULT_ANALYSIS_END, help="UTC end datetime in ISO format.")
    analysis_parser.add_argument("--spacing-m", type=int, default=DEFAULT_ANALYSIS_SPACING_M, help="Sampling spacing in meters.")
    analysis_parser.add_argument("--output-path", default=None, help="Optional CSV output path. Defaults under workspace/analysis.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    command = args.command or "download"
    if command == "download":
        limit = None if args.limit == 0 else args.limit
        paths = run_download(
            args.prefecture,
            args.dataset_id,
            args.start,
            args.end,
            limit,
            args.download_dir,
            args.workspace_dir,
        )
        for path in paths:
            print(path)
        return 0

    if command == "analyze":
        csv_path = run_analysis(args.area_name, args.start, args.end, args.spacing_m, args.output_path)
        print(csv_path)
        return 0

    parser.error(f"unknown command: {command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
