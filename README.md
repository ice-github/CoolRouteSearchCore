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
  --prefecture 愛知県 \
  --start 2025-07-01 \
  --end 2025-08-31 \
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
  --parallelism 4 \
  --download-dir download \
  --workspace-dir workspace
```

京都市でも同じ要領で実行できます。

```bash
uv run python main.py analyze \
  --area-name 京都府京都市 \
  --start 2025-07-01 \
  --end 2025-08-31 \
  --spacing-m 1000 \
  --parallelism 4 \
  --download-dir download \
  --workspace-dir workspace
```

## Outputs

`workspace/analysis/<area_name>/` に次の成果物を出力します。

- `lst_mean_local_<area_name>_<start>_<end>_<spacing>m.csv`
- `lst_mean_local_<area_name>_<start>_<end>_<spacing>m_sampling_preview_{min,mean,max}.png`
- `lst_mean_local_<area_name>_<start>_<end>_<spacing>m_sampling_surface_{min,mean,max}.html`
- `lst_mean_local_<area_name>_<start>_<end>_<spacing>m_sampling_topdown_{min,mean,max}_<spacing>m_topdown.png`
- `lst_mean_local_<area_name>_<start>_<end>_<spacing>m_sampling_compare_{min,mean,max}_<spacing>m.png`
- `lst_mean_local_<area_name>_<start>_<end>_<spacing>m_sampling_points.geojson`
- `lst_mean_local_<area_name>_<start>_<end>_<spacing>m_sampling_boundary.geojson`
- `lst_mean_local_<area_name>_<start>_<end>_<spacing>m_sampling_summary.json`

補足:

- `sampling_preview_*.png` はサンプリング点の 2D 可視化です。
- `sampling_surface_*.html` は 3D 可視化です。各サンプリング点を 3D の球状マーカーとして表示し、エリア外形も重ねて表示します。
- `sampling_topdown_*.png` は 3D 可視化を orthographic の真上視点で描いたものです。
- `sampling_compare_*.png` は 2D preview と 3D topdown を左右に並べた比較画像です。
- `dataset-id` は GCOM-C LST 用の固定値 `10002019` を使います。
- `--parallelism` は scene 集計の並列度です。省略時は `4` です。
- `--download-dir` と `--workspace-dir` は用途ごとに分けられます。

## LST Analysis API

`lst_analysis.py` は `main.py analyze` から使うのが簡単です。直接使う場合は、以下の関数を呼びます。

- `estimate_sampling_load(area_name, start, end, download_dir, workspace_dir, [1000, 100, 10], dataset_id=10002019)`
- `generate_sampling_points(area_name, spacing_m, output_dir, download_dir, workspace_dir, dataset_id=10002019)`
- `compute_lst_point_means(area_name, start, end, download_dir, workspace_dir, spacing_m, output_path, dataset_id=10002019, parallelism=4)`

## Notes

- 取得済み HDF5 は既存の `download/` を再利用します。
- 行政区域ポリゴンは MLIT の `KsjTmplt-N03-2025` から取得します。
- HDF5 は `Image_data/LST` と `Image_data/QA_flag` を `rasterio` で直接開き、GCOM-C の等面積投影上でサンプル点評価します。
- ダウンロードは prefecture 名を `get_prefecture_bbox()` に渡し、CSW で対象 HDF5 の URL を取得して、未取得ファイルのみを Docker 上の Playwright で取得します。

## Tests

```bash
uv run pytest
uv run python -m compileall gcom.py main.py administrative_division.py prefecture_bbox.py lst_analysis.py analysis scripts playwright
```

## GitHub Actions

GitHub Actions では `workflow_dispatch` の手動実行で実ダウンロード確認を行います。
`tests/test_gportal_integration.py` は実際に G-Portal から 1 件ダウンロードし、ファイルが書き込まれることを確認します。
workflow では `secrets` を job-level `if` で直接判定せず、step-level の preflight で確認します。credentials 未設定時は失敗ではなくスキップ扱いになります。

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
