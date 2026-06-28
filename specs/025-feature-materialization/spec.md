# Feature Specification: 特徴量 materialization 基盤 (Feature Materialization / Parquet Feature Store)

**Feature Branch**: `025-feature-materialization`

**Created**: 2026-06-28

**Status**: Draft

**Input**: 重い as-of/過去由来特徴の計算を「生成フェーズ」に分離し parquet に materialize、学習/評価/serving はそれを read する。新 signal なし・採用済みモデル出力不変（パリティ）・スキーマ変更なし。

## 背景 (Why)

現在 `build_feature_matrix` は全レース/出走/結果を pandas にロードし、as-of/過去由来の集計（history・020 recent_form/aptitude/class_transition・020 human_form・023 pace）を predictor インスタンスごとに毎回 in-memory で再計算する。20 年規模（2007–現在、約 62k races/883k entries/94k horses）で feature-eval（候補+baseline）や ablation（group 数+1）がこれを繰り返すと、メモリと再計算コストが嵩む。次の Feature 026（血統適性特徴）は種牡馬→産駒多数の重い cross 集計を伴い in-memory 再計算ではスケールしない。本 feature は **infra のみ**：重い as-of 特徴の生成を 1 回だけ実行して parquet に materialize し、消費側は read する基盤を導入する（026 が載る土台）。**新しい予測特徴は足さず、採用済みモデルの出力を一切変えない。**

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 生成フェーズ（parquet materialize）(Priority: P1)

データ担当者が CLI を 1 回実行すると、全プール（2007〜データ通過日）の as-of/過去由来特徴が per-(race_id, horse_id) で `artifacts/features.parquet` に書き出され、manifest（データ範囲・行数・FEATURE_VERSION・決定論ハッシュ・生成時刻）が付随する。

**Why this priority**: 基盤の中核。生成物が無いと read 経路も 026 も成立しない。MVP。

**Independent Test**: 生成 CLI を実データ/合成データで実行 → parquet + manifest が出力され、同一データで 2 回実行すると bit 一致（決定論）。manifest にデータ範囲・行数・version・ハッシュが記録される。

**Acceptance Scenarios**:

1. **Given** ingest 済みプール, **When** 生成 CLI を実行, **Then** per-(race_id, horse_id) の as-of 特徴 parquet と manifest が出力される。
2. **Given** 同一データ, **When** 生成を 2 回実行, **Then** parquet 内容（値・行集合）と manifest ハッシュが一致（決定論）。

---

### User Story 2 - builder の read+merge とパリティ (Priority: P1)

学習/評価で `build_feature_matrix` を呼ぶと、materialize 済み parquet を read+merge し、static/current-race 特徴は従来どおり計算して固定スキーマに整える。得られる行列は **materialize 前の in-memory 計算と bit 一致**する。

**Why this priority**: 「速くするが出力は不変」を保証する核心。パリティが崩れると採用済みモデルが変わる（憲法 III/V 違反）。MVP。

**Independent Test**: 同一データで (a) materialize→read 経路と (b) 現行 in-memory 経路の `build_feature_matrix` 出力を比較し、全特徴列が bit 一致。parquet 欠落/カバレッジ不足時は黙って古い値を使わず fail-closed もしくは fallback 計算する。

**Acceptance Scenarios**:

1. **Given** materialize 済み parquet, **When** build_feature_matrix を呼ぶ, **Then** 出力行列は in-memory 経路と全列 bit 一致（FEATURE_VERSION 据え置き）。
2. **Given** parquet が対象レースをカバーしていない（古い/欠落）, **When** build_feature_matrix を呼ぶ, **Then** 黙って古い値を使わず、fail-closed か当該分の fallback 計算で正しい値を返す。
3. **Given** materialize 経由で学習した予測, **When** in-memory 経由の予測と比較, **Then** 予測（win/top2/top3）が一致。

---

### User Story 3 - serving 新規レースの fallback (Priority: P2)

未来（未確定）レースを serving する際、parquet は履歴のみで当該レースを含まないため、新規レース分だけ単一レースの as-of 計算で補完し、訓練と同一定義の特徴を得る。

**Why this priority**: materialize の盲点（履歴のみ）を塞ぎ、live serving を壊さない。

**Independent Test**: parquet に無い新規レースを serving し、その特徴が生成フェーズと同一ロジック（同一値）で補完されることを確認。

**Acceptance Scenarios**:

1. **Given** 履歴のみの parquet と新規レース, **When** 当該レースを serving, **Then** その馬の as-of 特徴が単一レース fallback 計算で生成フェーズと同値になる。

---

### Edge Cases

- parquet が存在しない（初回）→ fail-closed か全件 fallback（黙って欠損列で予測しない）。
- ingest 後・生成前（staleness）→ カバレッジ検査で検知し古い値を使わない。
- parquet が破損/dtype 不整合 → fail-closed（誤値で続行しない）。
- 新規（未来）レースが parquet 非カバー → 単一レース fallback。
- 生成対象が空（データなし）→ 空 parquet + manifest（エラーにしない）か明示エラー（計画で確定）。
- 浮動小数の parquet round-trip → dtype/precision を保持し bit 一致を壊さない。

## Requirements *(mandatory)*

### Functional Requirements

**US1 — 生成フェーズ**
- **FR-001**: システムは CLI で全プール（INGEST_SCOPE_START 以降〜データ通過日）の as-of/過去由来特徴を per-(race_id, horse_id) で parquet（`artifacts/` 配下、.gitignore 済み）に materialize しなければならない。
- **FR-002**: materialize 対象は過去由来 as-of 特徴のみ（history 群・020 recent_form/aptitude/class_transition・020 human_form・023 pace_time/position_style）。**static/current-race 特徴（race 条件・age/sex/frame/horse_number/jockey_id/trainer_id/weight/weight_diff/field_size）は対象外**で builder が従来計算する。
- **FR-003**（codex P0）: 生成は manifest を併記しなければならない: データ範囲（開始/通過日）、行数、FEATURE_VERSION、決定論ハッシュ、生成時刻、**および入力ソース fingerprint**＝特徴計算に使う races/race_horses/race_results の射影カラムのハッシュ。範囲・行数だけでは「範囲内の行変更・後 backfill」を検知できないため fingerprint を必須とする。
- **FR-004**: 生成は決定論でなければならない（同一データで 2 回実行すると parquet 値・行集合・manifest ハッシュが一致）。
- **FR-005**: 再生成は手動 CLI（ingest 後にオペレータ実行）とし、DB テーブルを新設してはならない（憲法 VI、parquet は artifacts キャッシュ）。

**US2 — read+merge とパリティ**
- **FR-006**: `build_feature_matrix`/`assemble_feature_matrix` は materialize 済み parquet を (race_id, horse_id) で read+merge し、static/current-race 特徴を計算して固定スキーマ（ALL_COLUMNS）に整えなければならない。
- **FR-007**（最重要・非交渉, codex P0）: materialize 経路の出力行列は現行 in-memory 計算の出力と **完全一致**でなければならない（数値許容差なし＝`check_exact`/`check_dtype`/**ALL_COLUMNS 列順**込みで一致）。parquet スキーマ契約として 列順固定・明示 dtype（float64 保持・null は 0 でなく null・ID は non-null）・`(race_id, horse_id)` 決定論ソートを定め、round-trip テストに null/疎行・同日行・pace 列・static 系を含める。許容差比較は診断専用に留め、bit 一致を release gate にする。
- **FR-008**: 出力が同一であるため **FEATURE_VERSION は据え置き**（出力同一＝採用済みモデル予測は不変）。materialize 導入で 020/023 等の特徴値・モデル予測が変わらないことをパリティ回帰テストで保証しなければならない。
- **FR-009**（codex P0, 既定 fail-closed に決定）: parquet が無い／古い（manifest の **source fingerprint 不一致**）／対象レース未カバーの場合、**黙って古い値・欠損で続行してはならない**。**既定は fail-closed**（training/eval/serving とも明示エラー）。fallback 計算は **parquet カバー外の未来レースに限り**許可し audit warning を出す。カバレッジ検査は「要求キー + race_date + manifest version + source fingerprint + 行数 + 最新 ingest race_date」で行う（fingerprint なしの範囲/行数だけに依存しない）。

**US3 — serving fallback**
- **FR-010**: parquet 非カバーの新規（未来）レースは、生成フェーズと同一ロジックの単一レース as-of 計算で補完し、生成フェーズと同値の特徴を返さなければならない。

**横断（憲法整合）**
- **FR-011**: 生成フェーズは strict-before（対象レースより前）・同日除外・跨馬統計の対象行+同日除外（020 human_form 機構）を保持しなければならない。per-row の as-of 値はプール末尾非依存で、materialize はリーク境界を新たに広げてはならない（憲法 II）。
- **FR-012**: 市場オッズ・今走の結果は特徴にしない（既存方針、leak-guard）。
- **FR-013**: 確率整合性（009 win→joint）は不変（本 feature は計算経路のみ変更、出力同一）。専用 assert は設けず、**予測一致テスト（win/top2/top3, T012）で透過的に担保**する（analyze G2）。
- **FR-014**: DB スキーマ変更を行わない（migration head 不変、新 ORM テーブルなし）。
- **FR-015**: parquet は非コミット（artifacts）だが DB から決定論再生成可能とし、manifest で再現性・staleness 検知を支えなければならない（憲法 V）。
- **FR-016**: 生成は 1 回・消費は read という分離で、性能予算（生成時間・メモリ上限・eval 反復 read コスト）を計画で定義し実データで確認しなければならない。
- **FR-017**（codex P0, 二重ロジック禁止）: 生成フェーズ・serving fallback・パリティ比較は**同一の重特徴 builder 関数を呼ぶ単一実装**でなければならない（as-of ルールを別実装しない＝035/036 型の定義ズレ防止）。materialize 対象列は registry の group メタから**機械的に**決定し、static/current-race 列が除外されていることをテストで証明する。「同一合成 target race で generator 出力 == fallback 出力」契約テストを必須とする。
- **FR-018**（codex P0, backfill 無効化 + 末尾非依存）: 「新行の追加」と「既存行の変更/backfill」を区別し、**後者は source fingerprint で parquet を必ず無効化**する。materialize 後に target/同日/未来レースの結果を変更しても当該 target の特徴が変化しないこと（リークテスト）を保証する。materialize 対象に加える as-of 特徴は「pool-end 非依存（cutoff 不変）」をテストで宣言・確認したものに限る（**現行 materialize 集合の不変性は T016 で証明、cutoff 不変 eligibility は将来 as-of 特徴を足す際の手順**, analyze G3）。
- **FR-019**（codex P1, 段階有効化／analyze G1 明確化）: read 経路は parity/leak テスト全合格まで **opt-in**（単一フラグ `use_materialized`、既定無効）とする。**group 単位の検証は parity テストを column group ごとにパラメータ化して確認する rollout 規律**で担保し、全 group の bit パリティが緑になってからフラグを有効化する（runtime の per-group 部分切替トグルは作らない＝本番は全 as-of 一括 materialize か in-memory のどちらか）。別の一時キャッシュ概念を乱立させず、Feature 026（血統）も本基盤の形に載せる。

### Key Entities *(include if feature involves data)*

- **materialized 特徴 parquet**: per-(race_id, horse_id) の as-of/過去由来特徴列。`artifacts/features.parquet`（非コミット）。
- **manifest**: データ範囲・行数・FEATURE_VERSION・決定論ハッシュ・生成時刻（再現性/staleness 検知）。
- **生成器（features 内）**: 既存 history/extra/human_form/pace の as-of 計算を 1 回パスで実行し parquet 化。builder の read 経路と計算ロジックを共有。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**（パリティ）: materialize 経路と in-memory 経路の build_feature_matrix 出力が全特徴列 bit 一致（差分 0）。
- **SC-002**: materialize 経由と in-memory 経由で学習したモデルの予測（win/top2/top3）が一致。
- **SC-003**: 生成は決定論（2 回実行で parquet 値・manifest ハッシュ一致）。
- **SC-004**: parquet 欠落/カバレッジ不足で「黙って古い値・欠損」が 0 件（fail-closed か fallback で正値）。
- **SC-005**: serving 新規レースの特徴が生成フェーズと同値（fallback 計算）。
- **SC-006**: DB migration head 不変・新 ORM テーブル 0・FEATURE_VERSION 不変。
- **SC-007**: 性能予算を満たす（生成 1 回で完了し、eval 反復は read で済む。具体値は計画で設定し実データで確認）。
- **SC-008**（codex Q2/Q4）: ソース fingerprint で staleness を検知＝範囲内の行変更/backfill 後に build が **fail-closed**（黙って古い値 0 件）。materialize 後に結果を変更しても当該特徴は不変（リーク不在）。
- **SC-009**（codex Q3）: 同一合成 target race で **generator 出力 == fallback 出力**（単一実装の契約テスト合格）。materialize 対象列に static/current-race 列が 0 件。
- **SC-010**（codex Q5）: read 経路は単一 opt-in フラグ（既定無効）で、**parity テストが column group ごとに合格**（全 group bit 一致）してからフラグを有効化できる。runtime per-group トグルは設けない。

## Assumptions

- materialize 形式は parquet（artifacts 配下、.gitignore 済み）。DB feature_snapshots は当面見送り（deferred）。
- カバレッジ不足/古い parquet の既定挙動は **fail-closed に決定**（codex P0）。fallback は parquet カバー外の未来レースに限定。
- 生成・read の as-of 計算ロジックは現行 features 実装（history/extra/human_form/pace）を共有・再利用し、二重定義を避ける。
- 性能予算の具体値（生成時間・メモリ）は実データ（horseracing DB, [[local-db-setup]]）で計画時に設定。

## codex レビュー所見 (top risks folded)

infra spec として codex second opinion を実施（CLAUDE.md 方針）。反映:
- **Q1 (P0) パリティ**: 数値許容差なしの完全一致（check_exact/check_dtype/列順）+ parquet スキーマ契約（dtype/null/ソート）→ FR-007。
- **Q2 (P0) staleness**: 既定 **fail-closed** + manifest に **source fingerprint**（範囲/行数では範囲内 backfill を見逃す）、fallback は未来レース限定 → FR-003/009。
- **Q3 (P0) 二重ロジック**: 生成/fallback/parity が単一実装、materialize 列は registry から機械決定、generator==fallback 契約テスト → FR-017。
- **Q4 (P0) 末尾非依存/backfill**: 既存行変更は fingerprint で無効化、cutoff 不変テスト合格特徴のみ materialize、materialize 後の結果変更で特徴不変 → FR-018。
- **Q5 (P1) スコープ**: read 経路 opt-in + group 単位で parity 確認しながら有効化（blast radius 制御）→ FR-019。
- **クロスカット top3**: silent stale parquet / generator-fallback ドリフト / parity 許容差への妥協 — いずれも release gate（fingerprint・単一実装・bit 一致）で封じる。

## Scope (Out / Deferred)

- DB の feature_snapshots テーブル化（永続・監査強化が本当に要るときの将来）
- 自動再生成スケジュール（P2 自動運用）
- 血統適性特徴そのもの（Feature 026 で本基盤に載せる）
- static/current-race 特徴の materialize（安価なので対象外）
- parquet partitioning / 形式最適化
