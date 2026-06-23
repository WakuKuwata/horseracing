# Feature Specification: netkeiba スクレイピングによる未来レース取り込み

**Feature Branch**: `008-netkeiba-scraping`

**Created**: 2026-06-23

**Status**: Draft

**Input**: User description: "netkeiba スクレイピングによる未来レース取り込み(出馬表 + 前売りオッズ + 結果、JRA-VAN backfill)。行儀のよいスクレイパ。netkeiba ID は id_mappings 経由でのみ JRA-VAN ID に対応付け(推測結合禁止)。スキーマ変更なし。"

## 概要

netkeiba から **未来レースの出馬表・前売りオッズ・結果**をスクレイプし、既存テーブル
(races/race_horses/horses/jockeys/trainers/race_results/id_mappings/ingestion_jobs)に取り込む。
これにより Feature 006 serving が**未来レースを予測**でき、Feature 007 betting が**実オッズ**で EV を出せる。
**行儀のよいスクレイパ**(robots.txt 遵守・レート制限・ローカルキャッシュ・適切な User-Agent・指数バックオフ)。
スキーマ変更なし。

**最重要(憲法 I)**: netkeiba の ID は JRA-VAN と体系が異なるため、**`id_mappings`(source='netkeiba')を介してのみ**
JRA-VAN ID に対応付ける。対応が取れない場合は手動修正キュー(`mapping_status`)に載せ、**名前+生年などで推測結合
してはならない**。未マッピングの馬は **debut/Unknown** として leak-safe に渡す(存在しない過去成績に 0 を入れない)。

「利用者」は人間ではなく、取り込みを実行するオペレーター。スキーマ変更なし。複勝・馬連・三連複・推定オッズは対象外。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 未来レースの出馬表を取り込み、ID を安全に対応付ける (Priority: P1) 🎯 MVP

オペレーターが対象レース/日付を指定すると、netkeiba の出馬表が取り込まれ、races/race_horses/horses/jockeys/
trainers に upsert される。netkeiba ID は id_mappings 経由で JRA-VAN ID に対応付けられ、未対応はキューに載る。

**Why this priority**: serving が未来レースを予測する前提。ID マッピングの安全性(憲法 I)が本フィーチャーの核。

**Independent Test**: 保存済み出馬表 HTML フィクスチャを使い、出馬表が races/race_horses/horses 等に取り込まれ、
マッピング済み馬は JRA-VAN canonical_id で、未マッピング馬は **JRA-VAN ID 空間と衝突しない一意の代替 ID** で
保存され、未マッピングが手動修正キューに載ることを確認(ネットワーク非依存)。

**Acceptance Scenarios**:

1. **Given** 出馬表 HTML, **When** 取り込み, **Then** 未来 race(race_id/日付/開催/距離/馬場)と出走馬(枠/馬番/
   騎手/調教師/斤量、entry_status='started')が保存される。
2. **Given** id_mappings に対応のある netkeiba 馬, **When** 取り込み, **Then** その馬は **JRA-VAN canonical_id** で
   race_horses/horses に入り、JRA-VAN 履歴に結合できる。
3. **Given** id_mappings に対応の無い netkeiba 馬, **When** 取り込み, **Then** **一意の名前空間付き代替 ID**
   (JRA-VAN ID と衝突しない)で保存され、`id_mappings` に未マッピング(手動修正待ち)として記録される。**名前+生年で
   推測結合しない**。
4. **Given** 未マッピング馬(代替 ID), **When** 特徴量生成, **Then** 過去成績なし=**debut/Unknown** として扱われ、
   他馬の履歴が混入しない(同一 Unknown ID の使い回し禁止)。
5. **Given** JRA-VAN 互換の 12 桁 race_id を構成できない netkeiba レース, **When** 取り込み, **Then** **行を作らず**
   未マッピングとして通知する(偽の数値 race_id を作らない)。

---

### User Story 2 - 前売りオッズを取り込み、結果時点オッズを壊さない (Priority: P1)

オペレーターが対象レースを指定すると、netkeiba の締切前単勝オッズが取り込まれ、**結果未確定レースの**
race_horses.odds が最新値で上書きされる。確定済み(JRA-VAN 結果のある)レースの最終オッズは壊さない。

**Why this priority**: Feature 007 の EV を closing-oracle ではなく実オッズ寄りにする。ただし歴史データの最終オッズ
保護が必須。

**Independent Test**: 結果未確定レースに前売りオッズを取り込むと race_horses.odds が更新され updated_at が進む。
**結果確定済み(race_results のある)レースには netkeiba オッズを書かない**ことを確認。

**Acceptance Scenarios**:

1. **Given** 結果未確定レースと前売りオッズ, **When** 取り込み, **Then** race_horses.odds が最新値で上書きされ、
   updated_at が進む(スナップショット履歴は保存しない、憲法 V)。
2. **Given** 結果確定済み(race_results のある)レース, **When** 前売りオッズ取り込み, **Then** その odds は
   **更新しない**(JRA-VAN 最終オッズを保護)。
3. **Given** オッズ欠損/不正値, **When** 取り込み, **Then** その馬の odds は更新しない。

---

### User Story 3 - 結果を backfill する(JRA-VAN を壊さない) (Priority: P1)

オペレーターが対象レースを指定すると、netkeiba のレース結果が取り込まれ、`race_results` の**欠損のみを補完**する。
JRA-VAN が取り込んだ既存行は上書きしない。

**Why this priority**: JRA-VAN 未取得分の結果を埋め、評価/採点を可能にする。authoritative ソース保護が必須。

**Independent Test**: 既存 JRA-VAN race_results 行を持つレースに netkeiba 結果を取り込んでも既存行が**変化しない**
(insert-only)。結果の無いレースには新規行が作られることを確認。

**Acceptance Scenarios**:

1. **Given** race_results に既存行のあるレース, **When** netkeiba 結果取り込み, **Then** 既存行は**一切変更されない**
   (insert-only、JRA-VAN 優先)。
2. **Given** race_results の無いレース, **When** 取り込み, **Then** 着順/結果状態(finished/stopped/disqualified)/
   タイム等が新規に保存される。
3. **Given** 取消・除外馬(非出走), **When** 取り込み, **Then** その馬に race_results 行を作らない(entry_status のみ)。
4. **Given** 同着 1 着, **When** 取り込み, **Then** 同一 finish_order を共有して表現する(余分な状態を作らない)。

---

### Edge Cases

- **未マッピング ID の名前空間衝突**: 代替 ID は JRA-VAN ID と衝突せず、netkeiba ID ごとに**一意**(同一 Unknown を
  複数馬で使い回さない)。後でマッピングが付いたら canonical_id に解決できる。
- **race_id 構成不能**(開催場/回/日/レース番号が取れない): 行を作らず未マッピング通知。
- **HTML 構造変化**: 必須要素が取れない場合は fail-close(誤データを作らない)+ ingestion_jobs に errors 記録。
- **再スクレイプ**: idempotent(重複行・破壊なし)。
- **2007 年より前**: 対象外(ID 体系が異なる)。
- **robots.txt 不許可パス / レート超過**: 取得しない/待機する。
- **取消・除外の出馬表反映**: entry_status を started 以外に更新(母集団から除外、憲法 IV)。
- **オッズの意味の差**: netkeiba 前売り(締切前)と JRA-VAN 最終(結果時点)を混同しない。前売りは結果未確定レース
  のみに書く。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: システムは netkeiba の出馬表・前売りオッズ・結果ページを**行儀よく**取得する MUST(robots.txt 遵守、
  レート制限、ローカルキャッシュ、適切な User-Agent、指数バックオフ)。
- **FR-002**: システムは netkeiba の馬・騎手・調教師 ID を **`id_mappings`(source='netkeiba')経由でのみ** JRA-VAN ID
  に対応付ける MUST。対応が無い場合は推測結合せず、`id_mappings` に未マッピング(手動修正待ち)として記録する。
- **FR-003**: マッピング済みエンティティは **JRA-VAN canonical_id** で core テーブルに保存する MUST。未マッピングは
  **JRA-VAN ID 空間と衝突しない一意の名前空間付き代替 ID**(`nk:` 接頭辞、netkeiba ID ごとに一意)で保存する MUST。
  `horses` は `data_source='netkeiba'` を記録する(jockeys/trainers には data_source 列が無いため記録しない)。同一
  Unknown ID を複数エンティティで使い回してはならない。代替 ID は JRA-VAN の数値 ID/12 桁 race_id 形式と一致しない。
- **FR-004**: システムは未マッピング馬を特徴量で **debut/Unknown**(過去成績なし)として扱い、他馬の履歴を混入させない
  MUST(存在しない過去成績に 0 を入れない)。
- **FR-005**: システムは netkeiba レースから **JRA-VAN 互換の 12 桁 race_id**(`YYYYVVKKDDRR`、開催場コード対応表で
  構成)を生成する MUST。構成できない/妥当でない場合は **races/race_horses に行を作らず**未マッピングとして通知する
  (偽の数値 race_id を作らない)。
- **FR-006**: 出馬表取り込みは未来 race と出走馬(枠/馬番/騎手/調教師/斤量、entry_status='started')を upsert する MUST。
  取消・除外は entry_status に反映する MUST。
- **FR-007**: 前売りオッズ取り込みは **結果未確定レース**(race_results の無いレース)に限り race_horses.odds を最新値で
  上書きし updated_at を進める MUST。**結果確定済みレースの odds は更新しない**(JRA-VAN 最終オッズ保護)。スナップ
  ショット履歴は保存しない。
- **FR-008**: 結果取り込みは **insert-only**(欠損補完)で、**既存 race_results 行を一切上書きしない** MUST
  (JRA-VAN authoritative 保護)。非出走馬に race_results 行を作らない。結果状態は既存 enum(finished/stopped/
  disqualified)に対応付ける。同着は finish_order 共有で表現する。
- **FR-009**: 取り込みは **idempotent** MUST(再実行で重複・破壊なし)。
- **FR-010**: システムは各取り込みを **`ingestion_jobs`** に監査記録する MUST(source='netkeiba'、job_type、対象
  scope、件数、status、時刻、parser/logic バージョン)。部分失敗は errors と status に反映する。
- **FR-011**: システムは 2007 年以降のみを対象とする MUST(2006 以前は ID 体系が異なる)。
- **FR-012**: システムは CLI で対象レース/日付を指定して scrape-entries / scrape-odds / scrape-results を実行できる MUST。
- **FR-013**: パーサは**保存済み HTML フィクスチャ**に対する単体テストで検証できる MUST(ネットワーク非依存)。必須要素
  欠損時は fail-close(誤データを作らない)。
- **FR-014**: モデルは netkeiba オッズ/人気を特徴量に使わない MUST(オッズは betting のみ。リーク境界は 005/006/007 で
  既に担保)。

### Key Entities *(include if feature involves data)*

- **ScrapedEntry / ScrapedOdds / ScrapedResult**: 出馬表/オッズ/結果ページのパース結果(netkeiba ID・名前・値)。
- **IdMapping**(`id_mappings`): netkeiba ID(source/source_id) → JRA-VAN canonical_id(`entity_type` 馬/騎手/調教師)。
  `mapping_status` で未マッピング/解決済み/競合を管理。推測結合しない。
- **core upsert 先**: races / race_horses / horses / jockeys / trainers(出馬表)、race_horses.odds(オッズ)、
  race_results(結果、insert-only)。
- **IngestionJob**(`ingestion_jobs`): netkeiba 取り込みの監査(source='netkeiba')。
- **PoliteFetcher**: robots/レート制限/キャッシュ/UA/バックオフを担う取得層(テストではモック)。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 出馬表 HTML から未来 race と出走馬が取り込まれ、マッピング済みは canonical_id、未マッピングは一意の代替 ID
  で保存され、未マッピングが手動修正キューに載る。
- **SC-002**: 未マッピング馬が特徴量で debut/Unknown として扱われ、他馬の履歴が混入しない(同一 Unknown 使い回し禁止)。
- **SC-003**: JRA-VAN 互換 race_id を構成できないレースは行が作られず通知される(偽 ID なし)。
- **SC-004**: 前売りオッズが結果未確定レースのみに書かれ、結果確定済みレースの最終オッズが保護される。
- **SC-005**: netkeiba 結果取り込みが既存 JRA-VAN race_results 行を一切変更しない(insert-only)。欠損のみ補完される。
- **SC-006**: 取り込みが idempotent で、各実行が ingestion_jobs に監査記録される。
- **SC-007**: パーサが保存済み HTML フィクスチャでネットワーク非依存にテストされ、必須要素欠損時に fail-close する。
- **SC-008**: 取り込んだ未来レースに対し Feature 006 serving が予測を生成できる(end-to-end)。

## Assumptions

- Feature 001(対象テーブル)・002(JRA-VAN 取込・upsert/監査の作法)・004(as-of 特徴、未マッピング=debut が
  leak-safe)・006(serving)が適用済み。
- **robots.txt / ToS は個人利用前提**。レート制限・キャッシュで負荷を最小化し、商用再配布はしない。取得不可パスは取得
  しない。
- netkeiba の開催場コード → JRA-VAN 開催場コード(VV)の対応表が用意できる(主要 JRA 競馬場)。地方/海外は対象外。
- 前売りオッズは単勝のみ(複勝/馬連等は対象外)。**オッズ HTML は静的 HTML として取得・パースする前提**(テストの
  フィクスチャも静的 HTML)。netkeiba のオッズページが JS 動的描画で静的 HTML から取れない場合は、取得層の既知の限界
  として扱い、動的レンダリング対応は将来フィーチャー(本フィーチャーの保証外)。
- id_mappings の初期マッピングは別途投入/手動運用(本フィーチャーは未マッピングを安全に扱い、キューに積むまで)。
- スキーマ変更なし。テストはネットワーク非依存(HTML フィクスチャ + 合成パース結果)。
- 結果状態・取消・除外は既存 enum にマッピング。新状態は作らない。
