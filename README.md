# CoolRouteSearchCore

JAXA G-Portal の GCOM-C HDF5 を Docker 上の Playwright で取得する最小構成です。

## 必要な環境変数

- `GPORTAL_USER`
- `GPORTAL_PASS`

## 必要なコマンド

- `uv`
- `docker`

## セットアップ

```bash
uv sync
```

## 実行

```bash
uv run python main.py
```

`main.py` はリポジトリ同梱の都道府県 `bbox` データから愛知県の範囲を使い、CSW で対象 HDF5 の URL を取得して、未取得ファイルのみを Docker 上の Playwright でダウンロードします。

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
