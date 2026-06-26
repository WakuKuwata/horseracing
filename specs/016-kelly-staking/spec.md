# Feature Specification: Kelly 賭け金最適化と bankroll backtest

**Feature Branch**: `016-kelly-staking`

**Created**: 2026-06-26

**Status**: Draft

**Input**: User description: "Kelly 基準による賭け金最適化と bankroll backtest。Feature 011/012 の exotic EV 推奨を入力に、flat stake ではなく Kelly 基準で最適賭け金比率を算出し bankroll 比例の stake を推奨する。p≠q を厳守し、推定オッズ使用時は二重疑似。評価先行で flat と同一条件比較。"

## 概要

Feature 011/012 は exotic 券種の期待値 EV（買い目 c ごとの的中確率 P_model(c) × 使用オッズ O(c)）を計算し、**固定額（flat stake）**で買い目を推奨してきた。本 feature は「**いくら賭けるか**」を最適化する: Kelly 基準で各買い目の最適賭け金比率を算出し、bankroll（資金）に比例した stake を推奨する。さらに、Kelly stake が flat stake に対して**リスク調整後の資金成長**で優位かを期間 backtest で評価する（評価先行）。

これは betting チェーン（007 単勝 EV → 011 exotic EV → 012 実オッズ → 013 バイアス補正）の自然な締めであり、「期待値は出すが賭け金は固定」という片手落ちを解消する。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 実オッズ Kelly 推奨生成（Priority: P1）🎯 MVP

ユーザーは特定レースに対し、各買い目の Kelly 最適賭け金比率と、現在 bankroll に対する推奨 stake 額を取得したい。確率はモデル確率 P_model、オッズは**実 exotic オッズ（012）**を使う最も信頼できる経路を最優先とする。

**Why this priority**: Kelly 賭け金算出が本 feature の中核価値。実オッズ経路は推定の不確実性が無く、最も健全な MVP。

**Independent Test**: 既知の P_model と実オッズを持つレースで Kelly 推奨を生成し、(a) 各買い目の Kelly fraction が定義式どおり、(b) 負 edge の買い目が見送られる、(c) fractional Kelly（λ）と cap が適用される、(d) 同一(レース,券種)内の合計賭け金比率が bankroll を超えない、を検証できる。

**Acceptance Scenarios**:

1. **Given** あるレースに P_model と実 exotic オッズが揃った複数買い目がある, **When** Kelly 推奨を生成する, **Then** 各買い目に Kelly fraction（fractional Kelly λ・cap 適用後）と bankroll 比例 stake が付与され、recommendations に append-only 保存される。
2. **Given** ある買い目の edge = P_model·O − 1 ≤ 0, **When** Kelly 推奨を生成する, **Then** その買い目は見送られ（stake=0/不採用）、保存されない。
3. **Given** 同一(レース,券種)で採用買い目が複数ある, **When** Kelly 配分を計算する, **Then** 相互排他性（1 通りのみ的中）を考慮した配分が行われ、合計賭け金比率は設定上限を超えない。
4. **Given** Kelly 設定（λ・cap・合計上限・初期/現在 bankroll）, **When** 推奨を生成する, **Then** これらの前提が logic_version と保存値から再現可能である。

---

### User Story 2 - bankroll backtest（Kelly vs flat、採否ゲート）（Priority: P1）

ユーザーは期間を指定して、Kelly stake で資金を逐次更新した場合の bankroll 推移を、flat stake（011/012）と**同一条件**で比較し、Kelly を採用すべきか判断したい。

**Why this priority**: 憲法 III（評価先行）。Kelly は理論上 log 資金成長を最大化するが分散が大きく、実装の正しさ・λ 設定の妥当性は backtest でしか確かめられない。これが**採用ゲート**。

**Independent Test**: 結果が既知の期間で Kelly と flat の両方の bankroll 経路を生成し、終端 bankroll・対数成長率・最大DD・破産確率・分散・最大連敗を計測、同一条件（同じ買い目母集団・同じオッズ源・同じ期間）で比較できる。

**Acceptance Scenarios**:

1. **Given** 結果が確定した期間, **When** Kelly backtest を実行する, **Then** 終端 bankroll / 対数成長率 / 最大ドローダウン / 破産確率 / 分散 / 最大連敗が算出される。
2. **Given** 同一期間・同一買い目母集団, **When** Kelly と flat を比較する, **Then** 両者が同一条件で評価され、success 判定は「flat に対しリスク調整後成長で優位」（単なる ROI>1 ではない）で行われる。
3. **Given** 過去オッズの時系列性, **When** 破産確率を推定する, **Then** レース順序・時系列を保つ評価（walk-forward を主、リサンプリングは補助）で行い、順序を壊す単純シャッフルに依存しない。
4. **Given** 実 exotic オッズが無い区間, **When** backtest する, **Then** その区間は二重疑似 ROI として明示され、実オッズ区間と分離集計される。

---

### User Story 3 - 推定オッズ Kelly の二重疑似ラベルと安全抑制（Priority: P2）

ユーザーは実 exotic オッズが無い買い目について、推定オッズ（010）由来の Kelly 推奨を、**二重疑似**であると明示され、かつ推定誤差で過大賭けにならないよう安全に抑制された形で取得したい。

**Why this priority**: 推定オッズ上の Kelly は誤差に敏感（Kelly fraction の分母が O−1 のため低オッズで不安定化）。憲法 V の誤読防止と、実運用破産回避の両面で必須だが、実オッズ経路（US1）が動いた後の拡張。

**Independent Test**: 実オッズ欠損・推定オッズのみの買い目で、(a) 出力が二重疑似（double_pseudo=true, is_estimated_odds=true）と標識される、(b) 推定オッズ時は別の（より保守的な）λ・追加フィルタが適用され、生の Kelly より stake が抑制される、を検証できる。

**Acceptance Scenarios**:

1. **Given** ある買い目に実 exotic オッズが無く推定オッズ（010）のみ, **When** Kelly 推奨を生成する, **Then** is_estimated_odds=true・double_pseudo=true で保存され、推定オッズ使用が明示される。
2. **Given** 推定オッズ由来の買い目, **When** Kelly fraction を算出する, **Then** 実オッズ時より保守的な λ または上限が適用され、低オッズ・低 edge・推定不確実性の高い買い目はフィルタで除外または大幅抑制される。
3. **Given** backtest, **When** 推定オッズ区間と実オッズ区間が混在する, **Then** ラベルが分離され、二重疑似 ROI が実 ROI と混同されない。

---

### Edge Cases

- **同着（dead heat）**: 的中判定・払戻が分割される場合の bankroll 更新規則（按分）を定義する。
- **小頭数**: 複勝の頭数依存（009 の field-size 規則: 5–7=top2, 8+=top3, ≤4=なし）に従い、成立しない券種は母集団から除外。
- **推定不能（オッズ欠損）**: P_model か使用オッズの一方でも欠ければ canonical field から除外（011/012 と同一の単一経路）。
- **負 edge / ゼロ edge**: 見送り（stake=0、保存しない）。
- **O ≈ 1（極低オッズ）**: Kelly fraction の分母 O−1 が 0 に近づき不安定。最小オッズ閾値または上限 cap で保護する。
- **bankroll 枯渇**: backtest 中に bankroll が破産閾値を割った経路は ruin として記録し、以降の賭けを停止する規則を定義。
- **採用後の取消（scratch）**: 推奨生成後に出走取消が判明した買い目は void/skip（011/012 と同一規約）。
- **合計賭け金が bankroll 超過**: 正規化により合計賭け金比率を設定上限以内に収める。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: システムは各買い目 c の edge = P_model(c)·O(c) − 1 と Kelly fraction f*(c) = edge / (O(c) − 1) を算出 MUST。確率は必ず**モデル確率 P_model**（009 をモデル win 確率 p に適用）を用い、市場 vote share q を確率として用いてはならない（p≠q）。
- **FR-002**: システムは fractional Kelly（実効 fraction = λ·f*、λ は設定可能・既定 0.25）と、1 買い目あたり比率上限 cap、(レース,券種) 合計賭け金比率上限を適用 MUST。
- **FR-003**: edge ≤ 0（負・ゼロ edge）の買い目は見送り MUST（stake=0、recommendations に保存しない）。
- **FR-004**: 同一(レース,券種)内で複数買い目を採用する場合、相互排他性（1 通りのみ的中）を考慮した配分 MUST。簡易 heuristic（個別 Kelly + 合計正規化）を用いる場合は、その近似性と多項アウトカム Kelly 厳密解との差を backtest で計測・明示 MUST（採用方式を logic_version に記録）。
- **FR-005**: 使用オッズ O は**実 exotic オッズ（012）を最優先**とし、無い場合のみ推定オッズ（010）にフォールバック MUST。推定オッズ使用時は is_estimated_odds=true・double_pseudo=true で標識 MUST。
- **FR-006**: 推定オッズ由来の Kelly は、実オッズ時より保守的な λ または上限を適用し、低オッズ・低 edge・推定不確実性の高い買い目をフィルタ MUST（推定誤差による過大賭けの抑制）。
- **FR-007**: 賭け金決定はレース結果（着順）を一切参照してはならない MUST（リーク境界）。結果は backtest の採点にのみ使用する。
- **FR-008**: Kelly stake および Kelly fraction は決してモデルの特徴量・学習入力に戻してはならない MUST（リーク境界）。
- **FR-009**: 取消・除外・推定不能・成立しない券種は母集団から除外し、011/012 と同一の canonical field / selection 正規化の単一経路を通る MUST。
- **FR-010**: システムは Kelly 推奨を recommendations テーブルに append-only 保存 MUST。market_odds_used / estimated_market_odds_used / is_estimated_odds / double_pseudo / pseudo_odds / pseudo_roi は 011/012 の規約を踏襲する。
- **FR-011**: Kelly 設定（λ・cap・合計上限・初期 bankroll・bankroll 更新規則・odds_source・配分方式）と各買い目の採用 fraction は、後から再現・監査できる形で永続化 MUST（logic_version + 保存値）。
- **FR-012**: システムは期間指定の bankroll backtest を提供 MUST。Kelly stake で bankroll を逐次更新し、終端 bankroll・対数成長率・最大ドローダウン・破産確率・分散・最大連敗を算出する。
- **FR-013**: backtest は flat stake（011/012）と**同一条件**（同一買い目母集団・同一オッズ源・同一期間）で比較 MUST。success 判定は「flat に対しリスク調整後成長で優位」であり、単なる回収率 ROI>1 を success としてはならない。
- **FR-014**: 破産確率の推定はレース順序・時系列性を保つ評価（walk-forward を主、リサンプリングは補助）で行い、順序を壊す単純シャッフルに依存してはならない MUST。
- **FR-015**: 実 exotic オッズが無い backtest 区間は二重疑似 ROI として明示し、実オッズ区間と分離集計 MUST。
- **FR-016**: 同着は的中・払戻を規則に従い按分して bankroll 更新 MUST。bankroll 破産閾値割れの経路は ruin として記録し以降の賭けを停止する。
- **FR-017**: CLI でレース指定の Kelly 推奨生成と、期間指定の bankroll backtest を提供 MUST。日本語の規約・出力を維持する。
- **FR-018**: スキーマ変更は最小限とする。既存 recommendations での表現を優先し、kelly_fraction 等の新規列が必要な場合は憲法 VI の下で正当化 MUST。

### Key Entities *(include if feature involves data)*

- **Kelly 推奨（Kelly recommendation）**: 買い目（bet_type, selection）、P_model、使用オッズ O、odds_source（real/estimated）、edge、生 Kelly fraction f*、実効 fraction（λ・cap・配分後）、推奨 stake、is_estimated_odds、double_pseudo、logic_version、prediction_run_id、race_id。011 の recommendations を踏襲・拡張。
- **Kelly 設定（Kelly config）**: λ（実/推定別）、1 点上限 cap、合計上限、初期 bankroll、bankroll 更新規則、最小オッズ閾値、配分方式（多項 Kelly / heuristic）。logic_version に紐づき再現可能。
- **bankroll backtest 結果**: 期間、戦略（Kelly / flat）、終端 bankroll、対数成長率、最大DD、破産確率、分散、最大連敗、実/二重疑似区間の分離集計、baseline 比較と success 判定。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 実オッズと P_model が揃った任意のレースで、各採用買い目の Kelly fraction が定義式（fractional Kelly・cap・配分後）と一致し、負 edge が 100% 見送られる。
- **SC-002**: 同一(レース,券種)の採用買い目の合計賭け金比率が、いかなる入力でも設定した合計上限を超えない（0 件の上限超過）。
- **SC-003**: 同一レース・同一設定で 2 回 Kelly 推奨を生成したとき、出力（fraction・stake・採否）が完全一致する（決定論）。
- **SC-004**: 推定オッズのみの買い目が生成する Kelly 推奨は 100% が double_pseudo=true・is_estimated_odds=true で標識され、実オッズ由来と区別できる。
- **SC-005**: 推定オッズ由来の Kelly stake は、同一買い目で実オッズを仮に用いた場合より保守的（同等以下の実効 fraction）である。
- **SC-006**: 期間 backtest で Kelly と flat が同一条件で比較され、終端 bankroll・対数成長率・最大DD・破産確率・分散・最大連敗の 6 指標が両戦略について算出される。
- **SC-007**: backtest の success 判定がリスク調整後成長（例: 対数成長率と最大DD/破産確率の併記）に基づき、単なる ROI>1 で success としていない。
- **SC-008**: backtest で実オッズ区間と二重疑似（推定オッズ）区間が分離集計され、二重疑似 ROI が実 ROI と合算されない。
- **SC-009**: Kelly 設定（λ・cap・bankroll 前提・配分方式・odds_source）が保存値から完全に再現でき、過去の Kelly 推奨を後から監査できる。
- **SC-010**: オッズ・q・Kelly fraction・stake のいずれもモデルの特徴量・学習入力に出現しない（リーク境界のガード）。

## Assumptions

- **race_horses.odds の性質**: win オッズは終値寄り（013 と同様）。本 feature は retrospective 評価が目的で、実運用の pre-race オッズ運用は限定事項（013 と同じ開示）。
- **初期 bankroll**: backtest の初期資金は設定値（既定は単位資金 = 1.0 や 100 等、plan で確定）。stake は bankroll 比例。
- **flat baseline**: 011/012 の flat stake を baseline とし、同一買い目母集団・同一オッズ源で比較する。
- **配分方式**: MVP は単一券種内の相互排他性を考慮した配分。**券種間の相関を考慮した同時最適化は deferred**。
- **fractional Kelly λ**: 既定 0.25（quarter Kelly）。実/推定で別値を許す。
- **多項アウトカム Kelly**: 同一券種内の厳密な複数結果 Kelly を基本とするが、計算量・実装簡便性のため保守 heuristic を許容し、その近似誤差を backtest で開示する。
- **deferred**: 多変量同時 Kelly の厳密最適化（券種横断）、券種間相関、実資金運用（pre-race オッズ・最小賭け単位・控除後実配当の厳密化）、Kelly のモデル過信補正（P_model の校正/shrink/edge haircut は将来 feature）。
- **依存**: betting を拡張し probability（009/010）と 011/012 の canonical field / to_selection / recommendations 規約に依存。スキーマは原則無改変、必要時のみ最小追加。
