# Feature Specification: 区間ラップ ingest (Race-level Sectional Lap Ingest)

**Feature Branch**: `034-sectional-lap-ingest`

**Created**: 2026-06-30

**Status**: Implemented

**Input**: §4 第1段。区間ラップ(レースの 200m 毎ラップ + テン3F/上がり3F)は DB・scrape ともに未取得=純新データ。db.netkeiba.com の race ページから取得し新テーブル `race_laps` に格納する ingest 基盤。本 feature はデータ取り込みのみ(モデル特徴は Feature 035)。

## 概要・動機

030〜033 で公開情報・交互作用のレバーをほぼ収穫(features-008→011, win LogLoss 0.23277→0.23187)。codex が #1 新情報源に挙げた **sectional(区間ラップ)** は、各馬の上がり3F(last_3f)・通過順(corner_orders)を既に持つ中で唯一欠けている「**レース全体の前半ペース配分**」を与える。実地調査: race.netkeiba.com の結果ページは着順/上がり3F は持つがラップ節無し、**db.netkeiba.com の race DB ページに `summary="ラップタイム"` 表**(ラップ行=200m毎、ペース行=末尾 `(テン3F-上がり3F)`)が存在。本 feature でこれを取り込む。

## 利用者と価値

Feature 035 が lap_times を as-of 特徴化する土台。直接の利用者は ingest パイプライン/オペレータ。lap データは結果由来=今走特徴に絶対しない(過去走の as-of のみ、035 で)。

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - ラップ parser (Priority: P1, MVP)

db.netkeiba race ページの `ラップタイム` 表から 200m 毎ラップ配列 + テン3F/上がり3F を抽出する(network-free, 保存実 fixture でテスト)。

**Independent Test**: 2000m レースの実 fixture で lap_times が 10 要素(12.6…11.9)、pace_first_3f=36.0/pace_last_3f=35.5、合計=120.5。

**Acceptance Scenarios**:
1. **Given** ラップタイム表のある実ページ, **When** parse_laps, **Then** lap_times(float tuple)+ pace_first_3f/last_3f。
2. **Given** ラップ表の無いページ, **When** parse_laps, **Then** None(偽行を作らない)。

### User Story 2 - スキーマ + upsert (Priority: P1, MVP)

新テーブル `race_laps`(race_id PK, lap_times JSONB, pace_first_3f/last_3f, source, updated_at)。single-latest 上書き(履歴なし、憲法 V)。レース行が無ければ skip(FK、偽行なし)。

**Independent Test**: integration(testcontainer)で fixture 投入→1 行、再投入で重複なし、レース未存在で skip。

### User Story 3 - パイプライン + backfill CLI (Priority: P1)

`scrape_laps`(fetch→parse→upsert+ingestion_jobs 監査)+ CLI `scrape-laps`(--race-id 指定 or --from/--to で race_laps 欠損レースを date-range backfill)。polite HttpFetcher(robots/rate-limit/cache)。

**Independent Test**: 実 netkeiba で少数レースを end-to-end 取り込み、race_laps 行を確認。

---

### Edge Cases
- ラップ表の無いレース(古い/障害等) → skip。
- レース行未存在 → skip(FK 違反回避)。
- EUC-JP ページ → fetch 層が header charset 優先で UTF-8 化(既存)。

## Requirements *(mandatory)*

- **FR-001**: parse_laps(html, *, race_id) が `summary="ラップタイム"` 表からラップ配列 + `(first-last)` を抽出。無ければ None。
- **FR-002**: 新テーブル race_laps(migration 0007、新 ORM RaceLaps)。single-latest 上書き・履歴なし。
- **FR-003**: upsert_laps はレース行存在時のみ書込(FK)、空ラップは skip。
- **FR-004**: scrape_laps パイプライン(ingestion_jobs job_type='race_laps' 監査)+ CLI scrape-laps(date-range backfill = race_laps 欠損レース)。
- **FR-005**: lap データは結果由来 → モデル特徴にしない(035 で過去走 as-of のみ)。スキーマ以外の変更なし。

## Success Criteria *(mandatory)*

- **SC-001**: parse_laps が実 fixture で正しいラップ/ペースを返す(network-free unit テスト)。
- **SC-002**: pipeline integration(testcontainer)で書込/冪等/skip が緑。
- **SC-003**: 実 netkeiba で end-to-end 取り込みが成功(実 DB に行)。
- **SC-004**: scrape/db lint・既存テスト透過で緑。migration head 0006→0007。

## Out of Scope / Deferred
- lap_times を使った特徴量(Feature 035)。
- 全期間 17年 backfill の実行(オペレータが CLI で運用、本 feature は基盤+少数実証)。
- per-horse furlong sectional(netkeiba 無料では非提供)。
