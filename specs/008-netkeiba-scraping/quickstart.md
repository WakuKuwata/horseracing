# Quickstart: netkeiba 取り込みの検証

実装後に「出馬表→serving」「オッズ/結果 backfill」「ID マッピング安全性」が動くことを確認する手順。

## 前提

- Feature 001 適用 + 002 取込済み + 005 active モデル + 006 serving の PostgreSQL。
- Docker(testcontainers 用)。
- `scrape/` の依存をインストール(`uv sync`、db にパス依存、httpx/selectolax)。

## セットアップ

```bash
cd scrape
uv sync
export DATABASE_URL=postgresql+psycopg://...
```

## 取り込み(ローカル実行)

```bash
# 出馬表(未来レース)→ races/race_horses/horses/...(ID は id_mappings 経由)
uv run python -m horseracing_scrape scrape-entries --race-id 202506010101
uv run python -m horseracing_scrape scrape-entries --date 2025-06-01
# 前売りオッズ(結果未確定レースのみ更新)
uv run python -m horseracing_scrape scrape-odds --race-id 202506010101
# 結果 backfill(JRA-VAN 未取得分のみ、insert-only)
uv run python -m horseracing_scrape scrape-results --race-id 202506010101
```

期待: 行儀よく取得(robots/レート/キャッシュ)。出馬表が取り込まれ、未マッピング netkeiba ID は `nk:{id}` 代替で
保存され `id_mappings` に UNMAPPED として積まれる。各実行が `ingestion_jobs` に監査記録。

## end-to-end(出馬表→serving)

```bash
# 取り込んだ未来レースを Feature 006 で予測
cd ../serving
uv run python -m horseracing_serving predict --race-id 202506010101
```

期待: 未マッピング馬は debut/Unknown 特徴で leak-safe に予測される(SC-008)。

## テスト

Docker 必須(testcontainers)。**ネットワーク非依存**(HTML フィクスチャ + モック fetcher)。

```bash
cd scrape
uv run pytest tests/unit      # パーサ(フィクスチャ)・race_id 構成・代替 ID・odds 保護・insert-only(合成)
uv run pytest -m integration  # 実 DB で upsert/ID マッピング/backfill/監査/idempotency
```

検証する受け入れ基準:

- **SC-001/002 (ID)**: マッピング済み=canonical_id、未対応=一意 `nk:{id}` + UNMAPPED キュー。未マッピング馬が debut で
  他馬履歴を混入しない。
- **SC-003 (race_id)**: 構成不能レースは行を作らず通知(偽 ID なし)。
- **SC-004 (odds)**: 前売りオッズは結果未確定レースのみ更新、結果確定済みの最終オッズは不変。
- **SC-005 (results)**: netkeiba 結果が既存 JRA-VAN 行を変更しない(insert-only)、欠損のみ補完。
- **SC-006 (監査)**: idempotent + ingestion_jobs 記録。
- **SC-007 (パーサ)**: HTML フィクスチャでネットワーク非依存、必須欠損で fail-close。
- **SC-008 (e2e)**: 取り込んだ未来レースを serving が予測できる。

## 礼儀(robots/レート/ToS)

robots.txt を遵守し、ドメイン毎の最小間隔・ローカルキャッシュで負荷を最小化する。個人利用前提で商用再配布はしない。
取得不可パスは取得しない。実 netkeiba アクセスは手動 CLI 実行時のみ(テストはフィクスチャ)。
