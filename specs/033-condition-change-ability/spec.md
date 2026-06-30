# Feature Specification: 条件替わり×能力/時計 交互作用 (Condition-change × Ability/Time)

**Feature Branch**: `033-condition-change-ability`

**Created**: 2026-06-30

**Status**: Draft

**Input**: §3 中コスト第3弾。027「条件替わり(距離/馬場/going)」は単独 OOS で不採用(8/18 fold)でブランチ保全=未マージ=モデルに無い新情報。これを **新 base 列として再導入**(dist_change/surface_switch/going_change)+ 距離替わりの hinge(延長/短縮)× 末脚/時計の能力交互作用を加える。FEATURE_VERSION features-010→011、スキーマ変更なし。

## 概要・動機

032 の学びは「**交互作用は既存特徴の積(GBM 冗長)でなく、モデルが持たない新情報を主役にすべき**」だった([[feature-032-debut-pedigree-result]])。codex の独立判断(032 直前)でも「class_transition × rel_time 等の既存列積は GBM が木分割で学習済み=冗長 → 032/033 は未マージの 027 base(dist_change/surface/going)を再導入が主眼」と明示された。

本 feature の主役は **027 の条件替わり base 列**(dist_change/surface_switch/going_change、現 main に無い=新情報)。027 単独は全体 OOS で不発だったが、(a) 距離替わりを符号付き hinge(延長 dist_extension / 短縮 dist_shortening)に分け、(b) 「延長×強い末脚」「短縮×速い時計」という**非対称ドメインを浅い木で学べる能力交互作用**を足すことで、027 単独では届かなかった識別力を狙う(031 の running_style→field-composition、032 の sire→sire_debut と同じ「base を効く形に変換」)。class/斤量 × time の積は GBM 冗長として除外(codex)。

## 利用者と価値

意思決定支援([[product-goal-decision-support]])の利用者に、距離・馬場・going が変わる馬(市場が評価を誤りやすい)について、その条件替わりと自馬の末脚/時計能力を加味した win 確率を提示する。直接の利用者はモデル学習/評価/serving パイプライン(features 経由)。

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 条件替わり base(新情報) (Priority: P1, MVP)

各馬の今走条件と直前 started レース条件の差を as-of で算出: dist_change(距離差)・surface_switch(芝↔ダ方向付き)・going_change(馬場状態 ordinal 差)。

**Why this priority**: 027 の base 列で現 main に無い新情報。条件替わりそのものが市場の評価誤りを捉える土台。

**Independent Test**: 前走 1600m→今走 2000m で dist_change=+400、芝→ダで surface_switch=+1、良→重で going_change=+2(027 ordinal)。デビュー(前走無し)→ NaN。

**Acceptance Scenarios**:

1. **Given** 直前 started レースの距離/馬場/going, **When** 特徴を作る, **Then** dist_change=今走−前走距離、surface_switch(同=0/芝→ダ=+1/ダ→芝=−1/他=0)、going_change=今走−前走 ordinal。
2. **Given** デビュー馬(前走 started 無し), **When** 特徴を作る, **Then** 全 base NaN(0 埋めしない)。

---

### User Story 2 - 距離替わり hinge × 能力 交互作用 (Priority: P1, MVP)

距離替わりを符号付き hinge に分け(dist_extension=延長量・dist_shortening=短縮量)、自馬の末脚(rel_last3f_best)・時計(rel_time_avg)能力との交互作用を作る。

**Why this priority**: 本 feature の本命。027 単独不発を、「延長×末脚」「短縮×時計」の非対称を明示することで効く形に変換する。

**Independent Test**: dist_change=+400 → dist_extension=400・dist_shortening=0。dist_extension × (−rel_last3f_best) が dist_ext_x_closing。

**Acceptance Scenarios**:

1. **Given** dist_change, **When** hinge を作る, **Then** dist_extension=max(dist_change,0)・dist_shortening=max(−dist_change,0)。dist_change が NaN なら両 hinge NaN。
2. **Given** dist_extension と rel_last3f_best(末脚, 小=良), **When** 交互作用を作る, **Then** dist_ext_x_closing = dist_extension × (−rel_last3f_best)(延長で末脚が活きる=正に効く向き)。
3. **Given** dist_shortening と rel_time_avg(時計, 小=速い), **When** 交互作用を作る, **Then** dist_short_x_speed = dist_shortening × (−rel_time_avg)。
4. **Given** 能力が NaN(末脚/時計の履歴無し), **When** 交互作用を作る, **Then** NaN(0 埋めしない)。

---

### User Story 3 - リーク安全保証 (Priority: P1, MVP)

base は直前 started レース(strictly-before, merge_asof allow_exact_matches=False)のみ。能力は 023 build_pace_features の as-of 出力。今走の結果/オッズは読まない。

**Why this priority**: 憲法 II(非交渉)。027 の merge_asof 機構 + 023 as-of の組合せ。

**Independent Test**: leak-guard test — 自馬の今走結果・同日他レース・未来レース を変えても本群の列が不変。

**Acceptance Scenarios**:

1. **Given** あるレース, **When** 自馬の今走 finish_order/result を変える, **Then** 本群の列は不変。
2. **Given** あるレース, **When** 同日他レース・未来レースの結果/条件を変える, **Then** 不変(strictly-before)。
3. **Given** ソースコード, **When** grep, **Then** 今走の result/finish_order/odds 列を生参照しない。

---

### User Story 4 - materialization パリティ・カバレッジ (Priority: P2)

025 単一 as-of 源に condition_change ブロックを追加し materialize==in-memory が bit 一致。serving 未来レースは単一レース fallback。

**Independent Test**: 実 DB で materialize==in-memory を assert_frame_equal(check_exact, check_dtype)。本群列が materialized_columns に収録。

**Acceptance Scenarios**:

1. **Given** 実 DB プール, **When** materialize と in-memory を比較, **Then** 全列 bit 一致(float64)。
2. **Given** registry, **When** materialized_columns, **Then** 本群列が含まれ odds/payout/dividend トークンを含まない。
3. **Given** loader, **When** going 列, **Then** 既存ロード列(going は races に既存)で source_fingerprint 無改修。

---

### User Story 5 - 採用判定(事前登録 bundle OOS) (Priority: P1)

condition_change を 1 bundle として baseline=features-010 vs candidate=features-011 を walk-forward OOS で評価し、事前登録ゲートを通れば採用。

**Independent Test**: `feature-eval --drop-groups condition_change` の AdoptionReport。

**Acceptance Scenarios**:

1. **Given** 実 DB walk-forward OOS, **When** bundle を評価, **Then** primary(win LogLoss 改善 かつ ECE 非悪化)+ fold ガード(strict majority・worst-fold ECE 2e-3・worst-fold dLogLoss 5e-3)で adopted を機械判定。
2. **Given** 採用, **When** serving 再学習, **Then** lgbm-033 を active 昇格・lgbm-032 retired(feature_hash=features-011)。
3. **Given** 不採用, **When** 判定後, **Then** ブランチ保全(027 前例)・main は features-010/lgbm-032 のまま。条件替わりセグメント診断を SECONDARY で記録。

---

### Edge Cases

- デビュー馬(前走 started 無し) → base/hinge/交互作用 全 NaN。
- going が不明コード → going_ord NaN → going_change NaN(027 ordinal map 流用、字略形 良/稍/重/不 + 全字形)。
- 障害↔平地等 surface "other" → surface_switch=0(027 定義)。
- 能力(rel_last3f_best/rel_time_avg)が NaN(時計履歴無し) → 交互作用 NaN。
- novelty(is_first_dist_band/is_first_surface, 027)は本 feature では deferred(027 で単独不発、まず hinge×能力で検証)。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: dist_change=今走距離−直前 started レース距離、surface_switch(同0/芝→ダ+1/ダ→芝−1/他0)、going_change=今走−前走 going ordinal を、027 の `_prev_started`(merge_asof backward, allow_exact_matches=False)で算出する。前走無し→NaN。
- **FR-002**: dist_extension=max(dist_change,0)・dist_shortening=max(−dist_change,0)(dist_change NaN→両 NaN)を算出する。
- **FR-003**: dist_ext_x_closing=dist_extension×(−rel_last3f_best)・dist_short_x_speed=dist_shortening×(−rel_time_avg) を、023 build_pace_features の as-of 末脚/時計と掛けて算出する(再実装しない)。片側 NaN→NaN。
- **FR-004**: 欠損は 0 埋めせず NaN 伝播。全列 float64 固定。
- **FR-005**: base は strictly-before(直前 started レース)・能力は as-of。今走 result/finish_order/odds 非参照。leak-guard test(自馬今走・同日・未来 不変 + grep)。
- **FR-006**: 025 build_asof_features の単一源に condition_change ブロックを追加(pace 既算出を渡す)。going は races に既存ロード列で source_fingerprint 無改修。
- **FR-007**: registry に condition_change group 登録(PRE_ENTRY/NULL)、FEATURE_VERSION features-011。STATIC_COLUMNS に入れない。
- **FR-008**: feature-eval 既定 `--drop-groups` を condition_change に。win→joint(009) 不変。スキーマ変更なし。

### Key Entities

- **condition_change group(新規列, 全て float64, missing=NULL, 7 列)**: dist_change・surface_switch・going_change(base)・dist_extension・dist_shortening(hinge)・dist_ext_x_closing・dist_short_x_speed(能力交互作用)。
- **既存再利用**: 027 の `_runs`/`_prev_started`/`_surface`/`_GOING_ORD`、023 build_pace_features の rel_last3f_best/rel_time_avg、races の going(既存ロード)。

## Success Criteria *(mandatory)*

- **SC-001 (リーク, 非交渉)**: leak-guard test 全通過 + grep で今走 result/odds 非参照。
- **SC-002 (パリティ, 非交渉)**: 実 DB で materialize==in-memory bit 一致(assert_frame_equal check_exact/check_dtype)。
- **SC-003 (採用ゲート)**: walk-forward OOS が事前登録基準で機械判定。数値を見てから列/閾値を変えない。
- **SC-004 (正しさ)**: US1-3 の Independent Test(base 差・hinge・能力交互作用・NaN・float64)がユニットテストで検証される。
- **SC-005 (透過/セグメント診断)**: features lint/test 緑、cross-package 透過で緑。条件替わりセグメント効果を SECONDARY 診断で記録。

## Assumptions

- going は races に ~100% 程度 populate(027 で実 DB カバレッジ 89.5% を確認、字略形対応済)。
- 既存特徴同士の積(class_transition×time 等)は GBM 冗長として除外(codex)。本 feature の積は「新 base hinge × 能力」のみ。
- 採用ゲートの閾値・fold ガードは 020/023/026/030/031/032 と同型(事前登録)。

## Dependencies

- Feature 027(transition_features の base/helper、ブランチ保全)— 移植元。
- Feature 023(build_pace_features の rel_last3f_best/rel_time_avg)。
- Feature 025(materialization 単一源・パリティ)。
- Feature 020/030(feature-eval / adoption gate / FEATURE_GROUPS)。

## Out of Scope / Deferred

- novelty(is_first_dist_band/is_first_surface, 027)・class_transition×time・斤量×time 等の既存列積は deferred(GBM 冗長 or 027 単独不発)。
- surface_switch×末脚・going_change×能力 は初期 sparse でノイジー(codex)→ deferred。
- sectional(区間ラップ)は §4(DB 未取得)。
- market 超過 edge は SECONDARY(採否バーでない)。
