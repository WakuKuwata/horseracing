# Research: netkeiba スクレイピング取り込み

Phase 0。NEEDS CLARIFICATION なし。codex の 5 BLOCKER を解消する設計判断を記録する。

## R1. ID 名前空間の分離(BLOCKER 解消)

- **Decision**: マッピング済みエンティティは **JRA-VAN canonical_id**(`id_mappings.canonical_id`)で core テーブル
  (horses/jockeys/trainers)に保存。未マッピングは **`nk:{netkeiba_id}`** という名前空間付き代替 ID(netkeiba ID ごとに
  一意、コロン区切りで JRA-VAN 数値 ID 空間と衝突しない)で保存し、`horses.data_source='netkeiba'` を記録。同時に
  `id_mappings`(entity_type, source='netkeiba', source_id=netkeiba_id, canonical_id=NULL, mapping_status='unmapped')を
  upsert。
- **Rationale**: codex BLOCKER — 生 netkeiba ID を canonical horse_id に入れると JRA-VAN 血統登録番号 PK 空間を汚染。
  `nk:` 接頭辞で衝突回避、netkeiba 馬ごとに一意なので**同一 Unknown 使い回しによる他馬履歴リークが起きない**。
- **Alternatives considered**: 単一 "UNKNOWN" ID → 全未マッピング馬が履歴共有=リーク(禁止)。名前+生年で推測結合 →
  憲法 I 違反。

## R2. 未マッピング馬の leak-safe 履歴(debut)

- **Decision**: 未マッピング馬(`nk:{id}`)は JRA-VAN 履歴に結合しない。`features` の history は当該 horse_id の過去 race
  のみを as-of 集計するため、過去 race の無い `nk:{id}` は **career_starts=0=debut/Unknown** になる(0 代入しない)。
- **Rationale**: codex 確認 — debut 条件は「有効 horse_id の career_starts==0」。代替 ID が一意なので他馬の成績は混入しない。
- **Alternatives considered**: 未マッピング馬を JRA-VAN 類似名馬に結合 → リーク(禁止)。

## R3. 未来 race_id の構成(BLOCKER 解消)

- **Decision**: netkeiba レースの 年/開催場/回/日/レース番号 から `venues.build_race_id` で `YYYYVVKKDDRR` を構成し、
  **`is_valid_race_id` を通る場合のみ** races/race_horses に行を作る。構成不能/不正なら**行を作らず**、ingestion_jobs に
  skip/未マッピング通知。netkeiba の race_id は JRA コース番号(01–10)を用いるため多くは JRA-VAN 互換だが、開催場コード
  対応表で明示変換し検証する。
- **Rationale**: codex BLOCKER — 偽の数値 race_id は validation を通っても衝突/不一致リスク。Feature 002 の
  `derive_race_id`(構成→`is_valid_race_id`→不能なら MappingError)と同じ規律。
- **Alternatives considered**: netkeiba race_id をそのまま採用 → コード体系差で誤結合の恐れ。明示変換 + 検証。

## R4. 結果 backfill は insert-only(BLOCKER 解消)

- **Decision**: 結果取り込みは `race_results` への **insert-only**(`ON CONFLICT (race_id, horse_id) DO NOTHING`)。
  既存行(JRA-VAN)は一切更新しない。非出走(取消・除外)馬には行を作らない。結果状態は finished/stopped/disqualified に
  対応付け、同着は finish_order 共有で表現(余分な状態を作らない)。
- **Rationale**: codex BLOCKER — 汎用 upsert は衝突時に全非 PK 列を更新し JRA-VAN を破壊。insert-only で authoritative
  ソースを保護し欠損のみ補完。
- **Alternatives considered**: source 列追加で優先判定 → スキーマ変更(本フィーチャー外)。insert-only で十分。

## R5. 前売りオッズは結果未確定レースのみ上書き(BLOCKER 解消)

- **Decision**: `scrape_odds` は対象レースに **race_results が存在しない**(結果未確定)場合のみ race_horses.odds を最新値で
  上書きし updated_at を進める。結果確定済みレースの odds(JRA-VAN 最終)は更新しない。欠損/不正オッズの馬は更新しない。
- **Rationale**: codex BLOCKER — netkeiba 前売りが JRA-VAN 最終オッズを無言上書きする。結果未確定限定で歴史データを保護。
  既存 `test_odds_overwrite` の最新値上書き方針(憲法 V)とも整合(未来レースのオッズ更新)。
- **Alternatives considered**: 常時上書き → 歴史データ破壊。常時禁止 → 未来レースに odds を入れられず betting 不能。

## R6. idempotency と監査(Feature 002 踏襲)

- **Decision**: core upsert は PK 競合 upsert でべき等(entries)。各取り込みは `ingestion_jobs`(source='netkeiba'、
  job_type=entries/odds/results、scope=race_id or date、counts、status=succeeded/partial/failed、parser_version)に記録。
  必須要素欠損・未対応は fail-close + errors 記録。
- **Rationale**: codex/憲法 V。Feature 002 の `ingestion_jobs` 作法を踏襲し再現・監査を担保。
- **Alternatives considered**: 監査なし → 憲法 V 違反。

## R7. 行儀のよいスクレイパとテスト

- **Decision**: `PoliteFetcher` が robots.txt 遵守(`urllib.robotparser`)・最小レート制限(ドメイン毎の最小間隔)・
  ローカルファイルキャッシュ・明示 User-Agent・指数バックオフを担う。パーサは **HTML 文字列 → dataclass** の純粋関数で、
  保存済み HTML フィクスチャで単体テスト(ネットワーク非依存)。pipeline テストは fetcher をモック。
- **Rationale**: 商用サイトへの負荷最小化(個人利用前提)。HTML 構造変化に fail-close。CI でネットワーク非依存。
- **Alternatives considered**: ライブ取得テスト → 不安定・非礼。フィクスチャ + モック。

## R8. パーサライブラリ

- **Decision**: `selectolax`(高速・軽量)を第一候補、入手性問題時は `beautifulsoup4`+`lxml`。HTML エンコーディング
  (netkeiba は EUC-JP/UTF-8 の可能性)を取得層で吸収して文字列をパーサに渡す。
- **Rationale**: パース速度と保守性。エンコーディングは fetch 層で正規化。
- **Alternatives considered**: 正規表現パース → 壊れやすい。DOM セレクタを使う。
