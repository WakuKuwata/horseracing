# Quickstart: 意思決定支援の表示強化 (021)

実データ（horseracing DB, [[local-db-setup]]）で各 US の受入を確認する検証ガイド。実装詳細は tasks.md。

## 前提
- DB が head=0006、2007–2024 ingest 済み（既存）。
- reliability 表示には walk-forward OOS reliability を含む `model_versions` 行が必要（US2）。無ければ training の adoption 経路で 1 つ生成。
- `DATABASE_URL=postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing`

## US1: p と q の併記
1. API 起動 → `GET /api/v1/races/{race_id}/predictions`。
2. 各 horse に `win`(p) と `market_win_prob`(q) が**別フィールド**で入り、有効オッズなしの馬は `market_win_prob=null`（0 でない）。
3. `canonical_consistent=true` のとき `Σ market_win_prob ≈ 1`（canonical field 上）。
4. front レース詳細で p と q が併記され、乖離が**中立提示**（買い/色/ソートなし）、q に PseudoBadge、`odds_as_of`/`odds_source` 表示。
5. 期待: SC-001（p/q/差が 1 画面）、SC-002（pseudo ラベル 0 件漏れ）、SC-007（中立提示）、SC-008（不一致時は比較不可表示）。

## US2: 校正の可視化
1. `GET /api/v1/models/{model_version}/calibration` → `oos=true`、`source="walk_forward_oos"`、`bins[]`（pred_mean/realized_rate/count）、`ece`、`valid_years`、`n_total`。
2. 少数件 bin は `suppressed=true`。reliability 未収録モデルは 404 typed（サイレント空でない）。
3. front の校正図で予測 vs 実現が件数付きで描画、retrospective/OOS と model_version/期間が監査表示。
4. 期待: SC-003（校正図 + 件数 + retrospective 明示）、SC-008（OOS 判別可）。

## US3: データ裏付け
1. predictions の各 horse `data_backing`（weak/medium/strong/null）。
2. **採用判定（先行）**: 過去 OOS データで「weak 群の校正/誤差が medium/strong より悪い」ことを eval 側スクリプトで確認。確認できなければ `data_backing` は出さず US3 を defer（FR-012）。
3. front で裏付け弱が区別表示され「的中確信ではない」明示。指標が結果/オッズ不使用（事前情報のみ）。
4. 期待: SC-004（裏付け目安・低裏付け区別）、SC-006（leak なし）。

## 横断ゲート
- **read-only**: 新エンドポイントが `GET` のみ・write 関数を呼ばない（api テスト）。
- **契約同期**: live `/openapi.json` == committed `front/openapi.json`、生成型 drift-check 緑（SC-005, VI）。
- **leak-guard**: `market_win_prob`/`data_backing`/reliability/EV 等がモデル `model_input_features` に出現しない（SC-006, II）。
- **pseudo invariant**: front に「ラベルなし pseudo/推定値 0 件」（SC-002, V）。
- 期待 lint/test: `uv run ruff check` + `uv run pytest`（api/eval）、`pnpm test` + 型生成 drift（front）緑。
