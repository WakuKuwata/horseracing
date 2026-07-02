---
description: "Task list — 単勝推奨の製品結線 (045)"
---
# Tasks: 単勝(win)推奨の製品結線

## Phase 1: US1 - 読み出し+生成結線 (P1)
- [X] T001 [US1] `api/queries.py`: run 絞り推奨クエリを ALL bet types に拡張(win 含む)。`routers/recommendations.py`: win 行 selection dict→[horse_number] 正規化・horse_number 欠損除外
- [X] T002 [P] [US1] `api/tests/`: win 行が返る・selection=[馬番]・real 表示フィールド・欠損除外・run 絞り維持
- [X] T003 [US1] `betting/recommend.py`: `generate_recommendations(..., cfg: KellyConfig|None=None)` — cfg 時に 016 single_kelly+allocate_kelly で stake_fraction 付与(win 群相互排他)・lv に kelly cfg 反映。cfg=None は従来 flat(後方互換)
- [X] T004 [P] [US1] `betting/tests/`: Kelly opt-in の stake 付与・flat 後方互換・決定論
- [X] T005 [US1] `betting/cli.py`: recommend-serve の冪等を群単位(win/exotic)に細分化し、win=007(cfg 付き)+ exotic=016 の両群を生成。SKIPPED/OK 出力は群別に
- [X] T006 [P] [US1] `betting/tests/`: 群単位冪等(exotic 済み run に win 追補・逆も・完全 skip)
- [X] T007 [US1] 実 DB E2E: populate 済みレースに recommend-serve 再実行 → win 追補 → API が win 行(real)を返す → front 表示確認

## Phase 2: US2 - backfill 追補 (P2)
- [X] T008 [US2] `betting/cli.py`: recommend-backfill を群単位冪等に(win 追補対応)・件数集計を群別に
- [X] T009 [P] [US2] `betting/tests/`: backfill の win 追補・reconciliation
- [X] T010 [US2] 実 DB: populate 済み範囲に backfill 再実行 → win 追補確認

## Phase 3: Polish
- [X] T011 [P] betting/api/front スイート緑・front pseudo 不変条件・drift-check(openapi 変更があれば再生成)
- [X] T012 [P] `CLAUDE.md` 045 サマリ(マージ時に追記)
