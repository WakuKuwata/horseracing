# Feature Specification: 特徴量生成 (Feature Engineering)

**Feature Branch**: `004-feature-engineering`

**Created**: 2026-06-22

**Status**: Draft

**Input**: User description: "Feature Engineering — リーク安全な固定スキーマ feature matrix の生成"

## 概要

リーク安全な特徴量生成。固定スキーマの feature matrix を出力し、将来の学習 (Feature 005) と評価ハーネス
(Feature 003) が消費する。学習 (LightGBM)・校正・予測 serving は別フィーチャー。

スコープは「特徴量の計算 + メタデータ宣言 + リーク安全性の機構と検証」。MVP ではスキーマ変更せず
on-the-fly 計算とする (`feature_snapshots` は予測時点の監査用であり feature store の代替ではない)。

**最大リスク** (codex/憲法 II と一致): ① `race_date` 境界や同日情報による未来混入、② target encoding /
校正器の fold 漏れ、③新馬・取消・非完走を 0 埋めして意味を壊すこと。本フィーチャーはこれらを機構と
必須テストで防ぐ。

データ前提 (Feature 001/002 で実在):

- `races` (race_date 等)、`race_horses` (frame/horse_number/age/sex/weight/weight_diff/running_style/
  jockey_weight、odds/popularity=結果確定時)、`race_results` (finish_order/result_status/last_3f 等)、
  `horses` (sire/dam/damsire 名)、`jockeys`、`trainers`。
- `labels.derive_labels` (finished のみ)。2007 以降取込済み。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - リーク安全な過去成績特徴量を固定スキーマで生成できる (Priority: P1) 🎯 MVP

対象レースの各出走馬について、発走前静的特徴量 + 過去成績累積特徴量を、対象レースより前の日のデータ
のみで計算した固定スキーマの feature matrix を出力する。

**Why this priority**: 憲法 II (リーク防止) の最低条件を満たす中核。リーク安全な特徴量が無ければ学習
(005) は出発点を欠く。プロジェクトで初めて「モデルに渡せる特徴量」を提供する。

**Independent Test**: 合成データで、レース R の過去成績特徴量が `race_date >= R` の race を 1 件も
使わないこと (リーク検査)、新馬の過去成績が null で 0 でないこと、完走前提特徴量が非完走を除外する
ことを検証する。

**Acceptance Scenarios**:

1. **Given** 複数日のレース履歴を持つ馬、**When** レース R の feature matrix を生成する、**Then** 過去
   成績特徴量は `race_date < R` の race_results のみから計算され、同日・未来のレースを使わない。
2. **Given** 新馬 (過去出走 0)、**When** 特徴量を生成する、**Then** 過去成績系は null (Unknown) で、
   `is_debut=true` / `has_past_race=false` / `past_race_count=0` が設定され、0 と区別される。
3. **Given** 過去に取消・除外・競走中止を含む馬、**When** 完走前提特徴量 (avg_finish, prev_last3f 等)
   を計算する、**Then** 非出走 (取消/除外) と非完走 (中止/失格) は除外され、これらは別系統の履歴件数
   特徴量 (取消回数等) として 0 埋めせず保持される。
4. **Given** 同一の入力と as-of、**When** 特徴量を 2 回生成する、**Then** 結果が決定論的に一致する。

---

### User Story 2 - 特徴量メタデータを宣言・強制できる (Priority: P1)

全特徴量が source・availability_timing・missing_policy を宣言し、未宣言列があれば fail-fast する
FeatureRegistry を持つ。

**Why this priority**: 憲法 II は「全特徴量に source・利用可能タイミング・欠損処理を必須記載」を要求。
メタデータ強制が無いとリーク防止が宣言倒れになる。MVP は US1+US2 で「リーク安全 + メタデータ宣言済み」
の feature matrix が完成する。

**Independent Test**: feature matrix の全列が registry に登録され metadata を持つこと、結果後タイミング
や結果確定オッズが混入したら検出されることを検証する。

**Acceptance Scenarios**:

1. **Given** feature matrix の全列、**When** registry と照合する、**Then** 各列が source・
   availability_timing (出馬表前/枠順後/馬体重後/オッズ後/直前/結果後)・missing_policy を宣言している。
2. **Given** registry 未登録の列、**When** matrix を検証する、**Then** fail-fast で検出される。
3. **Given** `availability_timing='結果後'` の特徴量、**When** モデル入力候補を構成する、**Then** 機械的に
   除外される。
4. **Given** 結果確定時 odds/popularity を特徴量に混入させた場合、**When** 検証する、**Then** 検出される
   (モデル特徴量集合に含めてはならない)。

---

### User Story 3 - カテゴリ target encoding を train-only / out-of-fold で計算できる (Priority: P2)

騎手・調教師・開催場などの target encoding (勝率等) を、train fold のみ (または out-of-fold) で計算し、
valid/test に漏らさない。

**Why this priority**: target encoding は目的変数集約のため fold 漏れが起きやすい (最大リスク②)。ただし
MVP の静的・過去成績特徴量が成立した後に追加できるため P2。

**Independent Test**: encoding が valid 期間の結果を使わないこと、未知カテゴリの扱いが定義されることを
検証する。

**Acceptance Scenarios**:

1. **Given** walk-forward の train 境界 (日付)、**When** target encoding を fit する、**Then** 境界より前
   のデータのみを使い、valid 期間の結果を使わない。
2. **Given** train に存在しないカテゴリ、**When** valid に適用する、**Then** 未知カテゴリの既定値 (全体
   平均等) で扱われ、エラーや 0 埋めにしない。

---

### User Story 4 - feature matrix を materialize し効率化できる (Priority: P2)

date range / fold 単位で feature matrix を計算・キャッシュ (materialize) し、学習が高速に消費できる。

**Why this priority**: 学習の反復を高速化するが、正しさには不要。on-the-fly が成立した後の最適化のため P2。

**Independent Test**: materialize した matrix が on-the-fly と一致し、再現的であることを検証する。

**Acceptance Scenarios**:

1. **Given** date range、**When** materialize して再読込する、**Then** on-the-fly 計算と完全一致する
   (非破壊スキーマ拡張 or キャッシュ)。

---

### Edge Cases

- 新馬 (過去出走 0)。
- 低履歴 (1〜2 走)。
- 取消・除外 (非出走、実出走に含めない)。
- 競走中止・失格 (出走だが完走前提系から除外)。
- 同着 (`derive_labels` に従う)。
- 馬体重・枠順未確定の予測時点 (タイミング別特徴群)。
- 同一馬の初出走・年跨ぎ履歴。
- 取消明けの `days_since_last` (実出走基準で計算)。
- 同日に複数レース履歴がある場合 (前日までの cutoff で同日を除外)。

## Requirements *(mandatory)*

### Functional Requirements

**過去成績・固定スキーマ (US1)**

- **FR-001**: システムは各出走馬の過去成績累積特徴量を、対象レース R の `race_date` より厳密に前の日の
  `race_results` のみ (as-of cutoff) から計算しなければならない。同日・未来のレースを使ってはならない。
- **FR-002**: システムは発走前静的特徴量 (レース条件: 開催場/距離/芝ダ/馬場/天候/クラス/レース番号、馬
  属性: 年齢/性/枠/馬番、騎手/調教師 ID、馬体重/増減) を提供しなければならない。
- **FR-003**: システムは過去成績累積特徴量 (career_starts, days_since_last, prev_finish, prev_last3f,
  avg_finish, win_rate) を完走前提系では非完走・非出走を除外して計算しなければならない。
- **FR-004**: システムは取消/除外/中止の履歴を別系統の件数・率特徴量 (取消回数・除外回数・中止回数・前走
  が取消/除外/中止か) として保持し、これらを 0 埋めしてはならない。
- **FR-005**: システムは固定スキーマ (全馬同じ列) を維持し、過去成績が無い馬は null (Unknown) で渡し、
  0 と区別しなければならない。
- **FR-006**: システムは利用可否フラグ (`has_past_race`, `past_race_count`, `is_debut`, `is_low_history`)
  を提供しなければならない。`is_low_history` は実出走 (完走 or 中止 = 出走) 1〜2 走を true とし、閾値は
  設定可能とする (既定: 上限 2 走)。新馬 (0 走) は `is_debut` で別扱い (docs/modeling.md と整合)。
- **FR-007**: システムは特徴量計算を決定論的にしなければならない (同一入力・同一 as-of で同一出力)。
- **FR-008**: システムは評価対象を 2007 年以降に限定し、出力は Feature 003 の Predictor / 将来の学習が
  消費できる固定スキーマでなければならない。

**メタデータ・リーク境界 (US2)**

- **FR-009**: システムは全特徴量に source・availability_timing (出馬表前/枠順後/馬体重後/オッズ後/直前/
  結果後)・missing_policy のメタデータを宣言する FeatureRegistry を持たなければならない。
- **FR-010**: システムは feature matrix の列が registry に未登録、または metadata 未宣言の場合に
  fail-fast で検出しなければならない。
- **FR-011**: システムは `availability_timing='結果後'` の特徴量をモデル入力候補から機械的に除外できな
  ければならない。
- **FR-012**: システムは結果確定時 `odds`/`popularity` をモデル特徴量集合に含めてはならない。混入を検出
  できなければならない。

**target encoding (US3, P2)**

- **FR-013**: システムはカテゴリ target encoding を train 境界 (日付) より前のデータのみで fit し、
  valid/test の結果を使ってはならない。未知カテゴリは既定値 (全体平均等) で扱い、0 埋め・エラーにしては
  ならない。

**materialize (US4, P2)**

- **FR-014**: システムは feature matrix を materialize でき、materialize 結果が on-the-fly 計算と一致し、
  再現的でなければならない。

### Key Entities *(include if feature involves data)*

新規テーブルは MVP では作らない。論理対象:

- **FeatureRow (logical)**: race-horse 1 行の固定スキーマ特徴ベクトル (発走前静的 + 過去成績累積 +
  履歴件数 + フラグ)。
- **FeatureRegistry (logical)**: 特徴量名 → (source, availability_timing, missing_policy)。
- **FeatureMatrix (logical)**: 複数 race-horse の FeatureRow 集合。Predictor / 学習が消費。
- **as-of cutoff (logical)**: 対象レースの `race_date` 未満を強制する時点境界。
- **(P2) target encoding map**: train 境界以前で計算したカテゴリ→値。
- **(P2) materialized feature store**: date range / fold 単位のキャッシュ。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: リーク検査 — レース R の過去成績特徴量が `race_date >= R` の race を 1 件も使わない
  (合成データで 100% 検証)。
- **SC-002**: 新馬の過去成績系が null (0 でない) で、`is_debut` / `has_past_race` / `past_race_count` が
  正しい。
- **SC-003**: 完走前提特徴量が非完走 (中止/失格)・非出走 (取消/除外) を除外して計算される。履歴件数特徴量
  は 0 埋めされない。
- **SC-004**: feature matrix の全列が registry に metadata を持ち、未宣言・結果後混入・結果確定オッズ混入
  が fail-fast で検出される。
- **SC-005**: 同一入力・同一 as-of で特徴量が決定論的に再現する (2 回生成で完全一致)。
- **SC-006**: (P2) target encoding が valid 期間の結果を使わない (train 境界より前のみで fit)。

## Assumptions

- Feature 001 (スキーマ・`labels`・`validation`)、Feature 002 (取込済み実データ)、Feature 003
  (Predictor 契約・walk-forward 境界) に依存する。
- 実装は新パッケージ (特徴量、`horseracing-db` にパス依存) を想定。pandas + numpy を特徴量計算に使う
  (as-of cutoff は SQL で固定し、pandas は集計に使う。sort/groupby/shift のリーク検査を必須化)。具体は
  plan で確定。
- 過去成績の as-of 粒度は「対象レースの前日まで (`race_date < R`、同日除外)」を既定とする (保守側、同日
  レース結果の混入を構造的に防止)。
- 学習・校正・予測 serving は Feature 005 以降。特徴量の「価値 (baseline 超え)」検証は学習フィーチャーへ
  委ねる (本フィーチャーは正しさ・リーク安全・欠損・メタデータまで)。
- materialize テーブル (US4) とカテゴリ encoding (US3) は P2。MVP (US1+US2) はスキーマ変更なし・
  on-the-fly。
- テストは合成データ中心。実データはローカルスモーク。
- 結果確定時 `odds`/`popularity` はモデル特徴量に使わない (評価専用、Feature 003 と一致)。
