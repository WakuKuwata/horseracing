# Feature Specification: 低コスト特徴拡充 (Low-cost Feature Expansion)

**Feature Branch**: `030-low-cost-features`

**Created**: 2026-06-29

**Status**: Draft

**Input**: 予測の絶対品質を上げるため、DB に既に 99〜100% 入っているのに未活用の安価でリーク安全な特徴を一括追加する（§2「すぐ作れる」群）。最有力は **斤量(jockey_weight, 100% あるのに完全未使用)**。020 同型で 1 feature に複数 group を ablation 分離で載せ、walk-forward OOS で採用判定。025 materialization 基盤に乗せる。スキーマ変更なし。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 斤量(handicap)を特徴化 (Priority: P1)

予測モデルが各馬の **負担重量(斤量, jockey_weight)** と関連量（前走斤量差・斤量/馬体重比・出走馬中の相対斤量）を受け取れるようにする。斤量は事前確定の古典的好指標だが現状モデルに一切入っていない。

**Why this priority**: 100% populate されているのに未使用＝最も投資対効果が高い単独の cheap win。今走の値で完結（as-of 集計不要）でリークもない。

**Independent Test**: 合成データで jockey_weight=56.0、前走55.0 なら weight_change=+1.0、斤量/馬体重比・フィールド相対が正しく出る。値欠損は NaN。

**Acceptance Scenarios**:
1. **Given** 今走斤量56.0・前走 started race 斤量55.0, **When** 特徴生成, **Then** `carried_weight=56.0`・`carried_weight_change=+1.0`。
2. **Given** 斤量56.0・馬体重480, **When** 生成, **Then** `carried_weight_ratio=56/480`。
3. **Given** レース内の全馬の斤量, **When** 生成, **Then** `carried_weight_rel`=今走斤量 − 出走馬平均（同レース・事前既知）。
4. **Given** 前走なし/斤量欠損, **When** 生成, **Then** 該当列 NaN。

---

### User Story 2 - 複勝率(place_rate)を特徴化 (Priority: P1)

馬の as-of 複勝率（2着内率/3着内率）と距離帯別複勝率を追加する。現状は単勝(win_rate)のみで、好走の安定性を表す複勝系が無い。

**Why this priority**: win_rate と同じ実装機構（自馬 as-of, strictly-before）で安価。連対安定性は識別力に寄与しうる。

**Independent Test**: 過去 5 走中 2着内 3回 なら place_rate=0.6（対象レースより前のみ・同日除外）。

**Acceptance Scenarios**:
1. **Given** 過去走の着順履歴, **When** 生成, **Then** `place_rate`(top2)・`show_rate`(top3) が strictly-before で算出。
2. **Given** 距離帯別, **When** 生成, **Then** `dist_band_place_rate` が当該距離帯の as-of 複勝率。
3. **Given** デビュー, **When** 生成, **Then** NaN。

---

### User Story 3 - 人(騎手/調教師)拡充 (Priority: P2)

騎手/調教師の as-of 複勝率・直近フォーム・騎手×芝ダ・騎手調教師コンビ・乗り替わり(今走騎手 vs 直前 started race 騎手の変化)を追加する。現状は win_rate のみ。

**Why this priority**: human_form の確立機構（対象行＋同日除外）の拡張。コンビ/乗り替わりは市場が一律に織り込みにくい余地。

**Independent Test**: 騎手の複勝率・コンビ勝率が対象行＋同日除外で算出、乗り替わり flag が直前騎手と異なれば 1。

**Acceptance Scenarios**:
1. **Given** 騎手の過去騎乗, **When** 生成, **Then** `jockey_place_rate`・`jockey_recent_win_rate` が対象行＋同日除外。
2. **Given** 騎手×調教師, **When** 生成, **Then** `jt_combo_win_rate`(コンビ as-of 勝率)。
3. **Given** 今走騎手≠直前 started race 騎手, **When** 生成, **Then** `jockey_change=1`（同じなら0、デビュー NaN）。

---

### User Story 4 - コース適性・季節 (Priority: P2)

当該競馬場(venue)での自馬 as-of 勝率/複勝率（course_aptitude）と、レース日由来の季節特徴（month/season、静的）を追加する。**枠バイアス(draw_bias)は不採用**（codex Q2: baseline は既に frame/horse_number/venue/distance/field_size を静的に持ち LightGBM が course×draw 交互作用を学習可能・市場織り込み済みの公算）。

**Why this priority**: course は aptitude 系の自然な拡張。season は 100% データの安価な静的特徴（季節の馬場・年齢曲線・開催サイクル）。どちらも低優先で、独立ゲートで寄与を測る。

**Independent Test**: 当該 venue の自馬 as-of 勝率が対象レース/同日除外で算出。race_date から month/season が出る。

**Acceptance Scenarios**:
1. **Given** 自馬の当該 venue 過去走, **When** 生成, **Then** `venue_win_rate`/`venue_place_rate`(as-of, 対象行＋同日除外)。
2. **Given** race_date=2025-10-12, **When** 生成, **Then** `race_month`=10・`race_season`(季節区分, 静的・今走既知)。
3. **Given** 当該 venue 母数が閾値未満/デビュー, **When** 生成, **Then** venue 系は NaN。

---

### User Story 5 - 採用判定（OOS）と group 別寄与 (Priority: P1)

020/023/026 同型の walk-forward OOS で 030 候補（features-008）を baseline(features-007) と比較。**全体ゲート＋ group 別 ablation 診断**を出し、効いた群を見極める。各 group は事前登録仮説（実装前に固定）＝選択リークにならない。

**Why this priority**: 「効かない群が良い群を薄める」(027 教訓)を避け、何が効いたかを ablation で可視化するため。客観ゲートで採否を決める（憲法 III）。

**Independent Test**: `feature-eval --drop-groups <030群>` で baseline=features-007、`feature-ablation` で group 別寄与が出る。

**Acceptance Scenarios**:
1. **Given** 030 全群を事前固定, **When** feature-eval, **Then** 平均 win LogLoss 差・ECE 差・fold 別・worst-fold の AdoptionReport。
2. **Given** ablation, **When** 実行, **Then** group 別 OOS 寄与（診断）が出る。
3. **Given** 全体が横ばいでも特定群が明確に寄与, **When** 判定, **Then** その群を次サイクルで単独再評価できる（事前登録仮説）。

### Edge Cases
- **斤量/馬体重 欠損**: 比率・相対は NaN（0 補完しない）。
- **デビュー**: place_rate/人拡充/乗り替わり/course は NaN。
- **同日・対象行除外**: 跨馬集計(人/枠)は対象行＋同日除外（human_form 同型）。
- **脚質/展開は対象外**: `running_style` が `corner_orders`(結果)由来＝今走脚質はリーク。§3 で過去脚質ベースに再設計（本 feature では扱わない）。
- **odds/popularity**: 市場 q なのでモデル特徴にしない。
- **枠バイアス**: 市場織り込み済みで OOS 寄与が薄い可能性（ablation で確認、薄ければ次で落とす）。

## Requirements *(mandatory)*

- **FR-001**: handicap group（静的・今走既知）: `carried_weight`(jockey_weight)・`carried_weight_change`(今走−直前 started race)・`carried_weight_ratio`(斤量/馬体重)・`carried_weight_rel`(同レース平均差) を生成する MUST。欠損は NaN（**馬体重欠損時は ratio を 0 補完せず NaN 伝播**, codex Q3）。
- **FR-002**: place_rate group（as-of 自馬, strictly-before・同日除外）: `place_rate`(top2)・`show_rate`(top3)・`dist_band_place_rate` を生成する MUST。
- **FR-003**: human_form_plus group（as-of 跨馬, 対象行＋同日除外）: 騎手/調教師の複勝率・直近フォーム・騎手×芝ダ・`jt_combo_win_rate`・`jockey_change`(乗り替わり) を生成する MUST。
- **FR-004**: course_aptitude group（as-of 自馬, 対象行＋同日除外）: `venue_win_rate`/`venue_place_rate` を生成する MUST。母数 < 閾値は NaN。**draw_bias は不採用**（codex Q2: 既存静的 frame/horse_number/venue/distance/field_size で交互作用学習可・市場織り込み済み）→ Deferred。
- **FR-004b**: season group（静的・今走既知）: `race_month`(1-12)・`race_season`(季節区分) を race_date から生成する MUST（codex Q5）。
- **FR-005**: 全 as-of は対象レースより前のみ（`_cum_before_by`/`merge_asof(allow_exact_matches=False)`）、跨馬統計は対象行＋同日除外（human_form 同型）MUST。odds/popularity/今走結果(着順・corner・running_style)を特徴にしない MUST NOT（leak-guard）。
- **FR-006**: `running_style` は `corner_orders`(結果)由来のため**今走の脚質/展開は使わない** MUST NOT（脚質系は §3 で過去脚質ベースに再設計）。
- **FR-007**: 全 030 列は float64 固定（プール依存 dtype ドリフト回避＝025 パリティ）MUST。Unknown=NaN 維持（0 補完しない）。
- **FR-008**: 025 単一 as-of 源（`build_asof_features`）に as-of 群を追加、静的群（斤量）は `build_static_features` に追加。materialize==in-memory bit パリティ維持 MUST。source_fingerprint は races/race_horses/race_results を既にカバー（新ソース無し＝拡張不要）であることを確認する MUST。
- **FR-009**: registry に group（handicap/place_rate/human_form_plus/course_aptitude/season）登録、materialize 対象列が機械導出される MUST（静的群=handicap/season は STATIC_COLUMNS、as-of 群は materialized_columns）。列に odds/payout/dividend トークン無し MUST NOT。
- **FR-010**: FEATURE_VERSION features-007→008 に bump する MUST。
- **FR-011**: 採用は walk-forward OOS。**採用プロトコル（事前登録, codex Q4）**: 各 group は「単独で features-007 に足して同一ゲート（平均 win LogLoss 改善 AND ECE 非悪化 + strict majority + worst-fold tol）を通れば採用」と**実装前に固定**。出荷 features-008 = features-007 ＋ 通過 group の和集合。group/列/fold/baseline/指標/閾値を eval 前に凍結し、OOS 数値を見てから group の取捨や組合せを設計しない（選択リーク回避）。`feature-ablation` は診断のみ。市場 q 超過は採否バーにしない MUST。
- **FR-012**: win→joint(009) 不介入・LightGBM/binary・Unknown 維持 MUST。スキーマ変更なし（head 不変）MUST。

### Key Entities
- **Carried weight（斤量）**: race_horses.jockey_weight（負担重量, 100% populate）。事前確定の静的属性。
- **Place/show record**: 馬の as-of 複勝(top2)/3着内(top3) 集計。
- **Human aggregate+**: 騎手/調教師/コンビ の as-of 複勝率・直近フォーム・乗り替わり。
- **Course/draw aggregate**: venue 自馬適性・venue×distance 枠別バイアス。

## Success Criteria *(mandatory)*
- **SC-001**: 斤量関連が全出走馬で算出（jockey_weight 100%）。欠損は NaN・0 混入ゼロ。
- **SC-002**: leak-guard で今走結果(着順/corner/running_style)・同日他レース・未来 を変えても 030 列が不変。
- **SC-003**: materialize==in-memory bit 一致（030 列含む）。
- **SC-004**: walk-forward OOS の AdoptionReport ＋ group 別 ablation が出力され、採否が客観ゲートで決まる。
- **SC-005**: DB migration head 不変、features に新テーブル追加ゼロ。

## Assumptions
- jockey_weight=負担重量(斤量)、weight=馬体重、weight_diff=馬体重増減（既存）。斤量は事前確定でリーク無し。
- 距離帯(dist_band)・芝ダ(track_type)・venue は既存定義を再利用。複勝=top2、3着内=top3。
- draw_bias の母数閾値（min_starts）は plan で実分布から確定。
- 採用ゲート・ablation・fold 構成は 020/023/026 既存実装(eval)を再利用。group は事前登録仮説として固定（OOS で特徴選択しない）。
- 025 materialization 基盤・017 p校正は利用可能（main マージ済み）。
- codex 設計 second opinion を並走起動済み（脚質リーク・枠バイアス価値・採用戦略）。結果到着時に reconcile。

## Deferred
- **脚質/展開(pace_setup)**: running_style が結果由来のため、過去脚質ベースの as-of フィールド構成として §3(中コスト)で再設計。
- 休養明け×条件・距離替わり×上がり 等の**交互作用系**（§3）。
- スピード指数(補正タイム)・トラックバイアス推定（§3）。
- furlong sectional・調教タイム・血統 ID 化（§4, 新データ取得先行）。
- **draw_bias（枠/馬番バイアス）**: 既存静的 frame/horse_number/venue/distance/field_size で LightGBM が交互作用を学習可・市場織り込み済みの公算（codex Q2）→ 本 feature では作らない。
- **race.grade（G1/G2 等）**: 実 DB で 26.8% のみ・コードが不透明(E/C/B/A/L/H/G)・race_class(100%) と冗長 → コード解読込みで別途検討（codex Q5 は提案したが実データはスパース）。
- pace_setup（脚質/展開）は §3 で過去脚質ベース再設計。
