# 175 tasks — 判断記録の採点

順序は「10/31 前に要るもの → 純関数 → CLI → 検証」。[P] は並列可。各タスクの受入は plan D1〜D13 と quickstart を正本とする。

## Phase 0 — T0(実 DB・読み取りのみ)

- [x] T001 parquet 往復の sha 一致を実 DB の 1 日分(2026-10-25・455 行・120 列)で確認 → **一致(parquet・pickle とも)**。dtype の表示差は race_date/grade/dist_band の 3 列(str 表現の差・sha は不変)。再現ゲート(ビット一致)が最終防衛線。→ parquet 採用(D10)。

## Phase 1 — 174 の小変更(10/31 前)

- [x] T002 `live/near_post.py`: `save_day_features(day_features, features_dir) -> Path|None`(`<day>/features-<sha12>.parquet`・存在時は書かない・失敗は警告で捕捉継続)と `load_day_features(day, sha256, features_dir) -> DayFeatures|None`(読込後 `frame_sha256` 照合・不一致は None)。
- [x] T003 `live/cli.py near-post-capture --features-dir`(既定 `artifacts/near_post`)・dry-run でも保存。
- [x] T004 単体テスト: 保存→読込 sha 一致・既存ファイルを上書きしない・書き込み失敗で例外を外に出さない。

## Phase 2 — eval 純関数

- [x] T005 `eval/decision_quote_scoring.py`: `QuoteHorse`/`QuoteRecord`/`classify`(FR-002 の 10 段)/`marked`(列別の目印)/`column_tally`/`ratio_ci`/`paired`(同期 replicate の行差)/`verdict`(`display_update_rule.decide`)/診断(`calibration_cells`・`price_drift`・`retention`)/`normalize_148`/`build_report`。
- [x] T006 単体テスト `eval/tests/unit/test_decision_quote_scoring.py`: funnel 合計=件数・順序(2 欠陥→先の段)・同着=各勝馬の額・払戻欠落=レース除外・恒等候補=差 0 区間 [0,0]・決定論・帯境界・`decide` 結線(299/300)・語彙(禁止語なし)・window_out が capture_target の k を使う。

## Phase 3 — live CLI

- [x] T007 `live/near_post_score.py`: `load_quote_records(session, from, to)`(bulk 4 クエリ+確定単勝の来歴条件)/`load_candidates(path)`+sha 照合/`replay_race`(parquet or 再構築 → `rows_with_quote` → 現行再予測 → ビット一致 → 候補予測)/`run_score(...) -> dict`。
- [x] T008 `live/cli.py near-post-score`(contracts/cli.md)・凍結 JSON の sha 照合・`--out` 既存拒否・終了コード。
- [x] T009 live 単体テスト(fake predictor): 再現一致/不一致・候補 sha 不一致→終了 2・登録日前後の振り分け。
- [x] T010 live 統合テスト(testcontainers): decision_quotes 2 行+結果+払戻 → 集計=手計算・DB 行数不変。

## Phase 4 — 境界

- [x] T011 api: `test_prospective_funnel_parity_175.py`(同一合成レースで B8 と 175 の共有段が一致)。
- [x] T012 features leak-guard に `near_post_score`/`decision_quote_scoring` トークンを追加。

## Phase 5 — 運用

- [x] T013 `specs/175/candidates.json` に第 1 候補 `ens15-norm148-v1` を登録(sha=コード)。
- [x] T014 `scripts/near_post_ops.sh`: capture/morning に `--features-dir`、`score` サブコマンド。`specs/174/manual.md` に夜の 4 番目。
- [x] T015 実 DB で行 0 の完走(候補つき)と predictor のビット決定論を確認・拒否経路は単体テスト・result-review.md。quickstart §3〜§5 の実データ検証は 10/31 の試運転後。
