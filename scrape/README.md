# horseracing-scrape

netkeiba の **出馬表 / 前売りオッズ / 結果** を行儀よく取得・パースし、既存 core テーブルへ取り込むパッケージ。
`db` にパス依存。**スキーマ変更なし**。これにより Feature 006 serving が未来レースを予測でき、007 betting が
実オッズで EV を出せる。

## 礼儀(robots / レート / ToS)

`HttpFetcher` が **robots.txt 遵守・ドメイン毎の最小間隔・ローカルファイルキャッシュ・明示 User-Agent・指数
バックオフ**を担う。**個人利用前提**で商用再配布はしない。取得不可パスは取得しない。実 netkeiba へのアクセスは
手動 CLI 実行時のみ(**テストはネットワーク非依存**: HTML フィクスチャ + モック fetcher)。

## 設計の要点(憲法 I/II の核)

- **ID は id_mappings 経由のみ**(`idmap.resolve_entity`): マッピング済み=JRA-VAN `canonical_id`、未対応=一意の
  名前空間付き代替 ID **`nk:{netkeiba_id}`**(JRA-VAN ID と非衝突・netkeiba ID ごとに一意)+ `id_mappings` に
  UNMAPPED 行を積む。**名前+生年などで推測結合しない**。`horses` のみ `data_source='netkeiba'`。
- **未マッピング馬は debut/Unknown**: `nk:{id}` には過去 race が無いため features は career_starts=0=debut として
  leak-safe に扱う。一意なので他馬の履歴は混入しない。
- **偽 race_id を作らない**(`venues.build_race_id`): netkeiba 開催場→JRA-VAN VV 対応 + `is_valid_race_id`。構成
  不能/2007未満は None → **行を作らず skip**。
- **結果は insert-only**(`backfill_results`): `ON CONFLICT (race_id,horse_id) DO NOTHING`。既存 JRA-VAN 行を一切
  上書きしない。非出走馬に結果行を作らない。finished は finish_order 必須(制約整合)。同着は finish_order 共有。
- **前売りオッズは結果未確定レースのみ**(`update_odds`): 対象 race に `race_results` があれば skip(JRA-VAN 最終
  オッズ保護)。欠損/不正オッズは更新しない。最新値上書き + updated_at(スナップショット履歴なし)。
- **idempotent + 監査**: PK upsert でべき等。各実行を `ingestion_jobs`(source='netkeiba'/job_type/scope/counts/
  status/parser_version)に記録。致命的例外は **status=FAILED** を必ず記録(running に残さない)。
- オッズはモデル特徴に使わない(betting のみ。リーク境界は 005/006/007 で担保)。

### 既知の限界(将来)

未マッピング(`nk:{id}`)が後で canonical にマッピングされた場合、既存 `nk:` 行を canonical に **再キー化する
マイグレーション**が別途必要(本フィーチャーは UNMAPPED キューに積むまで)。複勝/馬連/三連複・推定オッズ・JS 動的
オッズ・地方/海外・id_mappings 自動解決は対象外。

## CLI

```bash
cd scrape
uv sync
export DATABASE_URL=postgresql+psycopg://...
# netkeiba ページ URL を指定(JRA-VAN race_id はページ内容から構成)
uv run python -m horseracing_scrape scrape-entries --url <netkeiba 出馬表 URL>
uv run python -m horseracing_scrape scrape-odds    --url <netkeiba オッズ URL>
uv run python -m horseracing_scrape scrape-results --url <netkeiba 結果 URL>
```

## テスト

```bash
cd scrape
uv run pytest tests/unit      # パーサ(フィクスチャ)・race_id 構成・代替 ID・礼儀(robots/レート/バックオフ/キャッシュ)
uv run pytest -m integration  # 実 DB で upsert/ID マッピング/debut/odds 保護/insert-only/idempotent
```

最重要テスト: `test_entries.py`(mapped/nk: + UNMAPPED キュー)、`test_unmapped_debut.py`(debut leak-safe)、
`test_odds.py`(最終オッズ保護)、`test_results.py`(insert-only)。
