# Research: 実 netkeiba パーサ (022)

Phase 0 調査。spec の決定事項を裏付け、未確定点を解消する。

## R1. 取得方式 (FR-013) — ハイブリッド

**Decision**: 出走表 (entries) と結果 (results) は **netkeiba のサーバ描画 HTML を静的取得して解析**。単勝オッズ (odds) は **netkeiba の内部 JSON データ** を利用。headless ブラウザは導入しない。

**Rationale / Evidence**:
- 実測 (2026-06-28, 1 回限り取得): `https://race.netkeiba.com/race/shutuba.html?race_id=202406050911` は HTTP 200・約 280KB の**静的 HTML** に `Shutuba_Table` / `HorseList` / `HorseName` / `/horse/{netkeiba_horse_id}` リンクを含む。→ 出走表は静的解析可能。
- netkeiba race_id は URL クエリ `race_id=YYYYVVKKDDRR` で、JRA-VAN race_id と**恒等**（probe で同一 ID がそのレースを返した。`venues.py` の会場コード恒等マップとも整合）。
- 結果ページ `race/result.html?race_id=...` も同系のサーバ描画 HTML（実装時に実サンプルで確認）。
- 単勝オッズはページ上で JS により後挿入されるのが通例で、静的 HTML に数値が出ない。netkeiba は odds 用の JSON データ取得経路を持つ（実装の最初のタスクで実エンドポイントとレスポンス形を確認・固定する）。

**Alternatives considered**:
- 全部 headless (Playwright): entries/results が静的で取れる以上、依存・CI・実行時間・礼儀コストに見合わない → 不採用。
- 全部静的 httpx: odds が JS 描画で取れない → 単勝オッズ (P3) が成立しない → 不採用。

**Open (実装最初のタスクで確定)**: odds JSON の実エンドポイント URL・パラメータ・レスポンススキーマ。変化に脆いので parser_version で版管理し、欠損時 fail-close。

## R2. 既存スタブの扱い (FR-012) — 置換

**Decision**: `parse/entries.py` / `odds.py` / `results.py` の中身を実 netkeiba 解析へ**置換**。実パーサ単一経路。合成フィクスチャ依存の既存テストは実 HTML/JSON フィクスチャベースへ更新。

**Rationale**: 並存は二重管理を生み、合成スキーマが残るとまた「動くふりのスタブ」を温存する。置換で単一経路にし、実フィクスチャでテストする方が明快。`exotic_odds.py` は本 feature の対象外（次段）なので今回は触らない（合成のまま据え置き、ただし実害は P1-P3 経路に無い）。

**Alternatives**: 別モジュール新設で並存 → 移行が長引き混乱、却下。

## R3. parse↔upsert の安定契約 — dataclass 維持

**Decision**: `models.py` の dataclass (`ScrapedEntry`/`ScrapedRace`/`ScrapedEntryHorse`、`ScrapedOdds`/`ScrapedOddsRow`、`ScrapedResult`/`ScrapedResultRow`) を parse と upsert の境界契約として**維持**し、parse 関数の内部実装だけ差し替える。`upsert.py` / `idmap.py` / `venues.py` / `pipeline.py` は無改修。

**Rationale**: 取得・ID 解決・DB 書き込み・保護ルール（INSERT-only 結果、result-pending のみ odds 上書き）は本物として検証済み。境界 dataclass を保てば変更面が parse 層に閉じ、リーク境界・単一最新値などの不変条件を既存のまま維持できる。

## R4. パーサ実装技術 — bs4 + lxml（新依存なし）

**Decision**: 既存の BeautifulSoup + lxml を使用し、CSS セレクタ + 限定的な正規表現で実 markup（`Shutuba_Table` の行、`/horse/{id}` からの ID 抽出、性齢・斤量の正規化等）から抽出。odds JSON は標準 `json` で解析。

**Rationale**: 既存 `parse/_common.py` が bs4/lxml 前提。新依存（selectolax 等）を足す利得は小さい。

## R5. 実 HTML フィクスチャの作り方

**Decision**: 1 回限りの取得スクリプト（polite: robots/1秒間隔/UA、既存 `HttpFetcher` 流用）で entries/results の HTML と odds JSON を保存 → 容量・PII（広告・無関係スクリプト等）をトリム → `scrape/tests/fixtures/` の合成 HTML を置換。構造変化 fail-close テストは、保存実 HTML から必須要素を欠落させた改変版で作る。

**Rationale**: 「ネットワーク非依存テスト維持（FR-010/SC-006）」と「実構造追従」を、保存した実サンプルで両立。取得は各ページ種別につき最小件数。

**Privacy/ToS**: 公開されている JRA 公式レース情報のみ。ログイン/有料ページは対象外。robots/レート遵守、個人利用前提（FR-011）。

## R6. race_id → netkeiba URL 構築

**Decision**: JRA-VAN race_id（`YYYYVVKKDDRR`）から netkeiba URL を構築するヘルパを追加（race_id 恒等 + 各ページ URL テンプレート）。

**憲法 I との整理**: 「ID は `id_mappings` 経由のみ・推測結合禁止」は**馬/騎手/調教師エンティティ**の話。race_id の URL 構築は、対象レースの取得先 URL を決める操作であり、エンティティの guess-join ではない。取り込み時の race_id は従来どおり `build_race_id`（検証付き）で確定し、無効なら行を書かない。→ 抵触しない（plan で codex に確認・記録）。

## R7. 憲法ゲート要点（plan Constitution Check で詳述）

- **II リーク境界**: odds/結果は取り込み専用、モデル特徴量に再投入しない（leak-guard テスト維持）。本 feature は特徴量を追加・変更しない。
- **V 単一最新値**: odds は最新値上書き・`updated_at` のみ（スナップショット禁止）。`upsert.update_odds` の既存挙動を維持。
- **I データ契約**: race_id 12桁・2007+、netkeiba エンティティは id_mappings 経由・未マップ surrogate。
- **III 評価先行**: 本 feature はモデル/特徴量を変更しないため walk-forward 採用ゲートの対象外（データ取得経路の修正）。該当 N/A を明記。

## R8. codex second opinion

plan 段で `codex:codex-rescue` に上記設計（5 案）をレビュー依頼済み。両案差分と採用根拠は本 plan の「Codex Second Opinion」節に記録する（憲法 品質ゲート）。
