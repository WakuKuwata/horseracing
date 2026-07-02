# Implementation Plan: 製品を実データで通す — 買い目(推奨)生成 (043)

**Branch**: `043-recommendation-generation`(worktree, base=main 7cd6ba2) | **Date**: 2026-07-02 | **Spec**: [spec.md](spec.md)

## Summary

`recommendations` テーブルが空で買い目/EV/Kelly が画面に出ない製品ギャップを、既存生成ロジック(016 Kelly=EV 選定+stake)を製品フローに結線して埋める。codex 実査で「読み出しが prediction_run で絞られず・stake_fraction 未露出・exotic/Kelly 二重生成」が判明したため、**読み出し是正を先に**行い、**単一セット(generate_kelly_recommendations のみ)**を生成、ops→betting subprocess(028 同型)でオンデマンド起動、backfill CLI で一括生成。スキーマ変更なし・read-only 014 不変・netkeiba 不要。

## Technical Context

**Language**: Python 3.12(db/api/ops/betting)+ TypeScript(front)
**Storage**: 既存(recommendations/ingestion_jobs)。**migration 追加なし**
**Testing**: pytest(api/ops testcontainer)+ Vitest/MSW(front)+ openapi drift-check
**制約**: ops は ML/betting を import しない(subprocess)。014 read-only。append-only 維持

## 主要な設計判断(codex 反映)

1. **単一生成器 = `generate_kelly_recommendations`**: EV 選定(`_blended_bets`=exotic と同一)+ Kelly stake を1セットで永続化。exotic を別途呼ばない(重複回避・stake_fraction 込み)。
2. **読み出しを選択 run で絞る**: `api.queries.exotic_recommendations(race_id)` → `(race_id, prediction_run_id)` で絞る。router は既に select_prediction_run で run を持つ → その run を渡す。
3. **schema 是正**: `RecommendationRow` に `stake_fraction` と `recommendation_id` を追加(read-only のまま)。
4. **冪等生成**: 選択 run に推奨が既にあれば生成スキップ。append-only 維持・重複防止。
5. **ops recommend job**: 028 predict と同型。betting CLI を `uv run --project betting` subprocess で実行(cwd=betting・VIRTUAL_ENV strip・timeout・exit-code→succeeded/skipped/failed)。**明示 prediction_run 指定**(API 選択則と一致)。
6. **odds/predict 無 → skipped(理由)**。succeeded+0 を作らない。
7. **betting CLI**: 既存 `recommend`/`kelly` サブコマンドに「選択 run 解決 + 冪等 skip」を持つ薄い `recommend-serve`(または既存拡張)を追加。API と同じ run 選択則を betting 側でも使う(または run_id を引数で渡す)。

## Project Structure(変更ファイル)

```
api/src/horseracing_api/
├── queries.py            # exotic_recommendations に prediction_run 絞り
├── schemas.py            # RecommendationRow += stake_fraction, recommendation_id
└── routers/recommendations.py  # 選択 run を query に渡す

betting/src/horseracing_betting/
└── cli.py                # recommend-serve: run 解決(active→最新)+ 冪等 skip + Kelly 生成

ops/src/horseracing_ops/
├── __init__.py schemas.py  # JOB_TYPE_RECOMMEND, KindT += "recommend"
├── enqueue.py            # enqueue_recommend
├── runner.py             # run_recommend(betting CLI subprocess, 028 同型)
├── worker.py             # recommend job をドレイン
└── routers/recommend.py  # POST /races/{race_id}/recommend → 202(028 同型)

front/
├── ops-openapi.json / ops-schema.d.ts  # recommend 型再生成(drift-check)
├── src/api/opsClient.ts  # recommendRace + Job kind
├── src/components/RecommendButton.tsx  # 新(PredictButton 同型)
├── src/components/RecommendationPanel.tsx  # stake_fraction 表示・行 key=recommendation_id
└── src/pages/RaceDetailPage.tsx  # RecommendButton 結線
```

## Constitution Check

- [x] **II リーク**: 推奨値はモデル特徴に戻さない/結果を選定に読まない(betting 既存)。ops 境界維持
- [x] **III 評価先行**: 表示・運用結線で OOS 対象外。SC-001〜006 の不変条件で機械検証
- [x] **IV 確率整合**: 009/EV は betting 既存(canonical field 一致確認済み)。介入なし
- [x] **V 再現性・監査**: append-only 維持・logic_version 記録・冪等スキップで重複制御(スキーマ変更なし)
- [x] **VI 分割規律**: API 契約(schema/openapi)先行で front。migration なし
- [x] **品質ゲート**: codex second-opinion 取得・反映済み(読み出し是正先行・単一セット・明示 run・skipped 明確化)

## 実装順(codex MVP)

1. **US1 読み出し是正 + 1レース生成 E2E**(最優先): queries run 絞り → schema stake_fraction/recommendation_id → RecommendationPanel 表示 → betting で1レース手動生成(明示 run)→ 実 DB で表示確認。ここまでで「空 UI 解消」が成立
2. **US2 ops recommend job + front ボタン**: ops 拡張(28 同型)+ opsClient + RecommendButton + drift-check
3. **US3 backfill CLI**: 冪等一括生成 + 監査集計
4. 横断: ops 境界テスト・read-only テスト・front pseudo バッジ不変条件・全スイート緑

## Complexity Tracking

スキーマ変更なし・新規パッケージなし。migration head 不変。read-only 契約は query/schema 是正のみで維持(新規書き込みエンドポイントは ops 側 = 既存 024/028 パターン)。
