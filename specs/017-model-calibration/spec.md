# Feature Specification: モデル確率校正と edge haircut による Kelly 過大賭け抑制

**Feature Branch**: `017-model-calibration`

**Created**: 2026-06-26

**Status**: Draft

**Input**: User description: "モデルの win 確率 p を Kelly 投入前に校正し edge haircut で保守化する。013 が市場 q を校正したのに対し本 feature はモデル p 側を校正（p≠q 維持）。校正品質を採用ゲート、Kelly リスク低減を diagnostic とする eval-first。"

## 概要

Feature 016 の Kelly 賭け金は `f*=edge/(O−1)`（edge=P_model·O−1）で、**確率誤差を増幅する**。016 の実データ評価でモデルが exotic 推定オッズ上で利益を出せず、codex は「P_model 過信 → 過大賭け」を最大リスクと指摘した。本 feature は、Kelly に渡す前に**モデルの win 確率 p を校正**（過信補正）し、さらに**edge haircut**で残差リスクに備えて保守化する。

Feature 013 が**市場 q**を realized 結果に対して校正したのに対し、本 feature は**モデル p**を校正する。両者は対象が異なり別系統（p≠q を厳守、p の校正結果を市場オッズ推定側に戻さない）。

校正は marginal win 確率に対して行い、009 結合確率エンジンに通して全券種の校正済み P_model' を得る。**ただし marginal の校正が exotic joint の校正を保証しない**（009 の PL/Harville は非線形）ため、009 後の券種別 reliability 非悪化を採用条件に含める（codex 指摘）。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - モデル確率校正器の学習と評価（Priority: P1）🎯 MVP

ユーザーは期間を指定して、モデルの win 確率 p を realized 1 着結果に対して校正する校正器を学習し、生 p と校正後 p' の校正品質（NLL/Brier/ECE/reliability、人気帯別 over/under）を比較して、校正を採用すべきか判断したい。

**Why this priority**: 校正品質の改善が本 feature の中核価値であり、憲法 III の採用ゲート。Kelly 適用（US2）の前提。

**Independent Test**: walk-forward で対象レース前のみで校正器を学習し、out-of-sample の win 結果に対して 生 p / 校正 p' の NLL・Brier・ECE・reliability を算出・比較。校正器が対象レース結果を読まないこと、方式・ハイパラ選択が学習窓内に閉じることを検証できる。

**Acceptance Scenarios**:

1. **Given** ある期間の race_predictions（モデル p）と確定結果, **When** walk-forward で校正器を学習・評価する, **Then** 生 p と校正 p' の NLL/Brier/ECE/reliability（全体 + 人気帯別 over/under）が算出され、採用判定が「NLL/Brier 改善を主・ECE/reliability を補助」で行われる。
2. **Given** 校正器の学習, **When** 方式（temperature/isotonic/beta 等）とハイパラを選ぶ, **Then** 選択は各 fold の学習窓内のデータのみで行われ（選択リーク無し）、評価窓の結果を見ない。
3. **Given** 学習窓のサンプルが基準（最小レース数・最小勝ち数・人気帯別最小数）未満, **When** 校正器を学習する, **Then** 保守的方式（temperature のみ、または identity フォールバック）に切り替わり、過学習を避ける。
4. **Given** 同着レース, **When** 校正学習する, **Then** 同着は教師から除外され、除外件数が surface される。

---

### User Story 2 - 校正 + haircut 適用 Kelly と過大賭け低減の検証（Priority: P1）

ユーザーは、校正済み P_model' と edge haircut を Feature 016 の Kelly 推奨・bankroll backtest に適用し、生 Kelly と同一条件で比較して、過大賭け（最大DD・破産確率・分散）が下がりリスク調整後成長が維持/改善されるかを確認したい。

**Why this priority**: 校正の運用価値（Kelly の安全化）を示す diagnostic。016 を de-risk する本 feature の目的。

**Independent Test**: 同一期間・同一買い目母集団で「生 Kelly」「校正のみ」「校正+haircut」を比較し、Kelly リスク指標（最大DD/破産確率/分散）と成長（対数成長率）を算出。校正が良くても Kelly が悪化する逆転ケースを検出できる。

**Acceptance Scenarios**:

1. **Given** 校正器と haircut 設定, **When** Kelly 推奨/backtest に opt-in で適用する, **Then** 生 p 経路は後方互換で維持され、校正方式・haircut 値・校正窓・選択方式が logic_version に記録される。
2. **Given** bankroll backtest, **When** 生 Kelly と校正+haircut Kelly を同一条件比較する, **Then** 過大賭け低減（最大DD・破産確率・分散の低下）とリスク調整後成長が算出され、success は「校正改善 かつ Kelly リスク非悪化（成長維持で破産/DD 低下）」で判定される。
3. **Given** 校正は改善したが Kelly が悪化する逆転ケース, **When** 評価する, **Then** その事実が明示され、採用は Kelly リスク非悪化を必須ガードとして判断される。
4. **Given** 校正と haircut の独立 on/off, **When** 比較する, **Then** 二重保守（過小賭けで成長を過度に削る）が検出され、役割（校正＝系統的過信、haircut＝残差/モデルリスク）が分離して評価される。

---

### User Story 3 - p≠q 両側校正の整合（2×2 評価）（Priority: P2）

ユーザーは、モデル p 校正（本 feature）と市場 q 校正（013）を併用したとき、二重補正で edge 分布が縮みすぎないかを 2×2（raw/cal p × raw/cal q）で確認したい。

**Why this priority**: 013 と本 feature を同時運用する際の整合性。両側が同じ realized 結果を教師にするため二重吸収の懸念がある（codex 指摘）。

**Independent Test**: raw p/raw q, cal p/raw q, raw p/cal q, cal p/cal q の 4 通りで EV・edge 分布・Kelly リスクを比較し、p 校正結果が市場オッズ推定側に戻らないこと（順序: q 校正で O_est 確定 → p 校正 P_model' と結合）を検証できる。

**Acceptance Scenarios**:

1. **Given** p 校正器（本）と q 校正器（013）, **When** 2×2 で評価する, **Then** 4 通りの EV・edge 分布・Kelly リスクが算出され、二重補正で edge が過度に縮む場合が検出される。
2. **Given** 両側校正の順序, **When** EV を計算する, **Then** q 校正で O_est を確定した後に p 校正 P_model' と結合し、p 校正結果は market odds 推定側に戻さない（p≠q 境界）。

---

### Edge Cases

- **小頭数・サンプル不足**: 校正窓が基準未満 → identity/temperature フォールバック。人気帯別サンプル不足の帯は校正を弱める。
- **同着（dead heat）**: 校正学習の教師から除外、件数 surface。
- **isotonic の段差/単調性**: 人気薄帯で段差・ranking 破壊が出る場合、ranking 影響検査を通らなければ採用しない。
- **校正で joint 悪化**: marginal は改善だが 009 後の券種別 reliability が悪化 → 採用しない（diagnostic で検出）。
- **過剰保守**: 校正 + 固定 haircut で正 edge を削りすぎ成長低下 → 検出して haircut を弱める。
- **取消・除外**: canonical field から除外し p' を再正規化（009/016 と同一経路）。
- **校正不能（p 欠損）**: 当該馬を母集団から除外。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: システムは race_predictions のモデル win 確率 p を realized 1 着結果に対して校正する校正器を学習 MUST。方式（temperature scaling / isotonic / beta 等）は eval で比較・選択する。
- **FR-002**: 校正器は train-only / walk-forward で対象レース開始より厳密に前のデータのみで学習 MUST（race_id tie-break、date-level の <= は禁止）。校正器は対象レースの結果を読んではならない。
- **FR-003**: 校正方式・ハイパラの選択は各 fold の学習窓内のデータのみで行う MUST（選択リーク禁止）。
- **FR-004**: 校正済み p' は canonical field 上でレース内再正規化し、009 エンジンが受け取るベクトルと一致させる MUST（評価も同じ正規化形に対して行う）。
- **FR-005**: システムは校正済み p' を 009 結合確率エンジンに通して全券種の P_model' を導く MUST。**marginal 校正が joint 校正を保証しないため**、009 後の券種別（馬連/三連単等）reliability を測り、joint が悪化していないことを採用条件に含める MUST。
- **FR-006**: システムは edge haircut（edge_adj = (1−h)·edge もしくは edge−h、h 設定可能）を Kelly の f* 計算前に適用 MUST。校正と haircut は独立に on/off 可能とし、役割（校正＝系統的過信、haircut＝残差/モデルリスク）を分離する。
- **FR-007**: 学習窓のサンプルが基準（最小レース数・最小勝ち数・人気帯別最小数、設定可能）未満の場合、保守的方式（temperature のみ、または identity フォールバック）に切り替える MUST。
- **FR-008**: 同着は校正学習の教師から除外し、除外件数を surface する MUST。
- **FR-009**: 校正済み p'・haircut・調整後 edge・Kelly fraction はモデルの特徴量・学習入力に戻してはならない MUST（leak-guard test）。
- **FR-010**: システムは生 p と校正 p' の校正品質（NLL / Brier / ECE / reliability、全体 + 人気帯別 over/under）を算出 MUST。採用ゲートは「NLL/Brier 改善を主、ECE/reliability を補助」とする。overconfidence 指標（reliability slope、上位確率帯の over/under、calibration-in-the-large）を含める。
- **FR-011**: システムは Feature 016 の generate_kelly_recommendations / bankroll backtest と 009 エンジンに、p 校正器 + haircut を opt-in で渡せる MUST。生 p 経路は後方互換で維持する。
- **FR-012**: システムは bankroll backtest で「生 Kelly」「校正のみ」「校正 + haircut」を**同一条件**で比較し、Kelly リスク指標（最大DD・破産確率・分散）と対数成長率を算出 MUST。success は「校正改善 かつ Kelly リスク非悪化」で判定し、Kelly リスク非悪化を必須ガードとする。校正改善だが Kelly 悪化の逆転を明示する。
- **FR-013**: システムは p 校正（本）と q 校正（013）の併用を 2×2（raw/cal p × raw/cal q）で評価できる MUST。順序は q 校正で O_est を確定 → p 校正 P_model' と結合とし、p 校正結果を market odds 推定側に戻さない。
- **FR-014**: 校正方式・haircut 値・校正窓・選択方式・base model_version を logic_version に記録 MUST（憲法 V、stake = fraction × bankroll の再現に校正情報を追加）。スキーマ変更は行わない（013 同様、校正パラメータは logic_version に格納）。
- **FR-015**: CLI で校正器の学習・評価（期間指定）、校正適用の Kelly 推奨 / backtest を提供 MUST。日本語の規約・出力を維持する。

### Key Entities *(include if feature involves data)*

- **確率校正器（probability calibrator）**: 方式（temperature/isotonic/beta/identity）、学習パラメータ、学習窓（race_id 範囲）、選択方式、base model_version、フォールバック条件。logic_version に焼き込み再現可能。
- **校正評価レポート**: 生 p / 校正 p' の NLL・Brier・ECE・reliability（全体 + 人気帯別 over/under・reliability slope・calibration-in-the-large）、009 後の券種別 reliability、同着除外件数、採用判定。
- **Kelly 比較レポート（diagnostic）**: 生 Kelly / 校正のみ / 校正+haircut の最大DD・破産確率・分散・対数成長率、2×2（p×q 校正）の edge 分布、過剰保守・二重補正の検出、success 判定。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 任意の評価期間で、生 p と校正 p' の NLL・Brier・ECE・reliability が walk-forward out-of-sample で算出され、採用判定が NLL/Brier 改善を主基準として下せる。
- **SC-002**: 校正器は対象レースの結果を 100% 参照せず（leak-guard）、方式・ハイパラ選択も学習窓内に閉じる（選択リーク 0 件）。
- **SC-003**: 同一入力で 2 回校正・評価したとき、校正器・指標・採用判定が完全一致する（決定論）。
- **SC-004**: 校正 p' は 009 入力ベクトルと一致（レース内 Σ=1 の正規化形）し、評価もその形に対して行われる。
- **SC-005**: 009 後の券種別 reliability が測られ、marginal は改善だが joint が悪化するケースが検出される（採用条件に反映）。
- **SC-006**: bankroll backtest で「生 Kelly」「校正のみ」「校正+haircut」が同一条件比較され、各々の最大DD・破産確率・分散・対数成長率が算出される。
- **SC-007**: 校正+haircut が生 Kelly に対し過大賭けを下げる（最大DD・破産確率の低下）ことを示せ、かつ成長低下が過剰でない（過剰保守の検出）。
- **SC-008**: success 判定が「校正改善 かつ Kelly リスク非悪化」であり、校正改善だが Kelly 悪化の逆転ケースが明示される（単一指標で採用しない）。
- **SC-009**: p×q 校正の 2×2 評価で、二重補正による edge 過縮小が検出できる。
- **SC-010**: 校正方式・haircut・校正窓・base model_version が logic_version から再現でき、過去の校正適用 Kelly を後から監査できる。スキーマ変更は 0。

## Assumptions

- **校正対象**: 初期は marginal win 確率 p の校正（temperature/beta を本命、isotonic は ranking 影響検査を通った場合のみ）。joint（券種別）の直接校正は deferred。
- **教師信号**: realized 1 着（win）を二値教師とし、レース内 exactly-one を per-horse binary 校正 → レース内再正規化で扱う。
- **walk-forward**: 013 と同じく race_id tie-break で対象レース開始より厳密に前。race_horses.odds は closing-leaning（retrospective 限界、013 と同じ開示）だが、モデル p は pre-race 特徴由来で odds ほどの leak 非対称性は無い（校正窓設計で考慮）。
- **haircut 既定**: 校正で系統誤差を補正後、残差用に小さな haircut（既定 h は plan で確定、例 0〜0.1）。固定 haircut だけでなく不確実性連動も候補（deferred 寄り）。
- **採用ゲート**: NLL/Brier 改善を主、ECE/reliability を補助、009 後 joint reliability 非悪化と Kelly リスク非悪化を必須ガード。
- **両側校正順序**: q 校正（013）で O_est 確定 → p 校正 P_model' と結合。p 校正は market odds 推定に戻さない。
- **依存**: betting / probability を拡張。016（Kelly）・009（結合確率）・013（q 校正の対）に依存。スキーマ無改変（logic_version に校正情報）。
- **deferred**: 多出力モデルの直接校正、結合確率の直接校正（組合せ爆発）、オンライン/適応校正、開催場/距離/条件別の校正、不確実性連動 Kelly、p×q 同時校正の本格運用。
