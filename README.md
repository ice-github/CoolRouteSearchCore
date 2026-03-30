# CoolRouteSearchCore

JAXA G-Portal の GCOM-C HDF5 を取得して、LST を解析するための最小構成です。

## Requirements

- `uv`
- `docker`
- `GPORTAL_USER`
- `GPORTAL_PASS`

## Setup

```bash
uv sync --group dev
```

## Quick Start

`main.py` はサブコマンドで使います。GCOM-C LST の dataset ID は固定値 `10002019` なので、通常は指定不要です。

### Download

```bash
uv run python main.py download \
  --prefecture 愛知 \
  --start 2024-01-01 \
  --end 2024-01-02 \
  --limit 1 \
  --download-dir download \
  --workspace-dir workspace
```

### Analyze

```bash
uv run python main.py analyze \
  --area-name 愛知県名古屋市 \
  --start 2025-07-01 \
  --end 2025-08-31 \
  --spacing-m 1000 \
  --download-dir download \
  --workspace-dir workspace \
  --output-path workspace/analysis/愛知県名古屋市/lst_mean_local_1000m.csv
```

京都市でも同じ要領で実行できます。

```bash
uv run python main.py analyze \
  --area-name 京都府京都市 \
  --start 2025-07-01 \
  --end 2025-08-31 \
  --spacing-m 1000 \
  --download-dir download \
  --workspace-dir workspace \
  --output-path workspace/analysis/京都府京都市/lst_mean_local_1000m.csv
```

## Outputs

`workspace/analysis/<area_name>/` に次の成果物を出力します。

- `sampling_preview_{min,mean,max}_*.png`
- `sampling_surface_{min,mean,max}_*.html`
- `sampling_points_*.geojson`
- `sampling_summary_*.json`
- `lst_mean_local_*.csv`

補足:

- `sampling_preview_*.png` はサンプリング点の 2D 可視化です。
- `sampling_surface_*.html` は 3D 可視化です。`z = 温度` と `色 = 温度` を併用します。
- `dataset-id` は GCOM-C LST 用の固定値 `10002019` を使います。
- `--download-dir` と `--workspace-dir` は用途ごとに分けられます。

## LST Analysis API

`lst_analysis.py` は `main.py analyze` から使うのが簡単です。直接使う場合は、以下の関数を呼びます。

- `estimate_sampling_load(area_name, start, end, download_dir, workspace_dir, [1000, 100, 10], dataset_id=10002019)`
- `generate_sampling_points(area_name, spacing_m, output_dir, download_dir, workspace_dir, dataset_id=10002019)`
- `compute_lst_point_means(area_name, start, end, download_dir, workspace_dir, spacing_m, output_path, dataset_id=10002019)`

## Notes

- 取得済み HDF5 は既存の `download/` を再利用します。
- 行政区域ポリゴンは MLIT の `KsjTmplt-N03-2025` から取得します。
- HDF5 は `Image_data/LST` と `Image_data/QA_flag` を `rasterio` で直接開き、GCOM-C の等面積投影上でサンプル点評価します。
- ダウンロードは prefecture キーワードを `get_prefecture_bbox()` に渡し、CSW で対象 HDF5 の URL を取得して、未取得ファイルのみを Docker 上の Playwright で取得します。

## Tests

```bash
uv run pytest
uv run python -m compileall gcom.py main.py administrative_division.py prefecture_bbox.py lst_analysis.py analysis scripts playwright
```

## GitHub Actions

GitHub Actions では `workflow_dispatch` または定期実行での実ダウンロード確認を行います。
`tests/test_gportal_integration.py` は実際に G-Portal から 1 件ダウンロードし、ファイルが書き込まれることを確認します。

GitHub Secrets:

- `GPORTAL_USER`, `GPORTAL_PASS`: `G-Portal Integration` に必要

## Prefecture BBoxes

全都道府県の `bbox` は `data/prefecture_bboxes.json` に含めています。

- 形式は `[floor(min_lon), floor(min_lat), ceil(max_lon), ceil(max_lat)]` です。
- 生成元は国土数値情報の「行政区域（ポリゴン）」です。

## Update BBoxes

行政界データの更新に追従したい場合は、次のコマンドで `bbox` 一覧を再生成できます。

```bash
uv run python scripts/update_prefecture_bboxes.py
```

このスクリプトは国土数値情報から全都道府県の行政区域データを取得し、`data/prefecture_bboxes.json` を上書きします。ダウンロード済み ZIP や展開済みシェープファイルは既存の `download/` と `workspace/` を再利用します。
