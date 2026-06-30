# Feature Specification: 低履歴×血統適性 交互作用 + 種牡馬デビュー戦適性 (Debut/Low-history × Pedigree)

**Feature Branch**: `032-debut-pedigree-interaction`

**Created**: 2026-06-30

**Status**: Draft

**Input**: §3 中コスト第2弾。市場が値付けしにくいデビュー馬・少数出走馬(=自馬の走力履歴が薄く市場が情報を持ちにくい)に効く血統シグナルを強化。026 が単独列で持つ種牡馬適性を、(a) 031 型の新情報=「同種牡馬の他産駒の **デビュー戦** 勝率」(026 の総合勝率にない条件付き集約)と、(b) is_debut/is_low_history による血統適性のゲーティング交互作用、として追加。FEATURE_VERSION features-009→010、スキーマ変更なし。

## 概要・動機

020 の市場 diagnostic は「モデル p は市場 q に負ける」「次のレバーは公開情報の追加でなく、市場が情報を持ちにくい領域」と示した([[feature-020-adoption-result]])。026 血統は「デビュー馬の98.6%を他産駒由来で補える」初のレバーだった([[feature-026-pedigree-result]])。031 は「モデルが持たない新情報(他馬の組合せ)」で大きく採用された([[feature-031-pace-scenario-result]])。

本 feature は両者を接続する: **市場が最も弱いデビュー/少数出走馬に対し、血統が持つ予測力を強化する**。codex の独立判断(032 直前)では「既存特徴同士の単純積は GBM が木分割で学習済み=冗長」と指摘されたため、本 feature の主役は積でなく **026 にない新情報=種牡馬の他産駒デビュー戦勝率**(`sire_debut_win_rate`)とする。ゲーティング交互作用(is_debut × sire_*)は副次で、GBM 冗長リスクを認めた上で bundle として OOS が採否を決める(030 の「単独では落ちる群も bundle で採用」前例)。

## 利用者と価値

意思決定支援([[product-goal-decision-support]])の利用者に、自馬実績の薄い馬(新馬・未勝利の少数出走)についても血統由来のより確かな win 確率を提示する。直接の利用者はモデル学習/評価/serving パイプライン(features 経由)。

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 種牡馬デビュー戦適性(新情報) (Priority: P1, MVP)

同じ種牡馬の **他の産駒** が、その **デビュー戦(各馬の初出走)** でどれだけ勝ったかを、対象レース日より前・自馬除外で as-of 集計した `sire_debut_win_rate` を生成する。

**Why this priority**: 026 は種牡馬の **総合** 勝率(全出走)しか持たない。「初出走でも走る血統か(早熟・仕上がりの早さ)」は別シグナルで、デビュー馬の評価に直結する新情報。031 の勝ち筋(モデルが持たない条件付き集約)と同型。

**Independent Test**: 種牡馬 S の他産駒 2 頭が過去にデビュー戦で 1 勝/1 敗 → 対象 S 産駒デビュー馬の `sire_debut_win_rate` = 0.5(自馬のデビュー戦は集計に入らない・strictly-before)。

**Acceptance Scenarios**:

1. **Given** 種牡馬 S の他産駒のデビュー戦が過去に複数, **When** S 産駒の特徴を作る, **Then** `sire_debut_win_rate` = 他産駒のデビュー戦勝利数 / デビュー戦数(自馬除外・strictly-before)。
2. **Given** 自馬が S 産駒で過去に S 産駒の他デビュー戦が無い, **When** 特徴を作る, **Then** NaN(0 埋めしない)。
3. **Given** デビュー戦の母数が min_starts 未満, **When** 特徴を作る, **Then** NaN(信頼できる母数のみ)。

---

### User Story 2 - 低履歴×血統 ゲーティング交互作用 (Priority: P1, MVP)

is_debut / is_low_history を 026 の種牡馬適性に掛けたゲーティング交互作用を生成し、「自馬実績が薄いほど血統に重みを置く」表現をモデルに与える。

**Why this priority**: codex は単純積を GBM 冗長と評価したが、デビュー/低履歴は実質 0/1 ゲートで、血統適性が「履歴の薄い馬でだけ効く」非対称性を浅い木で表現しやすくする可能性。bundle として OOS が判定。

**Independent Test**: is_debut=1 の馬で `debut_x_sire_win_rate` = sire_win_rate、is_debut=0 で 0。

**Acceptance Scenarios**:

1. **Given** is_debut と sire_win_rate, **When** 特徴を作る, **Then** `debut_x_sire_win_rate` = is_debut × sire_win_rate。
2. **Given** is_debut と sire_dist_band_win_rate, **When** 特徴を作る, **Then** `debut_x_sire_dist_band_win_rate` = is_debut × sire_dist_band_win_rate(自馬に距離実績が無いとき血統の距離適性が効く)。
3. **Given** is_low_history と sire_win_rate, **When** 特徴を作る, **Then** `lowhist_x_sire_win_rate` = is_low_history × sire_win_rate。
4. **Given** sire_* が NaN(種牡馬不明), **When** 交互作用を作る, **Then** NaN(0 埋めしない)。

---

### User Story 3 - リーク安全保証 (Priority: P1, MVP)

`sire_debut_win_rate` は同種牡馬の他産駒の strictly-before デビュー戦のみを使い(自馬除外・同日除外)、ゲーティング交互作用は既存 as-of 列(is_debut/is_low_history/sire_*)の積のみ。今走の結果/オッズを一切参照しない。

**Why this priority**: 憲法 II(非交渉)。新情報の条件付き集約(sire_debut)は 026 の自馬除外機構を厳密に踏襲する必要がある。

**Independent Test**: leak-guard test — 自馬の今走結果・同日他産駒のデビュー戦・未来レース を変えても本群の列が不変。

**Acceptance Scenarios**:

1. **Given** あるレース, **When** 自馬の今走 finish_order/result を変える, **Then** 本群の列は不変。
2. **Given** あるレース, **When** 同日に走る同種牡馬他産駒の結果を変える, **Then** `sire_debut_win_rate` 不変(同日除外)。
3. **Given** あるレース, **When** 未来の同種牡馬産駒デビュー戦を変える, **Then** 不変(strictly-before)。
4. **Given** ソースコード, **When** grep, **Then** 今走の result/odds 列を生参照しない。

---

### User Story 4 - materialization パリティ・カバレッジ (Priority: P2)

025 の単一 as-of 源 `build_asof_features` に debut_pedigree ブロックを追加し materialize==in-memory が bit 一致。serving 未来レースは単一レース fallback。

**Why this priority**: 既存採用済みモデルの予測を変えない(憲法 III/V)・serving 一貫のため必須の横断要件。

**Independent Test**: 実 DB で materialize==in-memory を `assert_frame_equal(check_exact=True, check_dtype=True)`。本群列が materialized_columns に収録。

**Acceptance Scenarios**:

1. **Given** 実 DB プール, **When** materialize と in-memory を比較, **Then** 全列 bit 一致(float64)。
2. **Given** registry, **When** materialized_columns を導出, **Then** 本群列が含まれ odds/payout/dividend トークンを含まない。
3. **Given** sire_name 由来の集計, **When** source_fingerprint, **Then** 026 で既に horses 血統列を fingerprint 包含済み=無改修(新ソース列なし)。

---

### User Story 5 - 採用判定(事前登録 bundle OOS) (Priority: P1)

debut_pedigree を 1 bundle として baseline=features-009 vs candidate=features-010 を walk-forward OOS で評価し、事前登録ゲートを通れば採用。

**Why this priority**: 憲法 III(非交渉)。codex 見積もりでは 031 より採用確率は不確実 → データが採否を決める。

**Independent Test**: `feature-eval --drop-groups debut_pedigree` の AdoptionReport。

**Acceptance Scenarios**:

1. **Given** 実 DB walk-forward OOS, **When** bundle を評価, **Then** primary(win LogLoss 改善 かつ ECE 非悪化)+ fold ガード(strict majority・worst-fold ECE 2e-3・worst-fold dLogLoss 5e-3)で adopted を機械判定。
2. **Given** 採用, **When** serving 再学習, **Then** lgbm-032 を active 昇格・lgbm-031 retired(feature_hash=features-010 整合)。
3. **Given** 不採用, **When** 判定後, **Then** ブランチ保全(027 前例)・main は features-009/lgbm-031 のまま。**デビュー馬セグメント限定の効果**を market_edge/セグメント診断で SECONDARY 確認(全体で薄くてもデビュー馬で効く可能性を記録)。

---

### Edge Cases

- 種牡馬不明(sire_name NaN) → sire_debut_win_rate / 全交互作用 NaN。
- 真のデビュー馬で同種牡馬の他デビュー戦が過去に無い → sire_debut_win_rate NaN(0 埋め禁止)。
- min_starts 未満のデビュー戦母数 → NaN。
- is_low_history の定義は既存 history group(is_low_history)を再利用(本 feature で再定義しない)。
- 名前ゆれ(全角半角/空白) → 026 の `_normalize_name`(NFKC+strip)を再利用。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `sire_debut_win_rate` = 同種牡馬の **他産駒のデビュー戦(各馬の初出走)** の strictly-before 勝率(自馬除外・同日除外、母数<min_starts→NaN)を算出する。026 の `_other_offspring`(sire 累積−自馬累積)機構を再利用する。
- **FR-002**: ゲーティング交互作用 `debut_x_sire_win_rate`・`debut_x_sire_dist_band_win_rate`・`lowhist_x_sire_win_rate`・`lowhist_x_sire_dist_band_win_rate` を、既存 as-of 列 is_debut/is_low_history(history)× sire_win_rate/sire_dist_band_win_rate(pedigree, 026)の積で算出する(再実装しない)。
- **FR-003**: 欠損は 0 埋めせず NaN を伝播する。全列 float64 固定。
- **FR-004**: 本群は今走の race_results/result_status/finish_order/オッズを読まない。sire_debut_win_rate は strictly-before・自馬除外・同日除外。ゲーティングは as-of 列の積のみ。leak-guard test(自馬今走・同日他産駒・未来 不変 + grep)で担保。
- **FR-005**: 025 `build_asof_features` の単一 as-of 源に debut_pedigree ブロックを追加し、materialize/in-memory/serving fallback が同一実装で同一値。新ソース列なし(sire_name は 026 で既にロード&fingerprint 包含)。
- **FR-006**: registry に debut_pedigree group を登録(列を PRE_ENTRY/missing=NULL)、FEATURE_VERSION を features-010 に更新。STATIC_COLUMNS には入れない(as-of/derived ⇒ materialized 自動収録)。
- **FR-007**: feature-eval の既定 `--drop-groups` を debut_pedigree にし、bundle 単位で baseline=features-009 vs candidate=features-010 を walk-forward OOS 評価できる。ablation は diagnostic 専用。
- **FR-008**: win→joint(009) は不変(特徴追加のみ)。LightGBM/binary・Unknown=NaN 維持。スキーマ変更なし。

### Key Entities

- **debut_pedigree group(新規列, 全て float64, missing=NULL)**: `sire_debut_win_rate`(新情報・条件付き集約)+ `debut_x_sire_win_rate`・`debut_x_sire_dist_band_win_rate`・`lowhist_x_sire_win_rate`・`lowhist_x_sire_dist_band_win_rate`(ゲーティング交互作用)。最終列確定は plan/contracts。
- **既存再利用**: history の is_debut/is_low_history、026 の sire_win_rate/sire_dist_band_win_rate と `_other_offspring`/`_normalize_name` 機構、loader の sire_name(as-of 経由)。

## Success Criteria *(mandatory)*

- **SC-001 (リーク, 非交渉)**: leak-guard test 全通過(自馬今走・同日他産駒・未来 不変)+ grep で今走 result/odds を生参照しない。
- **SC-002 (パリティ, 非交渉)**: 実 DB で materialize==in-memory が bit 一致(assert_frame_equal check_exact/check_dtype)。
- **SC-003 (採用ゲート)**: walk-forward OOS の AdoptionReport が事前登録基準で機械判定。数値を見てから列/閾値を変えない。
- **SC-004 (正しさ)**: US1-3 の Independent Test(デビュー戦集約値・ゲーティング積・NaN 伝播・float64)がユニットテストで検証される。
- **SC-005 (透過/セグメント診断)**: features lint/test 緑、eval/training/serving 既存テスト透過で緑。採否に加え、デビュー馬セグメントでの効果を SECONDARY 診断で記録(全体で薄くてもデビュー馬で効く可能性を可視化)。

## Assumptions

- sire_name は実 DB で ~100% populate(026 で確認済)。デビュー戦の特定=各馬の最初の started 出走。
- ゲーティング交互作用は GBM 冗長リスクがある(codex)。bundle として OOS が採否を決める(030 前例)。
- 採用ゲートの閾値・fold ガードは 020/023/026/030/031 と同型(事前登録)。

## Dependencies

- Feature 026(pedigree_features の sire 集約・`_other_offspring`・`_normalize_name`)— 必須前提。
- Feature 020(is_debut/is_low_history, history group)。
- Feature 025(materialization 単一源・パリティ)。
- Feature 020/030(feature-eval / adoption gate / FEATURE_GROUPS 機構)。

## Out of Scope / Deferred

- 条件替わり×能力/時計 交互作用(027 の dist_change/surface/going 再導入 + 時計/末脚)は **Feature 033**(本 feature の次)。
- damsire(母父)のデビュー戦適性・3代血統・インブリード・ニックスは deferred(026 同様 dam は母数小)。
- sire_id ベース結合(scrape 血統 ID 解決後)は deferred。
- sectional(区間ラップ)は §4(DB 未取得データ)。
- market 超過 edge は SECONDARY(採否バーでない)。
