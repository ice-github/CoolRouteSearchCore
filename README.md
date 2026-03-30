# CoolRouteSearchCore

JAXA G-Portal の GCOM-C HDF5 を Docker 上の Playwright で取得する最小構成です。

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

## TAKT

このリポジトリでは `takt` を project-level の `.takt/` だけで運用します。`~/.takt` は使いません。

ローカルでは常に最新 stable の `takt` を `npx` 経由で実行します。

```bash
npm run takt
npm run takt:run
npm run takt:list
```

標準フローは対話でタスクを整理してキューし、その後 `npm run takt:run` で worktree 実行する形です。`Execute now` は現在の working tree を直接変更するため、通常運用では使いません。

`.takt/config.yaml` では provider を `codex` に固定し、quality gate はこの Python プロジェクト向けに `uv sync --group dev`、`uv run pytest`、`uv run python -m compileall gcom.py main.py administrative_division.py prefecture_bbox.py scripts playwright` を使うようにしています。

## テスト

```bash
uv run pytest
uv run python -m compileall gcom.py main.py administrative_division.py prefecture_bbox.py scripts playwright
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
