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

このセットアップでは、Playwright の Python client は `uv` 環境に入り、ブラウザ本体はローカルにインストールしません。

## Playwright Layout

- Host 側は `uv add playwright` 相当の Python client を使います。
- Docker 側は public な `mcr.microsoft.com/playwright:v1.58.0-noble` をベースにした server image を build して使います。
- `uv` で入るのは host Python client だけなので、Docker 内で `playwright run-server` を起動するには server image 側にも Playwright package が必要です。
- official の `mcr.microsoft.com/playwright:v...` は browser 実体と system dependencies を提供する base image であり、repo がその上に pinned version の Playwright Server 機能を追加します。
- runtime の `npx` を避けるのは、local と CI の再現性を揃え、実行時の追加ネットワーク依存を減らすためです。
- Playwright Server の `docker run` はこの repo では `--ipc=host` を必須にせず、互換性優先で通常の Docker IPC を使います。Chromium の共有メモリ不足を避けるため、代わりに `--shm-size=512m` を指定します。
- 背景は Playwright の Docker docs を基準にしています: https://playwright.dev/docs/docker

## Quick Start

`main.py` はサブコマンドで使います。GCOM-C LST の dataset ID は固定値 `10002019` なので、通常は指定不要です。

### Download

```bash
uv run python main.py download \
  --prefecture 愛知県 \
  --start 2025-07-01 \
  --end 2025-08-31 \
  --limit 1
```

`--limit` は日ごとや月ごとの件数ではなく、`--start` から `--end` までの検索期間全体で見つかった HDF5 の最大件数です。
上の例では、`2025-07-01` から `2025-08-31` までの検索結果全体から最大 1 ファイルだけをダウンロードします。
全件ダウンロードしたい場合は `--limit 0` を使うか、`--limit` を省略します。

`download` の既定値:

- `--limit 0`
- `--download-dir download`
- `--workspace-dir workspace`

### Analyze

```bash
uv run python main.py analyze \
  --area-name 愛知県名古屋市 \
  --start 2025-07-01 \
  --end 2025-08-31 \
  --spacing-m 1000 \
  --parallelism 4
```

`analyze` の既定値:

- `--download-dir download`
- `--workspace-dir workspace`
- `--parallelism 4`

京都市でも同じ要領で実行できます。

```bash
uv run python main.py analyze \
  --area-name 京都府京都市 \
  --start 2025-07-01 \
  --end 2025-08-31 \
  --spacing-m 1000 \
  --parallelism 4
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
- `sampling_surface_*.html` は 3D 可視化です。各サンプリング点の球状マーカーに加えて surface も重ね、エリア外形も表示します。Z 方向は見やすさのため 2 倍に強調しています。
- `sampling_topdown_*.png` は 3D 可視化を orthographic の真上視点で描いたものです。
- `sampling_compare_*.png` は 2D preview と 3D topdown を左右に並べた比較画像です。
- `dataset-id` は GCOM-C LST 用の固定値 `10002019` を使います。
- `--parallelism` は scene 集計の並列度です。省略時は `4` です。
- `--download-dir` と `--workspace-dir` は省略時にそれぞれ `download` と `workspace` を使います。必要なら用途ごとに分けられます。

## LST Analysis API

`lst_analysis.py` は `main.py analyze` から使うのが簡単です。直接使う場合は、以下の関数を呼びます。

- `estimate_sampling_load(area_name, start, end, download_dir, workspace_dir, [1000, 100, 10], dataset_id=10002019)`
- `generate_sampling_points(area_name, spacing_m, output_dir, download_dir, workspace_dir, dataset_id=10002019)`
- `compute_lst_point_means(area_name, start, end, download_dir, workspace_dir, spacing_m, output_path, dataset_id=10002019, parallelism=4)`

## Notes

- 取得済み HDF5 は既存の `download/` を再利用します。
- 行政区域ポリゴンは MLIT の `KsjTmplt-N03-2025` から取得します。
- HDF5 は `Image_data/LST` と `Image_data/QA_flag` を `rasterio` で直接開き、GCOM-C の等面積投影上でサンプル点評価します。
- ダウンロードは prefecture 名を `get_prefecture_bbox()` に渡し、CSW で対象 HDF5 の URL を取得して、未取得ファイルのみを取得します。
- `uv run python main.py ...` はホスト側の Playwright Python client でログインとダウンロード制御を行います。
- Docker は Playwright Server と Chromium を提供するためだけに使います。ブラウザ本体をローカル環境へ入れたくないためです。
- `download` と `analyze` は同じ `GcomDownloader` 実装を使い、ブラウザ自動化経路も共通です。

## Tests

```bash
uv run pytest
uv run python scripts/build_playwright_server_image.py
RUN_GPORTAL_INTEGRATION=1 GPORTAL_USER=... GPORTAL_PASS=... uv run pytest -m integration
uv run python -m compileall gcom.py main.py administrative_division.py prefecture_bbox.py lst_analysis.py analysis scripts
```

## GitHub Actions

GitHub Actions では `workflow_dispatch` の手動実行で実ダウンロード確認を行います。
integration テストは実際に G-Portal から 1 件ダウンロードし、その HDF5 を共有 fixture として使って検証します。
ローカルで integration テストを流すときは `RUN_GPORTAL_INTEGRATION=1` と `GPORTAL_USER` / `GPORTAL_PASS` が必要です。
CI でも local と同じく public Playwright base image を使って server image を build してから integration テストを実行します。
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
