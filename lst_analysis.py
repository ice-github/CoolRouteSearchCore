import json
import os
from datetime import datetime
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


LST_DATASET_ID = "10002019"


def gportal_username_and_password_from_env() -> tuple[str | None, str | None]:
    return os.environ.get("GPORTAL_USER"), os.environ.get("GPORTAL_PASS")


def deduplicate_urls(urls: list[str]) -> list[str]:
    return list(dict.fromkeys(urls))


def infer_prefecture_name(area_name: str) -> str:
    prefectures = load_prefecture_bboxes()
    match = next((name for name in prefectures if area_name.startswith(name)), None)
    if match is None:
        raise ValueError(f"failed to infer prefecture name from area: {area_name}")
    return match


def _repo_root() -> Path:
    return Path(__file__).resolve().parent


def _create_default_output_dir(area_name: str) -> Path:
    output_dir = _repo_root() / "workspace" / "analysis" / area_name.replace("/", "_")
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _get_hdf5_urls_for_area(area_name: str, start: datetime, end: datetime) -> list[str]:
    bbox = get_administrative_bbox(area_name)
    wrapper = CSWWrapper()
    urls = wrapper.get_hdf5_urls(LST_DATASET_ID, start, end, bbox)
    return deduplicate_urls(urls)


def estimate_sampling_load(area_name: str, start: datetime, end: datetime, spacings_m: list[int]) -> list[dict]:
    urls = _get_hdf5_urls_for_area(area_name, start, end)
    download_dir = _repo_root() / "download"
    workspace_dir = _repo_root() / "workspace"
    prefecture_name = infer_prefecture_name(area_name)
    polygon_wgs84, _ = load_area_polygon(area_name, prefecture_name, download_dir, workspace_dir)
    metric_polygon = transform_polygon_to_metric(polygon_wgs84)
    result = estimate_sampling_load_for_polygon(area_name, prefecture_name, metric_polygon, len(urls), spacings_m)
    output_dir = _create_default_output_dir(area_name)
    (output_dir / "load_estimate.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result["estimates"]


def generate_sampling_points(area_name: str, spacing_m: int, output_dir: str) -> dict:
    download_dir = _repo_root() / "download"
    workspace_dir = _repo_root() / "workspace"
    prefecture_name = infer_prefecture_name(area_name)
    polygon_wgs84, _ = load_area_polygon(area_name, prefecture_name, download_dir, workspace_dir)
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
    spacing_m: int,
    output_path: str,
) -> str:
    urls = _get_hdf5_urls_for_area(area_name, start, end)
    username, password = gportal_username_and_password_from_env()
    downloader = GcomDownloader("download", "workspace", username or "", password or "")
    hdf5_file_paths = downloader.get_downloaded_file_paths(urls)

    download_dir = _repo_root() / "download"
    workspace_dir = _repo_root() / "workspace"
    prefecture_name = infer_prefecture_name(area_name)
    polygon_wgs84, _ = load_area_polygon(area_name, prefecture_name, download_dir, workspace_dir)
    metric_polygon = transform_polygon_to_metric(polygon_wgs84)

    resolved_output_path = (
        (_repo_root() / output_path).resolve() if not Path(output_path).is_absolute() else Path(output_path)
    )
    result = compute_point_means_for_scenes(
        area_name,
        prefecture_name,
        metric_polygon,
        hdf5_file_paths,
        spacing_m,
        str(resolved_output_path),
    )
    return result["csv_path"]
