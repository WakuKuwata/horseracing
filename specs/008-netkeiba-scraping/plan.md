# Implementation Plan: netkeiba スクレイピングによる未来レース取り込み

**Branch**: `008-netkeiba-scraping` | **Date**: 2026-06-23 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/008-netkeiba-scraping/spec.md`

## Summary

新パッケージ `scrape/`(`horseracing-scrape`、db 依存)に、netkeiba の 出馬表/前売りオッズ/結果 を**行儀よく**
取得・パースし、既存 core テーブルに取り込むパイプラインを実装する。netkeiba ID は `id_mappings`(source='netkeiba')
経由でのみ JRA-VAN ID に対応付け、未対応は**一意の名前空間付き代替 ID**(`nk:{id}`)+ 未マッピングキューに載せる
(推測結合禁止、憲法 I)。未来 race_id は JRA-VAN 互換 12 桁を構成できる場合のみ作る(偽 ID 禁止)。結果は
**insert-only**(JRA-VAN を上書きしない)、前売りオッズは**結果未確定レースのみ**に上書き(JRA-VAN 最終オッズ保護)。
idempotent + `ingestion_jobs` 監査。スキーマ変更なし。

codex の 5 BLOCKER を本 plan で機構的に解消する(下表)。テストはネットワーク非依存(HTML フィクスチャ + 合成パース
結果 + モック fetcher)。

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: `horseracing-db`(パス依存)、HTML パーサ(`selectolax` or `beautifulsoup4`+`lxml`)、
HTTP 取得(`httpx`)、stdlib `urllib.robotparser`(robots)、SQLAlchemy 2.0

**Storage**: PostgreSQL 16(書: races/race_horses/horses/jockeys/trainers/race_results/id_mappings/
ingestion_jobs)。HTTP レスポンスはローカルファイルキャッシュ。

**Testing**: pytest + testcontainers。**ネットワーク非依存**: パーサは保存済み HTML フィクスチャ、upsert/ID マッピング/
backfill/odds 保護は合成パース結果 + 実 DB、fetcher はモック(robots/レート/キャッシュ)。

**Target Platform**: Linux / macOS の手動 CLI 実行

**Project Type**: 単一の取り込みパッケージ(`horseracing-scrape`)

**Performance Goals**: 1 開催日(数十レース)を行儀よく取得(レート制限下)。パース/取り込みは秒オーダー。

**Constraints**: robots.txt 遵守・レート制限・キャッシュ・UA・指数バックオフ。netkeiba ID は id_mappings 経由のみ。
偽 race_id 禁止。結果 insert-only。オッズは結果未確定レースのみ。2007+。idempotent。

**Scale/Scope**: 出馬表/オッズ/結果の 3 取り込み。JRA 主要競馬場。地方/海外・複勝/馬連・推定オッズは対象外。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution v1.0.0 に基づくゲート:

- [x] **I. データ契約**: netkeiba ID は **`id_mappings`(source='netkeiba')経由のみ**で JRA-VAN へ対応付け、未対応は
  推測結合せず UNMAPPED キュー(`mapping_status`)。マッピング済みは canonical_id、未対応は衝突しない一意の代替 ID。
  race_id は JRA-VAN 互換 12 桁を構成できる場合のみ作る(`is_valid_race_id`、偽 ID 禁止)。2007+。日本語ラベル維持。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: 未マッピング馬は **debut/Unknown**(過去成績なし)として leak-safe に渡す。
  代替 ID は netkeiba 馬ごとに一意(同一 Unknown 使い回し=他馬履歴リークを禁止)。オッズはモデル特徴に使わない
  (005/006/007 で担保)。**PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: 本フィーチャーは**データ取り込み**でありモデル/特徴量を変更しない。walk-forward
  評価の対象外。取り込んだ未来レースは既存の serving/eval が消費する。**N/A(モデル変更なし)**
- [x] **IV. 確率整合性**: 取消・除外を entry_status に反映し、serving 側の母集団除外+再正規化を支える。**PASS(支援)**
- [x] **V. 再現性・監査**: 各取り込みを `ingestion_jobs`(source='netkeiba'/parser 版/件数/status/時刻)に記録。
  オッズは最新値上書き + updated_at のみ(スナップショット履歴なし)。**PASS**
- [x] **VI. feature 分割規律**: 既存テーブルのみ。スキーマ変更なし。id_mappings/ingestion_jobs 契約は Feature 001 確定済み。
  複勝/馬連/推定オッズは対象外。**PASS**
- [x] **品質ゲート**: `codex:codex-rescue` の second opinion を取得・記録(下表)。BLOCKER を本 plan で解消。**PASS**

### Second Opinion 記録(codex:codex-rescue — spec/plan 段階)

| 論点 | codex 助言 | 本 plan の対応 |
|---|---|---|
| **ID 名前空間衝突** | **BLOCKER**: 生 netkeiba ID を canonical horse_id に入れると JRA-VAN PK 名前空間を汚染 | マッピング済み=canonical_id、未対応=`nk:{id}` 代替(衝突せず一意)。`data_source='netkeiba'`(R1) |
| **未マッピング履歴リーク** | 同一 Unknown ID 使い回しで他馬履歴がリーク。debut 条件は有効 horse_id の career_starts==0 | netkeiba 馬ごとに一意な代替 ID。history は過去なし=debut(R1/R2) |
| **偽 race_id** | **BLOCKER**: 偽の数値 race_id は validation を通るが衝突/不一致。構成不能なら行を作るな | 開催場コード対応 + `is_valid_race_id`、不能なら行を作らず UNMAPPED 通知(R3) |
| **結果 backfill 上書き** | **BLOCKER**: 汎用 upsert は既存 JRA-VAN race_results を全列上書き | **insert-only**(ON CONFLICT DO NOTHING)backfill 専用パス(R4) |
| **オッズ上書き** | **BLOCKER**: netkeiba 前売りが JRA-VAN 最終オッズを無言上書き | **結果未確定レースのみ**(race_results 不在)odds 更新(R5) |
| 結果状態/取消・除外 | finished/stopped/disqualified に対応、非出走に race_results を作らない、同着は finish_order 共有 | 採用(R4) |
| idempotency/監査 | PK upsert べき等 + ingestion_jobs(002 と同じ作法)。fail-close | 採用(R6)。Feature 002 の作法を踏襲 |
| スクレイパ堅牢性 | robots/レート/キャッシュ/UA、HTML 構造変化に fail-close、テストは HTML フィクスチャ | 採用(R7)。fetcher はモック可 |

最重要リスク TOP3: ①ID 名前空間汚染 + 未マッピング履歴リーク ②偽 race_id ③JRA-VAN 結果/オッズの上書き。
①は canonical/代替 ID 分離 + 一意性、②は valid 構成 or skip、③は insert-only + 結果未確定限定で対応。

## Project Structure

### Documentation (this feature)

```text
specs/008-netkeiba-scraping/
├── plan.md
├── research.md          # ID 名前空間・race_id 構成・insert-only・odds 保護・礼儀・パーサ・監査
├── data-model.md        # パース結果・id_mappings 解決・upsert 規則・不変条件
├── quickstart.md        # 出馬表→serving、オッズ/結果 backfill、ID マッピングキュー確認手順
├── contracts/
│   ├── ingest.md        # scrape_entries / scrape_odds / scrape_results の契約
│   └── idmap.md         # resolve / register-unmapped / 代替 ID / race_id 構成の契約
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
scrape/                                    # 新パッケージ horseracing-scrape
├── pyproject.toml                         # db (path) + httpx + selectolax/bs4 + lxml
├── src/horseracing_scrape/
│   ├── __init__.py
│   ├── fetch.py                           # PoliteFetcher: robots/レート制限/キャッシュ/UA/バックオフ (注入可)
│   ├── venues.py                          # netkeiba 開催場→JRA-VAN コード + race_id 構成 (valid or None)
│   ├── idmap.py                           # id_mappings 解決 / 未マッピング登録 / 代替 ID 生成
│   ├── parse/
│   │   ├── entries.py                     # 出馬表 HTML → ScrapedEntry (network-free)
│   │   ├── odds.py                        # オッズ HTML → ScrapedOdds
│   │   └── results.py                     # 結果 HTML → ScrapedResult
│   ├── upsert.py                          # entries upsert / odds(結果未確定のみ) / results(insert-only)
│   ├── pipeline.py                        # scrape_entries/odds/results + ingestion_jobs 監査
│   └── cli.py                             # scrape-entries / scrape-odds / scrape-results --race-id/--date
└── tests/
    ├── fixtures/                          # 保存済み HTML(出馬表/オッズ/結果)
    ├── unit/                              # パーサ・race_id 構成・代替 ID・odds 保護・insert-only (合成)
    └── integration/                       # 実 DB で upsert/ID マッピング/backfill/監査/idempotency
```

**Structure Decision**: 取り込みは JRA-VAN(Feature 002)と責務が異なる(HTML 取得 + 別 ID 体系 + backfill/odds 保護)
ため新パッケージ `scrape/` を作り、db に依存。取得層(`fetch.py`)を分離してテストでモックし、パーサは純粋関数で
HTML フィクスチャ検証。upsert は entries(汎用)/ odds(結果未確定のみ)/ results(insert-only)を分ける。監査は
Feature 002 と同じ `ingestion_jobs` 作法。

## Complexity Tracking

> Constitution Check に違反なし。記入不要。
