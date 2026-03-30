# CoolRouteSearchCore

JAXA G-Portal の GCOM-C HDF5 を Playwright で取得し、`uv` 上で LST を解析する最小構成です。

## 必要な環境変数

- `TAKT_OPENAI_API_KEY`
- `GPORTAL_USER`
- `GPORTAL_PASS`

## 必要なコマンド

- `node`
- `npm`
- `uv`
- `docker`

## セットアップ

```bash
uv sync --group dev
npm install
```

## 実行

```bash
uv run python main.py
```

`main.py` はリポジトリ同梱の都道府県 `bbox` データから愛知県の範囲を使い、CSW で対象 HDF5 の URL を取得して、未取得ファイルのみを Docker 上の Playwright でダウンロードします。

## 名古屋市 LST 解析

名古屋市の 2025 年 7 月〜8 月 LST をサンプル点ごとに平均化するための入口として [`lst_analysis.py`](/home/ubuntu/workspace/CoolRouteSearchCore/.worktrees/nagoya-lst-analysis/lst_analysis.py) を追加しています。

- `estimate_sampling_load(area_name, start, end, [1000, 100, 10])`
- `generate_sampling_points(area_name, spacing_m, output_dir)`
- `compute_lst_point_means(area_name, start, end, spacing_m, output_path)`

解析は `uv` で直接動きます。

- 取得済み HDF5 は既存の `download/` を再利用します。
- 最新の行政区域ポリゴンは MLIT の `KsjTmplt-N03-2025` から取得します。
- HDF5 は `Image_data/LST` と `Image_data/QA_flag` を `rasterio` で直接開き、GCOM-C の等面積投影上でサンプル点評価します。
- 可視化成果物として `sampling_preview_*.png`、`scene_coverage_preview_*.png`、`sampling_points_*.geojson` を出力します。

## TAKT

このリポジトリでは `takt` を project-level の `.takt/` だけで運用します。`~/.takt` は使いません。

ローカルでは常に最新 stable の `takt` を `npx` 経由で実行します。

```bash
npm run takt
npm run takt:run
npm run takt:list
```

標準フローは対話でタスクを整理してキューし、その後 `npm run takt:run` で worktree 実行する形です。`Execute now` は現在の working tree を直接変更するため、通常運用では使いません。

`.takt/config.yaml` では provider を `codex` に固定し、quality gate はこの Python プロジェクト向けに `uv sync --group dev`、`uv run pytest`、`uv run python -m compileall gcom.py main.py administrative_division.py prefecture_bbox.py lst_analysis.py analysis scripts playwright` を使うようにしています。

## テスト

```bash
uv run pytest
uv run python -m compileall gcom.py main.py administrative_division.py prefecture_bbox.py lst_analysis.py analysis scripts playwright
```

## GitHub Actions

GitHub Actions では 2 系統の workflow を用意します。

- `TAKT`: `pull_request` の自動レビューと、OWNER による `@takt` コメント起動
- `G-Portal Integration`: `workflow_dispatch` または定期実行での実ダウンロード確認

GitHub Secrets:

- `GPORTAL_USER`, `GPORTAL_PASS`: `G-Portal Integration` に必要
- `TAKT_OPENAI_API_KEY`: `TAKT` review を有効にする場合のみ必要。未設定なら `TAKT` workflow は自動で skip されます

また、`takt` が pull request を作成・更新できるように、リポジトリ設定の `Settings > Actions > General > Workflow permissions` で `Allow GitHub Actions to create and approve pull requests` を有効にしてください。

## 都道府県 bbox データ

全都道府県の `bbox` は `data/prefecture_bboxes.json` に含めています。

- 形式は `[floor(min_lon), floor(min_lat), ceil(max_lon), ceil(max_lat)]` です。
- 生成元は国土数値情報の「行政区域（ポリゴン）」です。

## bbox の更新方法

行政界データの更新に追従したい場合は、次のコマンドで `bbox` 一覧を再生成できます。

```bash
uv run python scripts/update_prefecture_bboxes.py
```

このスクリプトは国土数値情報から全都道府県の行政区域データを取得し、`data/prefecture_bboxes.json` を上書きします。ダウンロード済み ZIP や展開済みシェープファイルは既存の `download/` と `workspace/` を再利用します。
