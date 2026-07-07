---
description: "Task list for 058 past-market as-of features (accuracy-first model B1)"
---

# Tasks: 過去走の市場評価 as-of 特徴 — 精度最優先モデル(B1)

**Input**: Design documents from `specs/058-market-history-features/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, quickstart.md

**Tests**: 含む(憲法 III + 品質ゲート)。中心 = leak-guard(挙動型)・parity・事前登録 feature-eval。

**作業ディレクトリ**: worktree `.claude/worktrees/058-market-history-features`(branch `058-market-history-features`)。パスは repo ルート相対。

**注記**: 特徴の配線(loader/past_market_features.py/materialize/registry・FEATURE_VERSION 015)は de-risk spike で実装済。以下の「実装済」タスクは確認/仕上げが主。

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [x] T001 グリーンベースライン: `uv run --project features pytest features/tests -q` で変更前(spike scaffolding 込み)の状態を把握。DATABASE_URL=`postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing`。
- [x] T002 スモーク確認: `build_feature_matrix(end_date=小さい日)` が past_market 4 列を生成・カバレッジ表示(spike 済 ~82%)、FEATURE_VERSION=features-015・125 列。

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: leak-guard テストの土台。合成 Frames に popularity を持たせられるようにする。

- [x] T003 `features/tests/_frames.py` `make_frames` が horse spec の `popularity`(と必要なら現走の人気)を race_horses に載せられるか確認、無ければ追加(既存 corner_trajectory テストの spec 形式に倣う)。
- [x] T004 past_market_features.py の最終確認(spike 実装): 4 列・`_rolling_asof`(recent-N)・`merge_asof(backward, allow_exact_matches=False)`・母集団=started かつ人気あり・beat_mkt=人気−着順・Unknown=NaN。命名が禁止トークン("odds"/"popularity")非含有。

**Checkpoint**: 合成 Frames で past_market を検証できる。

---

## Phase 3: User Story 1 - 過去市場評価特徴のリーク安全(Priority: P1)

**Goal**: past_market を strictly-before リーク安全に供給。今走市場評価は非特徴。

**Independent Test**: 今走人気を変えても不変・過去人気を変えると変化。

- [x] T005 [US1] `features/tests/unit/test_past_market_leak.py` 新規(挙動型 leak-guard、041 の型):
  - INV-L1 今走の人気を変えても past_market 不変
  - INV-L2 同日他レースを足しても不変
  - INV-L3 未来レースを足しても過去行不変
  - INV-P1(positive) 過去走の人気を変えると past_market が**変わる**(実際に過去人気を使用)
  - 名前検査: PAST_MARKET_COLUMNS が禁止トークン非含有・FEATURE_GROUPS の group=="past_market" と一致
- [x] T006 [US1] グローバル leak-guard 通過確認: `pytest features/tests/unit/test_feature020_leak_guard.py`(model_input_features に odds/popularity **名**が無い=asof_mkt_* 命名で通過)。**058 は grep 型ソース検査を本モジュールに追加しない**(正当に市場データ使用、plan D1)。
- [x] T007 [US1] materialize parity: 実 DB で materialize == in-memory build の bit 一致(features-015・新 4 列)。既存 parity テスト/CLI で確認(popularity は race_horses 全列ハッシュで fingerprint 自動包含=別途変更不要)。
- [x] T008 [US1] `uv run --project features ruff check features/src` クリーン。features スイート緑(新 leak テスト含む)。

**Checkpoint**: past_market がリーク安全に供給される(SC-001)。

---

## Phase 4: User Story 2 - 事前登録ゲートによる採否(Priority: P1)

**Goal**: フル walk-forward OOS で win 採否 + top2/top3 非悪化 MUST を、事前登録ゲートで判定。

**Independent Test**: baseline(past_market drop)vs candidate をフル窓で比較し plan 固定の閾値で採否。

**⚠️ 計算重(フル walk-forward)**: 実 DB・数十分〜。nohup+監視(056 教訓)。

- [x] T009 [US2] 事前登録ゲート再掲(plan 固定・**結果を見て動かさない**): 窓=FIRST_VALID..2024-12-31、binary+platt、baseline=features-014(--drop-groups past_market)、candidate=features-015、seed=42。PRIMARY=win 平均 LogLoss 改善+平均 ECE 非悪化(1e-3)+fold guards(strict majority・worst ECE 2e-3・worst dLL 5e-3)。MUST=top2/top3 平均 LogLoss 非悪化。
- [x] T010 [US2] フル feature-eval 実行(win 採否): `training feature-eval --from <FIRST_VALID> --to 2024-12-31 --drop-groups past_market`。出力(LogLoss/AUC/ECE/fold guards/adopted)を記録。
- [x] T011 [US2] top2/top3 併記測定(同一 harness を candidate/baseline 各 1 回・spike スクリプト流用): win/top2/top3 の overall LogLoss/ECE を並べ、**top2/top3 非悪化 MUST** を判定。結果を spec/plan の判定欄に記録。
- [x] T012 [US2] **採否決定**: PRIMARY(win)通過 かつ MUST(top2/top3 非悪化)を満たすなら ADOPT、否なら不採用(worktree 破棄 or 特徴見直し)。数値を見て閾値を動かさない(憲法 III)。決定を記録。

**Checkpoint**: 事前登録ゲートで採否が確定(SC-002)。

---

## Phase 5: User Story 3 - 精度最優先モデルの共存(Priority: P2、**採用時のみ**)

**Goal**: 採用時、精度最優先モデルを非 active 登録し 057 で共存。default(意思決定支援)は不変。

**前提**: T012 で ADOPT。不採用ならこのフェーズはスキップ。

- [X] T013 [US3] **serving 互換リスク検証(MUST・最重要)**: 実機確認で **features-015 を本番 registry に bump すると lgbm-057(features-014)が fail-closed** と確定(`load_serving_model` がグローバル `feature_hash(model_input_features())` を全モデルに要求)。**決定=案D**(codex second opinion + 分析の収束): 本番 registry/serving は features-014 据え置き、lgbm-058-acc は worktree(features-015)から backfill で予測永続化し API read 経路でセレクタ表示。**default serving/SC-005 完全不変**。features-015 コードは 058 ブランチに保全し main serving へマージしない。将来 live 予測が本番必須なら案C'(per-model feature_cols hash + 互換 allowlist + 旧列 byte parity gate)を別 feature。
- [X] T014 [US3] production 寄与確認(020 教訓): train-evaluate(pl_topk+TE+isotonic)production 19-fold で win 0.21597→0.21579(−0.00018)・top2 0.33988→0.33964(−0.00024)・top3 +0.00007。binary spike(−0.00028)より縮小=059 の overlap 前例どおりだが top2/top3 の狙いは改善維持。
- [X] T015 [US3] 精度最優先モデル学習・**非 active 登録**: lgbm-058-acc(pl_topk+TE jockey/trainer+isotonic・features-015)学習。top3 no-regression のみ FAIL で自動昇格せず candidate 保存、明示的に candidate 固定 + lgbm-057 active 維持。
- [X] T016 [US3] 用途ラベル付与(057): `set-model-label --display-name "精度最優先モデル" --purpose "過去市場評価(人気)含む・最高精度(複勝/ワイド向け)"` 適用済。
- [X] T017 [US3] 予測生成 + 切替確認(057/044): lgbm-058-acc を製品範囲(2024-01〜2026-07)に backfill、実 DB E2E(202506010101)で default(lgbm-057/features-014)⇄ 精度最優先(lgbm-058-acc/features-015)の切替・available_models is_selected・past_market 寄与を確認。既定=lgbm-057 active 不変(SC-003)。

**Checkpoint**: 精度最優先モデルが default を変えずに共存(SC-003/004/005)。

---

## Phase 6: Polish & Cross-Cutting

- [X] T018 features unit 161 緑 + past_market leak 6 緑 + ruff クリーン。058 変更は features/ のみ(training/serving/eval 未変更=回帰なし)。既存 leak-guard/parity 回帰なし。
- [X] T019 spec.md Status 更新(採否数値・serving 互換を記録)、CLAUDE.md ポインタ反映、メモリ [[feature-058-market-history-result]] 記録。

---

## Phase 7: 案C' — serving per-model 互換化(features-015 を本番 main へ)

**course change(2026-07-08)**: 当初 T013 は案D(本番 features-014 据え置き)だったが、ユーザー判断で案C'
(features-015 を本番 main にマージ・accuracy モデルの live 予測も可能に)へ切替。default lgbm-057
(features-014)の serving を byte-parity で維持することが最優先。

- [X] C1 de-risk(最重要): features-014 build vs features-015 build の共有列がバイト一致するか実証。
  同一 end_date(2008-06-30, 73,633 行)で両版を build → **共有 121 列が check_exact + check_dtype 一致**、
  差は past_market 4 列のみ。past_market は additive left-merge=既存列を perturb しない。GO。
- [X] C2 registry: `COMPATIBLE_PRIOR_FEATURE_VERSIONS`(hash ピン留め)+ `is_feature_version_servable`。
- [X] C3 model_loader: グローバル hash ゲートを exact-path(バイト不変)/ compat-path(互換版 pinned-hash
  + buildability + 自己整合 + categorical/encoder ⊆ cols)に分離、破れば fail-closed。ServingModel.feature_hash
  =model 自身。codex#3/#4 反映(hash ピン留め・categorical/encoder 検証)。
- [X] C4 audit(codex#7): compat 実行の logic_version に `reg=<現行版>` 付与(native と区別)。
- [X] C5 テスト: loader unit(exact/compat/subset/integrity/missing-preprocessor/hash-mismatch 9件)+
  `test_past_market_is_purely_additive`(構造的加算性)+ `test_feature_version_servability`(pinned-hash)。
  features 164 / serving 38 / training 67 / eval 72 緑・ruff クリーン。
- [X] C6 実 DB E2E: lgbm-057 が features-015 registry 下で compat-load、race 202506010101 の win prob が
  persisted features-014 値とバイト完全一致(16頭 mismatch 0)=SC-005 死守。監査マーカー確認。
- [X] C7 契約更新: serving.md の INV-S4 緩和を明記。
- [X] C8 main マージ: codex 最終レビューでブロッカー(同一版 hash 不一致バイパス)発見→修正+loader reject テスト 3 件追加後、`--no-ff` で merge(commit f2c2a33)。main FEATURE_VERSION=features-015。main の無関係な未コミット作業(admin/front/ops/betting/training win_model.py)とは disjoint=非干渉。
- [X] C9 マージ後検証: main(features-015)で active=lgbm-057 が compat-load・race 202506010101 の予測が persisted features-014 値とバイト完全一致(16頭 mismatch 0)、lgbm-058-acc は exact-load。serving 41 + features 164 緑。メモリ [[feature-058-market-history-result]] を案C' に更新。

---

## Dependencies & Execution Order

- **Setup(P1)** → **Foundational(P2、make_frames popularity)** → **US1(P1、leak-guard/parity)**。
- **US2(P1、フル eval)** は US1(特徴が正しい)後。計算重。
- **US3(P2)** は **T012 で ADOPT の時のみ**。T013(serving 互換)は US3 の最優先ゲート。
- **Polish** は全 story 後。

### 重要リスク(明示)

- **FEATURE_VERSION 015 bump × 現 active default モデル(lgbm-057=features-014)の serving 互換**(T013): merge 前に必ず検証。default 予測不変(SC-005)を壊してはならない。
- **binary spike の限界寄与過大評価**(020 教訓): production(pl_topk+TE)で再確認してから登録(T014)。

### 並行機会

- T005(leak テスト)と T007(parity)は別作業で並行可。
- US2 のフル eval(T010/T011)は長時間 = 監視しつつ他タスクは待機(同一 DB/計算資源)。

---

## Implementation Strategy

- **MVP = US1 + US2**: 特徴のリーク安全性 + 事前登録ゲートで「効くか」を確定。ここで不採用なら US3 に進まない(無駄な production 学習を回避)。
- **US3 は採用時のみ**: production 再学習は十数時間級(056 教訓)。ADOPT 確定後に実施。
- 不採用時: worktree を破棄 or 特徴を見直して再 spike。

## Notes

- codex unavailable → plan のセルフレビュー checklist を参照。復旧したら T012(採否)・T013(serving 互換)で second opinion 推奨。
- 事前登録ゲートは plan に固定。**結果を見てから閾値を動かさない**(憲法 III)。
- default(意思決定支援)モデルは past_market 非含有=独立性維持(p⊥q)。
