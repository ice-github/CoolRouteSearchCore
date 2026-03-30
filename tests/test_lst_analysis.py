from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image, ImageChops
from shapely.geometry import Polygon

import analysis.runner as runner
from analysis.runner import (
    compute_point_means_for_scenes,
    LST_VIS_MAX_C,
    LST_VIS_MIN_C,
    SamplingPoint,
    build_sampling_surface_figure,
    draw_points,
    generate_grid_points,
    load_scene,
    point_to_feature,
    preview_point_radius,
    sample_scene,
    write_sampling_point_cloud,
    write_sampling_preview,
    write_sampling_surface,
    write_sampling_preview_set,
    write_sampling_surface_set,
    write_sampling_surface_views,
    temperature_to_color,
    transform_polygon_to_metric,
)
from lst_analysis import (
    analysis_output_path,
    analysis_output_paths_from_csv_path,
    analysis_output_stem,
    compute_lst_point_means,
    deduplicate_urls,
    infer_prefecture_name,
)


SAMPLE_HDF5 = Path("/home/ubuntu/workspace/CoolRouteSearchCore/download/GC1SG1_20240101A01D_T0529_L2SG_LST_Q_3000.h5")


def _looks_like_point_pixel(pixel: tuple[int, int, int]) -> bool:
    red, green, blue = pixel
    return blue > 180 and (blue - max(red, green)) > 40


def _point_centroids(image: Image.Image) -> list[tuple[float, float]]:
    rgb_image = image.convert("RGB")
    width, height = rgb_image.size
    pixels = rgb_image.load()
    visited = [[False for _ in range(width)] for _ in range(height)]
    centroids: list[tuple[float, float]] = []

    for y in range(height):
        for x in range(width):
            if visited[y][x] or not _looks_like_point_pixel(pixels[x, y]):
                continue

            stack = [(x, y)]
            visited[y][x] = True
            count = 0
            sum_x = 0.0
            sum_y = 0.0

            while stack:
                current_x, current_y = stack.pop()
                count += 1
                sum_x += current_x
                sum_y += current_y
                for neighbor_y in range(max(0, current_y - 1), min(height, current_y + 2)):
                    for neighbor_x in range(max(0, current_x - 1), min(width, current_x + 2)):
                        if visited[neighbor_y][neighbor_x]:
                            continue
                        if not _looks_like_point_pixel(pixels[neighbor_x, neighbor_y]):
                            continue
                        visited[neighbor_y][neighbor_x] = True
                        stack.append((neighbor_x, neighbor_y))

            centroids.append((sum_x / count, sum_y / count))

    return sorted(centroids, key=lambda value: (value[1], value[0]))


def _normalized_centroids(image: Image.Image) -> list[tuple[float, float]]:
    rgb_image = image.convert("RGB")
    background = Image.new("RGB", rgb_image.size, (255, 255, 255))
    bbox = ImageChops.difference(rgb_image, background).getbbox()
    if bbox is not None:
        rgb_image = rgb_image.crop(bbox)
    width, height = rgb_image.size
    return [(x / width, y / height) for x, y in _point_centroids(rgb_image)]


def test_deduplicate_urls_preserves_order() -> None:
    assert deduplicate_urls(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]


def test_infer_prefecture_name_uses_area_prefix() -> None:
    assert infer_prefecture_name("愛知県名古屋市") == "愛知県"


def test_infer_prefecture_name_raises_for_unknown_area() -> None:
    with pytest.raises(ValueError):
        infer_prefecture_name("名古屋市")


def test_analysis_output_stem_and_paths_are_built_from_inputs() -> None:
    stem = analysis_output_stem("京都府京都市", datetime(2025, 7, 1), datetime(2025, 8, 31), 1000)

    assert stem == "lst_mean_local_京都府京都市_20250701_20250831_1000m"
    assert analysis_output_path("京都府京都市", datetime(2025, 7, 1), datetime(2025, 8, 31), 1000) == (
        Path(__file__).resolve().parents[1]
        / "workspace/analysis/京都府京都市/lst_mean_local_京都府京都市_20250701_20250831_1000m.csv"
    )


def test_analysis_output_paths_from_csv_path_uses_common_stem() -> None:
    paths = analysis_output_paths_from_csv_path(
        "workspace/analysis/京都府京都市/lst_mean_local_京都府京都市_20250701_20250831_1000m.csv"
    )

    assert paths["csv_path"] == "workspace/analysis/京都府京都市/lst_mean_local_京都府京都市_20250701_20250831_1000m.csv"
    assert paths["points_geojson_path"] == (
        "workspace/analysis/京都府京都市/lst_mean_local_京都府京都市_20250701_20250831_1000m_sampling_points.geojson"
    )
    assert paths["boundary_geojson_path"] == (
        "workspace/analysis/京都府京都市/lst_mean_local_京都府京都市_20250701_20250831_1000m_sampling_boundary.geojson"
    )
    assert paths["summary_path"] == (
        "workspace/analysis/京都府京都市/lst_mean_local_京都府京都市_20250701_20250831_1000m_sampling_summary.json"
    )
    assert paths["preview_paths"]["mean"] == (
        "workspace/analysis/京都府京都市/lst_mean_local_京都府京都市_20250701_20250831_1000m_sampling_preview_mean.png"
    )
    assert paths["surface_paths"]["max"] == (
        "workspace/analysis/京都府京都市/lst_mean_local_京都府京都市_20250701_20250831_1000m_sampling_surface_max.html"
    )


def test_load_scene_reads_hdf5_metadata() -> None:
    scene = load_scene(str(SAMPLE_HDF5), "Image_data/LST")

    assert scene.width == 4800
    assert scene.height == 4800
    assert scene.band_tags["Slope"] == "0.02"
    assert scene.band_tags["Error_DN"] == "65535"
    assert scene.footprint_metric.bounds[0] < scene.footprint_metric.bounds[2]


def test_sample_scene_returns_value_for_nagoya_point() -> None:
    scene = load_scene(str(SAMPLE_HDF5), "Image_data/LST")

    sample = sample_scene(scene, 136.8855, 35.1077)

    assert sample is not None
    value, row, col = sample
    assert value >= 0
    assert row >= 0
    assert col >= 0


def test_generate_grid_points_uses_metric_spacing() -> None:
    polygon = Polygon([(0, 0), (3000, 0), (3000, 3000), (0, 3000)])

    points = generate_grid_points(polygon, 1000)

    assert len(points) == 9
    assert points[0].x_metric == 500
    assert points[0].y_metric == 500


def test_transform_polygon_to_metric_preserves_polygon_shape() -> None:
    polygon = Polygon([(136.8, 35.0), (136.9, 35.0), (136.9, 35.1), (136.8, 35.1)])

    transformed = transform_polygon_to_metric(polygon)

    assert transformed.area > 0


def test_temperature_to_color_clips_to_fixed_scale() -> None:
    low = temperature_to_color(LST_VIS_MIN_C - 10)
    mid = temperature_to_color((LST_VIS_MIN_C + LST_VIS_MAX_C) / 2)
    high = temperature_to_color(LST_VIS_MAX_C + 10)

    assert low == temperature_to_color(LST_VIS_MIN_C)
    assert high == temperature_to_color(LST_VIS_MAX_C)
    assert low != high
    assert mid != low
    assert mid != high


def test_point_to_feature_includes_temperature_stats() -> None:
    point = SamplingPoint(
        point_id=1,
        lon=136.9,
        lat=35.1,
        x_metric=1_000.0,
        y_metric=2_000.0,
        x_6668=1_000.0,
        y_6668=2_000.0,
        sum_lst_c=72.0,
        min_lst_c=20.0,
        max_lst_c=28.0,
        valid_count=3,
    )

    feature = point_to_feature(point, 1000)

    assert feature["properties"]["valid_count"] == 3
    assert feature["properties"]["min_lst_c"] == 20.0
    assert feature["properties"]["mean_lst_c"] == 24.0
    assert feature["properties"]["max_lst_c"] == 28.0


def test_write_sampling_preview_set_writes_min_mean_max_images(tmp_path: Path) -> None:
    polygon = Polygon([(136.8, 35.0), (136.9, 35.0), (136.9, 35.1), (136.8, 35.1)])
    point = SamplingPoint(
        point_id=1,
        lon=136.85,
        lat=35.05,
        x_metric=1_000.0,
        y_metric=2_000.0,
        x_6668=1_000.0,
        y_6668=2_000.0,
        sum_lst_c=72.0,
        min_lst_c=20.0,
        max_lst_c=28.0,
        valid_count=3,
    )

    outputs = write_sampling_preview_set(tmp_path, "sampling_preview", polygon, [point], 1000)

    assert set(outputs) == {"min", "mean", "max"}
    for path_str in outputs.values():
        assert Path(path_str).exists()


def test_write_sampling_surface_set_writes_min_mean_max_html(tmp_path: Path) -> None:
    points = [
        SamplingPoint(
            point_id=1,
            lon=136.85,
            lat=35.05,
            x_metric=1_000.0,
            y_metric=2_000.0,
            x_6668=1_000.0,
            y_6668=2_000.0,
            sum_lst_c=72.0,
            min_lst_c=20.0,
            max_lst_c=28.0,
            valid_count=3,
        ),
        SamplingPoint(
            point_id=2,
            lon=136.86,
            lat=35.05,
            x_metric=2_000.0,
            y_metric=2_000.0,
            x_6668=2_000.0,
            y_6668=2_000.0,
            sum_lst_c=84.0,
            min_lst_c=24.0,
            max_lst_c=30.0,
            valid_count=3,
        ),
        SamplingPoint(
            point_id=3,
            lon=136.85,
            lat=35.06,
            x_metric=1_000.0,
            y_metric=3_000.0,
            x_6668=1_000.0,
            y_6668=3_000.0,
            sum_lst_c=90.0,
            min_lst_c=25.0,
            max_lst_c=31.0,
            valid_count=3,
        ),
        SamplingPoint(
            point_id=4,
            lon=136.86,
            lat=35.06,
            x_metric=2_000.0,
            y_metric=3_000.0,
            x_6668=2_000.0,
            y_6668=3_000.0,
            sum_lst_c=96.0,
            min_lst_c=26.0,
            max_lst_c=32.0,
            valid_count=3,
        ),
    ]

    outputs = write_sampling_surface_set(tmp_path, "sampling_surface", points, 1000)

    assert set(outputs) == {"min", "mean", "max"}
    for path_str in outputs.values():
        path = Path(path_str)
        assert path.exists()
        assert path.suffix == ".html"


def test_build_sampling_surface_figure_uses_temperature_for_height_and_color() -> None:
    points = [
        SamplingPoint(
            point_id=1,
            lon=136.85,
            lat=35.05,
            x_metric=1_000.0,
            y_metric=2_000.0,
            x_6668=1_000.0,
            y_6668=2_000.0,
            sum_lst_c=72.0,
            min_lst_c=20.0,
            max_lst_c=28.0,
            valid_count=3,
        ),
        SamplingPoint(
            point_id=2,
            lon=136.86,
            lat=35.05,
            x_metric=2_000.0,
            y_metric=2_000.0,
            x_6668=2_000.0,
            y_6668=2_000.0,
            sum_lst_c=84.0,
            min_lst_c=24.0,
            max_lst_c=30.0,
            valid_count=3,
        ),
        SamplingPoint(
            point_id=3,
            lon=136.85,
            lat=35.06,
            x_metric=1_000.0,
            y_metric=3_000.0,
            x_6668=1_000.0,
            y_6668=3_000.0,
            sum_lst_c=90.0,
            min_lst_c=25.0,
            max_lst_c=31.0,
            valid_count=3,
        ),
        SamplingPoint(
            point_id=4,
            lon=136.86,
            lat=35.06,
            x_metric=2_000.0,
            y_metric=3_000.0,
            x_6668=2_000.0,
            y_6668=3_000.0,
            sum_lst_c=96.0,
            min_lst_c=26.0,
            max_lst_c=32.0,
            valid_count=3,
        ),
    ]

    figure = build_sampling_surface_figure(points, "mean")
    trace = figure.data[0]

    assert trace.type == "surface"
    assert trace.z == trace.surfacecolor
    assert trace.cmin is None
    assert trace.cmax is None
    assert figure.layout.scene.zaxis.range is None


def test_build_sampling_surface_figure_uses_actual_range_for_high_values() -> None:
    points = [
        SamplingPoint(
            point_id=1,
            lon=136.85,
            lat=35.05,
            x_metric=1_000.0,
            y_metric=2_000.0,
            x_6668=1_000.0,
            y_6668=2_000.0,
            sum_lst_c=160.0,
            min_lst_c=150.0,
            max_lst_c=180.0,
            valid_count=3,
        ),
        SamplingPoint(
            point_id=2,
            lon=136.86,
            lat=35.05,
            x_metric=2_000.0,
            y_metric=2_000.0,
            x_6668=2_000.0,
            y_6668=2_000.0,
            sum_lst_c=150.0,
            min_lst_c=145.0,
            max_lst_c=170.0,
            valid_count=3,
        ),
        SamplingPoint(
            point_id=3,
            lon=136.85,
            lat=35.06,
            x_metric=1_000.0,
            y_metric=3_000.0,
            x_6668=1_000.0,
            y_6668=3_000.0,
            sum_lst_c=155.0,
            min_lst_c=140.0,
            max_lst_c=175.0,
            valid_count=3,
        ),
        SamplingPoint(
            point_id=4,
            lon=136.86,
            lat=35.06,
            x_metric=2_000.0,
            y_metric=3_000.0,
            x_6668=2_000.0,
            y_6668=3_000.0,
            sum_lst_c=165.0,
            min_lst_c=148.0,
            max_lst_c=182.0,
            valid_count=3,
        ),
    ]

    figure = build_sampling_surface_figure(points, "max")
    trace = figure.data[0]

    assert trace.type == "surface"
    assert trace.cmin is None
    assert trace.cmax is None
    assert figure.layout.scene.zaxis.range is None


def test_write_sampling_surface_views_renders_multiple_pngs(tmp_path: Path) -> None:
    points = [
        SamplingPoint(
            point_id=1,
            lon=136.85,
            lat=35.05,
            x_metric=1_000.0,
            y_metric=2_000.0,
            x_6668=1_000.0,
            y_6668=2_000.0,
            sum_lst_c=72.0,
            min_lst_c=20.0,
            max_lst_c=28.0,
            valid_count=3,
        ),
        SamplingPoint(
            point_id=2,
            lon=136.86,
            lat=35.05,
            x_metric=2_000.0,
            y_metric=2_000.0,
            x_6668=2_000.0,
            y_6668=2_000.0,
            sum_lst_c=84.0,
            min_lst_c=24.0,
            max_lst_c=30.0,
            valid_count=3,
        ),
        SamplingPoint(
            point_id=3,
            lon=136.85,
            lat=35.06,
            x_metric=1_000.0,
            y_metric=3_000.0,
            x_6668=1_000.0,
            y_6668=3_000.0,
            sum_lst_c=90.0,
            min_lst_c=25.0,
            max_lst_c=31.0,
            valid_count=3,
        ),
        SamplingPoint(
            point_id=4,
            lon=136.86,
            lat=35.06,
            x_metric=2_000.0,
            y_metric=3_000.0,
            x_6668=2_000.0,
            y_6668=3_000.0,
            sum_lst_c=96.0,
            min_lst_c=26.0,
            max_lst_c=32.0,
            valid_count=3,
        ),
    ]

    outputs = write_sampling_surface_views(tmp_path, "sampling_surface", points, 1000)

    assert set(outputs) == {"iso", "low_north", "low_east"}
    rendered = {name: Image.open(path) for name, path in outputs.items()}
    try:
        for image in rendered.values():
            assert image.size == (2400, 1800)
        assert rendered["iso"].tobytes() != rendered["low_north"].tobytes()
        assert rendered["iso"].tobytes() != rendered["low_east"].tobytes()
    finally:
        for image in rendered.values():
            image.close()


def test_point_cloud_render_matches_2d_preview_before_surface(tmp_path: Path) -> None:
    polygon = Polygon([(136.8, 35.0), (136.9, 35.0), (136.9, 35.1), (136.8, 35.1)])
    points = [
        SamplingPoint(
            point_id=1,
            lon=136.82,
            lat=35.02,
            x_metric=1_000.0,
            y_metric=2_000.0,
            x_6668=1_000.0,
            y_6668=2_000.0,
            sum_lst_c=72.0,
            min_lst_c=20.0,
            max_lst_c=28.0,
            valid_count=3,
        ),
        SamplingPoint(
            point_id=2,
            lon=136.88,
            lat=35.02,
            x_metric=2_000.0,
            y_metric=2_000.0,
            x_6668=2_000.0,
            y_6668=2_000.0,
            sum_lst_c=84.0,
            min_lst_c=24.0,
            max_lst_c=30.0,
            valid_count=3,
        ),
        SamplingPoint(
            point_id=3,
            lon=136.82,
            lat=35.08,
            x_metric=1_000.0,
            y_metric=3_000.0,
            x_6668=1_000.0,
            y_6668=3_000.0,
            sum_lst_c=90.0,
            min_lst_c=25.0,
            max_lst_c=31.0,
            valid_count=3,
        ),
        SamplingPoint(
            point_id=4,
            lon=136.88,
            lat=35.08,
            x_metric=2_000.0,
            y_metric=3_000.0,
            x_6668=2_000.0,
            y_6668=3_000.0,
            sum_lst_c=96.0,
            min_lst_c=26.0,
            max_lst_c=32.0,
            valid_count=3,
        ),
    ]

    preview_path = tmp_path / "preview.png"
    point_cloud_path = tmp_path / "point_cloud.png"
    surface_path = tmp_path / "surface.html"

    write_sampling_preview(str(preview_path), polygon, points, 1000)
    write_sampling_point_cloud(str(point_cloud_path), polygon, points, 1000)
    write_sampling_surface(str(surface_path), points, "mean")

    preview_image = Image.open(preview_path)
    point_cloud_image = Image.open(point_cloud_path)
    try:
        preview_centroids = _normalized_centroids(preview_image)
        point_cloud_centroids = _normalized_centroids(point_cloud_image)

        assert len(preview_centroids) == len(point_cloud_centroids) == 4
        for (preview_x, preview_y), (cloud_x, cloud_y) in zip(preview_centroids, point_cloud_centroids):
            assert abs(preview_x - cloud_x) <= 0.08
            assert abs(preview_y - cloud_y) <= 0.08
    finally:
        preview_image.close()
        point_cloud_image.close()

    assert surface_path.exists()


def test_draw_points_uses_fill_only_without_outline() -> None:
    class DummyDraw:
        def __init__(self) -> None:
            self.calls: list[tuple[tuple[float, float, float, float], dict]] = []

        def ellipse(self, bbox, **kwargs) -> None:
            self.calls.append((bbox, kwargs))

    dummy_draw = DummyDraw()
    polygon = Polygon([(0, 0), (1000, 0), (1000, 1000), (0, 1000)])
    point = SamplingPoint(
        point_id=1,
        lon=0.5,
        lat=0.5,
        x_metric=500.0,
        y_metric=500.0,
        x_6668=500.0,
        y_6668=500.0,
        sum_lst_c=24.0,
        min_lst_c=24.0,
        max_lst_c=24.0,
        valid_count=1,
    )

    draw_points(
        dummy_draw,
        [point],
        polygon.bounds,
        100,
        100,
        0,
        2,
        lambda sampled_point: (sampled_point.lon, sampled_point.lat),
        value_fn=lambda sampled_point: sampled_point.sum_lst_c / sampled_point.valid_count,
    )

    assert len(dummy_draw.calls) == 1
    _, kwargs = dummy_draw.calls[0]
    assert "outline" not in kwargs


def test_preview_point_radius_scales_for_100m_and_1000m() -> None:
    assert preview_point_radius(100) == 2
    assert preview_point_radius(1000) == 15


def test_compute_lst_point_means_emits_step_logs_and_passes_logger(monkeypatch, tmp_path: Path, capsys) -> None:
    captured: dict[str, object] = {}
    polygon = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])

    monkeypatch.setattr("lst_analysis._get_hdf5_urls_for_area", lambda *args, **kwargs: ["url-1", "url-2"])

    class DummyDownloader:
        def __init__(self, download_dir, workspace_dir, username, password) -> None:
            captured["download_dir"] = download_dir
            captured["workspace_dir"] = workspace_dir
            captured["username"] = username
            captured["password"] = password

        def get_downloaded_file_paths(self, urls):
            captured["urls"] = list(urls)
            return ["/tmp/scene-a.h5"]

    monkeypatch.setattr("lst_analysis.GcomDownloader", DummyDownloader)
    monkeypatch.setattr("lst_analysis.infer_prefecture_name", lambda area_name: "京都府")
    monkeypatch.setattr("lst_analysis.load_area_polygon", lambda *args, **kwargs: (polygon, None))
    monkeypatch.setattr("lst_analysis.transform_polygon_to_metric", lambda value: value)

    def fake_compute_point_means_for_scenes(
        area_name,
        prefecture_name,
        metric_polygon,
        hdf5_file_paths,
        spacing_m,
        output_path,
        log_fn=None,
    ):
        captured["compute_args"] = (
            area_name,
            prefecture_name,
            metric_polygon,
            list(hdf5_file_paths),
            spacing_m,
            output_path,
        )
        captured["log_fn"] = log_fn
        return {"csv_path": output_path}

    monkeypatch.setattr("lst_analysis.compute_point_means_for_scenes", fake_compute_point_means_for_scenes)
    monkeypatch.setattr("lst_analysis.gportal_username_and_password_from_env", lambda: ("demo-user", "demo-pass"))

    csv_path = compute_lst_point_means(
        "京都府京都市",
        datetime(2025, 7, 1),
        datetime(2025, 8, 31),
        "download/custom",
        "workspace/custom",
        1000,
        str(tmp_path / "out.csv"),
    )

    assert csv_path == str(tmp_path / "out.csv")
    assert captured["urls"] == ["url-1", "url-2"]
    assert captured["log_fn"] is not None
    stdout = capsys.readouterr().out
    assert "[analyze] resolving HDF5 URLs area=京都府京都市 dataset=10002019 start=2025-07-01T00:00:00 end=2025-08-31T00:00:00" in stdout
    assert "[analyze] found 2 HDF5 URL(s); starting download" in stdout
    assert "[analyze] downloading 2 HDF5 file(s)" in stdout
    assert "[analyze] loading area polygon area=京都府京都市 prefecture=京都府" in stdout
    assert "[analyze] generating sampling points spacing=1000m" in stdout
    assert "[analyze] starting point mean aggregation file_count=1" in stdout
    assert "[analyze] wrote analysis artifacts csv_path=" in stdout


def test_compute_point_means_for_scenes_emits_scene_progress_logs(monkeypatch, tmp_path: Path, capsys) -> None:
    polygon = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])
    points = [
        SamplingPoint(point_id=1, lon=0.5, lat=0.5, x_metric=0.0, y_metric=0.0, x_6668=0.0, y_6668=0.0),
        SamplingPoint(point_id=2, lon=1.5, lat=1.5, x_metric=0.0, y_metric=0.0, x_6668=0.0, y_6668=0.0),
    ]

    monkeypatch.setattr(runner, "generate_grid_points", lambda metric_polygon, spacing_m: points)
    monkeypatch.setattr(runner, "load_scene", lambda hdf5_path, sub_key: {"hdf5_path": hdf5_path, "sub_key": sub_key})
    monkeypatch.setattr(runner, "sample_scene", lambda scene, lon, lat: (100, 0, 0))
    monkeypatch.setattr(runner, "is_valid_qa_value", lambda value: True)
    monkeypatch.setattr(runner, "lst_dn_to_celsius", lambda value, scene: 25.0)
    monkeypatch.setattr(runner, "transform_polygon_to_wgs84", lambda value: value)
    monkeypatch.setattr(runner, "write_geojson", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "write_sampling_preview", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "write_sampling_surface", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "write_summary", lambda *args, **kwargs: None)

    summary = compute_point_means_for_scenes(
        "京都府京都市",
        "京都府",
        polygon,
        ["scene-a.h5", "scene-b.h5"],
        1000,
        str(tmp_path / "out.csv"),
    )

    assert summary["scene_count"] == 2
    assert summary["point_count"] == 2
    assert summary["valid_point_count"] == 2
    stdout = capsys.readouterr().out
    assert "[analyze] aggregating point means area=京都府京都市 prefecture=京都府 point_count=2 scene_count=2 spacing=1000m" in stdout
    assert "[analyze] loading scene 1/2: scene-a.h5" in stdout
    assert "[analyze] finished scene 1/2: valid_points=2 scene_count=1" in stdout
    assert "[analyze] loading scene 2/2: scene-b.h5" in stdout
    assert "[analyze] aggregation complete scene_count=2 valid_point_count=2 point_count=2" in stdout
