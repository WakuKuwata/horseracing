# Tasks: 着順の頭数正規化+ラグ分解 bundle(前走着順軸の測定的クローズ)

**Input**: Design documents from `/specs/088-finish-rank-decomposition/`

**Prerequisites**: plan.md, spec.md, research.md(D1-D10 codex 採否込), data-model.md, contracts/(feature-columns INV-C1..C11 / adoption-gate), quickstart.md

**Tests**: 含む(憲法の品質ゲート: leakage test・確率整合性・パリティは必須)。テストファースト — fixture・不変テストを先に書き、実装で緑にする。

**Organization**: US1=特徴構築(P1)/ US2=事前登録ゲート判定(P1)/ US3=判定後の後始末(P2)。US2 は US1 完了に依存(bundle が無いと評価できない)。US3 は US2 の verdict に依存(分岐)。

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

**Purpose**: 実装前の前提確定(推測固定禁止=憲法 III・spec FR-015)

- [X] T001 前提確認を実 DB で行い結果を specs/088-finish-rank-decomposition/plan.md 末尾に「着手時確定事項」として追記: (a) `model_versions` から active モデルと metadata.feature_hash を確定(スタックは `scripts/stack.sh` で起動) (b) `features-019`/`features-020` の使用痕跡が model_versions・artifacts に無いことを確認(FR-008: 019 は焼却済み・020 が未使用であること) (c) 現行 registry が features-018 であることを確認

---

## Phase 2: Foundational(パリティ基準の凍結 — US1 のテストが依存)

**⚠️ CRITICAL**: US1 のパリティ検証(SC-002)と compat pin(D4)はこのフェーズの成果物を基準にする

- [X] T002 [P] features-018 の canonical feature_hash を実測し specs/088-finish-rank-decomposition/research.md の D4 に記録(`cd features && uv run python -c "from horseracing_features.registry import ...; print(feature_hash(model_input_features()))"` 相当。bump 前に必ず実測 — compat pin の値になる)
- [X] T003 [P] features-018 baseline build を捕獲: `cd features && uv run python -m horseracing_features materialize --out <絶対パス>/artifacts/features_018_baseline.parquet`(bump 前の最後のフル build=T011 の共有列パリティ比較の基準。既存 artifacts/features.parquet を上書きしない)

**Checkpoint**: 基準 hash と baseline parquet が確保された — US1 着手可

---

## Phase 3: User Story 1 - 分解特徴の構築(リーク安全・バイト不変) (Priority: P1) 🎯 MVP

**Goal**: 10 列(data-model.md 定義表)を既存 as-of 機構の上に純加算で構築。既存共有列は 1 バイトも変えない。

**Independent Test**: 単体テスト(fixture・リーク不変・純加算・投影 parity)全緑 + 実 DB で共有列バイトパリティ(SC-002)+ compat E2E。

### Tests for User Story 1(実装より先に書き、FAIL を確認)

- [X] T004 [P] [US1] 手計算 fixture テストを features/tests/test_finish_decomposition_features.py に作成: contracts/feature-columns.md の fixture 全件(8頭出走5着=4/7・15完走最下位=14/17・最下位同着 1,2,2→0,0.5,0.5・2頭同着3着(10頭)=2/9・trend5 系列 [0.8,0.65,0.5,0.35,0.2]→傾き−0.15・INV-C11 従属等式 avg_last3_finish_pct==mean(3ラグ))+ INV-C1 値域・INV-C2 退化(n_started=1→NaN)・INV-C2a 範囲異常(finish_order>n_started→NaN)・NaN 伝播(窓内 NaN→集約 NaN)・min_periods(完走4走で avg_last5 系と trend5 が NaN)
- [X] T005 [P] [US1] リーク不変テスト(INV-C5)を同ファイルに追加: 対象レース自身の結果変更・同日他レースの結果変更・未来レースの追加/変更で 10 列が全行不変(既存 leak-guard テストのパターン踏襲)
- [X] T006 [P] [US1] 純加算・dtype テストを同ファイルに追加: INV-C7(bundle 追加前後で既存共有列 check_exact+check_dtype 一致=additive left-merge 構造テスト・058 の test_past_market_is_purely_additive 同型)+ INV-C8(10 列とも float64、完走ゼロの新馬プールでも dtype 不変)
- [X] T007 [P] [US1] 072 投影 parity テスト(INV-C10)を同ファイルに追加: `target_race_ids` 指定の出力 == full build の対象行(check_exact+check_dtype)。同日複数レース・対象馬の過去走が他会場に散る edge を含む。n_started(race-level primitive)が投影時も全過去レースから計算されること

### Implementation for User Story 1

- [X] T008 [US1] features/src/horseracing_features/finish_decomposition_features.py を新規実装: `_runs` 相当の finished 系列(history.py と同じ射影: races.race_date / race_results.finish_order・result_status / race_horses.entry_status)・n_started per race(STARTED 行数・race-level primitive)・finish_pct=(finish_order−1)/(n_started−1)(範囲検証込み)・10 列(lag は merge_asof backward, allow_exact_matches=False / rolling min_periods 固定 / expanding min / trend5 OLS)・`target_race_ids` 引数(per-horse 型: source を対象馬に絞り n_started は全過去で計算)→ T004-T007 を緑にする
- [X] T009 [US1] features/src/horseracing_features/registry.py を配線: FEATURE_GROUPS に 10 列 → `finish_decomp`・`FEATURE_VERSION = "features-020"`・`COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-020"]` = 018 の pin 集合をコピー+`"features-018": <T002 の実測 hash>` を追加(058 方式・全て hash 直 pin・推移させない)
- [X] T010 [US1] features/src/horseracing_features/materialize.py の build_asof_features に finish_decomp block を 1 箇所結線(target_race_ids 透過・025 単一 as-of 源)。ALL_COLUMNS/materialized_columns への自動派生を確認
- [X] T011 [US1] 実 DB フル build の共有列バイトパリティ(SC-002)を一度きり実測: features-020 build と T003 baseline の共有全列を全行 check_exact+check_dtype 比較(検証スクリプトは scripts/parity_088.py に置く)。結果(行数・不一致 0)を spec の測定結果欄に記録
- [X] T012 [US1] serving compat E2E(SC-005 の事前確認): T001 の active モデルを features-020 registry 下で compat-load し、1 レースの予測が persisted 値とバイト一致(mismatch 0)することを実 DB で確認(058 同型・logic_version に compat マーカーが付くこと)
- [X] T013 [P] [US1] カバレッジ監査スクリプト scripts/finish_decomp_coverage.py を作成(FR-018): 列別×年別の非欠損率・着順範囲異常の件数・退化(出走1頭)の実頻度・既存 avg_last3_finish との非欠損率比較を出力
- [X] T014 [US1] features 全テスト緑(`cd features && uv run pytest -q`)+ ruff クリーン(新規/変更ファイル)

**Checkpoint**: bundle は構築済み・既存を 1 バイトも変えていない・serving 互換維持 — 評価可能

---

## Phase 4: User Story 2 - 事前登録ゲートによる 1 bundle 判定 (Priority: P1)

**Goal**: contracts/adoption-gate.md の凍結手順で三値 verdict を機械的に得る。**診断段の結果がどうであれ判定段は必ず実行**(FR-013・codex 論点B 採用)。

**Independent Test**: verdict とレポート(効果量・CI・subgroup・fold 別)が artifact として残り、FR-013a の決定表から一意に判定が再構成できる。

### Implementation for User Story 2

- [X] T015 [US2] features-020 で materialize を再生成(`cd features && uv run python -m horseracing_features materialize --out <絶対パス>/artifacts/features.parquet`)し fingerprint/manifest 更新を確認
- [X] T016 [US2] gate-config.json を作成・凍結(**T017 の診断結果を観察する前に完了** — 診断を見てから閾値を凍結する経路を塞ぐ): 069 gate-config の形式を踏襲し**全て実キーで pin**(canonical hash は `_` 始まりキーを除外・harness は実キーのみ読む=`_comment` の数値は hash 保護外): **`evaluation_contract_version: "v2"`**(073 `assert_confirmatory` が非 v2 を即 fail-closed する MUST キー。**069 gate-config にはこのキーが無い**ので 073 gate-config を参照)/ `top_noninferior` / `calibration` / `subgroup_guard`(`non_inferior_margin_*` + **`critical_subgroups: ["2026_only","nk","2026_nk"]`**)/ `bootstrap.seed`(**20260713**=069/070/073 lineage)/ `bootstrap.b`(2000)/ **`eval_window`(from=2019-01-01・to=2026-08-09)** を specs/088-finish-rank-decomposition/gate-config.json に書き、canonical hash を算出して contracts/adoption-gate.md に追記(073 confirmatory 契約)。`_comment` は注釈のみ
- [X] T017 [US2] 診断段(非ゲート)を実行(T016 の凍結後): `cd training && uv run python -m horseracing_training feature-eval --drop-groups finish_decomp`(このサブコマンドに --use-materialized は無い=in-memory build で可)。AdoptionReport を保存し「診断・非ゲート」と明記して記録(判定に使わない・打ち切らない)
- [X] T018 [US2] 判定段の paired 評価を実行(T015・T016 完了後): `cd training && uv run python -m horseracing_training paired-eval --candidate "pl_topk:isotonic:0.3" --active "pl_topk:isotonic:0.3:drop=finish_decomp" --subgroups --confirmatory --gate-config <T016 のパス> --gate-config-hash <T016 の hash> --from <gate-config の eval_window.from> --to <gate-config の eval_window.to> --json <レポート絶対パス>`(アームは recipe spec で両側 fold ごと再学習=069 の drop=group 方式。保存モデル非消費。`--from`/`--to` は**両方必須**(片方だけだと窓照合が fail-closed で即停止=実証済み)。**`--seed`/`--bootstrap-b` は上書きしない**=凍結条件。長時間ジョブ=nohup+監視・056 運用ノート)
- [X] T019 [US2] verdict を確定: **verdict = `report["gate"]["adopted"]` AND `report["subgroups"]["subgroup_guard"]`**(FR-013a の一本化された正本。転記時にレポートの `bootstrap_ci.seed`/`b` が gate-config の凍結値と一致することを照合。個別数値の事後読み替え禁止。harness 併記の `report.decision`/`DECISION=` は 073 参考値 — 乖離時[underpowered 系]は本式を正とし値と cause を併記)で三値判定。`scripts/finish_decomp_coverage.py` を実行し(T013 で作成)、効果量・CI・fold パターン・subgroup 内訳・診断段(T017)・カバレッジ監査結果を specs/088-finish-rank-decomposition/spec.md の測定結果欄(新設)に転記(FR-014/017)。**OOS 後の列選別・閾値変更は行わない**(FR-010)

**Checkpoint**: verdict 確定 — 後始末フェーズは verdict の分岐に従う

---

## Phase 5: User Story 3 - 判定後の後始末 (Priority: P2)

**Goal**: REJECT でも ADOPT でも一貫した規律で閉じる(027/062/070 前例)。該当しない分岐のタスクはスキップし tasks.md にその旨記録。

**Independent Test**: REJECT 経路=revert 後の active serving 予測バイト一致(SC-004)。ADOPT 経路=compat pin モデルの予測バイト一致(SC-005)+昇格ゲート記録。

### Implementation for User Story 3(verdict=REJECT の場合)

- [X] T020 [US3] revert: registry の bump/pin/GROUPS 登録と materialize の結線を戻す(features-018 復帰)。finish_decomposition_features.py+test_finish_decomposition_features.py は build 非結線のまま保全し、単体テスト直呼びで緑を確認(070 同型: モジュール冒頭に「REJECT 済・非結線・負の結果の記録」コメント)
- [X] T021 [US3] features-018 で materialize を再生成し、実 DB E2E で active モデルの予測が変更前とバイト一致(mismatch 0)を確認(SC-004)。features 全テスト緑

### Implementation for User Story 3(verdict=ADOPT の場合)

- [ ] T022 [US3] **N/A(verdict=REJECT のためスキップ)** features-020 の本番モデルを学習: `cd training && uv run python -m horseracing_training train-evaluate --objective pl_topk --calibration isotonic --artifacts-dir <絶対パス>`(長時間ジョブ=nohup。相対パス禁止=[[weights-uri-relative-path-ops-bug]])→ **ユーザー承認を得てから** active 昇格(training の registry CLI)。旧版 pin モデル(features-018 系)の compat-load 予測バイト一致を再確認(SC-005)。昇格ゲートの全数値を spec 測定結果欄に記録

### 共通(どちらの verdict でも)

- [X] T023 [US3] CLAUDE.md の SPECKIT 区間 088 要約を結果(verdict・効果量・教訓)で更新(**手動 Edit のみ・agent-context スクリプト実行禁止**=[[agent-context-hook-clobbers-claudemd]])+ メモリ feature-088-finish-decomposition-result を保存し MEMORY.md に索引追加

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T024 最終確認: features(+ADOPT 時は training/serving)の全テスト緑・ruff クリーン・`git status` で意図した差分のみ(artifacts の誤追跡なし=[[perf-training-eval-speedup]] の symlink 事故前例)・コミット可能状態の報告(コミットはユーザー指示があってから)

---

## Dependencies & Execution Order

- **Phase 1 → 2 → 3 → 4 → 5 → 6 は直列**(この feature は 1 bundle の測定パイプラインであり、US 間の並列余地は構造的に無い)
- Phase 2 内: T002 と T003 は [P](別成果物)
- Phase 3 内: T004-T007 は [P](同一テストファイルだが独立テスト関数・実装前に全部書く)→ T008(実装で緑化)→ T009 → T010 → T011 → T012。T013 は T008 以降いつでも [P]
- Phase 4 内: T016(gate-config 凍結)は**診断段(T017)の結果を観察する前**に完了する(事前登録の汚染防止=直列 T016 → T017)。T018 は T015+T016 完了後(gate-config hash と `--to` が必要)。T017 と T018 は互いに独立(並行可)。T019 は T017+T018 完了後。なお T018(paired-eval)は fold ごと in-memory build のため T015 の parquet を消費しない — T015 が先にあるのは serving 系検証(T012)と materialize 整合のため
- Phase 5: T019 の verdict で REJECT(T020-T021)/ ADOPT(T022)に分岐。T023 は共通
- **重要**: T017(診断段)の結果で T018-T019 を省略してはならない(FR-013・contracts/adoption-gate.md)。paired-eval(T018)は保存モデルを消費しない(アームは recipe spec)ため、本番モデルの学習(train-evaluate)は ADOPT 分岐(T022)でのみ必要

## Parallel Example: User Story 1

```bash
# T004-T007 を先に全部書く(同一ファイル・独立関数):
cd features && uv run pytest tests/test_finish_decomposition_features.py -q   # 全 FAIL を確認
# T008 実装後:
cd features && uv run pytest tests/test_finish_decomposition_features.py -q   # 全緑
```

## Implementation Strategy

- **MVP = Phase 3(US1)まで**: bundle が構築されバイト不変・リーク安全が機械保証された状態。ここで一度止めて検証可能
- Phase 4 は長時間ジョブ(T018 の paired-eval は fold ごと両アーム再学習=十数時間級)を含むため、T015→T016(凍結)を先に済ませ、T018 を nohup で回している間に T017(診断)を流すのが実務順
- Phase 5 は verdict 次第で片方をスキップ(スキップした分岐は tasks.md にチェックせず「N/A(verdict=X)」と注記)
