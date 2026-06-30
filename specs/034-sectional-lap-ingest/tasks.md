---
description: "Task list — 区間ラップ ingest (034)"
---

# Tasks: 区間ラップ ingest (Race-level Sectional Lap Ingest)

**Input**: [spec.md](spec.md)

## Phase 1: 調査
- [X] T001 実地調査: db.netkeiba race ページに `summary="ラップタイム"` 表(ラップ=200m毎、ペース末尾=テン3F-上がり3F)確認。race.netkeiba 結果ページにはラップ節なし。実 fixture 保存(db_race_202406050911.html)

## Phase 2: parser (US1)
- [X] T002 [US1] `scrape/src/horseracing_scrape/models.py`: `ScrapedLaps`(key/lap_times/pace_first_3f/pace_last_3f)
- [X] T003 [US1] `scrape/src/horseracing_scrape/parse/laps.py`: parse_laps(html, *, race_id) — ラップ表抽出、無ければ None
- [X] T004 [P] [US1] `scrape/tests/unit/test_parse_laps.py`: 実 fixture で 10 ラップ・テン/上がり3F・合計検証・ラップ無し→None

## Phase 3: スキーマ + upsert (US2)
- [X] T005 [US2] `db/migrations/versions/0007_race_laps.py` + `db/src/horseracing_db/models/market.py` RaceLaps + models __init__ export。migration head 0006→0007
- [X] T006 [US2] `scrape/src/horseracing_scrape/upsert.py`: upsert_laps(single-latest 上書き、レース行存在時のみ、空 skip)

## Phase 4: パイプライン + CLI (US3)
- [X] T007 [US3] `scrape/src/horseracing_scrape/urls.py`: race_db_url。`pipeline.py`: scrape_laps(fetch→parse→upsert+監査 job_type='race_laps')
- [X] T008 [US3] `scrape/src/horseracing_scrape/cli.py`: scrape-laps サブコマンド(--race-id / --from --to date-range backfill = race_laps 欠損レース)
- [X] T009 [P] [US3] `scrape/tests/integration/test_pipeline_laps.py`(testcontainer): 書込/冪等/skip

## Phase 5: 検証・横断
- [X] T010 実 DB に migration 適用、実 netkeiba で少数レース(25)end-to-end 取り込み確認(24 行書込、1 skip)
- [X] T011 scrape/db lint・unit/integration・db テスト緑
- [X] T012 [P] `CLAUDE.md` に 034 サマリ追記
- [X] T013 オペレータ向け: 全期間 backfill 手順(`scrape-laps --from 2007-01-01 --to 2024-12-31`)を quickstart/spec に記載 → Feature 035 で lap 特徴の OOS

## 注意
- lap データは結果由来 → モデル特徴にしない(035 で過去走 as-of のみ)。
- backfill 実行はオペレータ(~62k ページ、polite で十数時間)。本 feature は基盤+少数実証まで。
