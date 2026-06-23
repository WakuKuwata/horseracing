<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan:
`specs/008-netkeiba-scraping/plan.md` (active feature: netkeiba スクレイピング取り込み).
Stack: Python 3.12, PostgreSQL 16, SQLAlchemy 2.0, Alembic, psycopg3, pytest + testcontainers; numpy/scikit-learn/pandas/lightgbm for ML; httpx + selectolax/bs4 for scraping.
Packages: `db/`, `ingest/`, `eval/`, `features/`, `training/`, `serving/`, `betting/`, `scrape/` (`horseracing-scrape`).
Scrape: polite netkeiba fetch (robots/rate-limit/cache/UA/backoff) of entries+odds+results into existing core tables; netkeiba IDs map to JRA-VAN via id_mappings only (no guess-join) — mapped→canonical_id, unmapped→unique `nk:{id}` surrogate + UNMAPPED queue (debut/leak-safe); future race_id must be a valid JRA-VAN 12-digit or no row written (no fake IDs); results backfill is INSERT-ONLY (never overwrite JRA-VAN); pre-race odds overwrite ONLY result-pending races (protect JRA-VAN final odds); idempotent + ingestion_jobs audit; parsers tested on saved HTML fixtures (network-free). No schema change. Odds never a model feature. 2007+.
<!-- SPECKIT END -->

## Codex agent の使用方針

**Proactively use codex** — 以下のトリガーに該当したら、ユーザーが明示的に頼まなくても
default で codex 系を起動する。「念のため」ではなく該当時は並走が既定動作。

### 起動トリガー（1 つでも該当したら使う）

**ユーザー発話キーワード**（second opinion 系）:
「設計どう思う」「2 案出して」「これで合ってる？」「比較して」「レビューして」
「アーキ」「方針」「相談」「迷ってる」「どっち」「妥当？」
→ `codex:codex-rescue` agent を Agent tool で並走させ意見を取る

**作業内容**:
- 新規 spec / plan / 設計ドキュメントを書く / 更新する時 → second opinion を必ず取る
- 新規モジュール・新規サービス・新規 API endpoint の設計 → 実装案を並走生成し比較
- 複数ファイル横断のリファクタ・移行・スキーマ変更 → アプローチを並走比較
- ML パイプライン / 学習ロジック / 特徴量設計の変更 → 独立検証（過去 035/036 で
  片側判断の校正ミスあり、[[pedigree-embedding-036-result]] 参照）
- 同一バグで 1 回直して再発、または root cause が読めない → `/codex:rescue` で diagnosis

**スタック時の追加ルール**: 「もう一回試す」前に必ず codex を呼ぶ。再試行 2 回目は禁止。

### 使わない場面（狭めに定義）
- 1 ファイル内の rename / typo / import 修正
- 既に固まった方針の機械的な反映
- ユーザーが明示的に「Claude だけで」と指示

### 実行ルール
- 並走時は Agent tool を単一メッセージから並列発火（Claude 自身の作業と同時進行）
- 初回は `/codex:setup` で CLI 稼働を確認
- codex 結果は鵜呑みにせず Claude 案と突き合わせ、相違があればユーザーに提示
- 起動を見送った場合、応答の冒頭で「codex 不要と判断した理由」を 1 行宣言する
  （これが最大の効き目 — 見送り判断が可視化される）
