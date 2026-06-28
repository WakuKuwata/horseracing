# Data Model: 実 netkeiba パーサ (022)

**DB スキーマ変更なし。** 本 feature は新テーブル・新カラムを追加しない。データモデルは (a) parse↔upsert の境界となる既存 dataclass（無改修で維持）と (b) 取り込み先の既存テーブルを再掲する。

## A. Parse 出力契約（`scrape/models.py`、無改修）

実 netkeiba 解析の結果は、この既存 dataclass に充填して `upsert.py` へ渡す。**形は変えない**（変更は parse 関数の内部実装のみ）。

| dataclass | フィールド | 取り込み先 |
|---|---|---|
| `ScrapedRaceKey` | year, track_code, kai, nichime, race_no | `build_race_id()` → race_id |
| `ScrapedRace` | key, race_date, distance, track_type, going, weather, race_class | `races` |
| `ScrapedEntryHorse` | netkeiba_horse_id, horse_name, frame, horse_number, netkeiba_jockey_id, jockey_name, netkeiba_trainer_id, trainer_name, weight, sex, age, entry_status | `horses`/`jockeys`/`trainers`/`race_horses` |
| `ScrapedEntry` | race, horses[] | （entries 集約） |
| `ScrapedOddsRow` | netkeiba_horse_id, odds, popularity | `race_horses.odds` |
| `ScrapedOdds` | key, rows[] | （odds 集約） |
| `ScrapedResultRow` | netkeiba_horse_id, finish_order, result_status, finish_time | `race_results` |
| `ScrapedResult` | key, rows[] | （results 集約） |

`ScrapedExoticOdds`/`ScrapedExoticRow` は本 feature 対象外（exotic は次段）。

### 実 netkeiba → dataclass マッピング（実装で実サンプル確定。現時点の根拠付き想定）

- **race_id 構成要素**: URL クエリ `race_id=YYYYVVKKDDRR` から year/track_code(=会場2桁)/kai/nichime/race_no を分解（netkeiba race_id = JRA-VAN race_id、probe 実証）。ページ内テキストからも交差検証。
- **出走馬**: `Shutuba_Table` の各行 → 枠(frame)/馬番(horse_number)、`HorseName` 内 `/horse/{id}` から `netkeiba_horse_id`、騎手 `/jockey/{id}`・調教師 `/trainer/{id}` リンクから ID、性齢テキスト（例「牡3」）→ sex/age、斤量 → weight、取消・除外表記 → `entry_status`（started/cancelled 等）。
- **結果**: 着順テーブル → finish_order、状態（除外/中止/失格）→ result_status、タイム文字列 → finish_time。
- **単勝オッズ**: odds JSON → `ScrapedOddsRow`。⚠️ **突合キーに注意**: 既存 `upsert.update_odds` は `resolve_entity(netkeiba_id=row.netkeiba_horse_id)` で **horse_id 一致**により更新する。odds JSON が **馬番 (race-local) のみ**で netkeiba_horse_id を含まない場合、`netkeiba_horse_id` を埋められず突合できない。対処: (a) JSON が horse_id を含むならそのまま、(b) 馬番のみなら `race_horses.(race_id, horse_number) → horse_id` で解決する経路を `update_odds`（または odds 取り込み側）に用意する。実エンドポイントのキーは T018/T019 で確定する。

## B. 取り込み先 既存テーブル（再掲・無改修）

- **races**: race_id(12桁 PK), race_date, venue_code, race_number, race_class, distance, track_type, going, weather …
- **horses / jockeys / trainers**: canonical_id（id_mappings で解決）または surrogate `nk:{netkeiba_id}`。
- **race_horses**: (race_id, horse_id), frame, horse_number, jockey_id, trainer_id, weight, sex, age, entry_status, **odds**・**popularity**（最新値上書き・`updated_at` のみ）。⚠️ 既存 `update_odds` は odds のみ書き popularity を落としている → 本 feature で popularity も書く小改修（カラムは存在、スキーマ変更なし）。
- **race_results**: (race_id, horse_id), finish_order, result_status, **finish_time**（Interval）（**INSERT-only**）。⚠️ 既存 `backfill_results` は finish_time を落としている → 本 feature で str(例 "1:34.5")→Interval 変換して書く小改修。`finish_time_diff`（着差）列は存在するが本 feature 対象外（取り込まない）。
- **id_mappings**: (entity_type, source=netkeiba, source_id) → canonical_id / mapping_status（未マップは UNMAPPED キュー）。
- **ingestion_jobs**: source=netkeiba, job_type(entries/odds/results), scope, status, summary(written/skipped/errors), parser/logic version, 時刻。

## C. 不変条件（Validation）

- **race_id**: `^[0-9]{12}$`、`build_race_id` 検証を通過した場合のみ書き込み（無効→行を書かない、偽 ID 禁止）。2007 年以降のみ。
- **エンティティ ID**: netkeiba→JRA-VAN は `id_mappings` 経由のみ。未マップは surrogate `nk:`、UNMAPPED キューに記録。推測結合禁止（憲法 I）。
- **odds**: result-pending な race のみ更新。結果のある race は上書きしない（JRA-VAN 最終オッズ保護）。スナップショット履歴を持たない（憲法 V）。
- **results**: INSERT-only。既存行を上書きしない（憲法・008 踏襲）。
- **リーク境界**: odds・結果はモデル特徴量に再投入しない（憲法 II、leak-guard テスト）。
- **fail-close**: 必須要素欠損時は行を書かず ingestion_jobs に errors 記録。必須フィールド（馬番/horse_id/finish_order）は strict parse（不正値を None に潰さず ParseError）。
- **race_id 照合**: URL の race_id と取得 HTML 本文の race_id が一致しない場合は fail-close（誤レース投入防止、codex 指摘）。
- **odds no-cache**: odds JSON は cache をバイパスして取得（古い odds を書かない、憲法 V）。

## D. State / フロー

```
netkeiba URL (race_id から構築)
  → HttpFetcher (robots/rate-limit/cache)  [既存・無改修]
  → parse_entries / parse_results (HTML)  [本 feature: 実装置換]
    parse_odds (JSON)                      [本 feature: 実装置換 (JSON 化)]
  → ScrapedEntry / ScrapedResult / ScrapedOdds  [既存 dataclass]
  → idmap.resolve_entity / venues.build_race_id  [既存・無改修]
  → upsert_entries / backfill_results / update_odds  [既存・無改修]
  → races / race_horses / race_results / id_mappings (+ ingestion_jobs 監査)
```
