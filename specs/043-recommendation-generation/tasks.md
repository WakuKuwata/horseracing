---
description: "Task list — 製品を実データで通す・買い目生成 (043)"
---

# Tasks: 製品を実データで通す — 買い目(推奨)生成

**Input**: [plan.md](plan.md) / [spec.md](spec.md)（codex second-opinion 反映）

**Organization**: MVP = US1（読み出し是正 + 単一セット生成 + 実 DB E2E）。US2 = ops job + ボタン。US3 = backfill。

## Phase 1: Setup / 前提確認
- [X] T001 前提確認: 予測(prediction_run)+ 単勝オッズ + real_exotic_odds の有無を実 DB で確認し、E2E に使えるレース(予測+オッズあり)を1つ特定。`generate_kelly_recommendations` が単一セット(EV+stake)を出すこと・betting が serving を transitively import することを再確認

## Phase 2: US1 - 読み出し是正 + 1レース生成 E2E (P1, MVP)
- [X] T002 [US1] `api/src/horseracing_api/queries.py`: `exotic_recommendations(session, race_id, *, prediction_run_id)` に変更し、選択 run で絞る。`routers/recommendations.py`: select_prediction_run の run を渡す(run 無→typed-empty)
- [X] T003 [US1] `api/src/horseracing_api/schemas.py`: `RecommendationRow` に `recommendation_id: str` と `stake_fraction: float | None` を追加。router で map
- [X] T004 [P] [US1] `api/tests/`: 推奨読み出しが選択 run のみ・重複排除・stake_fraction/recommendation_id 露出・read-only(GET のみ)を検証(_synth に recommendation シード追加)
- [X] T005 [US1] `betting/src/horseracing_betting/cli.py`: `recommend-serve --race-id`(または `--prediction-run`)サブコマンド = API と同一 run 選択則(active→最新)で run 解決 + **選択 run に推奨があれば冪等 skip** + `generate_kelly_recommendations` 呼び出し。オッズ無/予測無は非ゼロ exit or 明示 skip 出力
- [X] T006 [P] [US1] `betting/tests/`: recommend-serve の run 解決・冪等 skip・odds無 skip をテスト
- [X] T007 [US1] `front/src/components/RecommendationPanel.tsx`: stake_fraction 表示・行 key=recommendation_id・pseudo バッジ維持。`front/openapi.json`/`src/api/types.ts` 再生成(schema 変更反映・drift-check)
- [X] T008 [P] [US1] `front/src/components/RecommendationPanel.test.tsx`: stake 表示・pseudo バッジ不変条件・重複行 key
- [X] T009 [US1] 実 DB E2E: T001 のレースで `recommend-serve` 実行 → recommendations 永続化 → API が選択 run のみ返す → RecommendationPanel 表示確認(手動1レース、codex MVP)

## Phase 3: US2 - ops recommend job + front ボタン (P2)
- [X] T010 [US2] `ops/src/horseracing_ops/__init__.py`+`schemas.py`: `JOB_TYPE_RECOMMEND="recommend"`・`KindT += "recommend"`。`enqueue.py`: `enqueue_recommend`(in-flight dedup)
- [X] T011 [US2] `ops/src/horseracing_ops/runner.py`: `run_recommend` = betting CLI を `uv run --project betting` subprocess 実行(028 の cwd/VIRTUAL_ENV strip/timeout/exit-code→succeeded/skipped/failed を踏襲)。`worker.py` で recommend をドレイン
- [X] T012 [US2] `ops/src/horseracing_ops/routers/recommend.py`(新): `POST /races/{race_id}/recommend` → 202 JobAccepted(028 predict router 同型)。`app.py` 登録
- [X] T013 [P] [US2] `ops/tests/`: recommend job の enqueue/route/subprocess mapping + **境界テスト維持**(ops が betting/serving/ml を import しない)
- [X] T014 [US2] `front/ops-openapi.json`/`ops-schema.d.ts` 再生成 + `src/api/opsClient.ts` に `recommendRace`+kind。`src/components/RecommendButton.tsx`(新, PredictButton 同型)。`RaceDetailPage.tsx` 結線(予測ボタンと別)
- [X] T015 [P] [US2] `front/src/components/RecommendButton.test.tsx`: ジョブ状態ラベル・完了で recommendations invalidate

## Phase 4: US3 - 一括生成(backfill) (P3)
- [X] T016 [US3] `betting/src/horseracing_betting/cli.py`: `recommend-backfill --from --to`(日付範囲)= 予測+オッズあり run に冪等生成、per-race 例外隔離、生成/スキップ理由別件数を集計出力
- [X] T017 [P] [US3] `betting/tests/`: backfill 冪等・per-race 隔離・件数集計

## Phase 5: Polish & 横断
- [X] T018 [P] ops 境界テスト緑(SC-005)・014 read-only テスト緑(SC-006)
- [X] T019 [P] front drift-check(openapi/ops-openapi/型 コミット一致)+ pseudo バッジ不変条件
- [X] T020 [P] 全パッケージ lint/test 緑(api/ops/betting/front/db)
- [X] T021 実 DB: 数レース backfill → 一覧/詳細横断で実データ表示確認
- [X] T022 [P] `CLAUDE.md` 043 サマリ追記(main マージ時に main の実内容へ追記=分岐衝突回避)

## Dependencies
- US1(T002-T009)が土台。T002/T003(read 是正)→ T007(front)→ T009(E2E)。T005(生成)→ T009。
- US2 は US1 後(読み出しが正しくないとボタンで出しても壊れる)。T010→T011→T012→T014。
- US3 は生成(T005)後。
- 憲法: II(ops 境界・リーク)/III(不変条件テスト)/V(冪等・append-only)/VI(schema/openapi 先行・migration なし)。

## 実装戦略
1. **読み出しを正す**(codex 最重要)→ 2. 1レース手動生成で E2E → 3. ops job+ボタン → 4. backfill。
2. 単一セット=`generate_kelly_recommendations` のみ(exotic 二重生成しない)。
3. 冪等スキップ + 選択 run 絞りで append-only の重複を表示に出さない(スキーマ変更なし)。
