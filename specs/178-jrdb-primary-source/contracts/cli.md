# Contract: CLI(`scrape jrdb-*`・`live source-probe`)

共通の起動時検証(176 `jra/policy.py` と同型・1 件も送る前に落ちる): policy に `jrdb_enabled: true`、env `JRDB_MEMBER_USER`/`JRDB_MEMBER_PASSWORD`
(無ければ `credentials_missing`・ログに出さない)、`HORSERACING_JRDB_MIN_INTERVAL_S`(既定 5 秒・下限 2 秒)、アーカイブ取得は
開催日 08:00〜19:00 JST を拒否(`archive_blackout`・`--force-blackout` は無い)。

| コマンド | 入力 | 書くもの | 冪等性 |
|---|---|---|---|
| `jrdb-fetch --date D [--kinds …] [--archive --from --to]` | 会員 URL | `artifacts/jrdb/<kind>/<yyyymmdd>/*.txt`(展開済み)+`jrdb_files(status=fetched)` | 同 sha は再 DL しない(HEAD で Last-Modified/size を見て同じなら skip) |
| `jrdb-fetch --show-held [--date D]` | DB+artifacts(読取) | なし(`held` 行ごとに現行版との行差分: 片側だけの行・変わった項目。要求 0 件・job 行なし) | — |
| `jrdb-apply-held FILE_ID --profile P [--yes --replaces CURRENT_ID\|none]` | `jrdb_files`+展開済みテキスト | `--yes` 無し: 差分表示のみ(exit 2・書込み 0)。差分が不完全(本文欠落・INV-F1/F3 違反・decode 失敗)・`--replaces` が現行版と違う・同じ鍵に承認済み未適用の版がある → exit 2。`--yes --replaces`: 現行版を `superseded`・held を `fetched` にして同じ日の writer(`COMMAND_FOR_KIND`: KYI/BAC/UKC→entries・TYB→prerace・SED/SRB→results・HJC→payouts)を再実行。先着保持の項目は規則どおり変わらない | 承認後は同 sha の通常取込と同じ |
| `jrdb-parity --year Y --files-dir ABS` | ファイル+DB(読取) | `artifacts/jrdb/parity/<Y>.json` | 既存パスは拒否 |
| `jrdb-persons-collect --kind jockey\|trainer --index-url U --index-cname C [--max-pages N]` | JRA 公式の名鑑一覧(運用者が公開サイトの遷移から URL/cname を渡す・cname は保存しない)+DB の 5 桁 ID | `artifacts/jrdb/persons/jra-profiles.jsonl`(append-only・既収集 ID は取らない) | 176 fetcher(共有スロット・10 秒)。拒否で停止=exit 1・再実行で続きから |
| `jrdb-persons --files-dir ABS [--dry-run]` | KZA/CZA | `id_mappings(source=jrdb)` mapped/conflict | 既存 mapped は触らない |
| `jrdb-entries --date D` | BAC+KYI+UKC | races/race_horses/horses(+来歴) | 同 sha は変更 0 |
| `jrdb-prerace --date D [--venue E|W|L]` | TYB | body_weight/entry_status(+来歴) | 同上 |
| `jrdb-results --date D` | SED 正式版+SRB | race_results/race_laps/確定オッズ(+来歴) | 同上。速報版は `fetched` で止める |
| `jrdb-payouts --date D` | HJC | official_win_payouts/exotic_odds(+来歴) | 同上 |
| `jrdb-repair --column C --from --to [--dry-run]` | アーカイブ+DB | 段 3a の修復(基準合格列のみ・列名は allowlist) | 値が変わる行だけ |
| `jrdb-rebuild --from --to --db-url URL` | アーカイブ | 段 3b の別 DB(entries→prerace→results→payouts の順) | 同上 |
| `jrdb-audit --date D` | DB(読取) | 来歴・`jrdb_files`・artifacts の整合報告 | — |
| `live source-probe --date D --mode official-odds|dependencies|arrivals` | 公式経路/DB/会員 URL(HEAD) | `artifacts/jrdb/probe/<D>.jsonl` | append-only |
| `live source-trial --date D --db-url URL --files-dir ABS` | 複製 DB+アーカイブ+公式経路 | `artifacts/jrdb/trial/<D>.jsonl` | append-only |

summary(ingestion_jobs)の共通キー: `files`, `rows_read`, `rows_rejected(桁ずれ)`, `races_written`, `static_mismatch`, `unresolved_person`,
`alias_*`(176 と同じ)、`stale_version`, `blackout_refused`。
