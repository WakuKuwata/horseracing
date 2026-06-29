# Feature Specification: 展開・ペース構成特徴 (Race Pace Scenario / Field Composition)

**Feature Branch**: `031-pace-scenario-features`

**Created**: 2026-06-29

**Status**: Draft

**Input**: §3 中コスト第1弾。各馬の strictly-before 優勢脚質を出走馬フィールド内で leave-one-out 集約し、レース展開(ペース構成)と「自馬脚質×フィールド構成」の相互作用をリーク安全に特徴化。FEATURE_VERSION features-008→009、スキーマ変更なし。

## 概要・動機

これまでの公開情報特徴(020 recent_form/aptitude・023 pace_time/position_style・027 transition)は **単独馬の能力** を捉えるもので、識別力はやや上がるが市場 q を超える edge は出ていない([[feature-020-adoption-result]]/[[feature-023-pace-time-result]])。次のレバーは「市場が値付けしにくい **レース内の相互作用(展開)**」と「デビュー馬(026 血統)」。

本 feature は repo 初の **field-composition(出走馬の組合せ)特徴**。「この馬が誰と走るか=展開」を捉える。各馬の strictly-before の優勢脚質(逃げ/先行=front, 差し/追込/マクリ=closer)は 023 `pace_features.py` が既に as-of(対象レース日より前・同日除外・`merge_asof(allow_exact_matches=False)`)で算出済み。これを **今走 race_id 内で leave-one-out 集約** し、フィールドのペース構成と自馬脚質との相性を表す。人間ハンデキャッパーが出馬表時点で読む「展開予想」と同じ情報のみを使う。

## 利用者と価値

意思決定支援([[product-goal-decision-support]])の利用者(=予測を見る人間)に対し、モデルが「単独能力」だけでなく「展開の有利不利」を加味した win 確率を提示できるようにする。直接の利用者はモデル学習/評価/serving パイプライン(features を経由)。

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - フィールド構成(ペース予想)の特徴化 (Priority: P1, MVP)

各レースの出走馬について、同レース他馬(自馬除外=leave-one-out)の as-of 優勢脚質を集約し、レースのペース構成(先行馬が多い→ハイペース→差し有利、等)を表す連続値特徴を生成する。

**Why this priority**: 本 feature の中核シグナル。フィールドのペース構成は展開予想の土台で、これ単体でも「想定ペース」という新情報を与える。

**Independent Test**: 合成 3 頭(過去に先行2頭・差し1頭)のレースで、差し馬の `field_front_rate_ex_self` が他2頭の as-of front_runner_rate の平均、`pace_imbalance_ex_self` が front−closer になることを検証。自馬は集計から除外される。

**Acceptance Scenarios**:

1. **Given** 3 頭立てで A・B が過去 front 傾向・C が closer 傾向, **When** C 行の特徴を作る, **Then** `field_front_rate_ex_self` = mean(A,B の as-of front_runner_rate), `field_closer_rate_ex_self` = mean(A,B の as-of closer_rate), `pace_imbalance_ex_self` = field_front − field_closer。
2. **Given** ある馬がデビュー馬で他馬も全て過去走無し, **When** 特徴を作る, **Then** field 集計は NaN(0 埋めしない)・`field_style_coverage` = 0。
3. **Given** 1 頭のみ脚質判明・他は不明, **When** 特徴を作る, **Then** `field_style_coverage` = 判明数/field_size を反映、集計は判明馬のみで算出。

---

### User Story 2 - 自馬脚質 × フィールド構成の相互作用 (Priority: P1, MVP)

各馬の as-of 脚質(own.front_runner_rate/closer_rate/rel_corner_pos_avg)とフィールド構成の積/差で、展開の有利不利(相性)を表す相互作用特徴を生成する。

**Why this priority**: codex の独立判断でも「相互作用を主役に」が推奨。フィールド集計単独は position_style への上積みが薄い恐れがあり、相互作用が識別力の本命。

**Independent Test**: 差し馬(own.closer_rate 高)でフィールドに先行馬が多い(field_front_rate_ex_self 高)とき `closer_setup` が大きくなることを確認。

**Acceptance Scenarios**:

1. **Given** own.front_runner_rate と field_front_rate_ex_self, **When** 特徴を作る, **Then** `front_pressure` = own.front_runner_rate × field_front_rate_ex_self(自分も先行でフィールドも先行多=競り合い不利)。
2. **Given** own.closer_rate と field_front_rate_ex_self, **When** 特徴を作る, **Then** `closer_setup` = own.closer_rate × field_front_rate_ex_self(差しでフィールド先行多=展開向く)。
3. **Given** own.rel_corner_pos_avg と同レース他馬の rel_corner_pos_avg 平均, **When** 特徴を作る, **Then** `style_mismatch` = own.rel_corner_pos_avg − mean_ex_self(rel_corner_pos_avg)。
4. **Given** own.* または field 値が NaN, **When** 相互作用を作る, **Then** 結果は NaN(0 埋めしない)。

---

### User Story 3 - リーク安全保証 (Priority: P1, MVP)

field 集計は同レース他馬の **strictly-before の as-of 脚質のみ** を使い、今走の結果(着順/corner_orders/finish_time/running_style/result_status)を一切参照しない。自馬は leave-one-out で除外、他馬も同日除外を保持。

**Why this priority**: 憲法 II(非交渉)。field-composition は repo 初で「他馬の値を読む」新しいパターン。リーク境界を新設しないことの保証が release gate。

**Independent Test**: leak-guard test — 自馬の今走結果・同レース他馬の今走結果・同日他レース・未来レース のいずれを変えても本群の全列が不変。

**Acceptance Scenarios**:

1. **Given** あるレース, **When** 自馬の今走 finish_order/corner_orders/running_style を変える, **Then** 本群の列は不変。
2. **Given** あるレース, **When** 同レース他馬の今走結果を変える, **Then** 本群の列は不変(他馬の **過去** のみ使うため)。
3. **Given** あるレース, **When** 同日他レース・未来レースの結果を変える, **Then** 本群の列は不変。
4. **Given** ソースコード, **When** grep, **Then** 今走の running_style/corner_orders を field 集計で参照していない(過去 as-of 経由のみ)。

---

### User Story 4 - materialization パリティ・カバレッジ (Priority: P2)

025 の単一 as-of 源 `build_asof_features` に pace_scenario ブロックを追加し、materialize 経路と in-memory 経路が bit 一致する。serving 未来レース(parquet 非カバー)は単一レース fallback で同一実装により field 集計する。

**Why this priority**: 既存採用済みモデルの予測を変えない(憲法 III/V)・serving との一貫性のため必須だが、US1-3 の上に乗る横断要件。

**Independent Test**: 実 DB で materialize==in-memory を `assert_frame_equal(check_exact=True, check_dtype=True)`。pace_scenario 列が materialized_columns に収録される。

**Acceptance Scenarios**:

1. **Given** 実 DB プール, **When** materialize 経路と in-memory `build_feature_matrix` を比較, **Then** 全列 bit 一致(float64)。
2. **Given** parquet 非カバーの未来レース, **When** serving が特徴を作る, **Then** 単一レース fallback が生成と同一値を返す。
3. **Given** registry, **When** materialized_columns を導出, **Then** pace_scenario 列が含まれ、odds/payout/dividend トークンは含まれない。

---

### User Story 5 - 採用判定(事前登録 OOS) (Priority: P1)

pace_scenario を 1 bundle として、baseline=features-008 vs candidate=features-009(=008+pace_scenario) を walk-forward OOS で評価し、事前登録ゲートを通れば採用。

**Why this priority**: 憲法 III(評価先行・非交渉)。採否はデータが決める。

**Independent Test**: `feature-eval --drop-groups pace_scenario` で baseline=features-008、candidate=full の AdoptionReport を取得。

**Acceptance Scenarios**:

1. **Given** 実 DB walk-forward OOS, **When** bundle を評価, **Then** primary(平均 win LogLoss 改善 かつ ECE 非悪化)+ fold ガード(strict majority・worst-fold ECE 2e-3・worst-fold dLogLoss 5e-3)で adopted を機械判定。
2. **Given** 採用, **When** serving 再学習, **Then** lgbm-031 を active 昇格・lgbm-030 retired(feature_hash 整合)。
3. **Given** 不採用, **When** 判定後, **Then** ブランチ保全(027 前例)・main は features-008/lgbm-030 のまま。

---

### Edge Cases

- 全馬デビュー(過去走無し)レース → field 集計全 NaN、`field_style_coverage`=0。0 埋めしない。
- 1 頭立て(leave-one-out で他馬 0) → field_*_ex_self は NaN。
- 取消馬の扱い → フィールド母集団は `entry_status` ベース(取消確定タイミングを serving と一致)。今走 result_status は使わない。
- 脚質 Unknown 多発レース → coverage で明示、判明馬のみで集計(0 埋め回避)。
- 同日に複数レース出走(稀) → 各馬の as-of 値は既に同日除外済みなので安全。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 各 (race_id, horse_id) に対し、同レース他馬(自馬除外)の as-of front_runner_rate/closer_rate の平均から `field_front_rate_ex_self`・`field_closer_rate_ex_self`・`pace_imbalance_ex_self`(=front−closer) を算出する。
- **FR-002**: 自馬の as-of 脚質値(own.front_runner_rate/closer_rate/rel_corner_pos_avg)とフィールド構成から `front_pressure`・`closer_setup`・`style_mismatch` を算出する。own.* は 023 と同一定義・同一機構を再利用し二重実装しない。
- **FR-003**: `field_style_coverage` = 脚質判明馬数 / field_size を別特徴として算出する。
- **FR-004**: 欠損(過去走無し・脚質不明)は 0 埋めせず NaN を伝播する。全列 float64 固定。
- **FR-005**: field 集計は同レース他馬の strictly-before(対象レース日より前)の as-of 脚質のみを使い、今走の race_results/result_status/finish_order/corner_orders/running_style を読まない。自馬は leave-one-out 除外、他馬は同日除外を保持。
- **FR-006**: フィールド母集団は entry_status ベース(取消確定タイミングを serving と一致)。
- **FR-007**: 025 `build_asof_features` の単一 as-of 源に pace_scenario ブロックを追加し、materialize/ in-memory/ serving fallback が同一実装で同一値を返す(ドリフト無し)。
- **FR-008**: registry に pace_scenario group を登録(列を PRE_ENTRY/missing=NULL)、FEATURE_VERSION を features-009 に更新。新ソース列は無し(running_style/corner は 023 で既にロード済み)ので source_fingerprint は無改修。
- **FR-009**: feature-eval の既定 `--drop-groups` を pace_scenario にし、bundle 単位で baseline=features-008 vs candidate=features-009 を walk-forward OOS 評価できる。ablation(field_only/interaction_only/diversity_only)は diagnostic 専用で採否に使わない。
- **FR-010**: win→joint(009) は不変(特徴追加のみ)。LightGBM/binary・Unknown=NaN 維持。スキーマ変更なし。

### Key Entities

- **pace_scenario group(本 feature の新規列, 全て as-of/field-derived, float64)**: `field_front_rate_ex_self`・`field_closer_rate_ex_self`・`pace_imbalance_ex_self`・`front_pressure`・`closer_setup`・`style_mismatch`・`field_style_coverage`(列の最終確定は plan/contracts で)。
- **既存再利用**: 023 pace_features の per-horse as-of 脚質(front_runner_rate/closer_rate/rel_corner_pos_avg)、loader の running_style/corner_orders(過去 as-of 経由)、entry_status、field_size。

## Success Criteria *(mandatory)*

- **SC-001 (リーク, 非交渉)**: leak-guard test 全通過(自馬今走・他馬今走・同日・未来の不変)+ ソース grep で今走脚質/結果を field 集計に使わない。
- **SC-002 (パリティ, 非交渉)**: 実 DB で materialize==in-memory が bit 一致(assert_frame_equal check_exact/check_dtype)。FEATURE_VERSION は features-009 に上がるが、materialize 導入自体は出力を変えない(009 の値は生成経路に依らず同一)。
- **SC-003 (採用ゲート)**: walk-forward OOS の AdoptionReport が事前登録基準で機械判定され、採否が客観的に決まる(数値を見てから列/閾値を変えない)。
- **SC-004 (正しさ)**: US1-3 の Independent Test(leave-one-out 集計値・相互作用値・coverage・NaN 伝播)がユニットテストで検証される。
- **SC-005 (透過)**: features の lint/test 緑、eval/training/serving 既存テストが透過で緑、build_feature_matrix 経由で training/eval が恩恵を受ける。

## Assumptions

- 023 の per-horse as-of 脚質(front_runner_rate/closer_rate/rel_corner_pos_avg)は既に正しくリーク安全に算出されている(本 feature はそれを field 集約するのみ)。
- 脚質判明率は netkeiba カバレッジに依存(過去走の running_style 由来)。デビュー馬・古いレースで NaN が増えるが coverage で明示。
- 採用ゲートの閾値・fold ガードは 020/023/030 と同型を流用(事前登録)。

## Dependencies

- Feature 023(pace_features の per-horse as-of 脚質)— 必須前提。
- Feature 025(materialization 単一 as-of 源・パリティ機構)。
- Feature 020/030(feature-eval / adoption gate / FEATURE_GROUPS 機構)。

## Out of Scope / Deferred

- codex Q3 の他の中コスト交互作用候補(距離替わり×末脚・クラス替わり×時計・枠順×脚質×コース・斤量変化×能力・低履歴×血統/人)は §3 後続の別 feature。
- ペースの本格モデリング(furlong 毎 sectional・想定隊列の動的シミュレーション)・脚質の連続埋め込み(036 前例)は §4/deferred。
- time-level cutoff(post_time)は deferred(004 は date-level)。
- market 超過 edge は SECONDARY(採否バーでない)。
