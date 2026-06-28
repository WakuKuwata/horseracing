# Feature Specification: 実 netkeiba パーサ (real netkeiba HTML parsing)

**Feature Branch**: `022-netkeiba-parser`

**Created**: 2026-06-28

**Status**: Draft

**Input**: User description: 実netkeibaパーサ — 実際のnetkeiba HTML(出馬表/単勝オッズ/結果ページ)を解析する本物のパーサを実装し、既存008の合成HTMLスキーマ前提スタブを置換する。

## 背景 *(context)*

feature 008 (netkeiba-scraping) の取得層 (`scrape/fetch.py` の `HttpFetcher`: httpx + robots.txt + per-domain rate-limit 1秒 + exponential backoff + file cache)、ID 解決 (`idmap.py`: netkeiba→JRA-VAN は `id_mappings` 経由、未マップは surrogate `nk:` + UNMAPPED キュー)、race_id 構築 (`venues.py`: `build_race_id`、JRA-VAN race_id=`YYYYVVKKDDRR`、会場コードは netkeiba と恒等)、DB 書き込み (`upsert.py`: races/race_horses/race_results、JRA-VAN 結果は INSERT-only 保護、pre-race odds は result-pending のみ上書き) はいずれも本物として動作する。

一方、**parse 層 (`parse/entries.py` / `odds.py` / `results.py` / `exotic_odds.py`) はテスト用に発明した合成 HTML スキーマ (`div.race[data-year/data-track/...]`, `tr.horse[data-horse-id/...]`) 前提のスタブ**であり、実 netkeiba の HTML を一度も解析できない。008 spec は FR-013 / SC-007 で「保存済み HTML フィクスチャでネットワーク非依存にテストできること」しか要求しておらず、合成フィクスチャでその弱い要件を満たしていた。結果として scrape (008) およびそれに依存する live serving (019) は実 netkeiba では機能しない。

本 feature はこの唯一の実害スタブ (parse 層) を、実 netkeiba HTML を正しく解析する本物のパーサに置換し、既存の取得・ID 解決・書き込みの本物の配管へ接続する。これにより live serving (019) と将来の RaceFront からの「1日分実データ更新」が実際に動作する土台を完成させる。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 出走表を実 netkeiba から取り込む (Priority: P1)

オペレーターが、これから行われる (結果未確定の) レースの出走表 netkeiba ページを指定すると、実ページの HTML が解析され、未来 race (race_id / 開催日 / 開催場 / 距離 / 馬場種別) と出走馬 (枠 / 馬番 / 馬 ID / 騎手 / 調教師 / 性齢 / 斤量 / 出走状態) が既存テーブル (`races` / `horses` / `jockeys` / `trainers` / `race_horses`) に取り込まれる。

**Why this priority**: 出走表は予測の入力 (特徴量生成の母集団) であり、これ無しには予測も推奨も生成できない。実 netkeiba 連携の最小の価値はここにある。これ単独で「未来レースの予測を回せる」状態になる。

**Independent Test**: 保存した**実 netkeiba 出走表 HTML フィクスチャ**を `FixtureFetcher` 経由で取り込み (ネットワーク非依存)、未来 race と出走馬が正しいフィールドで DB に入り、マッピング済み馬は canonical_id、未マッピングは surrogate `nk:` で記録され、UNMAPPED キューに積まれることを検証する。

**Acceptance Scenarios**:

1. **Given** 実 netkeiba 出走表ページの保存 HTML, **When** 取り込みを実行, **Then** 有効な JRA-VAN 12桁 race_id を持つ未来 race と全出走馬が `races`/`race_horses` 等に取り込まれる。
2. **Given** 出走取消・除外を含む出走表, **When** 取り込み, **Then** 各馬の出走状態 (entry_status) が正しく区別されて記録される (取消馬を出走として誤記録しない)。
3. **Given** 2007 年より前 / JRA 以外の開催で有効な race_id を構築できないページ, **When** 取り込み, **Then** 行を書き込まず skip し、`ingestion_jobs` に記録する (偽 ID を作らない)。

---

### User Story 2 - 結果を実 netkeiba から取り込む (Priority: P2)

オペレーターが、確定した (結果が出た) レースの結果 netkeiba ページを指定すると、実ページが解析され、各馬の着順・競走状態・タイムが `race_results` に INSERT-only で取り込まれる (既存の JRA-VAN 結果は上書きしない)。

**Why this priority**: 結果はバックテスト・評価 (007/011/016) と予測の答え合わせに必要。出走表 (P1) で予測を回せるようになった後、実績で評価するために要る。

**Independent Test**: 保存した実 netkeiba 結果 HTML フィクスチャを取り込み、`race_results` に着順・状態・タイムが入り、同一レースに既存結果がある場合は上書きしないことを検証する。

**Acceptance Scenarios**:

1. **Given** 実 netkeiba 結果ページの保存 HTML, **When** 取り込み, **Then** 出走各馬の着順・競走状態 (完走/中止/失格) が `race_results` に記録される。
2. **Given** 既に JRA-VAN 由来の結果がある race, **When** netkeiba 結果取り込み, **Then** 既存行は上書きされない (INSERT-only)。

---

### User Story 3 - 単勝オッズを実 netkeiba から取り込む (Priority: P3)

オペレーターが、結果未確定レースの単勝オッズ netkeiba ページを指定すると、実ページが解析され、各馬の単勝オッズ・人気が `race_horses.odds` に最新値で上書きされる (結果のある race は JRA-VAN 最終オッズ保護のため更新しない)。

**Why this priority**: 推奨 (EV/Kelly) は市場オッズを使うが、予測自体はオッズ無しで回る。出走表・結果より優先度は下。netkeiba オッズページは動的描画の懸念が最も大きい (下記前提参照)。

**Independent Test**: 保存した実 netkeiba オッズ HTML/データのフィクスチャを取り込み、result-pending race の `race_horses.odds` が更新され、結果のある race は更新されないことを検証する。

**Acceptance Scenarios**:

1. **Given** result-pending race の実 netkeiba 単勝オッズデータ, **When** 取り込み, **Then** `race_horses.odds` が最新値で更新される (スナップショット履歴は保存しない)。
2. **Given** 結果が確定済みの race, **When** オッズ取り込み, **Then** odds は更新されない (JRA-VAN 最終オッズ保護)。

---

### Edge Cases

- netkeiba の HTML 構造が変化し必須要素が取得できない場合は **fail-close** (誤データを作らない) し、`ingestion_jobs` に errors を記録する。部分的に取得できた場合も、必須要素を欠く行は書かず errors に計上する。
- 同一馬名でも netkeiba ID が JRA-VAN にマッピングされていない場合は surrogate `nk:` を用い、推測結合しない (デビュー馬・新規エンティティ対応)。
- 出走表ページにオッズ列が含まれていても、それを結果・特徴量として扱わない (リーク境界・責務分離)。
- ページが想定と異なる種別 (例: 地方競馬・海外) で有効な JRA-VAN race_id を構築できない場合は skip。
- 文字エンコーディング・全角/半角・タイム表記 (例: `1:34.5`) の正規化を行う。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: システムは**実 netkeiba の出走表ページ HTML** を解析し、race と出走馬の各フィールド (race_id 構築に必要な 開催年/会場/回/日次/レース番号、開催日、距離、馬場種別、および 枠/馬番/馬ID/騎手ID/調教師ID/性齢/斤量/出走状態) を抽出できなければならない (MUST)。
- **FR-002**: システムは**実 netkeiba の結果ページ HTML** を解析し、各馬の着順・競走状態・タイムを抽出できなければならない (MUST)。
- **FR-003**: システムは**実 netkeiba の単勝オッズ**データを解析し、各馬の単勝オッズと人気を抽出できなければならない (MUST)。
- **FR-004**: 抽出結果は既存の取得層 (`HttpFetcher`)・ID 解決 (`id_mappings` 経由、未マップは surrogate `nk:` + UNMAPPED キュー)・race_id 構築 (`build_race_id`)・DB 書き込み (`upsert.py`) に接続しなければならない (MUST)。新たな DB スキーマ変更は行わない (MUST NOT)。
- **FR-005**: netkeiba の 馬/騎手/調教師 ID は `id_mappings` 経由でのみ JRA-VAN と対応付け、推測結合してはならない (MUST NOT)。未来 race は有効な JRA-VAN 12桁 race_id を構築できる場合のみ書き込み、構築不能なら行を書かない (MUST)。
- **FR-006**: 必須要素を取得できない場合、システムは fail-close し誤データを作らず、`ingestion_jobs` に job 種別・scope・件数・status・時刻・parser/logic バージョン・errors を記録しなければならない (MUST)。
- **FR-007**: 単勝オッズの取り込みは **result-pending な race のみ** を対象とし、結果のある race の odds は上書きしてはならない (MUST NOT)。オッズはスナップショット履歴を保存せず最新値で上書きし `updated_at` のみ保持する (MUST)。
- **FR-008**: 結果の取り込みは INSERT-only とし、既存 (JRA-VAN 由来含む) の結果行を上書きしてはならない (MUST NOT)。
- **FR-009**: netkeiba から取得した odds・結果は、モデルの入力特徴量に再投入してはならない (リーク境界、MUST NOT)。この不変条件は leak-guard テストで担保する (MUST)。
- **FR-010**: パーサは**保存済みの実 netkeiba HTML フィクスチャ**に対する単体テストでネットワーク非依存に検証できなければならない (MUST)。フィクスチャは実ページ由来とし、合成 data-* スキーマを置換する (MUST)。
- **FR-011**: 取得は netkeiba の robots.txt とレート制限 (既存 1秒/ドメイン間隔) を遵守し、個人利用の範囲で礼儀正しく行わなければならない (MUST)。利用規約上スクレイプが許容されない場合は取得しない方針を明記する。
- **FR-012**: 既存スタブパーサ (合成スキーマ前提) は実パーサへ**置換**し、合成フィクスチャに依存した既存テストは実フィクスチャベースへ更新しなければならない (MUST)。実パーサは単一経路とし、移行期間の並存は行わない (決定: 置換)。**対象は entries / results / 単勝 odds の 3 パーサに限る**。exotic odds パーサ (`parse/exotic_odds.py`) は本 feature 対象外で合成のまま残置する (次段)。
- **FR-013**: システムは netkeiba の動的描画ページに対して必要なデータを確実に取得できなければならない (MUST)。取得方式は**ハイブリッド**とする (決定): 出走表 (entries) と結果 (results) は**サーバ描画 HTML を静的取得して解析**し、単勝オッズ (odds) は **netkeiba 内部の JSON データ (埋め込み JSON ないし JSON エンドポイント) を利用**する。headless ブラウザ (Playwright 等) は導入しない。実 netkeiba ページの構造を実サンプルで確認したうえで、この方式の妥当性を plan で検証する (構造が想定と異なる場合は plan 段で方式を見直す)。

### Key Entities *(include if feature involves data)*

- **出走表 (entries) の解析結果**: 未来 race のメタ (開催年/会場/回/日次/レース番号 → race_id、開催日、距離、馬場種別) と出走馬の集合 (枠/馬番/netkeiba馬ID/馬名/騎手ID・名/調教師ID・名/性別/年齢/斤量/出走状態)。既存 `races`/`race_horses`/`horses`/`jockeys`/`trainers` に対応。
- **結果 (results) の解析結果**: race_id と各馬 (netkeiba馬ID) の 着順・競走状態 (完走/中止/失格)・タイム。既存 `race_results` に対応。
- **単勝オッズ (win odds) の解析結果**: race_id と各馬 (netkeiba馬ID) の 単勝オッズ・人気。既存 `race_horses.odds` に対応。
- **実 HTML フィクスチャ**: 実 netkeiba の出走表/結果/オッズページを保存したテスト用 HTML。ネットワーク非依存テストの基盤。
- **取り込みジョブ監査 (ingestion_jobs)**: 既存テーブル。job 種別・scope・件数・status・parser/logic バージョン・errors。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 保存済みの実 netkeiba 出走表 HTML フィクスチャから、未来 race と全出走馬が正しいフィールドで既存テーブルに取り込まれ、マッピング済みは canonical_id・未マッピングは surrogate で記録される (出走馬の取りこぼし 0、誤フィールド 0)。
- **SC-002**: 保存済みの実 netkeiba 結果 HTML フィクスチャから、出走各馬の着順・状態・タイムが `race_results` に取り込まれ、既存結果がある場合は上書きされない。
- **SC-003**: 保存済みの実 netkeiba 単勝オッズフィクスチャから、result-pending race の odds が更新され、結果のある race は更新されない。
- **SC-004**: 必須要素を欠く (構造変化を模した) フィクスチャに対し、システムは行を書かず fail-close し、`ingestion_jobs` に errors を記録する。
- **SC-005**: netkeiba 由来の odds・結果がモデル特徴量に現れないことを leak-guard テストで確認できる。
- **SC-006**: 全パーサテストがネットワーク非依存で完結する (テスト実行時に外部 HTTP を行わない)。
- **SC-007**: 実 netkeiba から取得したデータで予測 serving (006/019) がエラーなく予測を生成できる (出走表→特徴量→予測のエンドツーエンドが実データで成立)。

## Assumptions

- 取得層 (`HttpFetcher`)・ID 解決 (`idmap.py`)・race_id 構築 (`venues.py`)・DB 書き込み (`upsert.py`) は本物として再利用でき、本 feature では変更しない (取得方式の決定 FR-013 によっては取得層に追補が入りうる)。
- DB スキーマ変更は行わない。既存テーブルに取り込む (憲法 VI / 008 系踏襲)。
- exotic odds (複勝/馬連/馬単/ワイド/三連複/三連単) の実パーサは本 feature の対象外 (次段 deferred)。
- RaceFront 側の「更新」ボタン・write API は本 feature の対象外 (別 feature)。本 feature は CLI / 既存 pipeline 関数経由で取り込みを実行する。
- 自動スケジューリング・複数ソース・ログイン必須ページは対象外。個人利用・手動実行前提 (憲法 技術制約)。
- 実 netkeiba HTML サンプルは、本 feature 内で **polite 設定 (robots/rate-limit) のもと 1 回限りの取得を許容**して保存し、テストフィクスチャ化する (決定)。以後のテストはこの保存フィクスチャに対してネットワーク非依存で実行する。取得は entries/results/odds 各ページ種別につき必要最小限の件数に限る。

## Out of Scope

- DB スキーマ変更。
- exotic odds の実パーサ (次段)。
- RaceFront の write UI / write API (別 feature)。
- 自動スケジューリング、定期再取得、複数データソース、odds スナップショット履歴。
- ログイン/有料会員限定ページの取得。
- 着差 (`race_results.finish_time_diff`) の取り込み（finish_time のみ対象。着差は対象外）。
