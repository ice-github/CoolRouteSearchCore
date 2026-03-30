import argparse
import os
from datetime import datetime

from gcom import CSWWrapper, GcomDownloader
from lst_analysis import analysis_output_path, analysis_output_paths_from_csv_path, compute_lst_point_means
from prefecture_bbox import get_prefecture_bbox


GCOM_C_LST_DATASET_ID = "10002019"
# This repo's examples target the GCOM-C LST product on G-Portal, so the
# dataset ID is fixed here unless the upstream catalog ID changes.


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
    prefecture_name: str,
    utc_start: datetime,
    utc_end: datetime,
    limit: int | None,
    download_dir: str,
    workspace_dir: str,
    dataset_id: str = GCOM_C_LST_DATASET_ID,
) -> list[str]:
    _log(
        f"[download] prefecture={prefecture_name} dataset={dataset_id} "
        f"start={utc_start.isoformat()} end={utc_end.isoformat()} "
        f"download_dir={download_dir} workspace_dir={workspace_dir}"
    )
    bbox = get_prefecture_bbox(prefecture_name)

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
    area_name: str,
    start: datetime,
    end: datetime,
    download_dir: str,
    workspace_dir: str,
    spacing_m: int,
    parallelism: int = 4,
    dataset_id: str = GCOM_C_LST_DATASET_ID,
) -> str:
    output_path = analysis_output_path(area_name, start, end, spacing_m)
    _log(
        f"[analyze] area={area_name} dataset={dataset_id} spacing={spacing_m}m parallelism={parallelism} "
        f"start={start.isoformat()} end={end.isoformat()} "
        f"download_dir={download_dir} workspace_dir={workspace_dir} output_path={output_path}"
    )
    csv_path = compute_lst_point_means(
        area_name,
        start,
        end,
        download_dir,
        workspace_dir,
        spacing_m,
        str(output_path),
        dataset_id,
        parallelism=parallelism,
        log_fn=_log,
    )
    paths = analysis_output_paths_from_csv_path(csv_path)
    _log(f"[analyze] wrote csv_path={paths['csv_path']}")
    _log(f"[analyze] wrote points_geojson_path={paths['points_geojson_path']}")
    _log(f"[analyze] wrote boundary_geojson_path={paths['boundary_geojson_path']}")
    _log(f"[analyze] wrote summary_path={paths['summary_path']}")
    for stat, path in paths["preview_paths"].items():
        _log(f"[analyze] wrote preview_{stat}={path}")
    for stat, path in paths["surface_paths"].items():
        _log(f"[analyze] wrote surface_{stat}={path}")
    return csv_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download GCOM-C HDF5 data or run LST analysis.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    download_parser = subparsers.add_parser("download", help="Download HDF5 data for a prefecture.")
    download_parser.add_argument("--prefecture", required=True, help="Prefecture name used to find the bbox, such as 愛知県.")
    download_parser.add_argument(
        "--dataset-id",
        default=GCOM_C_LST_DATASET_ID,
        help=f"CSW dataset ID. Defaults to the fixed GCOM-C LST ID {GCOM_C_LST_DATASET_ID}.",
    )
    download_parser.add_argument("--start", type=_parse_datetime, required=True, help="UTC start datetime in ISO format.")
    download_parser.add_argument("--end", type=_parse_datetime, required=True, help="UTC end datetime in ISO format.")
    download_parser.add_argument("--limit", type=int, required=True, help="Maximum number of URLs to download. Use 0 for all.")
    download_parser.add_argument("--download-dir", required=True, help="Directory where downloaded files are written.")
    download_parser.add_argument("--workspace-dir", required=True, help="Workspace directory mounted into the container.")

    analysis_parser = subparsers.add_parser("analyze", help="Run LST analysis for an area.")
    analysis_parser.add_argument("--area-name", required=True, help="Administrative area name such as 愛知県名古屋市.")
    analysis_parser.add_argument("--start", type=_parse_datetime, required=True, help="UTC start datetime in ISO format.")
    analysis_parser.add_argument("--end", type=_parse_datetime, required=True, help="UTC end datetime in ISO format.")
    analysis_parser.add_argument(
        "--dataset-id",
        default=GCOM_C_LST_DATASET_ID,
        help=f"CSW dataset ID. Defaults to the fixed GCOM-C LST ID {GCOM_C_LST_DATASET_ID}.",
    )
    analysis_parser.add_argument("--spacing-m", type=int, required=True, help="Sampling spacing in meters.")
    analysis_parser.add_argument(
        "--parallelism",
        type=int,
        default=4,
        help="Number of scenes to process in parallel during aggregation. Defaults to 4.",
    )
    analysis_parser.add_argument("--download-dir", required=True, help="Directory where downloaded files are written.")
    analysis_parser.add_argument("--workspace-dir", required=True, help="Workspace directory mounted into the container.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "download":
        limit = None if args.limit == 0 else args.limit
        paths = run_download(
            args.prefecture,
            args.start,
            args.end,
            limit,
            args.download_dir,
            args.workspace_dir,
            args.dataset_id,
        )
        for path in paths:
            print(path)
        return 0

    if args.command == "analyze":
        csv_path = run_analysis(
            args.area_name,
            args.start,
            args.end,
            args.download_dir,
            args.workspace_dir,
            args.spacing_m,
            args.parallelism,
            args.dataset_id,
        )
        print(csv_path)
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
