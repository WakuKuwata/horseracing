# Quickstart: 178 の検証手順(段ごと)

前提: 別 worktree で実装。本番 DB には段 2 以降まで書かない。会員認証は env だけ(ログ・repo に出さない)。

## 段 0(現構成の診断・2 開催日)
```
live source-probe --date 2026-11-14 --mode official-odds      # 公式 T−3 オッズの取得時刻と成否(netkeiba 不使用)
live source-probe --date 2026-11-14 --mode dependencies       # 来歴・data_source・id_mappings から netkeiba 依存の棚卸し
live source-probe --date 2026-11-14 --mode arrivals           # 会員 URL の HEAD を 10 分おき(08:00〜19:00 は昨年以前を触らない)
```
期待: `artifacts/jrdb/probe/2026-11-14.jsonl` にレースごと 1 行。SC-001。

## 段 1(parity・READ ONLY)
```
scrape jrdb-fetch --archive --from 2008-01-01 --to 2008-12-31 --kinds BAC KYH UKC SED SRB HJC KZA CZA   # 週中のみ
scrape jrdb-parity --year 2008 --files-dir /abs/artifacts/jrdb
```
期待: `columns.first_3f.by_segment` で 1200m だけ rate 1.0、他は不合格。`counts.races.jrdb >= db`。SC-002。
公開サンプル(SED080913 等)だけで同じ CLI が動くこと=ログイン前の smoke。

## 段 2(writer・本番 DB の複製)
```
scrape jrdb-persons --files-dir … --dry-run → 報告 → --dry-run なしで id_mappings
DATABASE_URL=<複製> scrape jrdb-entries --date 2026-11-01 && scrape jra-entries 2026-11-01 && scrape scrape-entries …(netkeiba)
DATABASE_URL=<複製> scrape jrdb-results --date 2026-11-01 && scrape jra-results 2026-11-01
# 順序を変えて再生(公式→JRDB→公式再取込)し、決定論項目の最終状態が同じ・保護対象不変・2 回目変更 0
```
期待: `static_mismatch=0`・新規 `nk:` 行 0・SC-003/004。

## 段 3a(修復)
```
scrape jrdb-repair --column odds --from 2025-01-01 --to 2026-11-30 --dry-run
```
期待: 基準合格列のみ受理(`first_3f` を渡すと allowlist で拒否)。139 と同じ波及測定の記録。SC-005/006。

## 段 3b(再構築・別 DB)
```
createdb horseracing_jrdb && alembic upgrade head(DATABASE_URL=…_jrdb)
scrape jrdb-rebuild --from 1999-01-01 --to 2026-11-30 --db-url postgresql+psycopg://…/horseracing_jrdb
```
期待: `artifacts/jrdb/rebuild/compare-<year>.json` で各年 races/entries/results が現行 DB 以上。SC-009。

## 段 3.5(運用成立試験・隔離環境・2 開催日)
```
live source-trial --date 2026-11-21 --db-url <複製・netkeiba 行を含まない> --files-dir …
```
期待: 全レースで T−3 記録・結果・確定オッズ・払戻が揃う。JRDB 当日ファイルを 1 日分遅らせても緊急モードで復旧。SC-001a。

## 段 4
policy `netkeiba_enabled: false` → 開催日 1 日。netkeiba への要求 0 件(ingestion_jobs で確認)。SC-007。
