# Research: 特徴量 materialization 基盤 (025)

codex second opinion を踏まえた技術判断。各項 Decision / Rationale / Alternatives。

## R1: パリティを bit 一致で保証（P0）
- **Decision**: materialize 経路と in-memory 経路の `build_feature_matrix` 出力を `pandas.testing.assert_frame_equal(check_exact=True, check_dtype=True)` で **ALL_COLUMNS 列順込み完全一致**。parquet スキーマ契約 = 列順固定・明示 dtype（float64 保持、nullable は null のまま 0 にしない、ID non-null）・`(race_id, horse_id)` 決定論ソート。round-trip テストに null/疎・同日・pace・static を含む。
- **Rationale**: FEATURE_VERSION 据え置きで採用済みモデル入力を不変に保つには、数値許容差＝入力の静かな再定義。bit 一致を release gate にするしかない。
- **Alternatives**: 許容差比較（却下: モデル入力が変わる）。full-matrix materialize（static 含む）→ serving 新規レースの static は当該レース由来で parquet 非カバー、かつ as-of 限定の方がスコープ明確 → 却下、as-of のみ。

## R2: staleness 検知＝source fingerprint + fail-closed（P0）
- **Decision**: manifest に「特徴計算に使う races/race_horses/race_results の射影カラム」を決定論ハッシュした **source fingerprint** を保存。build 時に「要求 (race_id,horse_id) 網羅 + fingerprint 一致 + manifest version 一致 + 最新 ingest race_date」を検査。不一致/未カバーは **fail-closed**（training/eval/serving ともエラー）。fallback は parquet カバー外の**未来レースのみ**＋ audit warning。
- **Rationale**: データ範囲・行数だけでは「範囲内の行変更・後 backfill」を見逃す（silent stale = EV/Kelly 歪み）。fingerprint で内容変化を捕捉。
- **Alternatives**: 既定 fallback（却下: 黙って古い/再計算が紛れ、materialize の意味が曖昧）。日付/行数のみ検査（却下: backfill を見逃す）。

## R3: 生成・fallback・parity の単一実装（P0）
- **Decision**: as-of 計算は既存ブロック関数 `build_history_features`/`build_extra_features`/`build_human_form_features`/`build_pace_features` を**唯一の源**とする。materialize 生成・serving 新規レース fallback・パリティ比較はすべてこれを呼ぶ。materialize 対象列は `registry`（FEATURE_GROUPS + history 由来）から**機械導出**し、static/current-race 列を除外。「同一合成 target race で generator==fallback」契約テスト。
- **Rationale**: 035/036 の片側判断ミスと同型＝定義の二重化はリーク/不整合の温床。構造的に単一実装を強制する。
- **Alternatives**: 生成と fallback を別実装（却下: ドリフト）。

## R4: 末尾非依存性と backfill 無効化（P0）
- **Decision**: materialize 対象は「pool-end 非依存（cutoff 不変）」をテストで確認した as-of 特徴に限る。human_form（daily cumsum−当日）・pace（過去レース内相対）は末尾非依存で安全。**既存行の変更/backfill は source fingerprint 変化で parquet を必ず無効化**。materialize 後に target/同日/未来の結果を変更しても当該 target 特徴が不変であることを leak test で保証。
- **Rationale**: 「過去行は後からデータ追加で変化しない」前提が崩れるのは result-time backfill 経路。fingerprint で確実に無効化。
- **Alternatives**: 追加=即無効化せず差分更新（却下: 複雑・リーク検知漏れ）。

## R5: スコープ＝全 as-of 一括だが read は opt-in 段階有効化（P1）
- **Decision**: 生成は全 as-of group を materialize するが、**read 経路は既定 off（opt-in フラグ／有効 group リスト）**。parity/leak テスト全合格まで本番デフォルトにしない。group 単位で parity を確認しながら有効化。026 血統も本基盤の形で載せる（別キャッシュを作らない）。
- **Rationale**: 全 as-of の blast radius（history/human_form/pace 同時）を、opt-in と group 別 parity で制御。
- **Alternatives**: 血統だけ先行 materialize・既存は据え置き（却下: 一時キャッシュ乱立・後の一般化困難）。read 即デフォルト ON（却下: 未検証で採用済みモデルに影響しうる）。

## R6: 既存資産の再利用
- 既存ブロック関数（history/extra/human_form/pace）= 単一 as-of 源。`loader.load_frames`（射影カラム＝fingerprint 対象）。`schema.ALL_COLUMNS`（列順）。`registry.FEATURE_GROUPS`（materialize 列の機械導出）。
- 既存 `cli.build-features --out`（full-matrix dump）は本 feature の as-of-only materialize + read/manifest/fingerprint へ発展的に置換／別サブコマンド化。
- parquet I/O は既存 `to_parquet` 実績（pyarrow）。
