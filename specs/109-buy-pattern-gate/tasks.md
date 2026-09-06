# Tasks: 買い目パターン採否ゲート(109)

**Input**: Design documents from `/specs/109-buy-pattern-gate/`
**Prerequisites**: plan.md, spec.md, research.md(D1-D14), data-model.md, contracts/(gate-config / pattern-spec / evidence / cli), quickstart.md

**Tests**: 憲法の品質ゲート(leakage test・評価ハーネス test)と spec の SC-003/004/006 が明示的にテストを要求するので、各フェーズに契約テストを含める。

**Organization**: Foundational(行構築・列挙・**freeze**)→ US1(ゲートと自己検証・★中断点)→ US2(screening)→ US3(確認窓と verdict)。パターン族の列挙と凍結は結果を読まない(FR-009)ので Foundational に置く。これにより自己検証は実際の 393 本のマスクで行える(research D6-6・codex plan 採用済み設計への復帰=analyze C1/I1)。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 並列可(別ファイル・未完了タスクに依存しない)
- **[Story]**: US1 / US2 / US3
- 実行は `cd training && uv run …`(107 型 driver・DB は `DATABASE_URL`)

## Path Conventions

- 純 scorer と列挙: `eval/src/horseracing_eval/buy_pattern_gate.py` / `buy_patterns.py`、bootstrap 追加: `eval/src/horseracing_eval/bootstrap.py`
- 単体テスト(numpy fixture のみ・eval に pandas は無い): `eval/tests/unit/test_buy_pattern_gate.py` / `test_buy_patterns.py` / `test_bootstrap_block.py`、CLI 統合テスト(training 環境・実 DB と束が無ければ skip): `scripts/tests/test_buy_pattern_gate_cli.py`
- driver: `scripts/buy_pattern_gate.py`(`--spec-dir` で凍結物の置き場を差し替え可=テスト隔離)
- 凍結物・証拠・verdict: `specs/109-buy-pattern-gate/{gate-config.json, patterns.json, population.json, evidence/, verdict.json}`

---

## Phase 1: Setup

- [X] T001 前提確認: `artifacts/oof/8bdde268…/bundle.json` の `bundle_digest` が `specs/108-arm-e-attestation/evidence/oof-bundle.json` と一致すること、DB(`localhost:15432`)に接続できることを確認し `specs/109-buy-pattern-gate/evidence/preconditions.json` に記録(clean tree の検査は T020 の freeze が行う)
- [X] T002 [P] 骨組み作成: `eval/src/horseracing_eval/buy_pattern_gate.py`、`eval/src/horseracing_eval/buy_patterns.py`、`eval/tests/unit/test_buy_pattern_gate.py`、`eval/tests/unit/test_buy_patterns.py`、`eval/tests/unit/test_bootstrap_block.py`、`scripts/tests/__init__.py`、`scripts/tests/pytest.ini`(rootdir 固定・`cd training && uv run pytest ../scripts/tests -q` で収集されることを確認)、`scripts/tests/test_buy_pattern_gate_cli.py`、`scripts/buy_pattern_gate.py`(argparse に `freeze / selftest / screen / confirm / recompute` と共通引数 `--spec-dir`(既定 `specs/109-buy-pattern-gate`)・`--gate-config-hash`・`--survivors-hash`・`--smoke` を登録・各サブコマンドは `NotImplementedError`。全サブコマンドが実行時の `git rev-parse HEAD` と dirty フラグを `run_code_sha` / `run_tree_dirty` として出力に含める共通関数を持つ)
- [X] T003 [P] import 境界テスト: `eval/tests/unit/test_buy_pattern_gate.py` に「`buy_pattern_gate` / `buy_patterns` が `horseracing_betting` / `horseracing_training` を import しない」を追加

---

## Phase 2: Foundational(行構築・列挙・凍結)

**Purpose**: 賭け候補行、母集団、bootstrap 拡張、hash 規約、パターン族の列挙と凍結。全ストーリーの前提。列挙と freeze は結果を読まないので選択リークにならない。

- [X] T004 `scripts/buy_pattern_gate.py` に BetRow ローダ `load_bet_rows(bundle_path, db_url) -> pandas.DataFrame` と、eval 側へ渡す変換 `to_arrays(df) -> dict[str, np.ndarray]` を実装(DataFrame と parquet は scripts 側だけ。eval の関数は numpy 配列と dict のみを受ける。data-model §1。束 `predictions[race_id][horse_id].win` × `race_horses` × `race_results` × `races`。`interval_days / prev_finish / tataki_2` は started 行限定の SQL `lag() over (partition by horse_id order by race_date)`=`folklore_candidates.sql` の定義を流用。`field_size` は登録頭数。`n_winners`(finish_order=1 の頭数)列を持ち同着レースの行も保持する)
- [X] T005 `buy_pattern_gate.py` に母集団固定 `fix_population(arrays) -> (arrays, PopulationReport)` を実装(入力は numpy 配列の dict。research D1 (a)-(d)。同着レース(`n_winners ≥ 2`)は配列に残しマスクで主判定から除外。除外理由を結果前/結果後に分けて計上・`population_hash`・年別件数。年別件数は gate-config の `expected_races_per_year`(DB 実測 3,451〜3,456・2026 は部分年)と比較して **記録のみ・拒否しない**。INV-P1..P4 を assert)
- [X] T006 [P] `buy_pattern_gate.py` に導出列(numpy 実装) `q / fav_q / entropy / p_rank / p_over_q / ev / p_past_quantile / entropy_past_tertile` を実装(entropy は `dispersion_bands.normalized_entropy`。厳密過去分位はレース日昇順 expanding で「その日より前」から。2008 年は null。同率は `horse_number` 昇順)
- [X] T007 [P] `eval/src/horseracing_eval/bootstrap.py` に **ベクトル化した** `race_block_ratio_bootstrap_ci_v1(num: np.ndarray[patterns×days], den: np.ndarray[patterns×days], day_keys, *, block: Literal["race_day","iso_week","calendar_month"], b, seed, alpha) -> list[RatioBootstrapCI]` を追加(2-D 入力・1-D は 1 行の特例。日→ブロック集約 → 反復ごとのブロック index を **1 回だけ**生成し全パターンで共有(同期抽選)→ 行列演算。各行の `replicates` を返す。**これが 109 の唯一の bootstrap 実装で recompute も同じ関数を呼ぶ**。固定ブロック・クラスタ bootstrap であり moving-block ではない)
- [X] T008 [P] `eval/tests/unit/test_bootstrap_block.py`: `block="race_day"` の 1 行入力の点推定と反復が既存 `race_day_cluster_ratio_bootstrap_ci_v1` と同じ seed で **許容誤差 1e-12** で一致(加算順が異なるのでビット一致は要求しない)/ 2 行入力の各行が 1 行入力と同じ index を使う(同期抽選)/ 週・月ブロックで n_blocks が減る / ブロック内の日が同時に選ばれる / 同一入力の 2 回呼び出しがビット一致
- [X] T009 `buy_pattern_gate.py` に第二集計 `independent_aggregate(bets)`(純 Python dict 累積)と正準キー `selection_hash(bets)`(sorted `(race_id, horse_number)` の `stable_hash`)を実装。不一致は `AggregationMismatch`
- [X] T010 `buy_pattern_gate.py` に `load_gate_config(path, expected_hash)` と `verify_frozen(kind, path, expected_hash)`(kind ∈ patterns / population / survivors・`stable_hash` 照合)を実装(不一致はそれぞれ `GateConfigMismatch` / `FrozenArtifactMismatch`。`patterns_hash` / `code_sha` / `windows.confirmatory[1]` が空の設定は `selftest / screen / confirm` で拒否)
- [X] T011 [P] `eval/tests/unit/test_buy_pattern_gate.py`(numpy fixture): 母集団固定の各除外が流れ図に計上(1 頭でもオッズ/p 欠損なら除外)/ 第二集計の一致と例外 / `selection_hash` の行順不変 / gate-config・patterns・population・survivors の **4 種それぞれ**の hash 不一致 fail-closed / 厳密過去分位が同日以降を見ない
- [X] T012 `eval/src/horseracing_eval/buy_patterns.py` に軸・帯・述語を実装(research D4 の境界を定数で。`Condition(axis, field, op, lo, hi, value, available_at)`、`op ∈ {eq, in_band, is_true, is_null}`、`race_class` 正準化、季節=6〜9 月、`tataki_2`。`won / finish_order / payout` を field に持つ条件は `ForbiddenField`)
- [X] T013 `buy_patterns.py` に列挙 `enumerate() -> Family` を実装(辞書順決定論。C 単独 13 + C×H 84 + C×EV 28 + M 単独 10 + M×H 18 + X 単独 12 + M×X 192 + F 36 = 393。F の単一因子 × H は生成しない。`available_at` は最遅値。`controls` 4 本と `control_aliases {ev1_all: X.ev_ge_1}` を別キー)
- [X] T014 `buy_patterns.py` に適用 `mask_for(pattern, rows) -> np.ndarray[bool]` を実装(欠損は False・`horse_rule` は AND)
- [X] T015 [P] `eval/tests/unit/test_buy_patterns.py`: 件数 393 / id 一意 / 重複 9 本不在 / 帯が互いに素で被覆 / レース単位のみは `horse_rule` 非 null / `available_at` 最遅値 / 禁止 field で例外 / 列挙 2 回で同一 JSON
- [X] T016 [P] `eval/tests/unit/test_buy_patterns.py`: 結果並べ替え不変(SC-006)/ 欠損述語は False / 同率一意化 / 手計算 fixture で C×EV と M×X の各 1 本が正しい馬を選ぶ
- [X] T017 `specs/109-buy-pattern-gate/gate-config.json` を contracts/gate-config.md の形で作成(`patterns_hash` / `code_sha` / `windows.confirmatory[1]` は空。`expected_races_per_year` を DB 実測から記入・出所を `_source` に注記)
- [X] T018 `scripts/buy_pattern_gate.py` に `freeze` を実装: `git status --porcelain` 非空なら拒否 → `enumerate()` → `patterns.json` 書き出し → `patterns_hash` / `code_sha=HEAD`(=列挙コードの SHA)/ `windows.confirmatory[1]`(DB の最終確定レース日)を gate-config に転記 → hash 表示。既存 `patterns.json` は拒否。`--smoke` かつ非既定 `--spec-dir` のときのみ dirty-tree 検査を免除。freeze 後に tree が dirty になること(生成物)と、以降のサブコマンドは dirty を検査せず `run_code_sha` を記録することを docstring に明記
- [ ] T019 コミット(freeze の clean tree 条件のため・path 明示列挙): `eval/src/horseracing_eval/{buy_pattern_gate.py,buy_patterns.py,bootstrap.py}`、`eval/tests/unit/test_buy_*`、`eval/tests/unit/test_bootstrap_block.py`、`scripts/tests/`、`scripts/buy_pattern_gate.py`、`specs/109-buy-pattern-gate/`(gate-config.json 含む)、`CLAUDE.md`、`.specify/feature.json`
- [ ] T020 `freeze` を実行して `patterns.json`(393 本)と転記済み `gate-config.json` を生成し、hash を `evidence/freeze.json` に記録
- [ ] T021 凍結物をコミット(path 明示): `specs/109-buy-pattern-gate/{patterns.json,gate-config.json,evidence/freeze.json}`。以後の変更は禁止(変更は新しい feature)

**Checkpoint**: 賭け候補行・母集団 hash・393 本の凍結族・gate-config hash が揃い、テスト緑。

---

## Phase 3: User Story 1 - ゲート契約の凍結と自己検証 (Priority: P1) 🎯 MVP ★中断点

**Goal**: 判定ゲート(集計・p 値・Holm・降格・感度・状態機械)を純 scorer として実装し、**凍結済みの 393 本の実マスク**で合成注入のサイズと検出力曲線を測る。サイズが片側 2.5% を超えるならゲートを直すまで US2 に進まない。

**Independent Test**: 実データの結果を判定に使わず、凍結済みマスクと合成データだけで `evidence/selftest.json` が生成され、SC-001(サイズの下側限界 ≤ 2.5%・検出力曲線・MDE・降格率)が確認できる(対照の既知帯は US3 で確認する)。

### 実装

- [X] T022 [US1] `buy_pattern_gate.py` にパターン適用と日別ベクトル化 `apply_masks(arrays, masks, day_universe) -> DayMatrix(payout[patterns×days], stake[patterns×days], bets)` を実装(日 index は窓の `day_universe`=sorted race_date 全体で固定・賭けのない日も列として持つ・`n_days` はそのパターンが stake>0 の日数・research D11)
- [X] T023 [US1] `buy_pattern_gate.py` に判定統計 `score(day_matrix, cfg) -> list[PatternScore]` を実装: T007 の bootstrap(主 b=20,000・seed 固定・全パターン同期の日抽選)、`p_profit = (1+#{R*−R̂ ≥ R̂−1})/(B+1)`、`p_futility = (1+#{R̂−R* ≥ c−R̂})/(B+1)`(c=1+δ)、参考 max-T 同時上限、分母ゼロ反復 → `NO_DECISION(zero_denominator_replicate)`・Holm 上 p=1、降格、leave-one-hit-out、`by_year`、**`mde_80 = (z_{1−α/m} + z_{0.80})·sd(replicates)`(α=0.025・m=生存者数、screening では m=1)**
- [X] T024 [US1] `buy_pattern_gate.py` に `holm_one_sided(pvalues, alpha=0.025)`(step-down。**降格・NO_DECISION は p=1 として m に数え、既存 `holm_adjust` のように途中で break しない**=降格 1 本が他の生存者の判定を巻き添えにしない)と状態機械 `decide(...)`(優先順位: SCREENED_OUT → NO_DECISION(cannot_run | demoted | zero_denominator_replicate | sensitivity_split)→ ADOPT_CLOSE(利益検定が全分析版で棄却)→ RULED_OUT(利益非棄却 ∧ futility 棄却)→ NOT_ADOPTED)を実装
- [X] T025 [US1] `buy_pattern_gate.py` に感度分析 `sensitivities(arrays, masks, cfg)` を実装: 週/月ブロック(T007)、同着レース(`n_winners ≥ 2`)をマスクで戻した `equal_split`(odds/n_winners)と `half_odds`(odds/max(2, n_winners))の再集計。各版で T023 → T024 を回し分類の一致を返す
- [X] T026 [US1] `buy_pattern_gate.py` に対照 `controls(rows) -> {no_bet, favorite, cap11_all, cap21_all}` を実装(`no_bet` は `roi=None, accounting_sentinel=1.00`・`ev1_all` は `alias_of="X.ev_ge_1"` の表示のみ)
- [X] T027 [US1] `buy_pattern_gate.py` に合成注入 `synthesize(rows, masks, target_idx, rho, shape, cfg, rng) -> won_synthetic` を実装(research D6: `π ∝ q·exp((λ₀+u_day)·s)`・λ₀ は u を積分した周辺 ROI が ρ になるよう最後に二分法で解く・形 `klmin_roi_tilt / long_odds_top20pct / days_10pct`・感度 `odds_neutral` は達成不能なら `infeasible`・各レース勝者 1 頭 categorical・境界帰無 `target_1.00_others_le_1.00` の制約つき構成、実行不能は無効報告)
- [X] T028 [US1] `buy_pattern_gate.py` に τ 推定 `estimate_day_effect(rows, mask_ref, rng)` を実装(発見期の日別払戻総額の実測分散と categorical 条件付き分散 `V_d` を基準にした simulation matching)
- [X] T029 [US1] `buy_pattern_gate.py` に自己検証 `selftest(rows, family, cfg, rng) -> SelftestReport` を実装: **対象は凍結済み 393 本の実マスク**。**判定は本番と同じ `decide()`・同じ分析版集合(日クラスタ+週/月ブロック)で行う**(同着感度は合成では発生しない)。サイズ=賭け数の分位 5 点(最小・25%・中央・75%・最大)の代表パターンを生存者集合とみなし、各 i について **全体境界**(i=1.00・他 ≤1.00)と **部分帰無**(i=1.00・他の 1 本=1.10・残り ≤1.00)の 2 構成で外側 2,000 × 内側 20,000 を回し、対象 i が ADOPT_CLOSE になる率の点推定と二項片側 95% **下側**・上側限界を報告(合格=下側限界 ≤ 2.5%)。検出力曲線 ρ ∈ {1.00,…,1.15} を同じ 5 本で外側 1,000 × 内側 5,000・形 3 種+オッズ中立・80% MDE・降格率。陰性対照 ρ=0.796。「全 393 本の同時帰無」は行わない(m と生存者集合が未定義)。対象ごとに `evidence/selftest-partial-<i>.json` に checkpoint を書き再開できる。全 393 本の screening 窓 `selection_hash` を記録する(screen が照合)
- [X] T030 [US1] `scripts/buy_pattern_gate.py` に `selftest` を実装: gate-config hash 照合 → 行構築・母集団固定 → `patterns.json` 読込と hash 照合 → T029 → `evidence/selftest.json`(`passed`・`run_code_sha`・`run_tree_dirty`・実測所要時間。smoke では `smoke=true, passed=true`)。下側限界 > 2.5% なら exit 2

### テスト

- [X] T031 [P] [US1] `eval/tests/unit/test_buy_pattern_gate.py`: p 値 +1 補正と向き / futility の向き / Holm が降格を棄却せず **かつ降格 1 本が他の棄却を妨げない**(m 維持) / 優先順位(両検定棄却で ADOPT_CLOSE)/ NOT_ADOPTED 到達 / 感度分割で NO_DECISION / 分母ゼロ反復で NO_DECISION かつ m 不変 / `mde_80` の式
- [X] T032 [P] [US1] `eval/tests/unit/test_buy_pattern_gate.py`: 注入がレース内で勝者ちょうど 1 頭 / 周辺 ρ の再現(u 込み ±0.005)/ 形 3 種で選択外の π が q に一致 / オッズ中立の `infeasible` / 境界帰無の制約違反が無効報告
- [ ] T033 [US1] まず `selftest --extrapolate`(外側 20 反復)で総所要時間を外挿して quickstart に記録し、日単位なら checkpoint 再開を使って本実行。`evidence/selftest.json` を生成。サイズの下側限界 ≤ 2.5% なら Phase 4 へ。超過なら **判定統計と注入(T023/T027/T028)のみ**修正して再実行(列挙と導出列は不可・修正内容を research D6 に追記)。実測所要時間を quickstart に記録。検出力曲線と 80% MDE は実力として記録し合否にしない
- [ ] T033a [US1] コミット(path 明示): `specs/109-buy-pattern-gate/evidence/selftest.json` と partial(`screen` は clean tree 必須)

**Checkpoint ★中断点**: `evidence/selftest.json` の `passed=true` をコミット済み。実データの結果はまだ判定に使っていない。

---

## Phase 4: User Story 2 - 凍結パターン一覧の screening 窓実行 (Priority: P2)

**Goal**: 393 本を発見期 → 資格期の時間分割で流し、生存者 0〜5 本を機械的に選ぶ。

**Independent Test**: `evidence/screening-*` と `survivors.json` が生成され、`recompute --window screening-discovery` が一致する。

- [X] T034 [US2] `scripts/buy_pattern_gate.py` に `screen` を実装: 非 smoke は clean tree 必須 → gate-config hash 照合 → `selftest.passed` 確認 → 行構築・母集団固定 → 行スナップショットを `artifacts/109/rows-{discovery,qualification,confirmatory}.parquet` に固定(`rows_hash`)→ 全パターンの `selection_hash` が selftest の記録と一致することを照合 → `population.json`(`rows_hash`・窓ごとの `day_universe`・年別件数・流れ図)→ 発見期で 393 本 + 対照を T022/T023 → 点推定 ≥1 かつ非降格を資格期で再評価 → 点推定 ≥1 かつ非降格 → `min(LCB)` 降順 → 同一 `selection_hash` 統合 → 発見期∪資格期の賭け集合で Jaccard>0.9 の下位除去 → 上限 5 → `evidence/screening-{discovery,qualification}-summary.json` と `artifacts/109/screening-*-bets.parquet`(sha256 を summary に)と `evidence/survivors.json`(既存なら拒否)。SCREENED_OUT 理由別件数(discovery / qualification / rank / duplicate / **not_fired**)。smoke では `selftest.smoke=true` を通過扱い。証拠と `population.json` に `run_code_sha` を記録
- [X] T035 [US2] `scripts/buy_pattern_gate.py` に `recompute --window <w>` を実装(bets parquet と `population.json` の `day_universe` だけから T023 と同じ関数で主判定と感度 4 版を再計算し summary とビット一致。不一致は exit 1)
- [ ] T036 [US2] `screen` を実行。`population.json`、対照の参考値、`survivors.json`(0〜5 本)を確認し、`recompute` を discovery / qualification で一致確認
- [ ] T036a [US2] コミット(path 明示): `specs/109-buy-pattern-gate/{population.json,evidence/screening-*-summary.json,evidence/survivors.json}`(生存者集合を git に固定してから `confirm` を走らせる=`confirm` は clean tree 必須)

**Checkpoint**: 生存者 0 本でも Phase 5 の `confirm` は実行する(対照の確認窓集計と verdict の記録のため)。

---

## Phase 5: User Story 3 - 確認窓での一度きりの判定と記録 (Priority: P3)

**Goal**: 生存者を確認窓で一度だけ流し、5 状態の verdict を append-only で記録する。対照 4 本は生存者数に関わらず確認窓で集計する(SC-002)。

**Independent Test**: `confirm` が 4 hash の照合後にしか走らず、`verdict.json` に状態件数・生存者ごとの p 値と感度・証拠参照・限界注記・再開条件が含まれ、`recompute --window confirmatory` が一致する。

- [X] T037 [US3] `scripts/buy_pattern_gate.py` に `confirm` を実装: 非 smoke は clean tree 必須 → 4 hash 照合 → `artifacts/109/rows-confirmatory.parquet` を読み `rows_hash` 照合(DB を再読しない)→ **対照 4 本を常に集計** → 生存者ごとに T023(利益・futility・max-T)→ T025 感度 4 種 → Holm(m=生存者数)→ T024 → `evidence/confirmatory-{bets.parquet,summary.json}` → `verdict.json`(data-model §7。`evidence_refs` に窓・パス・sha256、`per_survivor` に `available_at` / `historical_close_signal`、`run_code_sha` / `run_tree_dirty`。既存パスは拒否・`--force` なし。生存者 0 本なら `SCREENED_OUT=非生存者数`(理由別内訳つき)と対照値と結論のみ)。`per_survivor` の列名は summary と同じ `p_profit_one_sided` / `p_futility_one_sided`
- [X] T038 [US3] `buy_pattern_gate.py` に結論生成 `conclusion_ja(states, cfg, selftest)` を実装(禁止表現「全パターン REJECT」「効果なし」「利益パターンは存在しない」・`limitations` 固定 5 項(closing_price_leak / payout_approximation / dead_heat_handling / no_correction_history / win_only)・`resume_conditions` 3 項・80% MDE の実力を含む)
- [ ] T039 [US3] `confirm` を実行し `verdict.json` を生成。`recompute --window confirmatory` の一致、対照の既知帯(cap21_all 0.80〜0.83 / favorite 0.76〜0.80 / no_bet sentinel 1.00。cap11_all と EV 別名は記録のみ)、状態の優先順位を確認

### テスト

- [X] T040 [P] [US3] `eval/tests/unit/test_buy_pattern_gate.py`: 結論文の禁止表現 / `limitations` 5 項と `evidence_refs` 必須 / verdict 必須キー / 生存者 0 本の verdict 形(対照値を含む)
- [X] T041 [P] [US3] `scripts/tests/test_buy_pattern_gate_cli.py`(training 環境で実行。`localhost:15432` の実 DB と束が無ければ skip。eval の testcontainer conftest は使わない): 一時 `--spec-dir` に gate-config をコピーして `--smoke`(2010 年の 1 か月・反復 200・dirty-tree 免除)で `freeze → selftest → screen → confirm` を通し、**4 hash(gate-config / patterns / population / survivors)を各 1 値改変**したとき該当サブコマンドが開始前に拒否 / `verdict.json` 既存で拒否 / `selftest.passed=false` を書き込むと `screen` 拒否 / `--smoke` が効果数値を redact し差の存在のみ表示 / 既定 `--spec-dir` での `--smoke` は拒否

---

## Phase 6: Polish & 後始末

- [ ] T042 spec.md 冒頭に実測結果を転記(状態件数・生存者・対照値・80% MDE・サイズ検定・所要時間・限界)。ADOPT_CLOSE が出た場合は FR-019 の下に後続 feature への引き継ぎ条件(106 実購入記録 / 065 凍結オッズ)を記録
- [ ] T043 [P] `ruff check eval scripts/buy_pattern_gate.py scripts/tests` と `cd eval && uv run pytest -q`(pandas 非依存で緑)と `cd training && uv run pytest ../scripts/tests -q` の緑。`git diff --stat -- db api front betting probability serving features ops admin training/src` が空(SC-007)
- [ ] T044 [P] memory 更新(`/Users/kuwatawaku/.claude/projects/-Users-kuwatawaku-workspace-horseracing/memory/` に 109 の結果を 1 ファイル + MEMORY.md 1 行)。CLAUDE.md の 109 要約を「完了」に更新
- [ ] T045 最終コミット(path 明示列挙・`git add -A` 禁止): `specs/109-buy-pattern-gate/`(verdict.json・evidence/confirmatory-summary.json・生存者と対照の確認窓 bets `evidence/confirmatory-bets.parquet`・spec.md・quickstart の実測欄)、`CLAUDE.md`、memory。`artifacts/109/` の全賭け行と行スナップショットはコミットせず `evidence_refs` の sha256 で参照

---

## Dependencies

- Phase 1 → Phase 2(T004-T016 → T017 → T019 コミット → T020 freeze → T021 コミット)→ US1(★中断点)→ US2 → US3 → Polish の直列。
- US1 の `selftest` は T020 の `patterns.json` に依存(実マスクで自己検証)。
- US2 の `screen` は `selftest.passed=true` と T033a のコミット(clean tree)に依存。
- US3 の `confirm` は `survivors.json` と T036a のコミット(clean tree)に依存し、生存者 0 本でも対照集計と verdict のために実行する。

## Parallel Execution Examples

- Phase 2: T006 / T007 / T008 / T011 は T004 と並列。T012-T016 は T004-T011 と並列(別ファイル)。
- US1: T031 / T032 は T023-T028 と並列。
- US3: T040 / T041 は T037-T038 と並列。
- Polish: T043 / T044 並列。

## Implementation Strategy

1. **MVP = Foundational + US1**(凍結族 + ゲート + 自己検証)。ここで止まっても「このゲートの検出力と偽陽性率」という独立した成果が残る。
2. US2 で screening。生存者 0 本で終わる可能性が高い(期待値の開示どおり)が verdict として記録する。
3. US3 は生存者 0 本でも対照集計と verdict のために実行する。
4. 実行時間は自己検証が支配的(4 時間級の見込み・T033 で外挿と実測・checkpoint 再開可)。統計的中断点は T033 のサイズ検定のみ。
