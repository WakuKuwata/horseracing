# Research: Entity Identity Resolution & Split Repair (067)

Phase 0。実 DB 調査(2026-07-12, ローカル horseracing DB)に基づく決定事項。すべて「結果を見て変えた閾値」ではなく、データ構造(採番・名前スキーム)の事実に基づく。

## R1. repair 方式 — 物理 re-key vs 仮想統合

**Decision**: 物理 re-key(サロゲート行の horse_id/jockey_id/trainer_id を canonical へ UPDATE)。

**Rationale**:
- features(history/pace/pedigree/relative_ability/rating…)・serving・api はすべて生ID文字列で group/merge_asof し `id_mappings` を辿らない(Explore で確認)。仮想統合はこれら**全 read 経路の改修**が必要で、1 箇所の漏れが silent 不整合になる。
- 物理 re-key なら下流は無改修で自然統合。
- 実 DB で安全性を確認済み:
  - netkeiba 個体が出走する 2,658 レースすべてが純 netkeiba(同一レースに jra_van 個体の混在 0)。
  - `nk:{id}→{id}` re-key での race_horses PK(race_id, horse_id)衝突 0 件。

**Alternatives considered**: 仮想統合(read 時に id_mappings で canonicalize)。可逆で非破壊だが改修面が広く保守的でない。**却下**。ただし衝突ガードは物理方式でも防御的に必須(将来データで衝突が出たら skip+報告)。

## R2. identity 照合規則 — entity 別(馬 exact / 騎手・調教師 prefix)

**Decision**:
- **馬**: `nk:` の数字部分 == 既存 `horses.horse_id` かつ NFKC 正規化後の馬名 exact 一致 → `mapped`。生年一致も要求し、不一致は `conflict`。
- **騎手/調教師**: `nk:` の数字部分 == 既存 `*_id`(JRA 免許番号)を主根拠。名前は netkeiba 短縮形+見習いマーカーのため、**先頭マーカー(△▲☆★◇◆*)除去 + NFKC 後に双方向 prefix 一致**(短い方が長い方の先頭一致)を裏取りとする。prefix 不一致は `conflict`。

**Rationale(実測)**:
- 馬: 番号一致 5,977 ペア全件で馬名 exact 一致、名前不一致 0、生年不一致は 2 のみ → exact が airtight。
- 騎手: 番号一致 163 のうち exact 名一致は 15 のみ。netkeiba は「江田照(↔江田照男)」「戸崎圭(↔戸崎圭太)」「△長浜(見習い斤量マーカー)」等の短縮表記。マーカー除去+prefix で **152/163** 一致。残り 11 は「石神道↔石神深道」「鮫島駿↔鮫島克駿」等の略記スキーム差(同一人物だが機械照合不能)や JV 側も 4 文字切詰(マーカン↔マーカンド)→ **conflict(手動)**。
- 調教師: 同様に prefix で **198/207**、残り 9 は conflict。
- 番号(免許番号/血統登録番号)は JRA 公式ID採番であり netkeiba も同一コードを用いる=**構造的同一**。名前照合はあくまで誤結合防止の裏取り。これは憲法 I の禁じる「名前だけ/番号だけの推測結合」ではない。

**Alternatives considered**:
- 番号のみで結合(名前照合なし): 速いが誤結合検出できず憲法 I に抵触。**却下**。
- 騎手/調教師を exact 名一致に限定: 15/163 しか救えず大半が分裂したまま。**却下**。
- 騎手/調教師を本 feature から除外(馬のみ): 単純だが human_form/target-encoding の劣化を残す。**却下**(prefix 裏取りで安全に取り込める)。

## R3. re-key と regenerate の対象分類

**Decision**:
- **re-key(値は正しい source-of-truth)**: `race_horses`, `race_results`(実測 nk: 行=37,334 / 37,003)。
- **re-key してから値を再生成(derived, 値は stale)**: `race_predictions`, `feature_snapshots`(各 72,998)。FK 整合のため一旦 re-key、その後 predict-backfill --force / materialize で正しい値に上書き。
- **再生成のみ(ID を JSON 内に保持し列 re-key 不可)**: `recommendations.selection`。該当レース(2025H2+/2026)の recommend-backfill で整合。
- **孤児マスタ削除**: `horses`/`jockeys`/`trainers` の解決済みサロゲート行(canonical が既存)。FK 参照を全て re-key/削除した後に削除。

**Rationale**: source-of-truth と derived を分け、derived は「一旦 FK 整合の re-key → 正値で再生成」。予測値は 2026 レースの他馬も分裂の影響を受けるため per-horse 削除でなく**該当日付範囲を predict-backfill --force で丸ごと再計算**する(044 経路)。

**FK 順序**: 各サロゲート S→canonical C につき (1) race_horses, race_results, race_predictions, feature_snapshots を S→C へ re-key(各 (race_id, C[, model]) 非存在をアサート=衝突 skip) → (2) horses/jockeys/trainers の S 行削除。全体を per-entity トランザクションで冪等化(S 行が既に無ければ no-op)。

## R4. parity / 版管理 — FEATURE_VERSION bump 不要

**Decision**: FEATURE_VERSION は bump しない。feature_hash 不変で採用済み lgbm-061(features-016)の serving 互換維持。

**Rationale**:
- 特徴の**計算ロジック**は不変。変わるのは**入力データの ID キー**のみ=データ修復。
- as-of は strictly-before。統合で canonical へ加わる netkeiba 走は 2025H2+/2026(後方)なので、**pre-2025 レースの as-of 集約には入らない → pre-2025 特徴はバイト不変**。血統自馬除外の二重計上も pre-2025 では発生しない(nk: 走が pre-2025 の sire 累積に入らないため)。
- 変わるのは統合走を過去に持つ直近レース(2025H2+/2026)の特徴値=**修正が目的**。
- 現行モデルの学習窓は pre-2025 に収まるため再学習不要。

**検証ゲート(III の代替)**: (a) pre-2025 の全特徴列が統合前後で `assert_frame_equal(check_exact=True)`(features/test_repair_parity)。(b) サヴォーナ型 fixture で直近レースの `prior_starts`/history 系が 0→実キャリア反映。

## R5. ingest-time identity(出血停止, FR-006/007)

**Decision**: `resolve_entity` に、mapped 行が無い場合の**サロゲート発行前 identity 照合**を追加。候補名(+ 馬は生年)と canonical マスタ行を照合し、R2 規則で一致なら mapped 行を insert して canonical を返す。不一致/対応なしは従来どおりサロゲート + unmapped。

**Rationale**: JRA-VAN フィード停止で 2026 は 100% netkeiba。2024 以前に存在する canonical は既に DB にあるので、scrape 時に照合すれば新規分裂を作らない。真の新規個体(2026 デビュー)は canonical 不在でサロゲート=正しい。

**Risk/緩和**: resolve_entity のシグネチャに候補名(+生年)追加が必要。upsert 側は scrape 済みの名前を持つため供給可能。照合対象テーブル(horses/jockeys/trainers)への read が resolve 内に増える=冪等・副作用なし。

## R6. codex second opinion(品質ゲート)

**Status**: **取得済み**(2026-07-12)。当初 Codex CLI 0.142.5 が既定モデル `gpt-5.6-sol` 非対応で失敗 → `volta install @openai/codex@latest`(0.144.1)で復旧 → read-only sandbox で成果物 + 実コードを読ませてレビュー取得。物理 re-key の採用は妥当と確認された一方、**実コードを読んだ結果 P0 の契約不整合を複数指摘**(全採用)。

### 採用した指摘(実コード読解による是正)

- **[C1] 派生「再生成」の誤解(P0)**: `predict-backfill --force` は上書きでなく**新 run 追加**(append-only, pipeline.py)。`features materialize` は parquet を書くだけで **DB `feature_snapshots` を更新しない**(cli.py)。`recommend-backfill` も新規行のみ=旧買い目 `selection.horse_id` が `nk:` のまま残り、backtest.py が JSON horse_id を結果に直接照合するため不一致。→ **是正**: 派生表は FK 整合のため re-key、旧 run/snapshot は legacy 監査、新 force run が最新=API 正値。**recommendations.selection JSON は物理 canonicalize**(R3/data-model)。
- **[C2] 衝突ガードの実キー誤り(P0)**: `race_predictions`/`feature_snapshots` の実 PK は共に `(prediction_run_id, horse_id)`。設計の `(race_id, prediction_run_id, C)`/`(race_id, feature_version, C)` は誤り。→ **是正**(data-model §3)。
- **[C3] 血統 ID 参照の漏れ(P0)**: `horses.sire_id/dam_id/damsire_id` は生 ID 文字列で `nk:` が入りうる(upsert.py:261)。サロゲート馬削除で論理 dangling。属性がサロゲート側のみにある場合の情報損失。→ **是正**: 血統 ID 3 列も re-key 対象、削除前ゲート「canonical 欠損・surrogate 有値の列=0」(R3)。
- **[C4] ingest 照合入力が現行経路に無い(P0)**: entries は名前+**年齢**、results は ID のみ、血統親は ID のみ。「名前+生年を渡せば後方互換」は不成立。→ **是正**: identity 情報は optional・不足時は自動昇格せず insufficient_evidence(unmapped)。entries は年齢→生年導出規則を明文化。result-only/血統親は既存 mapped のみ(R5/data-model)。
- **[C5] SC-001 件数矛盾**: 規則上の自動 mapped は馬 5,975/騎手 152/調教師 198(残りは conflict)。→ **是正**: SC-001 を自動 mapped 件数へ、手動残を分離(spec)。
- **[C6] トランザクション粒度**: 「衝突行だけ skip して他表更新」は部分統合 + 削除時 FK 違反。→ **是正**: 1 サロゲート→1 canonical **ペア単位の原子トランザクション**(全表衝突検査→1件でも衝突ならペア skip→全 re-key→残参照0確認→削除→commit)(R7/data-model)。
- **[C7] TOCTOU/writer 競合**: repair 中は writer(ingest/predict/profile-completion)停止 or advisory lock、regen 完了まで cutover 制御。開始日は固定でなく**実 re-key 最古 race_date** から算出。→ **是正**(quickstart/data-model)。
- **[C8] 監査弱**: RepairReport 非永続。→ **是正**: `ingestion_jobs.summary`(job_type=repair_splits)に repair run 情報を永続化(スキーマ変更なし, 憲法 V)(data-model §5)。
- **状態線引き**: 番号一致 + 情報「欠損」は conflict でなく insufficient_evidence(unmapped)。「欠損≠矛盾」。conflict/rejected は sticky。→ **是正**(state 遷移図)。
- **騎手/調教師 prefix**: 初回 backfill は blanket 自動承認でなく **dry-run 候補一覧を operator 承認**してから(番号再利用・外国人短期免許・同姓 prefix の別人リスク)。→ **是正**(承認ゲート)。

### codex が妥当と確認した点
- 物理 re-key vs 仮想統合の判断 = 妥当。
- 馬の「構造的 ID 一致 + NFKC 名 exact + 生年一致」= 十分保守的(false positive 低)。
- as-of 波及範囲「開始日以降の全レース再計算」= 妥当(騎手/調教師 form・within-race LOO・血統 self-exclusion が後続に波及)。
- pre-2025 バイト不変 = **canonical 属性を fill-null しない条件下で**正しい(→ 削除前ゲートで属性 merge を禁止)。FEATURE_VERSION bump 不要・再学習不要もこの条件付きで妥当。

## R7. re-key トランザクション粒度(codex#6)

**Decision**: **1 サロゲート S → 1 canonical C を 1 トランザクション**。手順は data-model §3 の原子手順。部分統合・巨大ロックを避け、失敗時はペア単位で rollback/再開。

## R8. 監査永続化(codex#8)

**Decision**: repair run を `ingestion_jobs`(既存, job_type=`repair_splits`)に記録し、`summary` に mapping 集合ハッシュ・affected_from・件数・衝突・ツール版を残す。スキーマ変更なしで再現性・監査(憲法 V)を満たす。
