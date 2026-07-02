# Implementation Plan: 製品をデータで満たす (044)

**Branch**: `044-product-population`(worktree, base=main 06861df) | **Date**: 2026-07-02 | **Spec**: [spec.md](spec.md)

## Summary
予測の日付範囲 backfill を serving に追加(043 recommend-backfill と対)。run_serving を per-day で回し p-parity を保ちつつ、active モデル run が無いレースのみ冪等生成、reconciliation 出力。これで predict-backfill → recommend-backfill(043)で製品を実データで満たせる。新予測ロジック無し・スキーマ変更無し・read-only 014 不変・netkeiba 不要。codex CLI 利用不可のため single-opinion(コードベース根拠)。

## Technical Context
- Python 3.12。serving(run_serving 流用)+ betting(043 既存)。
- Testing: pytest(serving testcontainer)。p-parity/冪等/reconciliation の不変条件テスト。
- 制約: p-parity(per-day build)、active-model 冪等、リーク境界不変、migration なし。

## 設計判断
1. **serving/pipeline.py に `run_serving_backfill(session, *, date_from, date_to, model_version=None, force=False)`**: モデルを1回ロード。日ごとに build_feature_matrix(end_date=D) → その日のレースを予測。run_serving の per-race ループ本体を共有ヘルパ `_predict_persist` に抽出し両者で再利用(重複回避・p-parity 保証)。
2. **冪等**: 各レースについて「model.model_version の prediction_run が存在?」を確認し、あればスキップ(force で無視)。件数を BackfillCounts(generated/skip_exists/skip_no_started/error, per-day error 隔離)で集計。
3. **CLI `serving predict-backfill --from --to [--model-version] [--force]`**: run_serving_backfill を呼び reconciliation を print。
4. US2 は実データ運用(predict-backfill + 043 recommend-backfill)+ 製品表示検証(手動)。

## Project Structure(変更)
```
serving/src/horseracing_serving/
├── pipeline.py   # run_serving の loop body を _predict_persist に抽出 + run_serving_backfill 追加
└── cli.py        # predict-backfill サブコマンド
serving/tests/    # p-parity・冪等・reconciliation・per-day 例外隔離
```

## Constitution Check
- [x] II リーク: run_serving 流用=新リーク面なし。結果非参照・as-of のみ・オッズ/結果は特徴に戻さない
- [x] III 評価先行: 表示/運用 feature。p-parity/冪等を不変条件テストで機械検証
- [x] IV 確率整合: 009 非介入(run_serving 経由)
- [x] V 再現性: p-parity(per-day build)・append-only(force 時新 run)・logic_version 記録(既存)
- [x] VI 分割規律: migration なし・read-only 014 不変・CLI のみ
- [x] 品質ゲート: codex CLI 利用不可を明示(未インストール)。設計は 019/025/026/043 の既存根拠に基づく

## 実装順
1. US1: pipeline リファクタ(_predict_persist 抽出)+ run_serving_backfill + CLI + テスト(p-parity/冪等/reconciliation)。
2. US2: 実 DB populate(predict-backfill + recommend-backfill)+ 製品表示検証。
3. Polish: serving/betting/api テスト緑・CLAUDE.md(main マージ時)。
