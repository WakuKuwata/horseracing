# Research: read-only 予測配信 API

憲法 VI（契約先行）/ II（リーク）/ V（監査）と codex second opinion（plan.md の表）を踏まえた設計判断。

## R1. 読み取り専用境界・依存境界（CRITICAL）

- **Decision**: `api/` は **`horseracing-db`（ORM 読み取り）+ `horseracing-probability`（純粋 009/010）のみ**に依存。**`horseracing-betting`
  には依存しない**。`/recommendations` は ORM の `Recommendation` を**直接 SELECT** で返し、`generate_exotic_recommendations`（INSERT/
  commit する書込）を import も呼出もしない。全ハンドラは SELECT のみ・commit しない。
- **Rationale**: codex CRITICAL — GET API が書込ジェネレータを呼ぶと副作用/データ汚染。betting 広域依存は書込経路露出リスク。read-only
  クエリ層に限定すれば書込が構造的に不可能。
- **Alternatives**: betting に依存して推奨を「無ければ生成」→ 書込発生・冪等性破壊。却下。

## R2. prediction_run の決定論選択（CRITICAL）

- **Decision**: `PredictionRun` を **`model_versions` に JOIN**（`PredictionRun.model_version == ModelVersion.model_version`。PredictionRun
  自体に adoption_status 列は無い）し、**(1) 採用モデル優先（`adoption_status='active'`）→ (2) `computed_at DESC` → (3) `prediction_run_id
  DESC` タイブレーク** で一意選択。選んだ `prediction_run_id`・`model_version`・`logic_version`・`computed_at` を応答に含める（監査再現）。
  race_date/post_time は使わず（nullable）、computed_at + run_id で全順序を保証。
- **Rationale**: codex CRITICAL — `PredictionRun` に current マーカが無く複数 run 可。決定論規則 + run_id 返却で再現可能。
- **Alternatives**: 最新 computed_at のみ → 同時刻タイで非決定。active 無視 → 古い/候補モデルを返す恐れ。却下。

## R3. 結合確率の制限と canonical 母集団（HIGH/IV）

- **Decision**: `/predictions` は per-horse win/top2/top3（軽量）を常に返す。**結合確率は `bet_type` 指定時のみ上位 K**（無指定で
  三連単等の大グリッドを返さない＝性能保護）。009 は **canonical 母集団**（取消・除外を除外 + 残存再正規化、011/009 と同規律）で適用し、
  結合確率の `logic_version` を含める。
- **Rationale**: codex HIGH — `joint_probabilities` は全組み合わせ materialize（三連単 ~ N(N−1)(N−2)）。bet_type + K で抑制。非出走馬
  混入を canonical で防ぐ。
- **Alternatives**: 常に全券種全組み合わせ → 大レスポンス/タイムアウト。却下。

## R4. オッズの実/推定判別・ラベル（HIGH/V/II）

- **Decision**: `/odds` は各行に **`odds_source`（real/estimated）・`is_estimated`・`coverage_scope`（exotic）・`updated_at`** を持つ
  判別スキーマ。単勝（real）/ 推定市場オッズ（010, estimated, 疑似）/ 実 exotic（012, real, coverage/updated_at）を**別 source・別
  フィールド**で返し混在させない。推奨は `is_estimated_odds`+`pseudo_odds`+`pseudo_roi`+`double_pseudo`（推定オッズ由来か）を露出。
  契約注記: これらの値は**モデル特徴に還流しない**。
- **Rationale**: codex HIGH — front が実/推定/疑似を誤認しないため。exotic_odds は最新値上書きなので updated_at を明示。
- **Alternatives**: 単一 `odds` フィールド → 実/推定混同。却下。

## R5. 監査・ラベルの全付与（HIGH/V）

- **Decision**: 予測 = run_id/model_version/logic_version/computed_at + 結合確率 logic_version。オッズ = source/estimated/coverage/
  updated_at。推奨 = 上記疑似ラベル群 + logic_version/computed_at。推定は**疑似**、exotic 推奨は**二重疑似**を明示。
- **Rationale**: 憲法 V。提示済み判断の事後検証。front の誤認防止。

## R6. エラーモデル（HIGH/MED）

- **Decision**: レース無し=**404**。レースはあるが予測/オッズ/推奨が無い=**200 + 型付き空セクション**（null ではない）。使用可能確率が
  無い結合確率算出（009 例外条件）・欠損オッズ（`market_implied_win_probs` 例外条件）=**型付き 409/422**（**500 にしない**）。不正
  race_id 形式=422。型付きエラー本体 `{status, code, detail}`。
- **Rationale**: codex HIGH/MED — 純粋ヘルパは空/非正入力で例外を投げるため、ハンドラで捕捉し型付き応答に変換。
- **Alternatives**: 例外を素通し → 500 連発で front が壊れる。却下。

## R7. ページング・版付け（MED）

- **Decision**: `/races` は **安定全順序** `race_date DESC NULLS LAST, venue_code NULLS LAST, race_number NULLS LAST, race_id`
  （nullable 列 + race_id タイブレークで全順序保証）+ offset/page ページング + **最大 page_size**（既定上限 200）+ `total`/`has_next`。
  **`total`/`has_next` はフィルタ（date/venue）適用後の COUNT** で算出（無フィルタ全件 COUNT は誤り）。全エンドポイント **`/api/v1/`** 前置、
  OpenAPI/`/docs`/`/openapi.json` 自動生成。
- **Rationale**: codex HIGH/MED — nullable 列で順序が揺れる/total がフィルタ前で誤るのを防ぐ。React 015 結合前に版固定。
- **Alternatives**: 無版・部分順序・全件 COUNT → 不安定/誤集計。却下。

## R8. ASGI セッション寿命（MED）

- **Decision**: FastAPI **lifespan** で app スコープ engine + sessionmaker（`db.session.create_db_engine`/`create_session_factory` 再利用）。
  依存 `get_session` が **per-request の読み取り専用 Session** を yield。**DB レベルで READ ONLY 化**（リクエスト開始時に
  `SET TRANSACTION READ ONLY` を実行 → 偶発書込を Postgres が物理的に拒否）し、finally で **rollback + close**（commit しない）。
  二重防御として T018 が AST/import-graph で書込 API・betting import を静的に禁止。
- **Rationale**: codex CRITICAL — rollback + 狭いソース走査だけでは flush/Core DML/raw SQL/別名/動的 import を取りこぼす。**DB READ ONLY
  トランザクション**が最強の保証（任意の書込を DB が拒否）、静的解析は補助。
- **Alternatives**: rollback のみ / grep のみ → 偶発書込・検出漏れ。却下。

## まとめ（設計判断 → 要件）

| 研究項目 | 対応 FR / SC |
|---|---|
| R1 read-only/依存境界 | FR-001 / FR-002 / FR-006 / SC-005 |
| R2 run 決定論選択 | FR-004 / SC-002 |
| R3 結合確率制限/canonical | FR-004 / FR-012 / SC-003 |
| R4 オッズ判別/ラベル | FR-005 / FR-007 / FR-008 / SC-004 |
| R5 監査 | FR-007 / SC-002 |
| R6 エラーモデル | FR-009 / SC-001 |
| R7 ページング・版 | FR-003 / FR-010 / SC-001 / SC-006 |
| R8 ASGI セッション | FR-011 |
