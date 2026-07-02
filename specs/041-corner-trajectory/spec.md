# Feature Specification: コーナー通過順の軌跡特徴 (Corner Trajectory)

**Feature Branch**: `041-corner-trajectory`

**Created**: 2026-07-02

**Status**: Draft

**Input**: User description: "過去走の『位置の変化』(直線の伸び・捲り・先行位置)を as-of 集約した新特徴 4 列。023 は位置の水準のみでデルタは未活用。DB 既存データのみ・スキーマ変更なし・FEATURE_VERSION features-011→012。"

## 概要

023 pace/position 特徴は「平均コーナー位置・脚質率」= **位置の水準**のみを使い、**位置の変化(トラジェクトリ)**は未活用だった。本 feature は過去走ごとの通過順デルタ —「最終コーナーから確定着順までに何頭抜いたか(直線の伸び)」「コーナー間で順位をどれだけ押し上げたか(捲り)」「最初のコーナーでの位置(先行度)」— を as-of 集約した 4 列を追加する。

**de-risk 済み**(spike, 2019+ 実データ 3 fold, 現行 production と同一学習経路 = cond_logit+TE, baseline=features-011):

| | winner-NLL | top1 | AUC |
|---|---|---|---|
| baseline(features-011) | 2.1087 | 0.2781 | 0.7942 |
| **+corner_traj(4列)** | **2.1052**(−0.0035) | **0.2801** | **0.7952** |

**全 3 fold で改善**・新列カバレッジ 89%。同時に検証した他候補は棄却済み(binary×cond_logit ensemble = 校正コスト構造的で不採用、レース内 rank/gap = flat)。

DB 既存データのみ(netkeiba アクセス不要=標準方針遵守)。スキーマ変更なし。FEATURE_VERSION features-011→**012**。

## 特徴定義

過去走ごとの生スコア(いずれも**当該過去走**の field_size で正規化):

| 生スコア | 定義 | 意味 |
|---|---|---|
| late_gain | (最終コーナー通過順 − 確定着順) / field_size | 正 = 直線で前を捕らえた(末脚の実効性) |
| early_pos | 最初のコーナー通過順 / field_size | 先行度の水準 |
| mid_move | 連続コーナー間の順位改善の最大値 / field_size | 捲り(中盤の押し上げ) |

as-of 集約(対象レースより**厳密に前**の過去走のみ・同日除外):

| 列 | 集約 |
|---|---|
| `asof_late_gain_avg` | late_gain の過去平均 |
| `asof_late_gain_best` | late_gain の過去最大 |
| `asof_early_pos_avg` | early_pos の過去平均 |
| `asof_mid_move_avg` | mid_move の過去平均 |

全 float64・NaN 伝播(過去走なし/corner 情報なしは NaN、0 埋め禁止)。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 軌跡特徴の算出 (Priority: P1)

新モジュールが過去走の corner_orders・確定着順・頭数から late_gain/early_pos/mid_move を算出し、対象レースより前の走のみを as-of 集約した 4 列を返す。

**Why this priority**: 本 feature の中核。

**Independent Test**: 合成データ(2 過去走 + 対象レース)で、対象行の asof_* が過去走のみから期待値どおり計算される(例: 過去走 corner ['5','3']・着順 1・10頭 → late_gain=(3−1)/10=0.2)。

**Acceptance Scenarios**:

1. **Given** 馬 H の過去走(corner ['5','3']・着順 1・10 頭), **When** 対象レース行の特徴を算出, **Then** asof_late_gain_avg = 0.2・asof_early_pos_avg = 0.5。
2. **Given** 複数コーナー ['8','6','2'] の過去走, **When** mid_move を算出, **Then** max(8−6, 6−2)/field = 4/field(捲り検出)。
3. **Given** デビュー馬(過去走なし), **When** 算出, **Then** 4 列すべて NaN(0 埋めしない)。
4. **Given** corner_orders が欠損/不正の過去走, **When** 算出, **Then** その走はスキップされ他の過去走から集約(全滅なら NaN)。

---

### User Story 2 - リーク安全保証 (Priority: P1)

軌跡特徴は対象レースの結果・通過順を一切参照しない(strictly-before + 同日除外)。

**Why this priority**: 憲法 II 非交渉。corner_orders/finish_order は結果由来データであり、as-of 境界の担保が release gate。

**Independent Test**: leak-guard — 対象レース自身の corner/着順を変更しても対象行の特徴が不変、同日他レースの変更でも不変、ソース grep で今走列の生参照なし。

**Acceptance Scenarios**:

1. **Given** 対象レースの corner_orders/finish_order を極端値に変更, **When** 再算出, **Then** 対象行の 4 列は不変。
2. **Given** 同日の別レース結果を変更, **When** 再算出, **Then** 不変(同日除外)。
3. **Given** 未来レースの追加/変更, **When** 再算出, **Then** 過去行の値は不変(pool-end 非依存)。

---

### User Story 3 - materialization パリティ (Priority: P2)

025 build_asof_features に単一経路で結線し、materialize 経路と in-memory 経路が bit 一致する。serving 未来レースは単一レース fallback(生成と同一実装)。

**Why this priority**: 憲法 III/V(パリティ非交渉)。ただし機構は 025-033 で確立済みの踏襲。

**Independent Test**: assert_frame_equal(check_exact=True, check_dtype=True) が 4 列込みで通過。新ソース列なし(corner_orders/finish_order/race_horses は既ロード)= source_fingerprint 無改修。

**Acceptance Scenarios**:

1. **Given** materialize 済み parquet, **When** read 経路と in-memory を比較, **Then** bit 一致(4 列含む)。
2. **Given** materialized_columns, **When** 検査, **Then** 4 列が収録され odds/payout/dividend トークンなし。

---

### User Story 4 - 採用判定(事前登録 18-fold OOS) (Priority: P1)

事前登録ゲートで features-012(+4列)が features-011 を上回るときだけ採用する。

**Why this priority**: 憲法 III 非交渉。

**Independent Test**: `feature-eval --drop-groups corner_trajectory` の AdoptionReport に事前登録基準を機械適用。

**Acceptance Scenarios**:

1. **Given** 18-fold walk-forward OOS, **When** baseline(features-011 相当)と candidate(features-012)を比較, **Then** PRIMARY(win LogLoss 改善 かつ ECE 非悪化)+ fold ガード(strict majority・worst-fold ECE 2e-3・worst-fold dLogLoss 5e-3)で採否。
2. **Given** 採用, **When** lgbm-041 を学習・登録, **Then** active 昇格・lgbm-039 retired・serving 自動ロード(feature_hash=features-012)。
3. **Given** 不採用, **When** ゲート未達, **Then** main は features-011/lgbm-039 のまま、ブランチ保全(027/037/038 前例)。

---

### Edge Cases

- **コーナー 1 つのみ**(短距離等): mid_move は連続ペアなし → NaN(その走の mid_move のみ欠損、late_gain/early_pos は有効)。
- **corner_orders 空 list / 数値化不能**: その走はスキップ(全過去走が該当なら NaN)。
- **同着・降着**: finish_order は確定着順(DB 値)をそのまま使用(late_gain のラベルとして)。
- **field_size 欠損/0**: その走はスキップ(0 除算回避)。
- **地方/海外からの転入初戦**: JRA 過去走なし → NaN(is_debut と同等の扱い、Unknown≠0)。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: システムは過去走ごとに late_gain/early_pos/mid_move を corner_orders・確定着順・当該走の started 頭数から算出する MUST(正規化は当該過去走の field_size)。
- **FR-002**: as-of 集約は対象レースより厳密に前の走のみ・同日除外で行い、`asof_late_gain_avg`・`asof_late_gain_best`・`asof_early_pos_avg`・`asof_mid_move_avg` の 4 列を返す MUST(全 float64・NaN 伝播・0 埋め禁止)。
- **FR-003**: 対象レース自身の corner_orders/finish_order/result を参照しない MUST(leak-guard: 今走変更・同日変更・未来変更で不変 + ソース grep)。
- **FR-004**: 4 列は registry に group=`corner_trajectory`・PRE_ENTRY・NULL で登録し、FEATURE_VERSION を features-012 に bump する MUST。
- **FR-005**: 025 build_asof_features に単一経路で結線し、materialize/in-memory の bit パリティを維持する MUST。新ソース列なし(source_fingerprint 無改修)を確認する MUST。serving 未来レースは単一レース fallback。
- **FR-006**: 採用判定は事前登録 18-fold walk-forward OOS(feature-eval --drop-groups corner_trajectory)で行う MUST。PRIMARY = win LogLoss 改善 かつ ECE 非悪化 + fold ガード。ablation/market_edge は SECONDARY 診断。
- **FR-007**: 採用時は lgbm-041(objective=cond_logit・TE jockey/trainer・isotonic = 現行 production 構成 + features-012)を学習・登録し active 昇格・lgbm-039 retired。不採用時はブランチ保全 MUST。
- **FR-008**: DB スキーマ変更なし MUST(migration head 不変)。probability/API/front は不変 MUST(feature 列はモデル内部)。
- **FR-009**: 版 bump の波及(features-011 リテラルを持つテスト)を 012 に更新する MUST(040 マージ後の main 基準)。

### Key Entities *(include if feature involves data)*

- **軌跡生スコア(per 過去走)**: late_gain/early_pos/mid_move。corner_orders(数値文字列 list)+ finish_order + field_size から導出。結果由来 = 特徴には as-of 集約のみで供給。
- **corner_trajectory 特徴群(4 列)**: 上記の as-of 集約。registry group=corner_trajectory、FEATURE_VERSION features-012。
- **model_version lgbm-041**(採用時のみ): features-012 + cond_logit + TE + isotonic。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 18-fold OOS で features-012 が features-011 の win LogLoss を改善し ECE を悪化させない(PRIMARY 通過)。
- **SC-002**: fold ガード通過(strict majority・worst-fold ECE ≤2e-3・worst-fold dLogLoss ≤5e-3)。
- **SC-003**: leak-guard 全通過(今走変更・同日変更・未来変更で不変、今走列の生参照なし)。
- **SC-004**: materialize parity bit 一致(4 列込み、実 DB)。source_fingerprint 無改修。
- **SC-005**: 4 列のカバレッジが実 DB で 85% 以上(spike 実測 89%)、デビュー馬は NaN(Unknown≠0)。
- **SC-006**: スキーマ不変・probability/API/front 既存テスト透過で緑。

## Assumptions

- corner_orders は数値文字列 list(実 DB 確認済、カバレッジ ~100%)。数値化不能要素を含む走はスキップ。
- finish_order の過去走利用は history(avg_finish/prev_finish)・023(finish_diff)と同じ既存リーク境界(結果は過去走のラベルとしてのみ)。
- 同日除外は 004 の daily cumsum−当日機構を踏襲(spike は shift(1) 近似だったが、production は既存機構で厳密化)。
- 採用ゲート閾値は 030-033 と同一(事前登録、数値を見てから動かさない)。
- 035 lap ブランチが branch 上で features-012 を名乗っているが未マージのため、main の次版として features-012 を使用(lap 特徴は将来マージ時に 013+ へ rebase)。

## Dependencies

- features loader(corner_orders/finish_order/race_horses は 023/020 で既ロード)・025 materialization・registry。
- training feature-eval(--drop-groups)・train-evaluate(--objective cond_logit、039)。
- serving は features-012 の feature_hash 整合(transparent)。

## Out of Scope (Deferred)

- コーナー別の個別遷移(2角→3角等)の詳細特徴。
- 軌跡×展開(031 field-composition)交互作用。
- 通過順の系列モデル化(embedding/RNN 等)。
- race_laps(部分 backfill)との結合。
