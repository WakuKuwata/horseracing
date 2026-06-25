# Feature Specification: 実 exotic オッズ取込と疑似→実 ROI 化

**Feature Branch**: `012-exotic-odds-ingest`

**Created**: 2026-06-24

**Status**: Draft

**Input**: User description: "実 exotic オッズ取込と疑似→実 ROI 化。008 の polite netkeiba 基盤を再利用し実 exotic 配当オッズ(複勝/馬連/馬単/ワイド/三連複/三連単)を取得・格納。新テーブル exotic_odds(001 以来初のスキーマ変更)。011 の推定オッズに実オッズを優先し is_estimated_odds=false / 実 ROI。評価先行: 推定 vs 実の乖離。"

## 概要

Feature 008(netkeiba 取込)の polite 基盤を再利用し、**実 exotic 配当オッズ**(複勝/馬連/馬単/ワイド/三連複/三連単)を
取得・パースして**新テーブル exotic_odds** に格納する。Feature 011 の exotic 推奨/バックテストは推定市場オッズ(010、PL
外挿=二重疑似)に依存していたが、本フィーチャーで**実 exotic オッズがあればそれを優先**し、`recommendations.market_odds_used`
=実値・`is_estimated_odds=false`・`pseudo_roi→実 ROI` とする(実オッズが無い券種/組み合わせは 011 の推定にフォールバック)。
評価先行(憲法 III)として、推定(010/011)vs 実 exotic オッズの**乖離**を券種別・レース単位で計測し、推定の妥当性を検証する。

**スキーマ変更**: `exotic_odds` テーブル + Alembic マイグレーション。**コア/取込/予測契約(0001–0004)以降で初の新テーブル
追加**(006–011 はスキーマ変更なし)であり、憲法 VI(feature
分割規律)に基づく正当化を plan に記録する(既存 7 テーブルには exotic オッズの置き場が無いため新テーブルが必須)。

**最重要(リーク境界)**: exotic オッズは**市場データ**であり、**予測モデルの特徴量に一切しない**(win オッズと同一扱い、憲法 II)。
事前オッズ=推奨用、確定オッズ=バックテスト払戻用。買い目決定はオッズと結果を区別し、結果(着順)は採点のみに使う。

「利用者」は人間ではなく、exotic オッズを取込・配線・評価するオペレーターと、将来の運用 UI。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 実 exotic オッズを取込んで exotic_odds に格納できる (Priority: P1) 🎯 MVP

オペレーターがレース(または期間)を指定すると、netkeiba から実 exotic 配当オッズ(6 券種)が polite に取得・パースされ、
`exotic_odds`(race_id・bet_type・selection・odds・coverage_scope・source・updated_at)に冪等に格納される(単一最新値、履歴なし)。

**Why this priority**: 実オッズの取込が本フィーチャーの土台。これが無ければ実 ROI 化も乖離評価もできない。

**Independent Test**: 保存済み HTML fixture(ネットワーク非依存)から 6 券種の exotic オッズをパースし、`exotic_odds` に
`selection`(011 と同一 JSONB 安全配列、順序券種=順序付き/無順序=整列/複勝=単一要素 `[i]`)で格納され、
`UNIQUE(race_id, bet_type, selection)` で冪等(再取込は最新値で**上書き**、重複行が増えない)であることを確認。

**Acceptance Scenarios**:

1. **Given** netkeiba の exotic オッズ HTML, **When** 取込, **Then** 6 券種それぞれの (組み合わせ→オッズ) が `exotic_odds` に
   格納され、`selection` は 011 と同一シリアライザ(`to_selection`)で正準化された配列、netkeiba ID は **008 の id_mappings 経由のみ**
   で解決(guess-join 禁止、mapped→canonical / unmapped→`nk:` surrogate)。
2. **Given** 同一レースの再取込, **When** 取込, **Then** `UNIQUE(race_id, bet_type, selection)` により冪等(最新値で**上書き**、
   `updated_at` のみ更新、重複行を作らない。憲法 V: スナップショット履歴を持たない)。`ingestion_jobs`(job_type='exotic_odds'、
   status、summary に期待/観測/欠損の組み合わせ数)で監査。
3. **Given** レース前(result-pending)から確定後まで, **When** 取込, **Then** `exotic_odds.odds` は常に**最新スクレイプ値で上書き**
   (レース前=事前オッズ、確定後=最終配当)。exotic は JRA-VAN 源が無く netkeiba 単独なので保護対象が無い。決定時オッズは
   推奨時に `recommendations.market_odds_used` へスナップショットし、上書き後も推奨の監査値は不変。
4. **Given** 取得ページが一部券種のみ/不完全, **When** 取込, **Then** 観測できた行のみ格納し `coverage_scope`(full/partial)を
   記録、欠損は監査(後段で推定フォールバック)。
5. **Given** future race_id(結果未確定・未来日), **When** 取込, **Then** 有効な JRA-VAN 12 桁 race_id でなければ行を書かない
   (008 と同一規律)。

---

### User Story 2 - 実 exotic オッズで推奨/バックテストを実 ROI 化 (Priority: P1)

オペレーターが exotic 推奨/バックテストを実行すると、実 exotic オッズがある (レース,券種,組み合わせ) は**実オッズで** ROI を
計算し、無い組み合わせは 011 の推定オッズ(二重疑似)にフォールバックする。

**Why this priority**: 011 の最大の弱点「二重疑似」を解消し、評価を実測に格上げする本フィーチャーの核。

**Independent Test**: 合成 exotic_odds とモデル予測で exotic 推奨を生成し、実オッズのある組み合わせは
`market_odds_used=実値`・`is_estimated_odds=false`・`pseudo_roi→実 ROI`(EV=P_model×実オッズ)、無い組み合わせは
`estimated_market_odds_used=O_est`・`is_estimated_odds=true`(011 のまま)で記録され、両者が selection の**完全一致**
(canonical horse_number 配列)で結び付くことを確認。

**Acceptance Scenarios**:

1. **Given** 実 exotic オッズのある組み合わせ, **When** 推奨生成, **Then** `market_odds_used=実オッズ`・`is_estimated_odds=false`・
   `estimated_market_odds_used=null`・`pseudo_odds=1/P_model`・`pseudo_roi=EV−1`(EV=P_model×**実オッズ**)。
2. **Given** 実オッズが無い組み合わせ, **When** 推奨生成, **Then** 011 の推定にフォールバック(`is_estimated_odds=true`・
   `estimated_market_odds_used=O_est`・二重疑似ラベル)。実と推定を**混在させず行単位で区別**。
3. **Given** 推奨後に取消が発生した馬を含む組み合わせ, **When** 採点, **Then** その買い目は **void/skip**(推定フォールバックで
   無理に払戻しない)として監査。
4. **Given** バックテスト, **When** 採点, **Then** 実 final オッズがある的中買い目は払戻=stake×**実オッズ**(実 ROI)、無ければ
   stake×O_est(疑似)。実払戻と疑似払戻を**レポートで明確にラベル分離**。
5. **Given** 実オッズと推定オッズで selection キーが食い違う恐れ, **When** 突合, **Then** 必ず 011 の `to_selection`/canonical
   horse_number で生成した同一配列で完全一致させる(順序券種=順序、無順序=整列、複勝=`[i]`)。

---

### User Story 3 - 推定 vs 実 exotic オッズの乖離を評価 (Priority: P1)

オペレーターが期間を指定すると、010/011 の推定オッズ O_est と実 exotic オッズの乖離が券種別・レース単位で計測され、推定の
妥当性が定量化される。

**Why this priority**: 憲法 III(評価先行)。推定(二重疑似)がどれだけ実態と乖離するかを測らずに推定フォールバックは正当化できない。

**Independent Test**: 合成の (推定 O_est, 実オッズ) ペアで、カバレッジ率・符号付き log 比 `log(実/推定)`・中央値/MAE/P90 が
券種別に算出され、推定(010/011)を baseline として実 vs 推定のラベルが分離表示されることを確認。

**Acceptance Scenarios**:

1. **Given** 推定 O_est と実 exotic オッズ, **When** 乖離評価, **Then** 券種別・レース単位で `log(実/推定)` の中央値・MAE・P90 と
   **カバレッジ率**(実オッズが存在した組み合わせ割合)が算出される。
2. **Given** 評価出力, **When** レポート, **Then** 推定(010/011)= baseline、実 = 実測としてラベルが分離され、推定側は二重疑似と
   明示される。
3. **Given** カバレッジが部分的なレース/券種, **When** 集計, **Then** 欠損を除外せず**カバレッジ率を明示**して集計(部分カバーを
   全カバーと誤認しない)。

---

### User Story 4 - CLI で exotic オッズ取込と乖離レポート (Priority: P2)

オペレーターが CLI で、レース/期間指定の exotic オッズ取込と、期間指定の推定 vs 実 乖離レポートを実行できる。

**Why this priority**: 運用効率。MVP(US1–US3)成立後の操作性。

**Independent Test**: CLI で exotic オッズ取込(レース/期間)と乖離レポート(期間)を実行し、取込件数(券種別・coverage)と
乖離指標(カバレッジ/log 比中央値/MAE/P90)が表示される。

**Acceptance Scenarios**:

1. **Given** race_id or 期間, **When** 取込 CLI, **Then** 券種別の格納件数・coverage_scope・unmapped 件数が表示される。
2. **Given** 期間, **When** 乖離レポート CLI, **Then** 券種別の推定 vs 実 乖離指標が推定=baseline 明示で表示される。

---

### Edge Cases

- **selection キー突合**: 実オッズと推奨/推定は必ず同一シリアライザ(`to_selection`、canonical horse_number)で生成した配列で
  突合。`5`(スカラ)と `[5]`(配列)、順序券種の順序差、無順序券種の整列差で取りこぼさない。一意制約は
  `(race_id, bet_type, selection)` の複合 B-tree(JSONB 等価)。
- **事前 vs 確定オッズ**(憲法 V 準拠): `exotic_odds` は (race_id, bet_type, selection) ごとに**単一の最新値 + updated_at**を持ち
  スナップショット履歴を持たない(`race_horses.odds` と同方針)。レース前スクレイプ=事前オッズ、確定後スクレイプ=最終配当が
  上書き。netkeiba 単独源のため保護対象は無いが、決定時オッズは推奨時に `recommendations.market_odds_used` へスナップショット
  して監査を担保。バックテストは過去レースの `exotic_odds`(=最終配当)を実払戻に用いる。
- **組み合わせ爆発**: 三連単 ~ P(N,3)(18 頭で 4896)・三連複 ~ C(N,3)。取得は netkeiba が公開する券種別グリッドを格納し
  `coverage_scope` で full/partial を区別。完全グリッドは**期待件数テスト**で証明できる場合のみ full とする。
- **カバレッジ部分**: 実オッズが無い組み合わせは推定(011)へフォールバック。バックテストの baseline(最低 O_est/均等)は実 ROI に
  するには全グリッドが必要 → 実オッズが無い券種/レースは疑似のままと明示。
- **取消・除外**: 推奨後の取消を含む買い目は void/skip(推定で無理に払戻しない)。canonical 母集団規律は 011 を継承。
- **同着・推定不能**: 011 の規則を継承(順序非一意の同着はレーススキップ+監査、複勝/ワイド圏内同着は的中)。
- **冪等・部分取得**: 再取込で重複を作らない。部分取得は `status=partial` + summary(期待/観測/欠損)で監査。
- **2007+**: 取得対象は 2007 年以降。netkeiba にしか exotic オッズは無い(JRA-VAN パイプラインには無い)。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: システムは netkeiba から実 exotic 配当オッズ(複勝/馬連/馬単/ワイド/三連複/三連単)を取得・パースし、008 の polite
  規律(robots/rate-limit/cache/UA/backoff)で取得する MUST。パーサは保存 HTML fixture でテストされネットワーク非依存 MUST。
- **FR-002**: システムは新テーブル `exotic_odds` に (race_id, bet_type, selection, odds, coverage_scope, source, updated_at) を
  格納する MUST。`selection` は Feature 011 と**同一の JSONB 安全配列**(`to_selection`、順序券種=順序付き/無順序=整列/複勝=
  `[i]`)で、`UNIQUE(race_id, bet_type, selection)` を持つ MUST。**スナップショット履歴を持たず最新値で上書き**(憲法 V、
  `race_horses.odds` と同方針)。スキーマ変更は Alembic マイグレーションで、憲法 VI の正当化を plan に記録する MUST。
- **FR-003**: システムは netkeiba ID を **008 の id_mappings 経由のみ**で解決する MUST(guess-join 禁止。mapped→canonical /
  unmapped→`nk:` surrogate + UNMAPPED 監査)。
- **FR-004**: システムは `exotic_odds.odds` を**最新スクレイプ値で上書き**する MUST(レース前=事前オッズ、確定後=最終配当)。
  exotic は netkeiba 単独源で JRA-VAN 保護対象が無いため上書き可。future race_id は有効な JRA-VAN 12 桁でなければ行を書かない MUST。
  決定時オッズは推奨時に `recommendations.market_odds_used` へスナップショットして監査を担保する MUST。
- **FR-005**: 取込は冪等で、`ingestion_jobs`(job_type='exotic_odds'、status、summary に期待/観測/欠損の組み合わせ数)で監査
  される MUST。部分取得は `status=partial` + `coverage_scope=partial`。
- **FR-006**: システムは exotic オッズを**予測モデルの特徴量に一切使用しない** MUST(市場データ、win オッズと同一のリーク境界、
  憲法 II)。
- **FR-007**: 推奨生成時、実 exotic オッズがある (レース,券種,組み合わせ) は実オッズで `EV=P_model×実オッズ`、
  `market_odds_used=実値`・`is_estimated_odds=false`・`estimated_market_odds_used=null`・`pseudo_roi=EV−1` を記録する MUST。
- **FR-008**: 実オッズが無い組み合わせは Feature 011 の推定(`is_estimated_odds=true`・`estimated_market_odds_used=O_est`・二重
  疑似)にフォールバックし、実と推定を**行単位で区別**(混在させない)する MUST。突合は必ず canonical horse_number の同一配列で
  完全一致 MUST。
- **FR-009**: バックテスト採点は、実 final オッズがある的中買い目は払戻=stake×**実オッズ**(実 ROI)、無ければ stake×O_est(疑似)
  とし、実払戻と疑似払戻を**レポートでラベル分離**する MUST。推奨後取消を含む買い目は void/skip MUST。
- **FR-010**: システムは推定(010/011)O_est と実 exotic オッズの乖離を券種別・レース単位で計測する MUST: カバレッジ率・符号付き
  `log(実/推定)` の中央値/MAE/P90。推定= baseline、実=実測でラベル分離、推定側は二重疑似明示。
- **FR-011**: 取込・突合・評価は決定論的 MUST(同一入力で同一)。`exotic_odds` は append/更新規則に従い監査可能(憲法 V)。
- **FR-012**: システムは CLI で、レース/期間指定の exotic オッズ取込と、期間指定の推定 vs 実 乖離レポートを実行できる MUST。
- **FR-013**: 本フィーチャーは実 exotic オッズ取込 + 実 ROI 配線 + 乖離評価に限定する。Kelly/資金管理・bias 補正・全グリッド完全
  カバレッジ保証・運用 UI は将来に明示分離する MUST。

### Key Entities *(include if feature involves data)*

- **ExoticOdds**(`exotic_odds`、新規): (race_id, bet_type, selection(JSONB 安全配列), odds, coverage_scope(full/partial),
  source(netkeiba), updated_at)。一意 = (race_id, bet_type, selection)。**単一最新値**(履歴なし、憲法 V)。レース前=事前、
  確定後=最終配当が同一行に上書き。
- **CoverageScope**: full(期待件数テストで完全グリッド証明)/ partial(部分取得)。
- **Recommendation**(既存、新規列なし): 実オッズ時 `market_odds_used`=実値・`is_estimated_odds=false`、推定時は 011 のまま。
- **乖離レポート**: 券種別・レース単位のカバレッジ率/log 比中央値/MAE/P90(推定= baseline、実=実測)。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 6 券種の実 exotic オッズが `exotic_odds` に 011 と同一の JSONB 安全配列 selection で格納され、
  `(race_id, bet_type, selection)` で冪等(再取込は最新値で上書き、重複ゼロ、履歴なし)。
- **SC-002**: netkeiba ID は id_mappings 経由のみで解決され、unmapped は `nk:` surrogate + UNMAPPED 監査。guess-join はゼロ。
- **SC-003**: `exotic_odds.odds` は最新スクレイプ値で上書き(レース前=事前、確定後=最終配当)、`updated_at` のみ保持(履歴なし)。
  future race_id の偽 ID 書込みゼロ。決定時オッズは recommendations にスナップショット済み。
- **SC-004**: 実オッズのある組み合わせは `market_odds_used`=実値・`is_estimated_odds=false`・実 ROI、無い組み合わせは 011 推定
  (二重疑似)にフォールバックし、両者が selection 完全一致で行単位に区別される。
- **SC-005**: バックテストが実払戻(実オッズ)と疑似払戻(推定)をラベル分離して算出し、推奨後取消は void/skip。
- **SC-006**: 推定 vs 実の乖離(カバレッジ率/log 比中央値/MAE/P90)が券種別・レース単位で計測され、推定= baseline でラベル分離。
- **SC-007**: exotic オッズが予測モデル特徴に一切使われない(リーク境界)。取込・突合・評価は決定論的・監査可能。

## Assumptions

- Feature 008(netkeiba 取込基盤)・009/010/011(確率・推定オッズ・exotic EV)が適用済み。`scrape`/`betting`/`probability` を
  再利用・拡張し、`db` に `exotic_odds` を追加する。
- exotic オッズは **netkeiba のみ**から取得(JRA-VAN パイプラインに exotic オッズは無い)。常に market データでモデル特徴にしない。
- **カバレッジ方針(既定)**: netkeiba が公開する券種別オッズグリッドを格納し `coverage_scope` で full/partial を区別。完全グリッドは
  期待件数テストで証明できる場合のみ full。三連単/三連複の大グリッドは取得コストが大きいため取込は**期間/レース駆動で polite**に行い、
  欠損は推定フォールバック + カバレッジ明示で扱う。大規模化時の分割/間引き/パーティションは将来最適化。
- 推奨の決定時オッズ(レース前の `exotic_odds.odds`)を `recommendations.market_odds_used` にスナップショット。バックテストは
  過去レースの `exotic_odds`(=最終配当に上書き済み)を実払戻に用いる。憲法 V によりオッズ履歴は保持しない。
- 同着・推定不能・canonical 母集団・selection 正準化は Feature 011 の規則を継承(本フィーチャーで再定義しない)。
- スキーマ変更は `exotic_odds` テーブル追加のみ(既存テーブルの破壊的変更なし)。Kelly/bias 補正/完全カバレッジ保証/運用 UI は将来。
- 日本語規約維持。
