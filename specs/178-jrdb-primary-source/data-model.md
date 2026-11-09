# Data Model: 178 JRDB を主情報源にする

スキーマ変更は migration **0025** の 1 本に限る(現行 DB)。段 3b の JRDB 正本 DB は同じ migration 群を適用した別 DB で、
表の追加は無い。

## 1. `Source` の拡張(CHECK 置換・0023 と同型)

`Source.JRDB = "jrdb"` を追加し、`ingestion_jobs` / `exotic_odds` / `race_laps` / `official_win_payouts` / 来歴 2 表 / `id_mappings` の
`source` CHECK を置換する。downgrade は `jrdb` 行があれば中断(0023 と同じ)。

## 2. 新表 `jrdb_files`(ファイル版・冪等性の単位)

| 列 | 型 | 制約 | 意味 |
|---|---|---|---|
| `jrdb_file_id` | uuid | PK | |
| `kind` | text | CHECK IN (BAC, KYI, KYH, KYG, UKC, KZA, KSA, CZA, CSA, TYB, HJC, SED, SEC, SRB, SKB, OZ, OW, OU, OT, OV, CYB, CHA, KAB, MZA, MSA) | 種別(版文字込み) |
| `data_date` | date | NOT NULL | ファイル名の yymmdd(マスタ系は仕様どおり木/月) |
| `variant` | text | NOT NULL・CHECK IN ('daily', 'venue_e', 'venue_w', 'venue_l', 'archive') | 日次/場別上書き(TYB)/バックナンバー |
| `sha256` | text | NOT NULL | 展開後テキストの sha |
| `size_bytes` | int | NOT NULL | |
| `record_length` | int | NOT NULL | 検出した行長(仕様と不一致なら status=rejected) |
| `source_url_sha256` | text | NOT NULL | 会員 URL は平文で持たない |
| `published_at` | timestamptz | NULL 可 | HTTP Last-Modified。無ければ NULL |
| `fetched_at` | timestamptz | NOT NULL | |
| `applied_at` | timestamptz | NULL 可 | 製品表に書いた時刻(parity/fetch のみは NULL) |
| `status` | text | CHECK IN ('fetched', 'partially_applied', 'applied', 'superseded', 'rejected', 'held') | held = 順序を決められない異内容(運用者承認待ち) |
| `race_status` | jsonb | NOT NULL・既定 `{}` | race_id → {status: applied/failed/skipped, reason, applied_at}。applied ⇔ 全レース applied |
| `ingestion_job_id` | uuid | FK ingestion_jobs・SET NULL | |
| UNIQUE | (`kind`, `data_date`, `variant`, `sha256`) | | 同一内容は 1 行 |

INV-F1: 同じ (kind, data_date, variant) で `applied`/`partially_applied` は常に 1 行以下。INV-F2: 異内容の新旧を自動で決めるのは
速報→正式・日次→バックナンバーだけで、それ以外は `held`(製品表を書かない)。INV-F3: `status='applied'` の行の sha と `artifacts/jrdb/` の
ファイルの sha が一致する(audit CLI が検査)。INV-F4: `applied` ⇔ `race_status` の全値が applied。同 sha の再投入は未 applied レースだけ処理。

## 3. 来歴(176 の 2 表)への JRDB 列

`field_group` の集合は不変。`source` に `jrdb` が入る。各項目群で JRDB が書く列(契約 [precedence-jrdb.md](contracts/precedence-jrdb.md)):

| 表 | field_group | JRDB の出所 |
|---|---|---|
| race_horse_field_provenance | entry_static | KYI(枠・馬番・性別・斤量・馬主) |
| | jockey / trainer | KYI(写像済み ID のみ) |
| | body_weight | TYB(当日)・SED(確定) |
| | entry_status | KYI 取消フラグ・SED 異常区分 |
| race_field_provenance | race_static | BAC |
| | post_time | BAC |
| | results | SED 正式版(race_atomic) |
| | laps | SRB |
| | win_payouts / exotic_payouts | HJC |
| | win_odds / place_odds | **書かない** |

### 3a. 利用可能時刻(FR-010)の導出経路

来歴 2 表に `published_at` 列は足さない(既存表の列追加は FR-017 の外・176 の列は source/observed_at/official_at/ingestion_job_id のみ)。「その値はいつ使えるようになったか」は次の経路で引く:

`race_*_field_provenance.ingestion_job_id` → `ingestion_jobs.summary.files[]`(JRDB の各ジョブが処理したファイルの `{jrdb_file_id, kind, data_date, variant, sha256, published_at}` の配列・T051)→ 項目群に対応する kind(entry_static/jockey/trainer/entry_status→KYI、body_weight→TYB または SED、race_static/post_time→BAC、results→SED、laps→SRB、win_payouts/exotic_payouts→HJC)の `published_at`。

規則: (a) 来歴の job_id は**現在値を採用した job**(precedence が採用を決めたときに書く=176 と同じ)なので、同じレースを後から再投入しても採用が変わらなければ job_id は動かない。(b) `jrdb_files.ingestion_job_id` は最後にそのファイルを処理した job を指す参考値で、導出には使わない(上書きされる)。(c) `published_at` が NULL(Last-Modified 無し)のときは `fetched_at` を上限として報告し「利用可能時刻 ≤ fetched_at」とだけ言う。(d) 1 job が同じ kind のファイルを 2 つ以上処理することはない(日次ファイルは kind×日付で 1 つ・バックナンバーは年度 zip 内の日次単位で別 job)。INV-F5: `summary.files[]` の `jrdb_file_id` は `jrdb_files` に存在する。

## 4. `id_mappings`(騎手・調教師)

既存表。`source='jrdb'`・`entity_type IN ('jockey','trainer')`・`source_id`=JRDB 5 桁コード・`canonical_id`=JRA 5 桁。
`status` は既存の mapped / unmapped / conflict / rejected。`resolution_note` に材料(名前一致・生年月日一致・所属・初免許年)を書く。
馬は作らない(血統登録番号が正規 ID)。

## 5. 値オブジェクト(DB を知らない・`jrdb/models.py`)

`JrdbRaceKey(venue, yy, kai, day_hex, r)` → `race_id`。`JrdbEntry`(KYI 1 行の採用列)・`JrdbHorse`(UKC)・`JrdbPerson`(KZA/CZA)・
`JrdbResult`(SED)・`JrdbRaceResult`(SRB)・`JrdbPayouts`(HJC)・`JrdbRaceProgram`(BAC)・`JrdbPreRace`(TYB)。指数類・コメント・基準オッズは
値オブジェクトに**持たない**(取り込まない列は decode しない)。

## 6. 段 0 / 段 3.5 / 段 1 の記録(ファイル・DB 外)

`artifacts/jrdb/probe/<day>.jsonl`(段 0: レースごとの公式オッズ取得時刻・成否・理由、依存の棚卸し、各版の Last-Modified)、
`artifacts/jrdb/trial/<day>.jsonl`(段 3.5)、`artifacts/jrdb/parity/<year>.json`(段 1: 列×区分の一致率・分母・例)。
append-only(既存パスは拒否)。DB に表は作らない(174 の capture 記録と違い、判断の記録ではなく運用の計測なので)。

## 7. 段 3b の JRDB 正本 DB

`DATABASE_URL_JRDB`(例 `…/horseracing_jrdb`)に同じ alembic head を適用。`races`/`race_horses`/`race_results`/`horses`/`jockeys`/`trainers`/
`official_win_payouts`/`exotic_odds`/`race_laps` を source=jrdb だけで埋める。`horses.data_source='jrdb'`。別定義 4 列は JRDB の定義
(前 3F は SED 259・通過順は 4 枠のまま・脚質は JRDB コード 1〜6 を文字列で・系統は JRDB 系統名)。年別件数比較は
`artifacts/jrdb/rebuild/compare-<year>.json`。
