# Tasks: Entity Identity Resolution & Split Repair

**Input**: Design documents from `specs/067-entity-identity-resolution/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/cli-contracts.md, quickstart.md

**Tests**: 本リポジトリ憲法(品質ゲート)が leakage / parity / idempotency / 監査テストを必須とするため、テストタスクを含める。

**Organization**: user story ごとに独立実装・独立テスト可能な形で分割。US1(repair)= MVP。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 並列可(別ファイル・未完了依存なし)
- **[Story]**: US1 / US2 / US3

## Path Conventions

- 主変更: `scrape/src/horseracing_scrape/`(idmap.py, upsert.py, 新 identity.py / repair.py, cli.py)
- テスト: `scrape/tests/{unit,integration}/`, `features/tests/`
- 新規パッケージ・migration なし。既存 `db` 共有モデルを利用。

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: モジュール雛形と CLI サブコマンド枠(ロジックなし)

- [x] T001 Create module stubs `scrape/src/horseracing_scrape/identity.py` と `scrape/src/horseracing_scrape/repair.py`、テストディレクトリ `scrape/tests/unit/`・`scrape/tests/integration/` を用意し、`scrape/src/horseracing_scrape/cli.py` に `resolve-identities` / `repair-splits` サブコマンドの空枠(引数パースのみ)を登録する

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: identity 照合の純関数と DB 解決コア。US1/US2/US3 すべての前提。

**⚠️ CRITICAL**: このフェーズ完了まで各 user story 実装に入れない

- [x] T002 [P] Implement `normalize_name`(NFKC + strip)と `strip_markers`(先頭 `△▲☆★◇◆*` 除去 → normalize)を `scrape/src/horseracing_scrape/identity.py` に実装
- [x] T003 Implement `classify_identity(entity_type, source_id, candidate_name, candidate_birth_year, canonical_row) -> Resolution` を `scrape/src/horseracing_scrape/identity.py` に実装(馬=名前 exact + 生年一致→mapped / 生年・名前矛盾→conflict、騎手・調教師=マーカー除去後 双方向 prefix 一致→mapped / 不一致→conflict、candidate 情報欠損 or canonical_row=None→unmapped(insufficient)、副作用なし・決定論)
- [x] T004 [P] Unit tests `scrape/tests/unit/test_identity.py`(馬 exact→mapped / 馬 生年不一致→conflict / 馬 名前欠損→unmapped / 騎手 prefix 江田照↔江田照男→mapped / 騎手 略記差 石神道↔石神深道→conflict / canonical 不在→unmapped / マーカー除去 △長浜 / prefix 境界=空文字・1文字・先頭空白・複数マーカー・Unicode 空白)
- [x] T005 Implement `resolve_identities(session, *, entity, dry_run) -> dict` コアを `scrape/src/horseracing_scrape/repair.py` に実装(unmapped の id_mappings を走査し、`source_id==既存 canonical *_id` のマスタ行をロード、`classify_identity` を適用、mapped/conflict/insufficient を id_mappings に書込[canonical_id・resolved_at・resolution_note]、mapped/conflict/rejected は sticky=再評価しない、dry_run は DB 非変更、冪等)

**Checkpoint**: 照合基盤 + DB 解決コアが揃い、US1/US2/US3 に着手可能

---

## Phase 3: User Story 1 - 既存分裂の統合で予測の履歴切れを解消 (Priority: P1) 🎯 MVP

**Goal**: 解決済みサロゲートを canonical へ物理 re-key し、直近レース予測が正しい過去走履歴で計算されるようにする。

**Independent Test**: サヴォーナ型 fixture(過去17走 canonical・直近2走サロゲート + mapped 行)に repair を実行し、統合後に直近レースの as-of 履歴が復活・サロゲートマスタ消滅・単一IDに統合、を確認。

- [x] T006 [US1] Implement `repair_splits(session, *, entity, dry_run, limit)` コアを `scrape/src/horseracing_scrape/repair.py` に実装: **1 サロゲート→1 canonical をペア単位の原子トランザクション**で処理(手順=全対象表の衝突を先に検査[実 PK: race_horses/race_results=`(race_id,C)`、race_predictions/feature_snapshots=`(prediction_run_id,C)`]→1件でも衝突ならペア全体 skip→re-key[race_horses の horse_id/jockey_id/trainer_id、race_results.horse_id、race_predictions.horse_id、feature_snapshots.horse_id、horses.sire_id/dam_id/damsire_id、recommendations.selection JSON 内 horse_id]→残存 S 参照ゼロ確認→削除前ゲート[canonical 欠損・surrogate 有値の属性列=0]→マスタ孤児削除→commit)、冪等(S 行なしなら no-op)、`--limit` 分割、`affected_from`(実 re-key 最古 race_date)算出
- [x] T007 [US1] Persist repair 実行監査を既存 `ingestion_jobs`(job_type=`repair_splits`)の summary に記録(件数・衝突・orphans・affected_from・mapping 集合ハッシュ・ツール版)を `scrape/src/horseracing_scrape/repair.py` に実装(スキーマ変更なし)
- [x] T008 [US1] Wire `scrape repair-splits [--entity horse|jockey|trainer|all] [--dry-run] [--limit N]` を `scrape/src/horseracing_scrape/cli.py` に結線し `RepairReport` を出力(dry-run は timestamps 含め DB 完全不変)
- [x] T009 [P] [US1] Integration tests `scrape/tests/integration/test_repair.py`: サヴォーナ型 re-key 正当性 / 冪等(2回目0変更) / **合成 PK 衝突でペア全体無変更(原子性、実 PK `(prediction_run_id, horse_id)`)** / 孤児削除後の FK・血統 ID(sire/dam/damsire)dangling 0 / 削除前属性ゲート(情報損失0) / dry-run DB 完全不変 / **旧単勝 recommendation の JSON ID canonicalize → backtest 着否 repair 前後一致** / `--limit` 分割 / 中断後再実行
- [x] T010 [P] [US1] Parity regression test `features/tests/test_repair_parity.py`: 最古影響日より前の全特徴列が repair 前 baseline と `assert_frame_equal(check_exact=True)`、加えて統合馬の過去走復活・同レース他馬の within-race 特徴変化・統合騎手/調教師の後続別馬への波及・同日結果非混入・血統 self-exclusion が canonical 履歴全体を除外

**Checkpoint**: US1 単独で「分裂 repair → 履歴復活 → parity 保持」が実データ fixture で成立(MVP)

---

## Phase 4: User Story 2 - 今後の取り込みで分裂を発生させない (Priority: P1)

**Goal**: ingest 時に identity 照合を通し、既存 canonical と一致する個体を新規サロゲート化しない。

**Independent Test**: 既存 canonical + 同名(馬は生年導出一致)個体を取り込み → nk: 行が作られず canonical に解決。情報欠損経路(result-only・血統親)は自動昇格しない。

- [x] T011 [US2] Extend `resolve_entity` を `scrape/src/horseracing_scrape/idmap.py` で拡張: optional な candidate_name/candidate_birth_year を受け、mapped 行が無い場合に `source_id==既存 canonical *_id` のマスタ行をロードし `classify_identity` 評価(mapped→mapped 行 upsert + canonical 返却=サロゲート作らない、conflict→記録 + サロゲート、insufficient/none→従来どおりサロゲート + unmapped)、引数省略時はバイト同等の従来動作(sticky 尊重)
- [x] T012 [US2] Wire `scrape/src/horseracing_scrape/upsert.py` の resolve_entity 呼び出しに利用可能な照合情報を供給(entries=名前 + 年齢→race_date から生年を導出する規則を実装・検証、results=ID のみ→情報供給なし、血統親=ID のみ→情報供給なし)
- [x] T013 [P] [US2] Ingest regression tests `scrape/tests/integration/test_entries.py`(+関連): canonical + 名前一致 → nk: 行を作らない / result-only・血統親経路は自動昇格しない / conflict・rejected が通常 ingest で mapped へ戻らない(sticky) / 照合情報省略で既存 scrape テスト緑(後方互換)

**Checkpoint**: US2 単独で「今後の分裂ゼロ」が成立(US1 と独立)

---

## Phase 5: User Story 3 - identity 解決の監査と保留 (Priority: P2)

**Goal**: どのサロゲートがどの根拠で解決/保留されたかを監査・確認でき、dry-run で承認できる。

**Independent Test**: resolve-identities を実行し、mapped 化行に根拠が記録され、conflict/insufficient が区別され、dry-run が DB 非変更であることを確認。

- [x] T014 [US3] Wire `scrape resolve-identities [--entity ...] [--dry-run]` を `scrape/src/horseracing_scrape/cli.py` に結線: T005 の `resolve_identities` を実行し entity 別の resolved / conflict / insufficient 件数 + conflict 例示 + **騎手/調教師の mapped 候補一覧(operator 承認用)** を出力、resolve 実行監査を `ingestion_jobs`(job_type=`resolve_identities`)に記録、dry-run は DB 完全不変
- [x] T015 [P] [US3] Tests `scrape/tests/integration/test_resolve.py`: dry-run DB byte-invariant(timestamps 含む) / mapped・conflict・insufficient の区別 / sticky(mapped・conflict の再評価なし) / 候補一覧出力

**Checkpoint**: US3 単独で「解決の監査・承認・保留」が成立

---

## Phase 6: Polish & Cross-Cutting Concerns

- [x] T016 [P] Leak-guard test: `features/` が `id_mappings`/`IdMapping` を import しないことを機械固定(`features/tests/` に import-graph アサート)
- [x] T017 Real-DB E2E **validated via rollback**(ローカル DB, commit→flush エイリアス + 最終 rollback で非永続): resolve(horse 5975/jockey 156/trainer 199 mapped・conflict 0/7/8・insufficient 5383/0/0=birth_year 欠損サロゲートは安全に未マップ)+ repair(6330 ペア・**衝突 0・held 0**[血統 ID gate 除外後]・rekeyed race_horses/results/predictions/snapshots/recommendations・affected_from 2025-10-11・errors 0)を実データで実証、POST-ROLLBACK で nk 馬 9647 が不変=DB 無改変を確認。**実際の永続 apply(writer 停止→本実行→materialize→predict-backfill --force→recommend-backfill→cutover)は operator 作業**(ユーザー承認前提のため未実行)。
- [ ] T018 [P] (任意) 旧サロゲートURL(`/horses/nk:...`)の扱いを `api/` に実装(canonical へ誘導 or typed 404)。repair 後は行がマージされ大半自動解決のため優先度低
- [x] T019 [P] Run full suites `scrape`/`features`(+ 影響する `serving`/`betting`/`api`)と ruff、migration head 不変(0011)・OpenAPI 不変・全緑を確認
- [x] T020 Update `specs/067-entity-identity-resolution/plan.md` の実装完了サマリと `MEMORY.md`/feature-067 メモに結果(統合件数・parity 結果・conflict 残数)を追記

---

## Dependencies & Execution Order

- **Setup(T001)** → **Foundational(T002–T005)** が全 story の前提。
- **US1(T006–T010)**: 前提=Foundational。repair は resolve 済み mapped 行が前提だが、テストは fixture で mapped 行を直接用意し US3 CLI に依存しない(独立)。
- **US2(T011–T013)**: 前提=Foundational(classify_identity)。US1 と独立。
- **US3(T014–T015)**: 前提=Foundational(resolve_identities コア)。US1/US2 と独立。
- **Polish(T016–T020)**: 全 story 後。T017 E2E は US1+US2+US3 実装済みが前提。
- Story 順序=P1(US1→US2)→P2(US3)。US1/US2/US3 は相互独立に実装・テスト可能。

## Parallel Opportunities

- Foundational 内: T002 と T004 は別ファイルで並列可(T003 は T002 に依存)。
- 各 story のテストタスク(T009/T010, T013, T015)は実装タスク完了後、別ファイルで並列可。
- US1・US2・US3 は Foundational 完了後に並列着手可能(別担当なら)。
- Polish の T016/T018/T019 は並列可。

## Implementation Strategy (MVP first)

1. **MVP = Setup + Foundational + US1**(T001–T010): 既存分裂を repair できる = 最大価値(直近予測の履歴復活)。この時点で実データの分裂解消が可能。
2. 次に **US2**(T011–T013): 出血停止(今後の分裂ゼロ)。
3. 次に **US3**(T014–T015): 解決の監査・承認フロー。
4. **Polish**(T016–T020): leak-guard・E2E・全緑・ドキュメント。

**運用上の推奨**: US2(出血停止)を US1 の repair 本実行より前に配備しておくと、repair 後に再び分裂が積み上がらない(quickstart の安全な実行順に一致)。実装順は MVP=US1 だが、実 DB への適用は US2 配備 → US1 repair の順。
