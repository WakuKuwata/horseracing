# Quickstart / 検証ガイド: 馬・騎手プロフィールページ

end-to-end で「レース詳細→馬/騎手リンク→プロフィール表示」を検証する手順。実装詳細は tasks.md / 実装フェーズ。本書は前提・起動・確認のみ。

## 前提

- ローカル Postgres（`horseracing` DB、`localhost:15432`）が head（`alembic upgrade head`）。**本 feature はマイグレーション追加なし**。
- 014 API と front は既存どおり起動可能。api テストは testcontainers で実 PostgreSQL を使う（マイグレーション適用済み）。

## 起動（ローカル）

1. **014 read-only API**: `uvicorn horseracing_api.app:app`（変更後も read-only・全 GET）。
2. **front**: 既存 dev サーバ。新ルート `/horses/:id`・`/jockeys/:id`。

## 検証シナリオ

### US1: 馬プロフィール（P1）

1. レース詳細を開く → 出走表の **馬名がリンク**（canonical ID の馬のみ。`nk:` surrogate は非リンク）。
2. 馬名クリック → `/horses/{horse_id}` に遷移。
3. 基本（名前/性/生年/データ元）＋血統（父/母/母父の名前）＋通算成績（出走/勝/連対率/複勝率/平均着順）＋レース別履歴（新しい順・ページング）が表示される。
- **期待**: 1 クリックで到達（SC-001）。成績が実績と一致（SC-003、手計算と照合）。実績ゼロ馬でも壊れず 0 件表示（SC-004）。

### US2: 騎手プロフィール（P2）

1. レース詳細の **騎手名がリンク**（ID 解決行のみ）。
2. クリック → `/jockeys/{jockey_id}`。騎乗成績（騎乗数/勝率/連対率/複勝率）＋騎乗履歴が表示。
- **期待**: 1 クリックで到達（SC-002）。ID 欠損/surrogate の騎手名は非リンク（SC-005）。

## API スモーク（curl）

```
GET /api/v1/horses/{horse_id}            -> 200 HorseProfile / 404
GET /api/v1/horses/{horse_id}/history?page=1&page_size=20 -> 200 Page[HorseHistoryRow]
GET /api/v1/jockeys/{jockey_id}          -> 200 JockeyProfile / 404
GET /api/v1/jockeys/{jockey_id}/history  -> 200 Page[JockeyHistoryRow]
```

## 不変条件テスト（必須）

- **read-only 不変**: 014 の全エンドポイントが GET のみ・行数不変（既存 `test_readonly_invariant.py`＋`test_no_write_boundary.py` が、新 endpoint 追加後も通る）。
- **集計正当性**: 既知の seed（数走・取消・中止を含む馬/騎手）で 出走数/勝率/連対率/複勝率/平均着順 が母数規則どおり（取消は母数外、中止は母数内・着順率外）。
- **Unknown と 0**: 実績ゼロ馬は率 null（'--' 表示）、出走数 0。
- **リンク化規則**: `nk:` surrogate と null の馬/騎手名は非リンク。canonical のみ `<Link>`。
- **エラー**: 未存在 ID→404、未処理 500 なし（馬/騎手 ID は固定フォーマットなし→形式 422 は設けない）。
- **leak-guard**: api は features/training を import しない（既存テスト）。プロフィール表示値はモデル特徴に渡らない。
- **front 型 drift**: 014 の committed `openapi.json` と生成型が一致（`HorseEntry` の jockey_id/trainer_id 追加＋新 schema 反映）。
- **front 状態**: 読み込み中／空（実績ゼロ）／型付きエラー の 3 状態が区別される。
