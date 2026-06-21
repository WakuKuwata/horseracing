# Research: Core DB スキーマと基盤テーブル契約

Phase 0 の調査結果。すべての NEEDS CLARIFICATION と技術選定を解決する。

## R1. データストアとマイグレーションツール

- **Decision**: PostgreSQL 16 + Alembic + SQLAlchemy 2.0 (Declarative typed models) + psycopg 3。
- **Rationale**: 参考実装 `aiuma` が同一スタックで、CHECK 制約・`ARRAY`・`Interval`・`Numeric`・
  正規表現 CHECK (`~`) を実利用している。パターンを最大限流用でき、移植コストが低い。本 feature の
  要件 (正規表現 CHECK、複合主キー、トリガ) はすべて Postgres で素直に表現できる。
- **Alternatives considered**:
  - SQLite: CHECK 正規表現・`ARRAY`・トリガの表現力が不足。学習データ規模 (~100 万行) でも将来不利。
  - 生 SQL マイグレーション: 型安全な ORM 契約 (下流が import するモデル) を失う。
  - SQLAlchemy autogenerate: CHECK / トリガ / 部分制約を取りこぼすため、`0001` は手書きとする。

## R2. アーキテクチャ配置 (サービス分割 vs 単一パッケージ)

- **Decision**: 単一の top-level 共有パッケージ `db/` (`horseracing-db`)。サービス分割しない。
- **Rationale**: 憲法「初期から独立サービスに分割することを強制しない」。aiuma の 3 サーバー分割は
  成熟後の構成で初回 DB 契約には過剰。DB は将来の api / training 双方が依存する共有契約なので、
  特定サーバー配下ではなく top-level に置くのが中立。codex second opinion も同結論。
- **Alternatives considered**:
  - `backend/db` 配下: backend 概念に紐づくが、training サーバーからの依存が backend 跨ぎになる。
  - aiuma 同様の 3 分割: 憲法違反かつ YAGNI。

## R3. 状態表現 (bool フラグ vs 状態列)

- **Decision**: `text + CHECK` の状態列。`race_horses.entry_status ∈ {started, cancelled, excluded}`、
  `race_results.result_status ∈ {finished, stopped, disqualified}`。同着は `finish_order` 共有で表現。
- **Rationale**: aiuma の `cancelled`/`disqualified` bool フラグでは「取消 vs 除外」「中止 vs 失格」の
  意味差が落ちる。spec は取消・除外=非出走、中止=出走非完走、失格=完走したが失格、を区別する契約を
  要求 (FR-011/FR-012)。状態列ならラベル導出で「完走前提集計から除外する対象」が明確になり、
  疑似着順への変換 (禁止) を構造的に避けられる。
- **Alternatives considered**:
  - bool フラグ複数: 状態の排他性を表現できず、不正な組合せ (started かつ cancelled) を許す。
  - Postgres ENUM: R4 参照。

## R4. 状態列の型 (Postgres ENUM vs text+CHECK)

- **Decision**: `text + CHECK`。
- **Rationale**: Alembic で Postgres ENUM は値追加・rename・rollback が重く (型の ALTER が必要)、
  初期 feature の変更耐性と相性が悪い。状態コードは確定済みでも、取込差異や監査ステータスは後続で
  増える可能性がある。CHECK は差替えマイグレーションで容易に拡張できる。aiuma も状態・種別を
  Text + CHECK で扱う。
- **Alternatives considered**:
  - Postgres ENUM: 型レベルで厳密だが拡張コスト高。
  - 制約なし text: 不正値混入を防げず、ラベル導出の前提が崩れる。

## R5. `updated_at` 自動更新

- **Decision**: DB トリガ (`BEFORE UPDATE` で `updated_at = now()`) を全テーブルに付与。
- **Rationale**: 憲法 V の監査列の信頼性を「書き手 (app / 手動 SQL / 別サービス) 非依存」にする。
  aiuma は `server_default now()` のみで UPDATE 時に追従しないため、本 feature では明示的にトリガを
  足す。
- **Alternatives considered**:
  - app レイヤで更新: 書き手ごとに漏れるリスク。複数サービスが書く前提では不適。
  - `server_default` のみ: INSERT 時のみ有効で UPDATE に追従しない。

## R6. 確率整合性の強制位置

- **Decision**: 行レベル整合 (`0 <= win <= top2 <= top3 <= 1`) は `race_predictions` の CHECK で DB 強制。
  レース内合計 (Σwin≈1, Σtop2≈2, Σtop3≈3) は行制約にできないため検証クエリ / 下流責務とする。
- **Rationale**: 憲法 IV を可能な範囲で DB に落とす。行 CHECK は安価で確実。レース横断の合計は
  許容誤差を伴うため、本 feature では検証クエリ (quickstart に記載) と下流の正規化責務に委ねる。
- **Alternatives considered**:
  - 合計も DB で強制: トリガ / 制約トリガが必要で複雑、許容誤差の扱いも DB 側に持ち込む難点。
  - 一切 DB 強制しない: 不正確率の混入を許し、憲法 IV を後段任せにする。

## R7. id_mappings の設計

- **Decision**: `(entity_type, source, source_id)` を一意キーに、`canonical_id` は nullable、
  `mapping_status ∈ {unmapped, mapped, conflict, rejected}`、`conflict_group_id`、`resolved_at`、
  `resolution_note` を持つ。
- **Rationale**: 憲法 I が JRA-VAN / netkeiba の横断利用を対応表経由に限定し推測結合を禁止。
  unique 制約だけでは「未対応」「衝突 (同一 source_id が複数 canonical に対応)」「保留」「却下」を
  表現できない。状態列で手動修正フローに乗せられる。aiuma に id_mappings は存在しないため新規設計。
- **Alternatives considered**:
  - canonical_id NOT NULL + unique のみ: 未対応・衝突を表現できない。
  - ソースごとの個別マッピング表: エンティティ種別 × ソースで表が増殖し冗長。

## R8. 2007 境界の強制位置

- **Decision**: 取込レイヤのバリデーションで強制。スキーマに `race_date` のハード CHECK を入れない。
  本 feature は再利用可能なバリデータ `is_in_scope(race_date) -> bool` (2007-01-01 以降) を
  `validation.py` で提供し、ユニットテストする (将来の取込 feature が import して使う)。
- **Rationale**: spec の確定事項 (FR-024)。境界は「取込ポリシー」であり、未来レース保持や将来の
  方針変更 (例: 過去分の ID 正規化後に 2006 以前を解禁) を DB 制約で妨げない。DB-only feature でも
  バリデータ + ユニットテストで受け入れ基準 (SC-006) を満たせる (codex 指摘 #2 への対応)。
- **Alternatives considered**:
  - `race_date >= 2007-01-01` の CHECK: ポリシーをスキーマに固定化し、未来レース / 方針変更を阻害。

## R9. ORM モデル定義の要否

- **Decision**: SQLAlchemy 2.0 Declarative の typed モデルを定義し、Alembic `0001` は手書き DDL。
  両者の一致をテストで担保。
- **Rationale**: 下流 feature (取込・serving) は型付きモデルを import して使う。モデルは「契約面」、
  マイグレーションは「デプロイ成果物」。CHECK / トリガを正確に制御するため migration は手書きにする。
- **Alternatives considered**:
  - migration のみ (モデルなし): 下流が生 SQL / 反射に依存し型安全を失う。
  - autogenerate 全面採用: CHECK / トリガ / 部分制約を取りこぼす。

## R10. テスト戦略

- **Decision**: pytest + testcontainers[postgres]。制約・トリガ・マイグレーション適用/ロールバックは
  使い捨て実 Postgres で検証。バリデータ (`validation.py`) はユニットで検証。
- **Rationale**: 正規表現 CHECK・複合主キー・トリガ・`ARRAY`/`Interval` は SQLite で再現できず、
  実 Postgres が必須。testcontainers でハーメティックに回せる。
- **Alternatives considered**:
  - SQLite インメモリ: Postgres 固有機能を検証できない。
  - 共有開発 DB: テスト間の汚染・並列実行で不安定。

## R11. docs の repo 同期 (codex 指摘)

- **Decision**: フォローアップとして Obsidian Vault の `horseracing/*.md` を repo の `docs/` に同期する
  ことを推奨 (本 feature の実装ブロッカーではない)。spec / FR は `docs/database.md` を source-of-truth
  として参照しており、repo 内に存在すべき。
- **Rationale**: spec が参照する正本が repo 外 (Vault) にあると、トレーサビリティとレビューが宙に浮く。
- **Alternatives considered**:
  - Vault 参照のまま: repo 単独で spec → 実装の追跡ができない。
