# `horseracing_scrape.jrdb` — JRDB 経路(Feature 178)

JRDB(有料会員・Shift_JIS 固定長・zip/LZH 配信)を **第 3 の writer** として入れる経路。176 の JRA 公式経路
(`../jra/README.md`)と同じ「薄い書込み層+共通の規則表」の形で、netkeiba を policy で止められる状態と、
学習データの正本を JRDB で作り直す(段 3b・別 DB)ための土台になる。netkeiba・JRA 公式の取得は触らない
(`HttpFetcher`/`JraFetcher` 不変・共有するものも無い=JRDB は会員 URL からのファイル取得で、スクレイピングではない)。

| モジュール | 役割 |
|---|---|
| `layout/` | 仕様書どおりの固定長レイアウト(BAC/KYI/UKC/KZA/CZA/TYB/HJC/SED/SRB)。行長は公開サンプルで検証 |
| `decode.py` | 行の切り出し(終端 CRLF/LF・桁ずれは `rejected`)と行→値オブジェクト(`models.py`)。プロファイルで別定義列を落とす |
| `ids.py` | レースキー 8 文字→12 桁 `race_id`、血統登録番号 8→10 桁 `horse_id`(決定論・`id_mappings` 不要) |
| `persons.py` | KZA/CZA × JRA 公式プロフィール → 騎手/調教師の `id_mappings(source=jrdb)`(名前だけの一致は書かない) |
| `profile.py` | 書込み先プロファイル `current` / `jrdb_canonical`(別定義 4 列は canonical だけ・FR-021a) |
| `files.py` | ファイルの同一性と版(`jrdb_files`): 同 sha は 1 行・自動の新旧は 速報→正式 と 日次→バックナンバー だけ・他は `held` |
| `held.py` | `held` の表示(差分)と運用者承認(現行版を `superseded`・writer を再実行) |
| `fetch.py` / `archive.py` / `blackout.py` | 会員 URL(平文で持たない)・zip/LZH 展開・開催日 08:00〜19:00 の過去データ取得拒否 |
| `policy.py` | 起動時検証(`jrdb_enabled`・認証 env・間隔・プロファイル束縛)。1 件も送る前に落ちる |
| `upsert_jrdb.py` | 薄い書込み層。races/race_horses/horses/race_results/race_laps/払戻を共通 writer と `precedence.py` 経由で書く |
| `parity.py` | 段 1: 列ごとの意味・数値の一致率(読み取り専用・`artifacts/jrdb/parity/<year>.json`) |
| `repair.py` / `rebuild.py` | 段 3a: 現行 DB の 1 列修復(allowlist・parity 合格が実行時ゲート)/ 段 3b: 別 DB の再構築 |
| `availability.py` | 来歴 job → `summary.files[]` → その値がいつ使えるようになったか(data-model §3a) |
| `cli.py` / `cli_write.py` | `scrape jrdb-*` サブコマンド(下表) |

契約: `specs/178-jrdb-primary-source/contracts/`(`files-and-ids.md` / `precedence-jrdb.md` / `cli.md` / `parity.md`)。
運用: `specs/178-jrdb-primary-source/manual.md`。wrapper: `scripts/jrdb_ops.sh`。

## CLI

| コマンド | 入力 | 書くもの |
|---|---|---|
| `jrdb-fetch --date D [--kinds …]` / `--archive --from Y --to Y` | 会員 URL | `artifacts/jrdb/<kind>/<yyyymmdd>/*.txt` + `jrdb_files(fetched)` |
| `jrdb-fetch --show-held [--date D]` | DB・artifacts(読取) | なし(held 行と現行版の差分を表示) |
| `jrdb-apply-held FILE_ID --profile P [--yes --replaces ID]` | `jrdb_files` | `--yes --replaces <current>` で現行版 `superseded`・held を再適用(writer 経由) |
| `jrdb-parity --year Y --files-dir ABS` | ファイル+DB(読取) | `artifacts/jrdb/parity/<Y>.json` |
| `jrdb-persons --files-dir ABS [--dry-run]` | KZA/CZA | `id_mappings(source=jrdb)` |
| `jrdb-entries` / `jrdb-prerace` / `jrdb-results` / `jrdb-payouts --date D --profile P` | 展開済みテキスト | 製品表(+来歴) |
| `jrdb-repair --column C --from --to [--dry-run]` | アーカイブ+parity 報告 | 段 3a(値が変わる行だけ) |
| `jrdb-rebuild --from --to --db-url URL --reference-db-url URL` | アーカイブ | 段 3b の別 DB |
| `jrdb-audit --date D` | DB(読取) | なし(来歴・`jrdb_files`・artifacts の整合+段の状態) |

`--profile` に既定は無い。`jrdb_canonical` を本番 `DATABASE_URL` に向けると起動検査で止まる。

## 禁止事項

1. **別定義の 4 列(`first_3f`・`corner_orders`・`running_style`・`sire_line/damsire_line`)を現行 DB に書かない**(FR-021a)。
   JRDB の定義は JRA-VAN/netkeiba と違う(前 3F は 1200m 以外で別定義・通過順は 4 枠詰め・脚質は語彙・系統は体系)。
   `profile.check_payload` が `current` で拒否する。`jrdb_canonical`(別 DB)だけが JRDB の定義で持つ。
2. **TYB・基準オッズを単複オッズの現在値に書かない**。JRDB は市場値の供給元ではない(T−3 の取得元は JRA 公式か netkeiba)。
3. **会員物を repo に置かない**: 配信ファイル・会員 URL(`source_url_sha256` だけ)・認証情報(`config/.env.jrdb`・gitignore)・
   ファイル由来の値。fixture は公開サンプル(`tests/fixtures/jrdb/public/`・`jrdb.com/program/data.html`)だけ(会員規約 第 5 条)。
4. **規則を複製しない**: 衝突検査・遷移表・先着保持・払戻の集合置換・オッズの採否の正本は `horseracing_scrape/precedence.py`。
   `upsert_jrdb.py` は共通 writer を呼ぶだけで、JRDB の都合で規則を分岐させない(176 と同じ)。
5. **開催日 08:00〜19:00 は過去データ(年度パック・バックナンバー)を取りに行かない**(`blackout.py`・policy `jrdb_archive_blackout`)。
6. **速報版 SED は入力にしない**(正式版=木曜だけ)。`held` は運用者が差分を読んで承認するまで書かない。

## ファイル配置

```
artifacts/jrdb/<KIND>/<yyyymmdd>/<KIND>yymmdd.txt        展開済み(gitignore)。同名の別版は .<sha12>.txt
artifacts/jrdb/parity/<year>.json                          段 1 の報告(既存パスは拒否)
artifacts/jrdb/repair/ ・ artifacts/jrdb/rebuild/          段 3a/3b の報告
artifacts/jrdb/probe/<D>.jsonl ・ artifacts/jrdb/trial/<D>.jsonl   段 0 診断・段 3.5 隔離試験(live 側・append-only)
config/.env.jrdb                                           JRDB_MEMBER_USER / JRDB_MEMBER_PASSWORD(gitignore)
config/source-operations.json                              policy 6 キー(jrdb_enabled / netkeiba_enabled / …)
```
