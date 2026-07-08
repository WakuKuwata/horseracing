# Tasks: 060 市場残差型・精度最優先モデル

**Input**: [spec.md](spec.md) / [plan.md](plan.md) / [research.md](research.md) D1–D11 / [contracts/market-offset.md](contracts/market-offset.md)

**Tests**: 憲法の品質ゲートにより leakage/確率整合性/評価ハーネス系テストは必須(オプションではない)。

**組織方針**: spike go/no-go(FR-009)を Foundational に置き、no-go なら Phase 3 以降に進まない。

## Phase 1: Setup

- [X] T001 training→probability の import 可否を確認 → **training は probability 非依存(pyproject: db/features/eval のみ)かつ probability→eval の依存方向のため、純関数再実装+手計算期待値の定義同一性テスト側を採用**(D11 フォールバック)。新規クロスパッケージ依存なし

## Phase 2: Foundational — offset コア + spike go/no-go(全ストーリーをブロック)

- [X] T002 [P] `training/src/horseracing_training/market_offset.py` 新規: q devig(D11 の方式)・`log(clip(q,1e-6,1))`・レース単位有効性判定(null/≤0/非数が 1 頭でもいれば invalid)の純関数 + 単体テスト(probability 実装との定義同一性を含む、INV-M1)
- [X] T003 `training/src/horseracing_training/cond_logit.py`: `pl_topk_objective`(および cond_logit_objective)に `offsets` 対応 — 閉包内で `race_softmax(preds + offsets)`(D1、init_score 不使用)。`offsets=None` は既存バイト不変。単体テスト: レース内定数シフト不変・offset 込み p で grad が計算されること
- [X] T004 `training/src/horseracing_training/win_model.py`: `WinModel.fit/predict` に `offsets` 引数(None=既存経路バイト不変)。fit は sort 順に整列した offsets を objective 閉包へ、predict は `raw_score=True` 出力に offsets を自前加算してから `race_softmax`。単体テスト
- [X] T005 [P] `training/src/horseracing_training/dataset.py`: `TrainingMatrix.frame` に補助列 `mkt_odds`(race_horses.odds、feature_cols 外・finish_rank 同型、D3)。テスト: feature_cols/feature_hash/カテゴリ列が不変であること
- [X] T006 `training/src/horseracing_training/predictor.py`: `LightGBMPredictor(market_offset=True)` opt-in — fit で model/calib 行それぞれの offset を構成(無効オッズレースは除外し件数を fit_info_ に記録、D4)、calib held-out も offset 込みで isotonic フィット、predict_race で同一経路。**INV-M2 等価性テスト**: 情報ゼロ特徴の offset モデル(校正 identity)の出力が q(clip・再正規化済み)と一致=加算漏れの機械検出
- [X] T007 挙動型 leak-guard テスト(INV-M5)を `training/tests/` に追加: (i) 他レース・未来レースのオッズ変更で対象レース予測不変 (ii) レース結果変更で予測不変 (iii) 対象レース自身のオッズ変更で予測変化(正の対照)
- [X] T008 eval 比較ドライバ + 専用 q baseline(`market_gate.py` + CLI `market-gate-eval`): オッズ完全カバー race_id 集合を fold ごとに確定し、(candidate / q baseline / lgbm-058-acc 構成) の 3 者を**同一集合**で evaluate する(D5)。既存 MarketBaseline は floor 補完のため流用しない(codex 発見)。除外レース件数・期間分布の報告を含む
- [X] T009 **spike go/no-go = GO**(実 DB・直近 3 fold 2024-2026、`artifacts/060-spike-tail3.json`): candidate win **0.20299** < market **0.20367**(−0.00068、go 条件 PASS)・acc 構成 0.21901 に −0.016。top2 0.31823<0.31877・win ECE 0.00073<0.00112 も市場超え。top3 のみ +0.00005 毛差(full で gate c 再判定)。**オッズカバレッジ実測: eval 母集団 67,418 レースで除外 0**(DB 全体でも欠損 2 レースのみ)

**⛔ T009 が go でない限り Phase 3 以降に進まない → GO 確認済み**

## Phase 3: User Story 1 — 学習と事前登録ゲート評価 (P1)

**Goal**: フル walk-forward で事前登録ゲート(contracts の 3 MUST)を機械判定し、全通過時のみ lgbm-060-mkt を candidate 登録

**Independent Test**: 実 DB で 19-fold 評価 → ゲート合否の機械判定 → default モデル不変

- [X] T010 [US1] CLI 実装(計画から変更: `--market-offset` を train-evaluate に足すのではなく、**`market-gate-eval`(T008)+`register-market-model`** の 2 コマンド構成に。register は gate レポート JSON の config を正として最終学習+candidate 固定登録=評価済み構成との乖離を構造的に防止。`--allow-gate-fail` は明示 user-override 記録付き)
- [X] T011 [US1] `artifacts.py`: metadata/preprocessor/metrics_summary.training に `market_offset` 記録(キーは offset モデルのみ=既存モデル出力バイト不変、INV-M3 テスト済)+ `save_model_version(register_as_candidate=True)`
- [X] T012 [US1] フル 19-fold 事前登録ゲート評価 実施(`artifacts/060-full-gate.json`)。**結果: gate (a) FAIL / (b) PASS / (c) FAIL → 自動登録なし(FR-004 どおり)**。
  - overall: market win 0.20259 / acc 0.21579(公表値と一致=再評価妥当) / **candidate 0.20267**(市場に +0.00008 毛差負け)。top2 +0.00048・top3 +0.00149 悪化。win ECE は candidate 0.00058 が最良
  - **by-fold 分析: 負けは小データ初期 fold(2008-2013)のみ。2014 以降は 13/13 fold 連続で市場勝ち(−0.0004〜−0.0011、2026 fold −0.00066)**=expanding-window の初期 fold アーティファクト(市場 baseline は学習データ不要で初期 fold が構造的に有利)。spike(2024-2026)の GO と整合
  - 除外レース 0(オッズ完全カバー 67,418/67,418)
- [X] T013 [US1] **ユーザー承認により user-override 登録済み**(2026-07-08): `register-market-model --allow-gate-fail` で lgbm-060-mkt を CANDIDATE 登録(override=True が adoption.reasons に記録)。`set-model-label` で用途明示(市場情報利用・default 非使用・retrospective・2014 以降 13/19 fold 市場超え)。active は lgbm-057 のまま不変

## Phase 4: User Story 2 — serving 結線 (P2)

**Goal**: model_version 明示指定時のみ市場残差モデルで予測、オッズ欠損は typed skip、default は byte-parity

**Independent Test**: 実 DB E2E で default 不変 + lgbm-060-mkt 予測成功 + オッズなし typed skip

- [X] T014 [P] [US2] `model_loader.py`: metadata/preprocessor の `market_offset` 整合検証付き透過(キー無し=既存後方互換)、degenerate+offset は load 拒否、`raw_predict(offsets=)` 両方向 fail-closed + integration テスト
- [X] T015 [US2] `predictor.py`(win_odds→q→offset を raw 段で加算)+ `pipeline.py`(`MarketOffsetSkip` 型付きスキップ・backfill `skip_no_odds` 計数・lv `;mkt=logq`): market_offset モデル時のみ対象レースの started 馬オッズを DB から読み q→offset 構成、raw score 段階で加算してから softmax(codex 指摘: raw_predict の型整理)。1 頭でも無効オッズ=typed skip(予測行を作らない、既存 live guard の「recommendation のみ停止」とは別の新分岐)。logic_version に `mkt=logq`(INV-M6)+ テスト
- [X] T016 [US2] 実 DB E2E 全通過: **(a) default lgbm-057 予測(202507010112・16頭)が persisted 値とバイト完全一致 mismatch 0(SC-002)** **(b) lgbm-060-mkt で 202502010101 予測成功(16頭)・lv=`feat=features-015;serve=serve-0.1.0;mkt=logq;sdisc=...`(SC-003/005)・active は lgbm-057 のみ** **(c) オッズ欠損レース 202504040406 で MarketOffsetSkip・予測行 0(SC-003)**

## Phase 5: Polish

- [X] T017 [P] 全パッケージテスト緑(training 89 / serving 44 / eval 72 / features 164)+ ruff クリーン
- [X] T018 [P] spec/plan/tasks 結果記録・CLAUDE.md SPECKIT セクション更新・memory(feature-060 結果)更新

## Dependencies

- Phase 2 は T002→T003→T004→T006 が同系列(T005/T007 は並行可)、T008 は T002-T006 後、T009 は T008 後
- **T009(spike go)が Phase 3+ 全体をブロック**
- US2 の T014/T015 は US1 の評価実行(T012、長時間)と並行開発可。T016(E2E)は T013(登録)後
- 長時間ジョブ(T012 フル 19-fold ~20 分+、058-acc 再評価含む)は nohup+監視(056 の教訓)

## Implementation Strategy

MVP = Phase 2 + US1(spike go → ゲート評価 → candidate 登録)。US2 は登録後に独立検証可能。no-go の場合は Phase 2 までで中断し、結果(市場に足せる情報が現特徴に無いという知見)を記録して終了する — それ自体が有効な成果。
