# Tasks: 上位3着ベースの荒れ度読み出し (top-3 chaos readout)

**Input**: Design documents from `/specs/084-top3-chaos-readout/`

**Prerequisites**: spec.md (rev2), plan.md (rev2), research.md (D1-D19), data-model.md (rev2),
contracts/ (4 本), quickstart.md

**Tests**: **含める**。憲法「開発・レビュー品質ゲート」が leakage / 時系列 split / 確率整合性 /
評価ハーネス test を必須としており、spec の FR-001..FR-037 / SC-001..SC-009 と contracts が
具体的なテストを要求している。

**Organization**: user story ごとにフェーズを分け、各フェーズが独立にテスト可能。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 並列実行可(別ファイル・未完了タスクへの依存なし)
- **[Story]**: US1..US6(spec.md の user story)。Setup / Foundational / Polish には付けない

## Path Conventions

複数パッケージ monorepo。`db/` `probability/` `eval/` `training/` `live/` `api/` `front/` の
各 `src/` と `tests/`。plan.md の Project Structure が正。

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: スキーマ追加と共有定義。ここが通らないと他の全フェーズが着地しない。

- [X] T001 `db/migrations/versions/0012_chaos_readout.py` に `chaos_snapshots` / `chaos_readouts` を追加(**追加のみ**・既存テーブル無改変)。列は data-model.md §1/§2 を正とし、index は `(race_id, captured_at DESC)` / `(race_id, status)` / `(chaos_snapshot_id)` / `(displayed_at DESC)`
- [X] T002 `db/src/horseracing_db/models/chaos.py` に `ChaosSnapshot` / `ChaosReadout` ORM を追加し `db/src/horseracing_db/models/__init__.py` から export
- [X] T003 `db/tests/integration/test_chaos_tables.py`: migration の `upgrade`→`downgrade`→`upgrade` 往復で既存テーブルが不変であること、**DB レベルの append-only 担保**(`chaos_readouts` への UPDATE を trigger または `REVOKE UPDATE` で拒否・FR-008。アプリ層の挙動は db パッケージから観測できないため DB 側で固定)、index 存在、**`chaos_snapshots` の `status='active'` が 1 レース 1 行以下**(部分 unique index・SNAP-4)
- [X] T004 **migration head assert の一括更新**(040 前例): `features/tests/unit/test_feature020_leak_guard.py` / `test_feature021_leak_guard.py` / `test_feature023_leak_guard.py` / `test_feature040_leak_guard.py` / `test_feature066_leak_guard.py` / `test_materialize_fallback_columns.py` / `live/tests/unit/test_no_schema_change.py` の `startswith("0011_")` を `"0012_"` に更新。`api/tests/integration/test_health_schema_check.py` が bundled head と一致することを確認
- [X] T005 [P] `probability/src/horseracing_probability/chaos_events.py` に事前登録イベント定義(**FR-010a / FR-010b / FR-010c / FR-010d / FR-010e** を実装)(`EventDefinition`: `key` / `label_ja` / **実行可能な型付き述語** / `infeasible_when_n_le` / `nested_under` / `lambda_sensitive` / `promotion_role` / `min_positives_for_decision`)。v1 は `s_ge_20`(N≤7 不可)/ `himo_are`(N≤9)/ `total_collapse`(N≤9・`lambda_sensitive=False`)/ `s_ge_30`(N≤10・診断専用)。述語は **FR-010a..010d の式を正**とし、特に `himo_are` は **`ra<=3 and (rb>=10 or rc>=10)`(or)**

**Checkpoint**: `uv run --project db alembic upgrade head` が通り、全パッケージのテストが緑

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 全 user story が依存するリーク境界と表示ラベルの土台

**⚠️ CRITICAL**: ここが終わるまで user story の作業を始めない

- [X] T006 [P] `features/tests/unit/test_feature084_leak_guard.py`: 表示軸名(`chaos_band` / `p_s_ge_20` / `himo_are` / `total_collapse` / `expected_top3_popularity_sum`)が feature registry / `materialized_columns()` / model recipe に現れないこと、`FEATURE_VERSION` 不変、migration head が `0012_`(**SC-005** / FR-036)
- [X] T007 [P] `front/src/lib/chaosLabels.ts`: **066 と別語彙の 5 段**(`t3_calm`/`t3_mild`/`t3_mid`/`t3_rough`/`t3_wild` = 揃う/やや揃う/標準/やや崩れる/崩れやすい・FE-6a)・**述語と**論理的に等価な**事象ラベル(「人気順合計が20以上」「**1〜3番人気が勝ち、2着か3着に二桁人気**」「二桁人気が勝つ」。「2・3着」の省略は and と誤読されるため禁止)・開示文言(FE-9 / FR-023)・確認前文言(FE-4 / FR-020「参考値(最終オッズでは検証済み／発走前オッズで検証中)」)

**Checkpoint**: リーク境界テストが緑・ラベル定義が front から import 可能

---

## Phase 3: User Story 2 — 荒れ分布の導出コア (Priority: P1)

**Goal**: 表示される数値が provenance ごとに単一の同時分布から導出され、入れ子事象の不等式が
構成上成立する純関数コア

**Independent Test**: DB 不要。`probability` の単体テストのみで全不変条件が検証できる

**Covers**: FR-010..FR-016 / SC-001 / SC-002 / SC-007 / INV-C1..C12 / C1..C7

### Tests for User Story 2

- [X] T008 [P] [US2] `probability/tests/unit/test_chaos_distribution.py`: 入力検証(C1 / FR-005 / FR-005a) — `set(q) != set(ranks)` / popularity に**重複または欠損** / `n<4` / 非正・NaN で `ValueError`。**番号の飛び(max>n)は受理**(取消レースの 26.1%)
- [X] T009 [P] [US2] 同ファイル: **`test_events_need_order`**(C2 / FR-010) — `(1,9,10)` と `(10,1,9)`(同 S=20・異なる着順)で `himo_are` / `total_collapse` が**異なる値**になる(BLOCKER 回帰)
- [X] T010 [P] [US2] 同ファイル(FR-012 / FR-013 / **SC-002**): `test_triple_mass_is_one_operational_lambda`(運用 λ × n=3..18 で \|Σ−1\| ≤ 1e-9)と `test_adversarial_lambda_raises`(λ=1.5/2.5 × 極端 q で `ChaosInvariantError`)。**`(0,5]` 全域が使えるとは主張しない**(D17)
- [X] T011 [P] [US2] 同ファイル: `test_no_global_renormalization`(C3 / FR-012) — 和で割る実装が無いことを AST で固定 + 意図的に壊した分布で例外
- [X] T012 [P] [US2] 同ファイル: marginal 検算(INV-C4..C7) — `test_first_place_marginal_equals_q`(λ 任意)/ `test_topk_marginals_match_049`(運用 λ)/ `test_lambda_one_matches_harville` / `test_expected_s_identity`(`E[S] == Σ rank_i·P(i∈top3)`)
- [X] T013 [P] [US2] 同ファイル: **`test_total_collapse_lambda_invariant`**(C6 / INV-C9 / FR-019) — 生と補正で `total_collapse` が一致(実測 5.6e-17)
- [X] T014 [P] [US2] 同ファイル: 構造的ゼロ(C5 / INV-C10 / **SC-007** / FR-014) — `test_structural_zero_is_zero_not_none`(n=7 で `s_ge_20 == 0.0` + 理由、**n=8 では正の値**)と `test_support_bounds`
- [X] T015 [P] [US2] 同ファイル: `test_nested_events_monotone`(INV-C8 / FR-015)と **`test_non_nested_not_forced`**(`s_ge_20` と `himo_are` に不等式を課していない)
- [X] T016 [P] [US2] 同ファイル: **C7 / FR-016** `test_deterministic` — 同一入力で出力がバイト一致し、dict の挿入順に依存しない
- [X] T017 [P] [US2] 同ファイル: `test_uniform_field_uniform_triples` / `test_permutation_equivariance`(INV-C11)/ **`test_uniform_eight_expected_s`**(一様 8 頭で `E[S] == 13.5`・D19 の根拠)

### Implementation for User Story 2

- [X] T018 [US2] `probability/src/horseracing_probability/chaos_distribution.py` に `ChaosDistribution` dataclass(`provenance` / `pmf` / `expected_s` / `event_mass`(**常に数値**)/ `structural_zero` / `triple_mass_sum` / `support`)と `ChaosInvariantError` を定義
- [X] T019 [US2] 同ファイル `chaos_distribution(q, ranks, events, *, stage_discount, eps, invariant_tol)`: 既存 `engine.joint_probabilities(stage_discount=)` を呼び、**`trifecta` を 1 回走査して S の PMF と全事象質量を同時集約**(C2 / FR-010)
- [X] T020 [US2] 同ファイル: 不変条件検査(INV-C1..C12 / **SC-002** / FR-012)。`triple_mass_sum` は**検査のために保持し、割らない**(C3)。構造的ゼロは `0.0` + 理由(C5 / FR-014)
- [X] T021 [US2] 同ファイル `chaos_readout(q, ranks, events, *, stage_discount, edges) -> (raw, adjusted, band)`: 生と補正を**別の分布**として返し、不変条件を**それぞれ独立に**検査(C4 / **SC-001** / FR-011)
- [X] T022 [US2] 同ファイル `band_of(p_primary, edges)`: **band 軸は `p_s_ge_20`**(E[S] ではない・FR-018 / D11)

- [X] T023 [P] [US2] 同ファイル: **`test_events_match_normative_predicates`**(**FR-010a / FR-010b / FR-010c / FR-010d / FR-010e**) — 実装が式と一致。特に `himo_are` の **or** 判定: `(1,10,5)`/`(1,5,10)` は true・`(1,5,6)` は false・`(4,10,11)` は false
- [X] T024 [P] [US2] `probability/tests/unit/test_chaos_leak_boundary.py`: **FR-037** — 導出コアが `race_results` / `finish_order` / モデル p を import も引数も取らないことを AST + 署名で静的に固定(T006 は FR-036 のみ検証しており FR-037 は未カバーだった)

**Checkpoint**: `uv run --project probability pytest tests/unit/test_chaos_distribution.py -q` が全緑

---

## Phase 4: User Story 4 — 凍結 artifact と事前登録 (Priority: P2・US3 の前提)

**Goal**: λ・境界・事象・閾値・昇格規則・最終判定日を結果を見る前に凍結し、後から動かせなくする

**Independent Test**: `training chaos-bands fit` が artifact を 1 個 publish し、同一 digest の
再発行が typed error になり、`valid_from <= fit_through` が拒否される

**注**: 優先度は P2 だが、US3(表示・P1)が境界を必要とするため実装順ではここに置く

**Covers**: FR-025..FR-029 / ART-1..9

### Tests for User Story 4

- [X] T025 [P] [US4] `eval/tests/unit/test_chaos_lambda.py`: 市場 q での λ fit が 049 と同一の条件付き NLL + golden-section であること、手計算ケースで λ2/λ3 が再現、`min_races` 未満で identity fallback(FR-028)
- [X] T026 [P] [US4] `training/tests/unit/test_chaos_artifact.py`: **時間ゲート**(ART-4 / FR-026 / D16) — `target_date > fit_through` かつ `target_date >= valid_from` のみ受理。**2026 年のレースが受理されること**と `fit_to` 当日が拒否されることを境界値で固定(rev1 の逆転の回帰テスト)
- [X] T027 [P] [US4] 同ファイル: `artifact_digest` が **payload 全体**から計算されること、承認 manifest 不一致で fail-closed、`O_EXCL` 相当の衝突しない作成、`valid_from > fit_through` の必須検証(ART-1..3/9 / FR-025)
- [X] T028 [P] [US4] 同ファイル: **ART-6 / FR-027** — `calibration_status` の `confirmed` 化が**新 version の発行**でのみ可能で、既存 artifact の書き換えが typed error になること
- [X] T029 [P] [US4] 同ファイル: `numeric_stability_report` が代表 + 敵対フィールドで Σ=1 を検査した結果を含み、緑でなければ publish されない(ART-8 / FR-029)

### Implementation for User Story 4

- [X] T030 [P] [US4] `eval/src/horseracing_eval/chaos_lambda.py`: 市場 q に対する λ2/λ3 の条件付き NLL fit(既存 `stage_discount.py` の数学を再利用・**049 の artifact は読まない** ART-7 / FR-028 / D6)。`probability` を import しない(依存方向)
- [X] T031 [P] [US4] `probability/src/horseracing_probability/chaos_artifact.py`: **artifact のロード + 検証**(storage-and-artifact.md「ロード時検証」8 項目・ART-9 の fail-closed)。`api` と `live` の**両方**が使うため `probability` に置く(`live` は `api`/`training` を import 不可・plan の Structure Decision 参照)
- [X] T032 [P] [US4] `probability/tests/unit/test_chaos_artifact.py`: ロード時検証 8 項目・digest 再計算不一致で fail-closed・承認 manifest 不一致で fail-closed・時間ゲート(**2026 年のレースが受理され `fit_to` 当日が拒否される**)
- [X] T033 [US4] `training/src/horseracing_training/chaos_bands.py` に `fit_artifact(...)`: λ fit(eval)→ 凍結窓の `P(S≥20)` 算出(probability)→ **五分位境界**→ payload 組み立て → digest 計算 → `O_EXCL` publish
- [X] T034 [US4] 同ファイル: payload に `s_threshold_basis="fit_window_p90"`(閾値 20 の出所・JRA-VAN の G1 観察は根拠に採らない)・`edges_basis="closing_history"`(ART-5)・`preregistration`(昇格規則 / 最小陽性 100 / 最小開催日 60 / 最終判定日)・`race_set_hash` / `fit_input_hash` / `code_sha` を含める
- [X] T035 [US4] `training/src/horseracing_training/cli.py` に `chaos-bands fit`(引数は contracts/cli.md §2)。除外理由別件数を出力し、承認 manifest への追記を案内
- [X] T036 [US4] **`config/chaos_bands_approved.json`** を作成し承認 digest を pin(ART-2 / FR-025)。**`artifacts/` 配下に置いてはならない**(`.gitignore` の `/artifacts` で全除外されコミット不能)
- [X] T037 [US4] 実 DB で fit を 1 回実行し、`lambda2 ≈ 0.8304` / `lambda3 ≈ 0.7111` / `quintile_edges ≈ [0.01957, 0.06593, 0.11181, 0.17031]` / `n_races_fit ≈ 13788` を確認(quickstart §2)

- [X] T038 [US4] 同ファイル: `field_size_reference_quantiles`(頭数別 `p_s_ge_20` 参照分位)を payload に含め、`within_field_size_percentile` の算出関数を実装(FR-018a / D9)
- [X] T039 [P] [US4] `training/tests/unit/test_chaos_artifact.py`(拡張): **FR-029a** — `eligibility_predicate` が fit / discovery / 表示で同一であること。**FR-029b** — `operational_lambda_envelope` 外の λ で publish が拒否され、テストの λ 値と envelope が一致すること

**Checkpoint**: artifact が 1 個存在し、再発行が拒否され、時間ゲートの境界値テストが緑

---

## Phase 5: User Story 1 — 発走前スナップショットの凍結 (Priority: P1)

**Goal**: 表示に使った市場観測を凍結し、DB のオッズ上書き後も表示・検証の根拠が変わらない

**Independent Test**: snapshot 捕捉 → `race_horses.odds`/`popularity` を書き換え → 凍結行から
再計算した全数値がバイト一致(**SC-003**)

**Covers**: FR-001..FR-009 / SNAP-1..7 / CAP-1..9

### Tests for User Story 1

- [X] T040 [P] [US1] `live/tests/unit/test_chaos_capture_guards.py`: **捕捉規律**(CAP-1..7 / FR-001..004) — 結果確定済みで捕捉しない / 取得**後**の再確認で弾く / `post_time` 不明は `capture_strength='weak'` / DB 読み取りのみは `unknown` / `--source` の自称を信用しない
- [X] T041 [P] [US1] 同ファイル: 拒否経路(SNAP-2/3 / FR-005) — popularity の重複・欠損 / 部分オッズ / **n<4**(fit と同一の適格条件・FR-029a)で**書き込まれず** typed skip に計上される
- [X] T042 [P] [US1] `live/tests/integration/test_chaos_capture_db.py`: **SC-003 / FR-017** — 凍結後に `race_horses` を書き換えても凍結行からの再計算がバイト一致
- [X] T043 [P] [US1] 同ファイル: `chaos_snapshots` と `chaos_readouts` が**同一トランザクション**で書かれる(CAP-8 / FR-008)。readout 書き込み時にも result-pending を再確認
- [X] T044 [P] [US1] 同ファイル: 出走取消で**旧行を void にし新行を追記**(SNAP-5 / FR-006)。**`status='active'` が 1 レース 1 行以下**であること(SNAP-4 / FR-007。within-race の多点保持は禁止)。`content_digest` と `chaos_snapshot_id` が分離されている

### Implementation for User Story 1

- [X] T045 [US1] `live/src/horseracing_live/chaos_capture.py`: 既存 `live/guards.py` の result-pending を再利用し、**キャッシュ不使用の新規取得** → 取得前後の pending 確認 → `captured_at < post_time` → `seconds_to_post` を**数値**で記録 → `capture_strength` 判定(CAP-1..7 / CAP-10 / FR-001..004)
- [X] T046 [US1] 同ファイル: `chaos_snapshots` 行と、US2 のコア + US4 の artifact で算出した `chaos_readouts` 行を**同一トランザクション**で書く(CAP-8 / FR-008)。UPDATE 禁止・結果確定後の INSERT 禁止
- [X] T047 [US1] `live/src/horseracing_live/cli.py` に `capture-chaos --race-id / --date [--min-seconds-to-post]`。typed skip の理由別件数と `capture_strength` 別内訳を出力(contracts/cli.md §1)
- [X] T048 [US1] `live/README.md` と `contracts/cli.md` に **operator 手順**を明記(CAP-9 / CAP-10 / FR-009): 推奨実行時点 T−30 分・`--min-seconds-to-post` の既定・網羅率目標・`confirmatory` は post_time 既知のレースのみ(実測 2026 年 100% / 2025 年 22.9% / 2024 年 0%)。**自動スケジューラは導入しない**(憲法「初期は全て手動実行」)

**Checkpoint**: 実レースで捕捉が成功し、SC-003 のバイト一致が確認できる

---

## Phase 6: User Story 6 — 運用 go/no-go パイロット (Priority: P1)

**Goal**: 凍結・確認機構を本実装する前に、実際の開催日で捕捉カバレッジと鮮度が閾値を満たすか測る

**Independent Test**: `training chaos-bands coverage` がカバレッジ率と `seconds_to_post` 分布を出す

**Covers**: FR-034 / FR-035

- [X] T049 [US6] `training/src/horseracing_training/chaos_bands.py` に `coverage_report(...)`: 対象期間の捕捉率・`capture_strength` 別内訳・`seconds_to_post` 分布・未捕捉レースの特性(FR-034)
- [X] T050 [US6] `training/src/horseracing_training/cli.py` に `chaos-bands coverage --from --to`
- [X] T051 [US6] 実開催日でパイロットを走らせ、事前登録した閾値と突き合わせて **go/no-go を記録**(FR-035)。未達なら凍結・確認機構を後回しにし「監査されていない現在オッズ」ラベルの縮小版で出す判断を spec に追記

**Checkpoint**: go/no-go の判断材料が数値で得られている

---

## Phase 7: User Story 3 — 5 段バンドと 3 事象の表示 (Priority: P1)

**Goal**: レース詳細で荒れ度が確率として読め、剥離の原因だったラベルが是正されている

**Independent Test**: レース詳細に確率 + バンド + 3 事象が出て、q 欠損・構造的ゼロ・確認前・
予測 run 無しの各状態が別々に描き分けられる

**Covers**: FR-018..FR-024 / API-1..10 / FE-1..13 / SC-004 / SC-006 / SC-009

### Tests for User Story 3

- [X] T052 [P] [US3] `api/tests/unit/test_race_chaos.py`: `RaceChaos` の available / unavailable の**タグ付き 2 形状**、available では数値フィールドが**必須・非 null**、`extra="forbid"`(API-7)
- [X] T053 [P] [US3] 同ファイル: **明示 keyword マップ**で応答を組み立てており `Model(**dict)` を使っていないこと + nested 値の非 null assertion(**API-7**・075 の splat-null 事故の再発防止)
- [X] T054 [P] [US3] 同ファイル: **SNAP-6** — 表示既定が `status='active'` の最新 `captured_at` 行であること。void 行を挟んでも正しい行が選ばれる
- [X] T055 [P] [US3] `api/tests/unit/test_race_chaos.py`(拡張): **FR-020a / API-5a** — `artifact_digest` が現行承認 digest と一致するとき永続 `chaos_readouts` の値を返し、不一致のときは再計算して乖離を検出可能にすること(黙って乖離しない)
- [X] T056 [P] [US3] `api/tests/integration/test_race_chaos_api.py`: **API-6 / SC-009 / FR-021** — **予測 run が無いレースでも `race_chaos` が返る**。run 選択より前に構築されている
- [X] T057 [P] [US3] 同ファイル: `unavailable_reason` の全値域(`no_snapshot` / `partial_market_odds` / `invalid_popularity_ranks` / `field_too_small` / `artifact_unavailable` / `out_of_validity_window` / `invariant_violation`)で **HTTP 200** のまま返る(API-5)
- [X] T058 [P] [US3] 同ファイル: **`race_dispersion` が本 feature 導入前とバイト一致**(**SC-004** / API-1 / FR-022)。`dispbands-v1.json` 不変
- [X] T059 [P] [US3] `api/tests/unit/test_no_write_boundary.py`(既存拡張): API が **`horseracing_live` を import しない**こと、全 path GET のままであること(API-4)
- [X] T060 [P] [US3] `front/src/components/RaceChaosPanel.test.tsx`: 主値が確率・バンドは粗いラベル(FE-1 / FR-018)、述語と等価なラベル表示(FE-2。「2・3着」の省略が無いこと)、`total_collapse` に λ 非適用の注記(FE-3 / FR-019)、確認前文言(FE-4 / FR-020)、構造的ゼロが「該当馬なし」(FE-8 / FR-014)、少頭数の平易な説明(FE-7)
- [X] T061 [P] [US3] 同ファイル: **`hasPreds` と独立に描画される**(FE-12 / **SC-009**)、鮮度表示「発走○分前・HH:MM 取得」(FE-13)、損益色・ソート・CTA・過度な小数精度が無いこと(FE-10 / FR-023)
- [X] T062 [P] [US3] 同ファイル: **FE-5** — 生の市場質量が方法詳細に置かれ主枠に同じ事象の百分率が 2 つ並ばないこと。**FE-11 / FR-024** — loading / typed-empty / typed-error / unavailable が別々の UI 状態として描き分けられること
- [X] T063 [P] [US3] 既存 pseudo 不変テスト(拡張): **API-8 / FR-024** — `race_chaos` の値がバッジ無しで描画されないこと
- [X] T064 [P] [US3] `front/src/components/RaceChaosPanel.test.tsx`(拡張): **FE-14 / FR-020 / FR-023** — **禁止語の不在**を assert(「暫定」「妙味」「edge」「儲」等)。必要文言の存在だけでなく禁止語の不在も固定する
- [X] T065 [P] [US3] `front/src/components/RaceDispersionPanel.test.tsx`(既存改修): 見出しが**「市場の支持集中度」**で折り畳み詳細に格下げされ、結果主張キャプションが撤去されていること(FE-6 / FR-022)

### Implementation for User Story 3

- [X] T066 [US3] `api/src/horseracing_api/chaos.py`: `chaos_snapshots` / `chaos_readouts` の読み出し(**`live` を import しない** API-4)+ **`probability.chaos_artifact` のローダを呼ぶ**(二重実装しない)+ US2 コア呼び出し
- [X] T067 [US3] 同ファイル: **`(content_digest, artifact_digest)`** をキーにしたキャッシュ(API-10)。engine 呼び出しは 1 レース 2 回(生 + 補正)
- [X] T068 [US3] `api/src/horseracing_api/schemas.py`: `RaceChaos` / `ChaosEvent` を**純追加**(data-model.md §6)。`extra="forbid"`、available 形状の数値は必須・非 null(API-7)
- [X] T069 [US3] `api/src/horseracing_api/routers/predictions.py`: `race_chaos` を**run 選択より前**に構築し typed-empty 応答にも含める(API-6 / FR-021)。既存 `race_dispersion` / `race_divergence` の経路は無改修
- [X] T070 [US3] OpenAPI 再生成(**SC-006** / API-2/3): `front/openapi.json` / `admin/openapi.json`(**byte 一致**)+ `schema.d.ts` を両方コミット
- [X] T071 [P] [US3] `front/src/components/RaceChaosPanel.tsx`: 主枠。確率 → バンド → 3 事象 → 鮮度 → 開示文言。整数%まで(FE-10)
- [X] T072 [P] [US3] `front/src/components/RaceDispersionPanel.tsx` と **`front/src/lib/dispersionLabels.ts`** 改修: 「市場の支持集中度」に改名し折り畳み詳細へ格下げ、`BAND_CAPTION`(結果主張キャプション)を撤去(FE-6 / FR-022)
- [X] T073 [US3] `front/src/pages/RaceDetailPage.tsx`: `RaceChaosPanel` を `hasPreds` と**独立に**描画(FE-12 / FR-021)

**Checkpoint**: quickstart §4 の全分岐(run 無し / N≤9 / N=8 / snapshot 無し / 2026 年レース)が期待どおり

---

## Phase 8: User Story 5 — 前向き検証(事前登録) (Priority: P3)

**Goal**: 登録後コホートのみで昇格判定し、未達は NO_DECISION で主枠から撤去する

**Independent Test**: `prospective-report` が登録直後に NO_DECISION を返し、必要レース数を明示する

**Covers**: FR-030..FR-034

- [X] T074 [P] [US5] `training/tests/unit/test_chaos_prospective.py`: **S が凍結順位から算出される**こと(現在の `race_horses.popularity` を使うと失敗する回帰テスト・FR-017)
- [X] T075 [P] [US5] 同ファイル: `capture_strength='confirmatory'` の行のみが確認コホートに入る(FR-004)/ `valid_from` 前が除外される(FR-026)/ **1 レース 1 行**(主 horizon・FR-032)
- [X] T076 [US5] `training/src/horseracing_training/chaos_bands.py` に `prospective_report(...)`: reliability / Brier / log score を全体・**頭数別・capture horizon 別**に出す(FR-033)。CI は開催日クラスタ bootstrap(seed 固定)。**AUC 単独で判断しない**
- [X] T077 [US5] 同ファイル: 昇格判定(FR-030 / FR-031) — **`p_s_ge_20` のみが支配**、`himo_are` 副次、`total_collapse` は λ 非適用で対象外、`s_ge_30` は診断専用で阻害しない。最小陽性 100 / 最小開催日 60 / 最終判定日を artifact から読み、未達は **NO_DECISION** + 「主枠から撤去せよ」
- [X] T078 [US5] 同ファイル: **捕捉カバレッジ**と除外レースの特性を必ず出力(FR-034・選択バイアス検知)
- [X] T079 [US5] `training/src/horseracing_training/cli.py` に `chaos-bands prospective-report --artifact`
- [X] T080 [P] [US5] `specs/084-top3-chaos-readout/` に 080 実配当検証の事前登録を確定(**三連複を主・三連単を副**、円/100円単位・log 配当・同着規約・全カバレッジ分母・最小陽性数)。実装は 080 のデータ到着後

**Checkpoint**: 登録直後に NO_DECISION と必要レース数(S≥20 で 0.5-0.8 年)が出力される

---

## Phase 9: Polish & Cross-Cutting Concerns

- [X] T081 **SC-008 の outcome 回帰**(US2 の受入): **`training/tests/integration/test_chaos_outcome_regression.py`**(凍結 fixture を要するので probability の unit には置かない)に `training chaos-bands diagnose --export-fixture` で生成した checksum 固定 fixture(2024+ 適格レース n=8,818 の popularity / オッズ / 1-3着のみ・parquet + SHA-256 をコミット)を置き、バンド別の実現 `S≥20` / `himo_are` / `total_collapse` が discovery 記録と整合し単調性と識別力の下限を満たすことを機械検証。**066 は他の全不変条件を満たしたまま失敗できたので、これが唯一の実質的な防壁**
- [X] T082 **意味論ゴールデンケース**(SC-008 / US2 の受入): **`live/tests/integration/test_chaos_golden_case.py`**(DB 依存)で 1着=1番人気・2着=17番人気・3着=18番人気 → `S=36` / `himo_are=true` / `total_collapse=false` を、**現在 DB の popularity を書き換えた後**の凍結行に対して検証
- [X] T083 [P] `eval/src/horseracing_eval/diagnostics_store.py`(拡張): `KIND_CHAOS_BANDS` 定数と `save_chaos_bands_run`(**転記のみ**・054 の `save_segment_edge_run` 同型)を追加。T075 の `--persist` の書き手(現状 kind 定数も save 関数も無い)
- [X] T084 [P] `training chaos-bands diagnose` の実装 + CLI(contracts/cli.md §3): バンド別実現率・reliability / Brier / log score・**N のみと `g(H,N)` のベースライン併記**・**頭数バケット内の AUC**(FR-033)・`--persist` で `diagnostic_runs`(kind=`chaos_bands`)へ転記のみ
- [X] T085 [P] **API-9 / SC-006**: CI で `app.openapi()` を front / admin の committed snapshot と比較するテストを追加(現行 `check-openapi.sh` は committed 同士の比較のみ)
- [X] T086 [P] フルパス p95 のベンチマーク(API-10)。**engine + 三つ組集約 + DB 読み + 直列化**の合計で計測し、**warm p95 < 15 ms / cold p95 < 30 ms** を確認(実測: engine 1.38ms + 集約 1.91ms = 3.3ms/provenance → 2 provenance で warm 6.6ms・cold 初回 17.7ms)。`bet_type` 指定時の追加コストも含める
- [X] T087 [P] `deploy/README.md` と `.claude/launch.json` に artifact パス / 承認 manifest の環境変数を追記
- [X] T088 全パッケージのテストと lint: `db` / `probability` / `eval` / `training` / `live` / `api` / `front` / `admin`、`ruff` / `tsc` / `eslint` / drift-check
- [X] T089 quickstart.md の 9 ステップを実 DB で通し、結果を spec の「主要な計測結果」と突き合わせる

---

## Dependencies & Execution Order

```text
Phase 1 (Setup: migration + ORM + events)         T001-T005
        │
Phase 2 (Foundational: leak-guard + labels)       T006-T007
        │
Phase 3 (US2 導出コア ─ 純関数・DB 不要)          T008-T024   ← MVP の中核
        │
        ├─→ Phase 4 (US4 artifact fit)            T025-T037   ← US3 の境界を作る
        │        │
Phase 5 (US1 捕捉)  ←┘                            T038-T046
   (readout 書き込みに US2 コア + US4 artifact が要る)
        │
Phase 6 (US6 go/no-go パイロット)                 T047-T049
        │
Phase 7 (US3 表示)  ← US1 + US2 + US4             T050-T071
        │
Phase 8 (US5 前向き検証) ← 全部 + 時間            T072-T078
        │
Phase 9 (Polish)                                  T079-T087
```

**優先度と実装順が食い違う箇所**: US4 は P2 だが US3(P1)が境界を必要とするため先に実装する。
US5 は P3 かつデータ蓄積待ちなので最後。

## Parallel Opportunities

- **Phase 1**: 最後の 1 件(events 定義)は他と独立(別ファイル)
- **Phase 2**: 2 件は完全並列
- **Phase 3**(T008-T024): `[P]` 付きテスト群は全て並列。実装群は逐次
- **Phase 4**(T025-T037): テスト群は並列。`eval/chaos_lambda.py` は別パッケージなので `training` 側と並列可
- **Phase 5**(T038-T046): テスト群は並列。実装は逐次
- **Phase 7**(T050-T071): テスト群は並列。実装は `api/chaos.py` → キャッシュ → `schemas.py` → `routers` → OpenAPI が逐次で、front の 2 コンポーネントは並列
- **Phase 8**(T072-T078): テスト 2 件は並列、080 事前登録は独立
- **Phase 9**(T079-T087): `[P]` 付きは並列

**注**: 具体的な並列可否は各タスク行の `[P]` マーカーを正とする(範囲表記は目安)。

## Requirement Coverage

(タスク本文の ID 参照から機械生成)

| 要件 | タスク |
|---|---|
| FR-001 | T038, T043 |
| FR-002 | (Phase 5) |
| FR-003 | (Phase 5) |
| FR-004 | T073 |
| FR-005a | T008 |
| FR-005 | T008, T039 |
| FR-006 | T042 |
| FR-007 | T042 |
| FR-008 | T003, T041, T044 |
| FR-009 | T046 |
| FR-010a | T005, T023 |
| FR-010b | T005, T023 |
| FR-010c | T005, T023 |
| FR-010d | T005, T023 |
| FR-010e | T005, T023 |
| FR-010 | T009, T019 |
| FR-011 | T021 |
| FR-012 | T010, T011, T020 |
| FR-013 | T010 |
| FR-014 | T014, T020, T058 |
| FR-015 | T015 |
| FR-016 | T016 |
| FR-017 | T040, T072 |
| FR-018 | T022, T058 |
| FR-019 | T013, T058 |
| FR-020 | T007, T058, T062 |
| FR-020a | T053 |
| FR-021 | T054, T067, T071 |
| FR-022 | T056, T063, T070 |
| FR-023 | T007, T059, T062 |
| FR-024 | T060, T061 |
| FR-025 | T027, T034 |
| FR-026 | T026, T073 |
| FR-027 | T028 |
| FR-028 | T025, T030 |
| FR-029 | T029 |
| FR-029a | T037 |
| FR-029b | T037 |
| FR-030 | T075 |
| FR-031 | T075 |
| FR-032 | T073 |
| FR-033 | T074, T082 |
| FR-034 | T047, T076 |
| FR-035 | T049 |
| FR-036 | T006, T024 |
| FR-037 | T024 |
| SC-001 | T021 |
| SC-002 | T010, T020 |
| SC-003 | T040 |
| SC-004 | T056 |
| SC-005 | T006 |
| SC-006 | T068, T083 |
| SC-007 | T014 |
| SC-008 | T079, T080 |
| SC-009 | T054, T059 |

## Independent Test Criteria (per story)

| Story | 独立テスト |
|---|---|
| US1 | 凍結後に `race_horses` を書き換えても再計算がバイト一致(SC-003) |
| US2 | DB 不要。純関数の不変条件のみで全検証(INV-C1..C12) |
| US3 | quickstart §4 の全分岐が期待どおり。`race_dispersion` バイト不変(SC-004) |
| US4 | artifact の再発行が拒否され、時間ゲートの境界値が正しい |
| US5 | 登録直後に NO_DECISION と必要レース数が出る |
| US6 | カバレッジ率と `seconds_to_post` 分布が数値で出る |

## Implementation Strategy

**MVP = Phase 1 → 2 → 3(T001-T024)**。導出コアだけで
「(1,9,10) と (10,1,9) を区別できる」「`himo_are` が or である」「λ が総崩れを動かさない」
「構造的ゼロが 0.0」「取消で番号が飛んでも受理する」といった**設計の核が正しいこと**が検証できる。
DB も artifact も front も要らない。

**次の増分** = Phase 4 + 5 + 6(T025-T049)。ここで **US6 の go/no-go** に到達する。
捕捉カバレッジ(および post_time 充足率)が閾値未満なら、Phase 7 の表示を
「監査されていない現在オッズ」ラベルの縮小版に落とす判断ができる(= 大きな手戻りを避ける分岐点)。

**最後** = Phase 7(表示・T050-T071)→ 8(前向き・T072-T078)→ 9(Polish・T079-T087)。
Phase 8 は数か月〜1 年の蓄積を待つため、コードは先に置いて結果は後から出る。

## Format Validation

全タスクが `- [ ] TID [P?] [Story?] 説明` の形式に従い、**ID は連番**。
**ファイルパスは成果物がファイルであるタスクに付す**。実行・記録のみのタスク(実 DB 実行の確認、
operator 手順の記録、全パッケージのテスト実行、quickstart の通し)は成果物がファイルでないため
パスを持たない — その場合は**記録先**(spec / contracts / 実行ログ)を説明に明記する。
Setup(T001-T005)/ Foundational(T006-T007)/ Polish(T079-T087)には Story ラベルを付けない。
User story フェーズの全タスクに `[US1]`..`[US6]` を付与。
