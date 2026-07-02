---
description: "Task list — セグメント別診断 (047)"
---
# Tasks: セグメント別モデル-市場診断
- [X] T001 [US1] `eval/src/horseracing_eval/segment_edge.py`(新): market_edge の fold 収集を流用し (race_id, horse_id, p, q, win) を集める → races/entries から事前登録セグメント属性を bulk 結合(debut=厳密前 started 無し)→ 軸別に n/勝率/LLp/LLq/gap/平均 の表(SegmentEdgeReport、決定論ソート)
- [X] T002 [P] [US1] `eval/tests/`: セグメント割当の網羅(Σn=全体)・決定論・**属性リーク境界(結果変更で割当不変)**・q_band/dist_band/class 境界値
- [X] T003 [US1] `training/src/horseracing_training/cli.py`: `segment-diagnostic` サブコマンド(feature-diagnostic 同型、--from/--to、表を print)
- [X] T004 [US2] 実 DB 実行(background 可)→ 所見を spec/research + メモリに記録(SECONDARY 注記)
- [X] T005 [P] eval/training スイート緑
- [X] T006 [P] CLAUDE.md 047 サマリ(マージ時に追記)
