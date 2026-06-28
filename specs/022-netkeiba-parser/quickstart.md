# Quickstart / Validation: 実 netkeiba パーサ (022)

実装が「実 netkeiba を解析して既存テーブルに取り込める」ことを end-to-end で確認する手順。実装コードは含めない（tasks.md / 実装フェーズ）。

## 前提

- worktree `022-netkeiba-parser`（main=020 ベース）。
- ローカル Postgres（`DATABASE_URL`、メモリ local-db-setup 参照）。マイグレーション適用済み（スキーマ変更なし）。
- ネットワーク非依存テストは保存フィクスチャで実行。1 回限りの実取得はフィクスチャ作成時のみ。

## 1. 実 HTML/JSON フィクスチャを 1 回だけ取得（polite）

```sh
# 実 netkeiba から entries/results/odds を 1 回取得し保存（robots/1秒間隔/UA 遵守）
uv run --directory scrape python -m horseracing_scrape capture-fixture \
  --race-id 202406050911 --kind entries --out scrape/tests/fixtures/real/
# kind を results / odds に変えて各 1 件（最小件数）
```
- 取得物は容量・無関係要素をトリムして `scrape/tests/fixtures/real/` に保存。
- 以後のテストはこの保存物に対してオフライン実行。

## 2. パーサ単体テスト（ネットワーク非依存）

```sh
uv run --directory scrape pytest tests/unit/test_parse_entries.py \
  tests/unit/test_parse_odds_results.py -q
```
**期待**: 実 HTML/JSON フィクスチャから ScrapedEntry/ScrapedResult/ScrapedOdds が正しいフィールドで生成。必須要素欠損の改変フィクスチャで `ParseError`（fail-close）。

## 3. 取り込み統合テスト（test DB / testcontainers）

```sh
uv run --directory scrape pytest tests/integration -q
```
**期待**:
- entries: 未来 race と全出走馬が `races`/`race_horses` に取り込まれ、マッピング済みは canonical_id、未マップは surrogate `nk:`＋UNMAPPED キュー。
- results: `race_results` に着順・状態・タイム。既存結果があれば上書きしない（INSERT-only）。
- odds: result-pending race の `race_horses.odds` が更新、結果のある race は更新されない。
- いずれも `ingestion_jobs` に summary（written/skipped/errors）が記録。

## 4. leak-guard

```sh
uv run --directory features pytest -k leak -q   # 既存 leak-guard 維持
```
**期待**: odds・結果由来の値がモデル特徴量に現れない（憲法 II）。

## 5. 実データ end-to-end（手動・任意）

```sh
# 1 で取得した実 race を CLI で取り込み → serving で予測まで通ることを確認
uv run --directory scrape python -m horseracing_scrape scrape-entries \
  --url "https://race.netkeiba.com/race/shutuba.html?race_id=<RID>"
uv run --directory serving python -m horseracing_serving predict --race-id <RID>
```
**期待 (SC-007)**: 実 netkeiba から取り込んだ出走表で特徴量生成→予測がエラーなく生成される。

## 受入チェック（spec SC 対応）

- [ ] SC-001 entries 取り込み（取りこぼし0/誤フィールド0、canonical/surrogate）
- [ ] SC-002 results 取り込み（INSERT-only 保護）
- [ ] SC-003 odds 更新（result-pending のみ）
- [ ] SC-004 fail-close（必須要素欠損で行を書かず errors 記録）
- [ ] SC-005 leak-guard（odds/結果が特徴量に出ない）
- [ ] SC-006 全パーサテストがネットワーク非依存
- [ ] SC-007 実データで予測 end-to-end 成立
