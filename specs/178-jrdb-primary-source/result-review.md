# 178 実装の結果記録(2026-11-09〜)

実装は `.claude/worktrees/178-jrdb-primary-source`(起点 main 86eeeafb)で行い、2026-11-09(月)に main へマージ(f1eebe8a・--no-ff・実装 5e541e9a)。
テスト・codex 採否・性能見積りは `research.md` D20〜D26 と `tasks.md`。以下は rollout(Phase 11)の記録。

## R2 本番反映(2026-11-09 月 10:08〜10:12 JST・非開催日)

| 手順 | 結果 |
|---|---|
| (1) 複製でリハーサル | `pg_dump -Fc`(25 秒・365 MB)→ `horseracing_r2_rehearsal` に restore(24 秒)→ `alembic upgrade 0025_jrdb_source` 1 秒で成功。`jrdb_files` 0 行・7 表の CHECK が `jrdb`/`jrdb_result` を含む名前で置換 |
| (2) 停止 | 実行中ジョブ 0(直近 24h: succeeded 972 / partial 151 / skipped 157)。`stack.sh stop` の後も **別セッションが 11/07 に起動した ops-api/worker(pidfile `logs/forecast-auto-*.pid`・launchd 配下に孤児化)が残っていた**ので SIGTERM で停止。subprocess・DB 接続とも 0 を確認 |
| (3) バックアップ→0025 | `artifacts/db_backups/horseracing-pre0025-final-20261109-101030.dump`(365 MB・gitignore)→ 本番に `alembic upgrade 0025_jrdb_source`(lock_timeout 5s・即時成功)→ `alembic_version = 0025_jrdb_source` |
| (4) コード切替 | main マージ済み(f1eebe8a→657fa8a3→e919e6ed)。policy は全フラグ既定(`jrdb_enabled false` / `netkeiba_enabled true` / `jra_emergency_entries false` / `near_post_source netkeiba`)=JRDB・緊急・停止の機能は登録だけで動かない |
| (5) 起動 | `stack.sh start api / ops-api / worker` → `GET /api/v1/health` が `alembic_current = alembic_head = 0025_jrdb_source`・`schema_in_sync true`、ops status `writes_accepted true`・`policy_mismatch false`、worker `io=2 cpu=3` で起動 |
| (6) netkeiba 経路の 1 開催日 | **未**(次の開催日 2026-11-15 で確認) |

復旧: JRDB 初回書込み前なのでコード巻き戻し+`alembic downgrade 0024_jra_track_conditions` が使える(`jrdb_files` 0 行・`jrdb` 行 0 のあいだ)。

## 認証情報と到達確認(2026-11-09)

`config/.env.jrdb`(600・gitignore)に会員 ID/パスワード。HEAD 2 件(BAC/KYI 261108)が HTTP 200・前日 15:40 JST 公開(research D16)。ダウンロードは未実施(R0a の規約確認の後に R1)。

## R2 後に見つけた欠陥 2 件(2026-11-09・同日修正)

- `scripts/jrdb_ops.sh` が呼ぶ `python -m horseracing_scrape.jrdb.cli` に `__main__` の入口が無く、**wrapper 経由では何も実行されず exit 0 で終わっていた**(統合テストは関数を直接呼ぶので緑のまま)。入口を追加し、本番 DB への `jrdb_ops.sh audit 2026-11-08` が 36 レース(全行 netkeiba・結果/確定オッズ/払戻 36/36・T−3 記録なし)を返すことを確認。
- tasks T037/R3 の `persons-collect` が wrapper にも CLI にも無かった(`jra/person_profile.collect_profiles` は関数だけ)。`jrdb-persons-collect --kind --index-url --index-cname [--max-pages]` を追加(176 の `validate_startup`+`JraFetcher` をそのまま使う・DB の 5 桁 ID だけを対象・jsonl は append-only・拒否で exit 1)。一覧ページの URL/cname は R3 当日に公開サイトの遷移から運用者が取る。

