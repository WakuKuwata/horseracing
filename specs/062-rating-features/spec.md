# Feature Specification: as-of レーティング特徴 (Elo / Bradley-Terry rating features)

**Feature Branch**: `062-rating-features`

**Created**: 2026-07-08

**Status**: Draft

**Input**: User description: "各馬に対戦結果から逐次更新する潜在能力レーティングを持たせ、レース前時点(as-of)のレーティングと派生量を新特徴群として追加、default モデル(lgbm-061)の win LogLoss を改善する。相手の質(誰に勝ったか)を既存勝率系は無視している。"

## 背景と位置づけ

- 既存の能力特徴(win_rate/place_rate/avg_finish 等)は「勝ったか/何着か」だけで、**対戦相手の質(強い相手に勝ったのか弱い相手に勝ったのか)**を織り込んでいない。
- 059 の within-race 相対化は「今走フィールド内」だけの相対で、キャリアを通じた対戦相手品質調整は未着手。
- レーティング(Elo/Bradley-Terry)は「対戦結果の連鎖から潜在能力を逐次推定」する古典的手法で、相手の質を自然に取り込む。競走馬でも実績のあるアプローチ。
- 061 の教訓: 「絶対軸・モデルが持たない軸」は pl_topk でも縮まず大きく効いた。レーティングは「相手品質で調整した能力」= 一部は既存能力と重複しうるが、対戦相手情報は新軸 → spike で重複度を実測する。
- データは DB 内既存のみ(finish_order/race_date/venue 等)。netkeiba 追加取得なし。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - レーティング特徴群の追加と事前登録ゲート評価 (Priority: P1)

オペレータが、対戦結果から逐次更新した as-of レーティング(レース開始時点で確定している値)の馬単位派生列を持つ features-017 を build し、フル walk-forward の事前登録ゲート(baseline=features-016)で採否を機械判定できる。

**Why this priority**: 本 feature の価値のすべて。ゲートを通らなければ採用しない(憲法 III)。

**Independent Test**: 実 DB で feature-eval(新群 drop=baseline)を実行し、win LogLoss 改善+ガード類の機械判定が出る。

**Acceptance Scenarios**:

1. **Given** 2007+ の実 DB, **When** features-017 を build, **Then** 新特徴列が全行に付与され(初出走は固定初期レーティング)、既存列はバイト不変
2. **Given** フル walk-forward feature-eval, **When** 実行, **Then** baseline(新群 drop)との比較で事前登録ゲートの合否が機械判定される
3. **Given** ゲート通過, **When** production 構成(pl_topk+TE+isotonic)で再学習(lgbm-062), **Then** 現 active(lgbm-061)比で全指標(win/top2/top3 LogLoss・ECE)非悪化を確認してから active 昇格をユーザーに諮る

---

### User Story 2 - 逐次状態の materialize 安全性 (Priority: P1)

レーティングは逐次更新される状態を持つため、per-row 独立な 061 以前の特徴より materialize 安全性の担保が厳しい。materialize 経路と in-memory 経路の出力が bit 一致し、未来レースの有無でレーティングが変わらない(pool-end 非依存)ことを保証する。

**Why this priority**: 逐次状態は本 feature 最大の技術リスク。ここが崩れると採用済みモデルの予測が非決定的になり、憲法 III/V に反する。US1 と同格の P1。

**Independent Test**: 同一 DB で複数回 build して bit 一致、未来レースを足しても過去行のレーティングが不変。

**Acceptance Scenarios**:

1. **Given** 同一データ, **When** materialize を 2 回実行, **Then** content_hash 一致(決定論)
2. **Given** 過去レースまでのデータ, **When** 未来レースを追加して再 build, **Then** 過去行のレーティング特徴は不変(pool-end 非依存)
3. **Given** in-memory build と materialized parquet, **When** レーティング列を比較, **Then** bit 一致

---

### User Story 3 - serving 互換の維持 (Priority: P2)

FEATURE_VERSION bump(features-016→017)後も、現 active lgbm-061(features-016)・candidate lgbm-058-acc / lgbm-060-mkt(features-015)の serving 予測がバイト不変で継続する。

**Why this priority**: 058/061 で確立した per-model feature_hash 互換の適用第3回。壊すと運用中の予測が停止する。

**Independent Test**: 実 DB E2E で lgbm-061 の予測が persisted 値とバイト一致・旧版モデルも compat-load。

**Acceptance Scenarios**:

1. **Given** features-017 registry, **When** lgbm-061(features-016)で予測, **Then** compat-path でロードされ予測値が従来とバイト一致
2. **Given** features-017 registry, **When** lgbm-058-acc / lgbm-060-mkt(features-015)で予測, **Then** compat-path でロード・予測成功

---

### Edge Cases

- 初出走馬(過去レースゼロ): 固定初期レーティング(NaN でなく事実としての初期値)。ただし「レーティング水準」と「信頼度(出走数)」を分離し、出走数 0 を明示
- 同日複数レース: 同日を跨いだ更新順序を厳密に定義(レース ID 昇順等の決定論規律)。今走の特徴には同日の他レース結果を混ぜない(023/026 同日除外と整合)。設計時に確定
- DNF・取消・失格: レーティング更新への算入方法(着順が付かない馬の扱い)を設計時に確定
- レーティングの初期不安定期(履歴が浅い時期の値のノイズ): 出走数の少ない馬のレーティングは信頼度列で明示
- 長期休養明け: 時間減衰(レーティングを初期値に引き戻す)を入れるかは設計判断(初版は入れない想定)

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 各レースの着順を多者間の対戦結果とみなし、対戦相手のレーティングを考慮して各馬のレーティングを逐次更新しなければならない(Elo 多者拡張 or Bradley-Terry/Plackett-Luce オンライン更新)。更新方式は plan で確定
- **FR-002**: レース R の特徴に入るレーティングは「R より前のレースで更新され R 開始時点で確定している値」でなければならない。R の結果でレーティングを更新してはならない(R の結果はラベルのみ)
- **FR-003**: 同日レースの更新順序を決定論的に定義し、今走の特徴に同日他レースの結果を混入させてはならない(同日除外規律)
- **FR-004**: 逐次更新は materialize 安全でなければならない: (a) 決定論(同一データで bit 一致)(b) pool-end 非依存(未来レース追加で過去行のレーティング不変)(c) in-memory/materialized 経路の bit 一致
- **FR-005**: 新特徴は既存ロード列のみから導出し(新ソース列を読まない)、source_fingerprint を不変に保つこと
- **FR-006**: FEATURE_VERSION を features-017 に bump し、既存列はバイト不変(additive)であること
- **FR-007**: serving 互換: features-016(lgbm-061)・features-015(lgbm-058-acc/lgbm-060-mkt)の compat-path ピン留めを追加し、既存モデルの予測バイト不変を実 DB で検証すること(058/061 同型)
- **FR-008**: リーク境界: grep 型 leak-guard(オッズ/配当トークン不使用)+ 挙動型テスト(今走結果を変えても今走レーティング特徴不変・過去結果を変えると変化・未来レース追加で不変)
- **FR-009**: 初出走馬は固定初期レーティング(0 埋めでない事実としての初期値)とし、レーティング水準と信頼度(出走数)を分離して欠損誤読を防ぐ
- **FR-010**: 採用ゲートは事前登録し変更禁止: フル walk-forward binary feature-eval(baseline=features-017 から新群 drop)で (a) win LogLoss 改善 (b) mean/worst-fold ECE ガード (c) strict majority + worst-fold LogLoss 上限(020/023/061 同型)。通過後 pl_topk 再学習 lgbm-062 全指標非悪化、active 昇格はユーザー判断
- **FR-011**: フル実装前に spike de-risk する: (1) 小規模既知データでレーティング計算の正しさ(期待レーティングへ収束)+ materialize 決定性 (2) binary + **pl_topk 両方**の少数 fold feature-eval(061 の「絶対軸は縮まない」に対し Elo は既存能力と重複しうるため pl_topk 確認必須)。no-go は中断・記録

### Key Entities

- **馬レーティング (horse rating)**: 対戦結果から逐次更新される潜在能力スカラー。各馬の時系列状態(レースごとに更新)。非永続・ビルド時に決定論再計算
- **as-of レーティング特徴群 (rating group)**: 馬単位の派生列(目安 3-6 列: レーティング水準・勢い(直近変化)・今走フィールド相対・出走数=信頼度)。features-017 の新 FEATURE_GROUPS。列セットは plan で確定
- **更新イベント (rating update)**: 1 レースの結果 → 出走各馬のレーティング差分。中間値(集約の入力)

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: フル walk-forward feature-eval で win LogLoss が baseline(features-016)から改善し、事前登録ガード全通過(参考: 直近採用 061 は −0.00054)
- **SC-002**: features-017 build で既存全列がバイト不変(共有列 check_exact+check_dtype 一致)
- **SC-003**: 逐次レーティングが materialize 安全(決定論・pool-end 非依存・in-memory/materialized bit 一致)
- **SC-004**: 既存 3 モデル(lgbm-061/058-acc/060-mkt)の serving 予測が features-017 registry 下でバイト不変(実 DB E2E mismatch 0)
- **SC-005**: 全パッケージテスト緑・migration/API/OpenAPI/スキーマ不変
- **SC-006**: レーティング計算が既知の小規模対戦データで期待どおり(強い馬のレーティングが高くなる)

## Assumptions

- 更新は総合レーティング(単一スカラー)から開始。条件別(距離/馬場/コース)レーティングは deferred
- 騎手/調教師レーティング・レーティングの不確実性(Glicko の RD)・当日オッズ統合はスコープ外
- 時間減衰(休養明けの初期値引き戻し)は初版では入れない(deferred 候補)
- レーティング更新のハイパーパラメータ(K 係数・初期値・スケール)は train 期間内の妥当な固定値とし、OOS で調整しない(選択リーク回避、017/035 前例)
- lgbm-062 の学習は既存 CLI(train-evaluate)で実施し、モデル切替・登録は 057/058 基盤を再利用
