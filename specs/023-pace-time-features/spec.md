# Feature Specification: ペース/時計シグナルの特徴量化 (Pace & Time Features)

**Feature Branch**: `023-pace-time-features`

**Created**: 2026-06-28

**Status**: Draft

**Input**: User description: 上がり3F・走破時計・通過順位・脚質の as-of 集計をリーク安全に特徴量化し、win 予測の識別力/校正を底上げ + 市場超過を診断する。

## 背景 (Why)

Feature 020 で、公開情報ベースの基本特徴（近走着順・距離適性・騎手調教師）を足してもモデル p は市場 q を超えられないと実データで確認された（市場 q win LogLoss 0.202 < モデル p 0.234、p−q edge は実現勝率と逆相関）。次のレバーとして、市場が相対的に織り込みにくい可能性のある **ペース・時計・脚質** シグナルを特徴量化する。これらは既に DB に取り込み済み（`race_results.last_3f` 上がり3F / `finish_time` 走破時計 / `finish_time_diff` 着差 / `corner_orders` 通過順位、`race_horses.running_style` 脚質）で、追加スクレイピングは不要。製品目的は意思決定支援（市場超過は努力目標）だが、絶対 win 品質の底上げ自体に価値がある。採用は 020 と同一の walk-forward OOS ゲートで、baseline（features-005=004+020）を上回る時のみ。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - リーク安全なペース/時計特徴の追加 (Priority: P1)

データ担当者が、各馬の「過去レースの上がり3F・走破時計・通過順位・脚質」を**対象レースより前のデータのみ**で as-of 集計した特徴量を、固定スキーマで予測モデルに供給できる。今走の結果時データ（time/順位/脚質）は一切使わない。

**Why this priority**: 新シグナルの追加が本 feature の中核。リーク安全に計算できて初めて評価できる。MVP。

**Independent Test**: 特徴量行列を生成し、(a) 対象レース当日以降のデータを変更しても各特徴が不変（cutoff）、(b) 今走の last_3f/finish_time/corner/running_style を変更しても特徴が不変（result-time 非参照）、(c) 過去成績のない馬は Unknown（0 代入でない）を確認できる。

**Acceptance Scenarios**:

1. **Given** ある馬が過去 N 走の記録を持つ, **When** 特徴量を計算する, **Then** 上がり3F/時計/通過順位/脚質の as-of 集計が対象レースより前のレースのみから算出される。
2. **Given** 対象レースの結果（着順・上がり・時計・通過順）, **When** それらを変更する, **Then** その馬のペース/時計特徴は一切変化しない（leak-guard）。
3. **Given** デビュー馬（過去走なし）, **When** 特徴量を計算する, **Then** ペース/時計特徴は Unknown（null）であり 0 を代入しない。

---

### User Story 2 - 条件正規化（距離・馬場）後の集計 (Priority: P1)

生の上がり3F・走破時計は距離・芝ダ・馬場状態・年代で水準が違うため、レース内相対化または条件別正規化を施してから as-of 集計する。これにより異なる条件のレースをまたいで比較可能な特徴になる。

**Why this priority**: 正規化なしの生時計は水準差に支配され特徴として無意味になりやすい（本 feature 成否の肝）。US1 と不可分の P1。

**Independent Test**: 同一馬の同等パフォーマンスが、異なる距離・馬場のレースでも正規化後におおむね同水準の特徴値になること、正規化に今走の結果を使っていないことを確認できる。

**Acceptance Scenarios**:

1. **Given** 距離・馬場の異なる過去 2 走で相対的に同等の上がり, **When** 正規化集計する, **Then** 生秒の差に比べ正規化後の特徴差が小さい（条件差の吸収）。
2. **Given** レース内相対化を使う場合, **When** 対象レースの正規化基準を作る, **Then** 基準は過去レースの相対値のみから作られ、今走の結果を含まない。

---

### User Story 3 - walk-forward OOS 採用判定 + 市場超過診断 (Priority: P2)

評価担当者が、固定した候補特徴セットを walk-forward OOS で baseline と比較し、win 予測品質が改善した時のみ採用を判定できる。さらに市場 q に対する超過も診断（主ゲートにしない）。

**Why this priority**: 評価先行（憲法 III）。改善が無ければ採用しない（020 で実証した正しい挙動）。

**Independent Test**: 020 のハーネス（feature-eval/feature-ablation/feature-diagnostic）を本特徴群に適用し、PRIMARY=平均 win LogLoss 改善 かつ ECE 非悪化 + fold 別差で判定、market_edge で市場超過を別途診断できる。

**Acceptance Scenarios**:

1. **Given** 固定候補（ペース/時計 group）, **When** walk-forward OOS 評価する, **Then** baseline 比で平均 win LogLoss 改善 かつ ECE 非悪化、勝ち fold 過半・最悪 fold ECE 非悪化なら adopted=true。
2. **Given** 改善が baseline 未満, **When** 評価する, **Then** adopted=false（false positive なし）。
3. **Given** 採用候補, **When** market_edge を回す, **Then** p−q gap・edge bucket 実現勝率が算出され「絶対改善≠市場超過」が明示される（診断のみ）。

---

### Edge Cases

- 過去走のない馬（新馬）→ 全ペース/時計特徴は Unknown（null）、0 代入しない。
- 競走中止・故障で time/order 欠損の過去走 → その走は集計から除外（欠損を 0 扱いしない）。
- 同日に複数出走（稀）や同日他レース → 同日・将来を除外（merge_asof allow_exact_matches=False）。
- corner_orders がコーナー数の少ない短距離で部分欠損 → 利用可能なコーナーのみ、頭数正規化。
- running_style が未記録の過去走 → その走を脚質分布の分母から除外。
- 距離帯/馬場のサンプルが極端に少ない条件での正規化基準 → 基準が不安定な場合の扱いを計画で定義（フォールバック）。

## Requirements *(mandatory)*

### Functional Requirements

**US1 — リーク安全な as-of 特徴**
- **FR-001**: システムは各出走馬について、過去レース（対象レースより前）のみから上がり3F・走破時計・着差・通過順位・脚質の as-of 集計特徴を算出しなければならない。
- **FR-002**: 今走の result-time 値（last_3f/finish_time/finish_time_diff/corner_orders/running_style）を特徴量に使ってはならない（leak-guard test で保証）。**leak-guard は今走結果の変更に加え、(a) 同走馬の今走値の変更、(b) 同日他レースの変更、(c) 未来年の時計水準（条件別基準）の変更に対しても各特徴が不変であることを検証しなければならない**（codex P0）。
- **FR-002a**（codex P0）: 正規化済みの「過去走 row」を**先に構築**し、その row だけを `merge_asof`/daily-before で as-of 集計する（今走 row を集計経路に混ぜない）。現 loader は last_3f までしか読まないため、finish_time/finish_time_diff/corner_orders/running_style の追加箇所が最大のリーク危険点であることを明記する。
- **FR-003**: as-of 計算は Feature 004/020 の `_cumulative_before`（daily cumsum−当日）+ `merge_asof(allow_exact_matches=False)` 機構を転用し、同日・将来レースを除外しなければならない。
- **FR-004**: 各特徴は source・利用可能タイミング（過去結果由来＝出走表前に確定）・欠損処理を必須記載し、過去成績のない馬は Unknown（null）とし 0 を代入してはならない。
- **FR-005**: 欠損した過去走（中止/故障で time・順位・脚質が無い）は当該特徴の集計から除外しなければならない（0 として混入させない）。

**US2 — 条件正規化**
- **FR-006**: 上がり3F・走破時計はレース内相対化または条件別（距離帯・芝ダ・馬場状態）正規化を施してから as-of 集計しなければならない（生秒を直接特徴にしない）。
- **FR-006a**（codex P0）: **主方式はレース内相対化**（各過去レース内で平均/基準との差を取り閉じ込める＝リーク面が小さい）。条件別 z-score を併用する場合、基準の平均/分散は **fold/date ごとの過去分布のみ**から作り（全期間で先に作らない＝未来時計水準の混入禁止）、少数サンプル条件は null または粗い条件にフォールバックしなければならない。
- **FR-006b**（codex 注意）: レース内相対化は「強いメンバー戦で好走した馬が相対値で不利に見える」逆転を生むため、メンバー強度の影響を緩和する設計（着差ベース併用等）を計画で検討する。
- **FR-007**: 正規化に用いる基準は過去レースの値のみから作り、対象レースの結果・同走馬の今走値・同日他レースを含めてはならない（正規化経路でのリーク禁止）。
- **FR-008**（analyze G1: US3 の position_style 任意 group に属する）: **MVP(P1) の正規化対象は pace_time（上がり3F・走破時計・着差）のみ**。通過順位の頭数正規化（相対位置）・脚質の過去分布集計は **US3 の任意 group `position_style` の一部**であり、ablation で寄与が確認できた場合のみ採用する（実装は T013）。
- **FR-009**: 正規化方式の具体定義（レース内相対 vs 距離帯/馬場 z-score vs 着差ベース）と少数サンプル条件のフォールバックは計画段階で確定し、過去データで識別力を確認しなければならない。

**US3 — 採用判定 + 診断**
- **FR-010**: 候補特徴セットは事前固定し（OOS で特徴選択しない＝評価モデル==デプロイモデル）、特徴選択・ハイパラ選択は各 walk-forward fold の学習窓内で行わなければならない（選択リーク禁止）。
- **FR-011**: 採用ゲート（PRIMARY）は walk-forward OOS の平均 win LogLoss 改善 かつ ECE 非悪化、+ fold 別差（**strict majority＝勝ち fold が過半数を厳密に超える**・最悪 fold ECE 非悪化・**最悪 fold の LogLoss 悪化が上限内**）で判定しなければならない（baseline=features-005）。codex P0: 020 実装の `n_win*2>=n_folds` は偶数 fold で半数通過になるため、本 feature では strict majority に揃える。
- **FR-011a**（codex P0）: 採用レポートには **条件別（距離帯・芝/ダ・going・開催年・q bucket）の LogLoss/ECE 差分**を含め、全体平均が条件別の崩れを隠さないようにしなければならない（ペース/時計は条件依存が強い）。
- **FR-012**: corner/style は MVP の主対象から外し**別 ablation group（任意）**として分離する（MVP は上がり3F・finish_time_diff・走破時計のレース内相対に集中、codex P1）。group ablation で各 group の寄与を分離算出し（diagnostic、採否に使わない）、market_edge（p−q calibration・edge bucket 実現勝率・q 条件付き LogLoss）で市場超過を診断しなければならない（SECONDARY、主ゲートにしない）。
- **FR-013**: feature importance のみで採否を決めてはならず、過学習対策を行わなければならない。具体的には (a) fold 安定性は採用ゲートの fold 別差・条件別差分（FR-011/011a, T011）で担保、(b) 正則化レンジ・early-stopping は **020 の LightGBMPredictor 既存設定を継承**（023 で学習ロジックは変更しない）、(c) 候補は事前固定（FR-010）で特徴数を増やしすぎない（analyze G2）。

**横断（憲法整合）**
- **FR-014**: 確率は Feature 009 の整合性（win→joint、0≤1着率≤2着以内率≤3着以内率≤1）を維持しなければならない。023 は win モデルの入力特徴を追加するのみで joint 派生ロジックに介入しないため 009 は不変＝本 feature で joint の新規 assert は不要（analyze A1、既存 009 不変条件で担保）。
- **FR-015**: 市場オッズ・今走の結果はモデル特徴にしてはならない（既存方針、leak-guard）。
- **FR-016**: スキーマ変更を行わず（データは取り込み済み、特徴は計算）、model_versions に新しい feature_version=features-006 を記録しなければならない。

### Key Entities *(include if feature involves data)*

- **ペース/時計特徴 group（MVP の主対象）**: 馬ごとの過去走 as-of 集計（**レース内相対化**した上がり3F の平均/ベスト、走破時計、着差 finish_time_diff）。source=過去 race_results、timing=出走表前確定、missing=Unknown。
- **位置取り/脚質 group（別 group・任意, codex P1）**: 相対通過順位（position/field_size・最終コーナー相対位置・位置取り変化）と過去脚質分布。ノイズ・主観分類・展開依存が大きいため MVP の主対象から外し ablation で寄与を確認、寄与が無ければ採用しない。欠損は 0 代入禁止。
- **採用レポート / ablation / market_edge**: 020 の評価成果物を本特徴群に適用（AdoptionReport / AblationReport / MarketEdgeReport）。条件別差分を追加（FR-011a）。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 新ペース/時計特徴はすべて、対象レース当日以降のデータ変更・今走結果変更に対して不変（リーク safe をテストで証明）。
- **SC-002**: 過去成績のない馬の新特徴は Unknown（null）であり 0 代入が 0 件。
- **SC-003**: 正規化後、異なる距離・馬場の同等パフォーマンスの特徴値が生秒より明確に近づく（条件差吸収を検証）。
- **SC-004**: walk-forward OOS で、新特徴採用時は「平均 win LogLoss 改善 かつ ECE 非悪化 かつ 勝ち fold 過半」を満たす場合のみ adopted=true（baseline 未超過なら false、false positive なし）。
- **SC-005**: group ablation と market_edge 診断が算出され、「絶対校正改善 ≠ 市場超過」が結果に明示される。
- **SC-006**: スキーマ変更が 0（db migration head 不変）、feature_version が features-006 として記録される。

## Assumptions

- 必要データ（last_3f/finish_time/finish_time_diff/corner_orders/running_style）は ingest 済みで利用可能（確認済み）。欠損は条件により発生し得るため除外方針で扱う。
- 正規化方式の具体（レース内相対化 / 距離帯・馬場別 z-score / 着差ベース）は計画段階で codex 検証の上確定する。
- 評価ハーネスは 020 の feature-eval / feature-ablation / feature-diagnostic を再利用（PREDICTOR-AGNOSTIC、eval は training に依存しない）。
- baseline は features-005（004+020）。本特徴は features-006。LightGBM/binary・009 win→joint を維持。
- 日本語規約維持。

## codex レビュー所見 (top risks folded)

新規 ML 特徴 spec として codex second opinion を実施（CLAUDE.md 方針）。反映:
- **正規化 (P0)**: 主＝レース内相対化、条件別 z-score は as-of 基準のみ・少数条件フォールバック（FR-006a）。強メンバー戦の相対不利は着差併用で緩和（FR-006b）。
- **リーク (P0)**: 正規化済み過去走 row を先構築→ as-of 集計、leak-guard を同走馬・同日・未来基準まで拡張（FR-002/002a）。loader 拡張箇所が最大の危険点。
- **corner/style (P1)**: MVP から外し別 ablation group（FR-012/Key Entities）。
- **市場織り込み (P1)**: 時計/上がりは最注目指標で市場に織り込み済みの公算が高く、「LogLoss 微改善・市場超過ゼロ」も十分あり得る → 023 は小さく進め、市場超過は主目的にしない（下記 deferred の次候補へ）。
- **採用ゲート (P0)**: strict majority + 条件別差分 + worst-fold LogLoss 上限（FR-011/011a）。

## Scope (Out / Deferred)

- furlong 毎の完全 sectional（DB には last_3f のみ、各ハロンのラップは未取得）
- 本格的なスピード指数（馬場差・展開・ペース補正の高度なモデリング）
- ranking objective・monotonic 制約・model family 変更・multi-output（win+place 直接）・pedigree 見直し・特徴量ストア化
- market 超過の本格追求（本 feature は診断まで）
- **次の候補シグナル（codex 推奨、別 feature）**: 条件替わり（前走不利・展開ミスマッチからの距離/馬場替わり）、距離短縮/延長 × 上がり性能の相互作用、トラック/開催日バイアスに逆らった好走 — 市場が過小評価しやすい相互作用系。023 が市場超過に届かなければ次の本命候補。
