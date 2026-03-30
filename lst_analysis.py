import json
import os
from datetime import datetime
from collections.abc import Callable
from pathlib import Path

from analysis.runner import (
    compute_point_means_for_scenes,
    estimate_sampling_load_for_polygon,
    generate_grid_points,
    load_area_polygon,
    transform_polygon_to_metric,
    write_geojson,
    write_sampling_preview,
)
from gcom import CSWWrapper, GcomDownloader
from prefecture_bbox import get_administrative_bbox, load_prefecture_bboxes


GCOM_C_LST_DATASET_ID = "10002019"
# G-Portal's GCOM-C LST product is queried through this dataset ID.


def gportal_username_and_password_from_env() -> tuple[str | None, str | None]:
    return os.environ.get("GPORTAL_USER"), os.environ.get("GPORTAL_PASS")


def deduplicate_urls(urls: list[str]) -> list[str]:
    return list(dict.fromkeys(urls))


def _log(message: str) -> None:
    print(message, flush=True)


def sanitize_path_component(value: str) -> str:
    return value.replace("/", "_").replace("\\", "_")


def analysis_output_stem(area_name: str, start: datetime, end: datetime, spacing_m: int) -> str:
    safe_area_name = sanitize_path_component(area_name)
    return f"lst_mean_local_{safe_area_name}_{start:%Y%m%d}_{end:%Y%m%d}_{spacing_m}m"


def analysis_output_dir(area_name: str) -> Path:
    output_dir = _repo_root() / "workspace" / "analysis" / sanitize_path_component(area_name)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def analysis_output_path(area_name: str, start: datetime, end: datetime, spacing_m: int) -> Path:
    return analysis_output_dir(area_name) / f"{analysis_output_stem(area_name, start, end, spacing_m)}.csv"


def analysis_output_paths_from_csv_path(csv_path: str | Path) -> dict[str, str]:
    csv_path_obj = Path(csv_path)
    output_dir = csv_path_obj.parent
    stem = csv_path_obj.stem
    return {
        "csv_path": str(csv_path_obj),
        "points_geojson_path": str(output_dir / f"{stem}_sampling_points.geojson"),
        "boundary_geojson_path": str(output_dir / f"{stem}_sampling_boundary.geojson"),
        "summary_path": str(output_dir / f"{stem}_sampling_summary.json"),
        "preview_paths": {
            stat: str(output_dir / f"{stem}_sampling_preview_{stat}.png")
            for stat in ("min", "mean", "max")
        },
        "surface_paths": {
            stat: str(output_dir / f"{stem}_sampling_surface_{stat}.html")
            for stat in ("min", "mean", "max")
        },
    }


def infer_prefecture_name(area_name: str) -> str:
    prefectures = load_prefecture_bboxes()
    match = next((name for name in prefectures if area_name.startswith(name)), None)
    if match is None:
        raise ValueError(f"failed to infer prefecture name from area: {area_name}")
    return match


def _repo_root() -> Path:
    return Path(__file__).resolve().parent


def _create_output_dir(area_name: str) -> Path:
    return analysis_output_dir(area_name)


def _resolve_repo_path(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else (_repo_root() / candidate)


def _get_hdf5_urls_for_area(area_name: str, start: datetime, end: datetime, dataset_id: str) -> list[str]:
    bbox = get_administrative_bbox(area_name)
    wrapper = CSWWrapper()
    urls = wrapper.get_hdf5_urls(dataset_id, start, end, bbox)
    return deduplicate_urls(urls)


def estimate_sampling_load(
    area_name: str,
    start: datetime,
    end: datetime,
    download_dir: str,
    workspace_dir: str,
    spacings_m: list[int],
    dataset_id: str = GCOM_C_LST_DATASET_ID,
) -> list[dict]:
    urls = _get_hdf5_urls_for_area(area_name, start, end, dataset_id)
    resolved_download_dir = _resolve_repo_path(download_dir)
    resolved_workspace_dir = _resolve_repo_path(workspace_dir)
    prefecture_name = infer_prefecture_name(area_name)
    polygon_wgs84, _ = load_area_polygon(area_name, prefecture_name, resolved_download_dir, resolved_workspace_dir)
    metric_polygon = transform_polygon_to_metric(polygon_wgs84)
    result = estimate_sampling_load_for_polygon(area_name, prefecture_name, metric_polygon, len(urls), spacings_m)
    output_dir = _create_output_dir(area_name)
    (output_dir / "load_estimate.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result["estimates"]


def generate_sampling_points(
    area_name: str,
    spacing_m: int,
    output_dir: str,
    download_dir: str,
    workspace_dir: str,
    dataset_id: str = GCOM_C_LST_DATASET_ID,
) -> dict:
    resolved_download_dir = _resolve_repo_path(download_dir)
    resolved_workspace_dir = _resolve_repo_path(workspace_dir)
    prefecture_name = infer_prefecture_name(area_name)
    polygon_wgs84, _ = load_area_polygon(area_name, prefecture_name, resolved_download_dir, resolved_workspace_dir)
    metric_polygon = transform_polygon_to_metric(polygon_wgs84)
    points = generate_grid_points(metric_polygon, spacing_m)

    resolved_output_dir = (_repo_root() / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir)
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    polygon_geojson_path = resolved_output_dir / f"sampling_boundary_{spacing_m}m.geojson"
    points_geojson_path = resolved_output_dir / f"sampling_points_{spacing_m}m.geojson"
    preview_path = resolved_output_dir / f"sampling_preview_{spacing_m}m.png"

    write_geojson(
        str(polygon_geojson_path),
        [{"type": "Feature", "geometry": polygon_wgs84.__geo_interface__, "properties": {"area_name": area_name}}],
    )
    write_geojson(
        str(points_geojson_path),
        [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [point.lon, point.lat]},
                "properties": {
                    "point_id": point.point_id,
                    "inside_city": point.inside_city,
                    "spacing_m": spacing_m,
                    "x_6668": point.x_6668,
                    "y_6668": point.y_6668,
                    "x_metric_m": point.x_metric,
                    "y_metric_m": point.y_metric,
                },
            }
            for point in points
        ],
    )
    write_sampling_preview(str(preview_path), polygon_wgs84, points, spacing_m)

    area_m2 = metric_polygon.area
    summary = {
        "area_name": area_name,
        "prefecture_name": prefecture_name,
        "spacing_m": spacing_m,
        "point_count": len(points),
        "approx_area_m2": round(area_m2, 3),
        "approx_area_km2": round(area_m2 / 1_000_000, 6),
        "polygon_geojson_path": str(polygon_geojson_path),
        "points_geojson_path": str(points_geojson_path),
        "preview_path": str(preview_path),
    }
    summary_path = resolved_output_dir / f"sampling_summary_{spacing_m}m.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary["summary_path"] = str(summary_path)
    return summary


def compute_lst_point_means(
    area_name: str,
    start: datetime,
    end: datetime,
    download_dir: str,
    workspace_dir: str,
    spacing_m: int,
    output_path: str,
    dataset_id: str = GCOM_C_LST_DATASET_ID,
    log_fn: Callable[[str], None] | None = None,
) -> str:
    log = log_fn or _log
    log(
        f"[analyze] resolving HDF5 URLs area={area_name} dataset={dataset_id} "
        f"start={start.isoformat()} end={end.isoformat()}"
    )
    urls = _get_hdf5_urls_for_area(area_name, start, end, dataset_id)
    log(f"[analyze] found {len(urls)} HDF5 URL(s); starting download")
    username, password = gportal_username_and_password_from_env()
    downloader = GcomDownloader(_resolve_repo_path(download_dir), _resolve_repo_path(workspace_dir), username or "", password or "")
    log(f"[analyze] downloading {len(urls)} HDF5 file(s)")
    hdf5_file_paths = downloader.get_downloaded_file_paths(urls)

    prefecture_name = infer_prefecture_name(area_name)
    log(f"[analyze] loading area polygon area={area_name} prefecture={prefecture_name}")
    polygon_wgs84, _ = load_area_polygon(area_name, prefecture_name, _resolve_repo_path(download_dir), _resolve_repo_path(workspace_dir))
    metric_polygon = transform_polygon_to_metric(polygon_wgs84)

    resolved_output_path = (
        (_repo_root() / output_path).resolve() if not Path(output_path).is_absolute() else Path(output_path)
    )
    log(f"[analyze] generating sampling points spacing={spacing_m}m")
    log(f"[analyze] starting point mean aggregation file_count={len(hdf5_file_paths)}")
    result = compute_point_means_for_scenes(
        area_name,
        prefecture_name,
        metric_polygon,
        hdf5_file_paths,
        spacing_m,
        str(resolved_output_path),
        log_fn=log,
    )
    log(f"[analyze] wrote analysis artifacts csv_path={result['csv_path']}")
    return result["csv_path"]
