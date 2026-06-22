# Feature Specification: 評価ハーネスと baseline (Evaluation Harness & Baseline)

**Feature Branch**: `003-eval-harness`

**Created**: 2026-06-21

**Status**: Draft

**Input**: User description: "Evaluation Harness & Baseline — walk-forward 評価・予測品質指標・整合性検証・baseline"

## 概要

特徴量生成・学習より「先に」評価基盤を用意する (憲法 III 評価先行 NON-NEGOTIABLE)。walk-forward
分割、Predictor 抽象 (将来の LightGBM / 校正器が差さる)、baseline、予測品質指標、確率整合性検証を
提供し、将来モデルを baseline と同一条件で比較できるようにする。

スコープは「評価基盤 (分割 + Predictor 契約 + 指標計算 + 整合性検証 + baseline)」。特徴量生成・学習・
予測 serving・買い目選定は別フィーチャー。MVP ではスキーマ変更を行わず、baseline 評価結果は既存
`model_versions.metrics_summary` (jsonb) に保存する。

「利用者」は人間エンドユーザーではなく下流コンポーネント (将来の学習・採用判定) と、評価を実行する
オペレーター。

**最大リスク**: 評価専用に使う「結果確定時」の `odds` / `popularity` が、将来モデルの特徴量に混入する
こと。本フィーチャーは baseline・特徴量・serving の責務境界を明文化してこれを防ぐ。

データ前提 (Feature 001/002 で実在):

- `races.race_date`、`race_results` (`finish_order`, `result_status`)、`race_horses` (`odds`=結果確定時
  単勝, `popularity`=結果確定時, `entry_status`)。
- `labels.derive_labels` が finished のみの win/top2/top3 ラベルを返す (同着は `finish_order` 共有)。
- 2007 年実データ取込済み。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - walk-forward 評価で予測品質を測れる (Priority: P1) 🎯 MVP

評価ハーネスが、Predictor を walk-forward out-of-sample で評価し、予測品質指標を出す。

**Why this priority**: 憲法 III (評価先行) を成立させる中核。これが無ければモデルの採用是非を判定できず、
以降の特徴量・学習フィーチャーが前提を欠く。プロジェクトで初めて「予測の良し悪しを測れる」価値を提供。

**Independent Test**: 合成データ (既知の確率・着順) で各指標が期待値どおりに算出され、確率整合性違反が
fail-fast されることを検証する。

**Acceptance Scenarios**:

1. **Given** race_date を持つ評価対象データと Predictor、**When** walk-forward 評価を実行する、
   **Then** train が valid より時系列で前に来る固定分割が適用され、未来データが過去評価に混入しない。
2. **Given** Predictor の出力確率、**When** 評価する、**Then** LogLoss / Brier / AUC / NDCG / ECE が
   label (win/top2/top3) 別に算出される。
3. **Given** `0<=win<=top2<=top3<=1` またはレース内合計 (≈1/2/3) を逸脱する確率、**When** 評価に渡す、
   **Then** 許容誤差を超える違反は fail-fast で検出され、黙って通らない。
4. **Given** 取消・除外を含むレース、**When** 評価母集団を作る、**Then** 取消・除外は除外され残り出走馬で
   再正規化され、非完走 (stopped/disqualified) はラベル母集団に含まれない。
5. **Given** 同一入力・同一分割、**When** 評価を 2 回実行する、**Then** 結果が決定論的に一致する。

---

### User Story 2 - baseline を測って「超えるべきバー」を確立する (Priority: P1)

人気順 baseline と一様 baseline を Predictor として実装し、ハーネスで測定して比較基準を作る。

**Why this priority**: 憲法 III の採用条件は「baseline 比較」。比較対象が無ければ評価が成立しない。MVP は
US1+US2 で「予測品質を baseline と比較できる」状態が完成する。

**Independent Test**: 2007 取込データで両 baseline を walk-forward 評価し、指標が算出され、市場 baseline
が一様 baseline を予測品質で上回る (妥当性チェック) ことを検証する。

**Acceptance Scenarios**:

1. **Given** `race_horses.odds`、**When** 市場 baseline を作る、**Then** win はインプライド確率
   (1/odds の正規化) として、top2/top3 は順位分布 (Harville 等) から導出され、Predictor 契約を満たす。
   この baseline は「結果確定時値ゆえリークあり (参照線専用)」と明示される。
2. **Given** 出走頭数 N、**When** 一様 baseline を作る、**Then** win=1/N 等の leak-free な確率を返す。
3. **Given** baseline の評価結果、**When** 保存する、**Then** `model_versions` (例: `model_family='baseline'`)
   の `metrics_summary` に格納され、後から将来モデルと同一評価条件で比較できる。
4. **Given** 2007 データ、**When** 両 baseline を評価する、**Then** 市場 baseline が一様 baseline を予測品質
   (例: LogLoss) で上回る。

---

### User Story 3 - 運用品質 (疑似ROI等) を測れる (Priority: P2)

単勝の馬券シミュレーションで運用指標を出す。

**Why this priority**: 採用条件 (憲法 III) は baseline 比較 + ECE であり ROI を必須化していない。予測品質
(US1/US2) が成立した後に拡張できるため P2。

**Independent Test**: 合成オッズ・着順で ROI / 的中率 / 最大DD が期待値どおりに算出されることを検証する。

**Acceptance Scenarios**:

1. **Given** Predictor 確率と結果確定時単勝オッズと購入ルール、**When** 単勝シミュレーションを実行する、
   **Then** 疑似ROI / 回収率 / 的中率 / 見送り率 / 最大ドローダウン / 最大連敗数が算出される。
2. **Given** 過去評価、**When** 運用指標を提示する、**Then** 結果確定時オッズによる「疑似評価」である旨が
   明示される。

---

### User Story 4 - 評価結果を永続化し比較できる (Priority: P2)

fold 別・全体の評価結果を保存し、モデル間比較レポートを出せる。

**Why this priority**: MVP の `metrics_summary` では fold 詳細の検索・比較が弱い。比較 UI / 多モデル運用が
必要になってから正規化すればよいため P2。

**Independent Test**: 2 つの Predictor を評価し、同一条件での指標差分が比較レポートで確認できることを
検証する。

**Acceptance Scenarios**:

1. **Given** 複数 Predictor の評価、**When** 保存・比較する、**Then** fold 別 (`walkforward_window_results`
   相当) と全体の指標が保存され、同一条件での差分レポートが出る (非破壊スキーマ拡張)。

---

### Edge Cases

- 少頭数レース (確率水準・ECE binning への影響)。
- 同着 (`derive_labels` に従い複数勝ち馬)。
- 取消・除外による母集団除外・再正規化。
- 全馬が非完走のレース (評価から除外)。
- valid 窓にデータが無い期間 (空 fold)。
- Predictor が整合性違反の確率を返した場合 (fail-fast)。
- `odds` 欠損・0 の馬 (市場 baseline での扱い)。

## Requirements *(mandatory)*

### Functional Requirements

**評価ハーネス・分割 (US1)**

- **FR-001**: システムは race_date 基準の固定 walk-forward 分割を提供しなければならない。スキームは
  **expanding-window train + 年次 valid**: train = valid 窓より前の全レース、valid を 1 年ずつ進める。
  2007 は初期 train 専用とし評価開始は 2008 年から。train が valid より時系列で前になり、未来データが
  過去評価に混入しないこと、ランダム分割を禁止することを保証する。窓幅・ステップの厳密定義は research
  で明記する。
- **FR-002**: システムは Predictor 抽象を定義しなければならない。Predictor はレース単位で全頭の
  win/top2/top3 確率を返す最小契約とし、将来の学習済みモデル・校正器・baseline が同一契約を満たす。
- **FR-003**: システムは予測品質指標 (LogLoss, Brier, AUC, NDCG, ECE) を label (win/top2/top3) 別に算出
  しなければならない。
- **FR-004**: システムは ECE を label 別に binning し、bin 数を設定可能とし、非完走・非出走を除外し、
  頭数別の診断も算出しなければならない。
- **FR-005**: システムは評価母集団から取消・除外 (`entry_status`=cancelled/excluded) を除外し、残り出走馬で
  再正規化しなければならない。ラベルは `labels.derive_labels` (finished のみ、同着は finish_order 共有) に
  従う。
- **FR-006**: システムは確率整合性を検証しなければならない: 各馬 `0<=win<=top2<=top3<=1`、レース内合計が
  `Σwin≈1`/`Σtop2≈2`/`Σtop3≈3` を許容誤差内に収まること。許容誤差は **label 別の設定可能な絶対誤差**と
  し、既定値は `|Σwin-1|≤0.05` / `|Σtop2-2|≤0.10` / `|Σtop3-3|≤0.15`。これを超える違反は fail-fast で
  検出し黙って通さない。
- **FR-007**: システムは評価を決定論的にしなければならない (同一入力・同一分割で同一結果)。
- **FR-008**: システムは評価対象を 2007 年以降に限定しなければならない (`is_in_ingest_scope` の境界と整合)。

**baseline (US2)**

- **FR-009**: システムは市場 baseline (人気順) を Predictor として提供しなければならない。win は
  `race_horses.odds` のインプライド確率 (1/odds の正規化)、top2/top3 は順位分布から導出する。`odds` が
  null/0 の馬は母集団最小の微小ウェイトを割り当ててから正規化する (確率 0 を避ける。Edge Cases 参照)。
- **FR-010**: システムは一様 baseline を Predictor として提供しなければならない (win=1/N 等の leak-free)。
- **FR-011**: システムは市場 baseline が「結果確定時値ゆえリークあり (参照線専用、モデル特徴量に使わない)」
  ことを明示しなければならない。
- **FR-012**: システムは baseline の評価結果を `model_versions.metrics_summary` に保存し、将来モデルと同一
  評価条件で比較できるようにしなければならない。MVP ではスキーマを変更しない。

**責務境界・provenance**

- **FR-013**: システムは baseline・特徴量・serving の責務境界を明文化し、結果確定時 `odds`/`popularity` が
  モデル特徴量に混入しないことを保証しなければならない (provenance 記載)。

**運用品質 (US3, P2)**

- **FR-014**: システムは単勝の馬券シミュレーションで疑似ROI / 回収率 / 的中率 / 見送り率 / 最大ドロー
  ダウン / 最大連敗数を算出できなければならない。結果確定時オッズによる「疑似評価」と明示する。連系・推定
  オッズは買い目フィーチャーへ deferred。

**永続化・比較 (US4, P2)**

- **FR-015**: システムは fold 別・全体の評価結果を保存し、複数 Predictor の同一条件比較レポートを出せ
  なければならない (必要なら `eval_runs` / `walkforward_window_results` 相当の非破壊スキーマ拡張)。

### Key Entities *(include if feature involves data)*

新規エンティティは MVP では作らない (P2 で検討)。論理対象:

- **Predictor (logical)**: レース単位で全頭の win/top2/top3 を返す契約。baseline / 将来モデルが実装。
- **walk-forward fold (logical)**: train 期間と valid 期間の組。race_date で区切る。
- **評価結果 (model_versions.metrics_summary)**: label 別の予測品質指標 + (P2) 運用指標を jsonb で保持。
- **(P2) eval_runs / walkforward_window_results**: fold 別結果の正規化保存。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 合成データで LogLoss / Brier / AUC / NDCG / ECE が既知の期待値と一致する (許容誤差内)。
- **SC-002**: 確率整合性違反 (範囲外・レース内合計逸脱) が fail-fast で 100% 検出される。
- **SC-003**: 2007 データで人気順 baseline と一様 baseline を walk-forward 評価でき、label 別指標が算出
  される。
- **SC-004**: 市場 baseline が一様 baseline を予測品質で上回る (win の LogLoss が厳密に小さい:
  `LogLoss(market) < LogLoss(uniform)`)。
- **SC-005**: baseline 評価結果が `model_versions.metrics_summary` に保存され、後から参照できる。
- **SC-006**: 同一入力・同一分割で評価が決定論的に再現する (2 回実行で完全一致)。

## Assumptions

- Feature 001 (スキーマ・`labels`・`model_versions`・`validation`) と Feature 002 (取込済み実データ) に
  依存する。
- 実装は新パッケージ (評価ハーネス、`horseracing-db` にパス依存) を想定。具体は plan で確定。
- 人気順→確率の変換方法 (インプライド確率 + Harville 等)、walk-forward 窓スキーム、確率合計の許容誤差は
  research/plan で確定する (上記 NEEDS CLARIFICATION 参照)。
- 運用指標 (US3) と永続化テーブル (US4) は P2。MVP (US1+US2) はスキーマ変更なし。SC-001〜006 は MVP
  (US1+US2) を対象とし、US3/US4 (P2) は意図的に SC を持たない (受け入れは各ストーリーの Independent Test
  と FR で担保)。
- テストは合成データ中心。実データ評価はローカルスモーク。
- 評価は手動実行 (オペレーター起動)。定期評価・自動採用判定は将来スコープ。
