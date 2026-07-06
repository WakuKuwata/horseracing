# Quickstart / 検証: 複数モデル切り替え基盤

実 DB E2E で受入シナリオを確認する手順(実装後)。前提スタック = [[local-db-setup]](docker postgres + API:8000 + ops worker)。

## 前提

- migration 0011 適用済(`display_name`/`purpose` 列)。
- 同一レースに 2 モデルの予測 run が永続化済(例: 採用モデル lgbm-055 + 別モデル)。無ければ:
  ```
  # 別モデルの予測を生成(044 の既存冪等 backfill、--model-version 指定)
  uv run --project serving serving predict-backfill --from <day> --to <day> --model-version <other-mv>
  ```

## US1: 用途ラベル

1. 用途メタを設定:
   ```
   uv run --project training <registry-cli> set-model-label \
     --model-version lgbm-055 --display-name "意思決定支援モデル" --purpose "市場から独立した予測"
   ```
2. `GET /api/v1/models` に `display_name`/`purpose` が透過されることを確認(未設定モデルは null)。
3. admin レジストリ/詳細ページで用途が表示され、`model_version` は不変。

**期待**: 技術 ID を知らなくても用途が言葉で判別できる(SC-001)。未設定=null 表示(0/空埋めしない)。

## US2: モデル指定予測(API)

1. `GET /races/{id}/predictions`(model 未指定)→ 採用モデルの run。本 feature 前と **run_id・各馬確率が同一**(SC-002)。
2. `GET /races/{id}/predictions?model_version=<other-mv>` → その run の確率・監査を返す。
3. `GET /races/{id}/predictions?model_version=<no-run-mv>` → **404 `prediction_unavailable`**(active に戻らない)。
4. `GET /races/{id}/predictions?model_version=<nonexistent>` → 404(500 でない)。
5. 応答 `available_models` に、このレースに run を持つ 2 モデルが並び、返した run のモデルだけ `is_selected=true`。

## US3: front セレクタ

1. レース詳細を開く → 採用モデルの予測が **採用バッジ付き**で表示、`available_models` からセレクタが描画される。
2. 別モデルを選択 → `?model_version=` 付きで再取得し予測が切替、どのモデルを見ているか常時明示。
3. run の無いモデルを直接指定した場合(defensive)→ loading/空/一般エラーと**別の「未生成」状態**(専用 testid)。

## 回帰・不変

- 既存 api テスト(予測未指定経路)無改修で緑(SC-002)。
- read-only: 全 path GET 不変テスト緑。
- OpenAPI: front/admin `openapi.json`+`schema.d.ts` 再生成、byte 一致 + drift-check 緑(SC-005)。
- migration head assert(features/live 等)0011 更新済で緑。
- リーク境界: leak-guard テスト不変(特徴量非関与)。FEATURE_VERSION 不変。
