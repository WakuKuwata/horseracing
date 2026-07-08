# Feature Specification: 本格スピード指数特徴 (speed figure features)

**Feature Branch**: `061-speed-figure-features`

**Created**: 2026-07-08

**Status**: Draft

**Input**: User description: "as-of 基準タイムに基づくスピード指数を過去走ごとに算出し、馬単位の as-of 集約を新特徴群として追加(023 から deferred)。features-015→016。"

## 背景と位置づけ

- 現行の時計系特徴(023)は**レース内相対**のみ: 過去走の走破タイム/上がり 3F を「そのレースの finisher 平均との差」で持つ。同レース内の比較には強いが、「**コース×距離×馬場条件の基準に対して絶対的にどれだけ速いか**」の軸が存在しない。レース全体が遅い(弱いメンバー構成の)レースで相対的に速くても、絶対水準は測れていない。
- 023 spec で「本格スピード指数」は明示的に deferred。060 完了後の非市場系レバーとして、精度改善提案(2026-07-08)の案2。
- 059(within-race 相対能力)とは直交: 059 は「今走フィールド内での相対」、本 feature は「コース条件の歴史的基準に対する絶対」。
- データは DB 内既存のみ(finish_time/distance/venue/track_type/going/carried_weight は全てロード済み)。netkeiba 追加取得なし。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - スピード指数特徴群の追加と事前登録ゲート評価 (Priority: P1)

オペレータが、as-of 基準タイム(コース条件別・対象レースより前のみで推定)に対する過去走スピード指数の馬単位集約を新特徴群として持つ features-016 を build し、フル walk-forward の事前登録ゲート(baseline=features-015)で採否を機械判定できる。

**Why this priority**: 本 feature の価値のすべて。ゲートを通らなければ採用しない(憲法 III)。

**Independent Test**: 実 DB で feature-eval(新群 drop=baseline)を実行し、win LogLoss 改善+ガード類の機械判定が出る。

**Acceptance Scenarios**:

1. **Given** 2007+ の実 DB, **When** features-016 を build, **Then** 新特徴列が全行に付与され(履歴不足・基準タイム標本不足のセルは NaN)、既存 121+α 列はバイト不変
2. **Given** フル walk-forward feature-eval, **When** 実行, **Then** baseline(新群 drop)との比較で事前登録ゲートの合否が機械判定される
3. **Given** ゲート通過, **When** production 構成(pl_topk+TE+isotonic)で再学習(lgbm-061), **Then** 現 active(lgbm-057)比で全指標(win/top2/top3 LogLoss・ECE)非悪化を確認してから active 昇格をユーザーに諮る

---

### User Story 2 - serving 互換の維持 (Priority: P2)

FEATURE_VERSION bump(features-015→016)後も、既存モデル — active lgbm-057(features-014)・candidate lgbm-058-acc / lgbm-060-mkt(features-015)— の serving 予測がバイト不変で継続する。

**Why this priority**: 058 で確立した per-model feature_hash 互換(compat-path)の適用第2回。壊すと運用中の予測が fail-closed で停止する。

**Independent Test**: 実 DB E2E で lgbm-057 の予測が persisted 値とバイト一致・features-015 系モデルも compat-load で予測可能。

**Acceptance Scenarios**:

1. **Given** features-016 registry, **When** lgbm-057(features-014)で予測, **Then** compat-path でロードされ予測値が従来とバイト一致
2. **Given** features-016 registry, **When** lgbm-058-acc / lgbm-060-mkt(features-015)で予測, **Then** compat-path(features-015 hash ピン留め)でロード・予測成功
3. **Given** 互換検証の破れ(列欠落等), **When** ロード, **Then** fail-closed(黙った縮退なし)

---

### Edge Cases

- 基準タイムの標本不足セル(コース×距離帯×馬場の組で strictly-before の finisher が min_samples 未満): 指数 NaN(0 埋め禁止、Unknown≠0)
- 過去走に finish_time が無い行(DNF・データ欠損): その過去走は指数計算から除外(集約の分母にも入れない)
- デビュー馬(過去走ゼロ): 全列 NaN
- 同日複数走・同日の他レースのタイム: 同日除外規律により基準タイムにも個馬集約にも入らない
- 距離帯の境界(1400/1800/2200 の 020 定義を再利用するか連続距離の別扱いか)は plan で確定
- 障害レース等の特殊トラック: track_type 単位の分割で自然に分離

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 基準タイムは(競馬場×トラック種別×距離帯(×馬場状態))単位で、**対象レースより strictly-before(同日除外)の finisher タイムのみ**から as-of 推定しなければならない。全期間プールでの推定は禁止(リーク)
- **FR-002**: 過去走スピード指数は「基準タイムに対する当該過去走タイムの標準化偏差」とし、指数の符号・スケールは全期間で一貫していること。標本不足セルは NaN
- **FR-003**: 馬単位の as-of 集約(avg/best/recent 系)は既存の strictly-before+同日除外機構に載せること。新特徴は数値列・NaN 伝播(0 埋め禁止)
- **FR-004**: 新ソース生列を読まない(既存ロード済み列のみ)こと。source_fingerprint 不変・materialize-safe(per-race 決定的・pool-end 非依存)
- **FR-005**: FEATURE_VERSION を features-016 に bump し、既存列はバイト不変(additive)であること
- **FR-006**: serving 互換: features-014(lgbm-057)・features-015(lgbm-058-acc/lgbm-060-mkt)の compat-path ピン留めを追加し、既存モデルの予測バイト不変を実 DB で検証すること(058 T013 同型)
- **FR-007**: リーク境界: grep 型 leak-guard(オッズ/配当トークン不使用)+ 挙動型テスト(今走・同日・未来のタイムを変更しても対象行の特徴不変、過去走タイムの変更で変化する正の対照)
- **FR-008**: 採用ゲートは事前登録し変更禁止: フル walk-forward binary feature-eval(baseline=features-015=新群 drop)で (a) win LogLoss 改善 (b) mean ECE 非悪化(tol 1e-3)+worst-fold ECE(tol 2e-3) (c) strict majority + worst-fold LogLoss 上限(023 同型)。通過後 production pl_topk+TE 構成で再学習し全指標非悪化を確認、active 昇格は最終的にユーザー判断
- **FR-009**: フル実装前に少数 fold の spike で de-risk する: binary で正の信号がなければ中断・記録。**059 の教訓(能力系は binary→pl_topk でゲイン縮小)を明記**し、binary spike が微小ゲインの場合は pl_topk spike も実施してから speckit 続行を判断

### Key Entities

- **基準タイム (baseline time)**: (venue×track_type×距離帯(×going)) セル別の as-of expanding 統計(平均・分散)。非永続・ビルド時に決定論再計算
- **過去走スピード指数**: 過去走 1 走ごとの標準化偏差。中間値(集約の入力)
- **スピード指数特徴群 (speed_figure group)**: 馬単位 as-of 集約列(数列は plan で確定、目安 3〜6 列)。features-016 の新 FEATURE_GROUPS

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: フル walk-forward feature-eval で win LogLoss が baseline(features-015)から改善し、事前登録ガード全通過(参考: 直近の採用特徴束は −0.0001〜−0.001 級)
- **SC-002**: features-016 build で既存全列がバイト不変(共有列 check_exact+check_dtype 一致)・materialized parity 維持
- **SC-003**: 既存 3 モデル(lgbm-057/058-acc/060-mkt)の serving 予測が features-016 registry 下でバイト不変(実 DB E2E mismatch 0)
- **SC-004**: 全パッケージテスト緑・migration/API/OpenAPI/スキーマ不変
- **SC-005**: 新特徴のカバレッジ(非 NaN 率)がレポートされ、履歴のある馬で実用水準(目安 80%+)

## Assumptions

- 対象タイムは走破タイム(finish_time)。区間ラップ由来の指数は 035 のデータ待ちでスコープ外
- 斤量補正・当日馬場差補正は初版スコープ外(plan で斤量は特徴側交互作用として検討可)
- 距離帯は 020 の既存 bins(≤1400/1800/2200)を基本とし、plan で妥当性確認
- 事前登録ゲートは 020/023 型の対称比較(モデル vs モデル)なので、060 で判明した expanding-window の市場 baseline 問題は非適用
- lgbm-061 の学習は既存 CLI(train-evaluate/model-eval)で実施し、モデル切替・登録は 057/058 基盤を再利用
