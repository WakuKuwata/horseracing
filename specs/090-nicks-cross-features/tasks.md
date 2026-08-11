# Tasks: ニックス(種牡馬×母父の配合相性)特徴

**Input**: Design documents from `/specs/090-nicks-cross-features/`

**Prerequisites**: plan.md, spec.md, research.md(D1-D10 + セルフレビュー), data-model.md,
contracts/feature-columns.md, quickstart.md

**Tests**: 含む(憲法の品質ゲート: リーク境界・決定性・parity の各テストは非交渉)。

**Organization**: US1(残差の供給=MVP)→ US2(事前登録ゲートで決着)→ US3(判定後の後始末)。
作業場所は worktree `.claude/worktrees/090-nicks-inbreeding`
(ブランチ `worktree-090-nicks-inbreeding`・base=089 マージ済み main `c77d9fc`)。

**この feature は null-is-success 型**。判定に最短で到達し、撤退手順を先に確定させる順序で
組んである。

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

**Purpose**: 変更前の緑と、判定に使う現行モデルを固定する

- [X] T001 変更前ベースラインを記録: `uv run --project features pytest features/tests -q` を worktree で実行し全緑を確認。あわせて実 DB で現行 active モデル版・FEATURE_VERSION(features-018 の想定)・**active モデルの `metadata.feature_hash` を実測**して記録する(T008 の compat pin と T017/T018 の後始末照合に使う。値の推測固定は禁止)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 定数の凍結。US1 実装の前提であり、**評価より前に固定する**ことが憲法 III の要求

- [X] T002 features/src/horseracing_features/nick_cross_features.py を新規作成し、**モジュール定数のみ**を先に確定させる: **`LAMBDA_L0 = 350.0` / `LAMBDA_L1 = 350.0`**(data-model §4 の値をそのまま写す。経験ベイズ最適 λ* = σ²_within/σ²_between = 0.0661/0.000185 ≈ 357 の丸め)・`EPS_LO = 1e-4` / `EPS_HI = 0.9`。**070 の λ=5.0 を流用しない**(実測で 2 桁小さく、n=20・0 勝のセルに −1.609 が付く。research D5a)。**旧案の `MIN_CELL` は作らない**(連続的な部分プーリングに変更したため閾値は存在しない)。実行時引数にしない(INV-N5/INV-N6)。値の導出根拠を docstring に記載

**Checkpoint**: 定数が凍結された → 以後、評価結果を見た変更は禁止

---

## Phase 3: User Story 1 - 配合相性の残差を特徴として供給する (Priority: P1) 🎯 MVP

**Goal**: 2 列(`nick_lift_log` / `nick_obs_count`)を、リーク安全・決定的・parity 一致で
特徴行列に供給する。

**Independent Test**: 手計算 fixture で残差・階層選択・観測数が定義どおりになり、
リーク境界(自馬除外・同日除外・結果非流入)が機械的に成立し、materialize と in-memory が
ビット一致する。

### Tests for User Story 1(実装と同一 PR 内・先に書いて赤を確認)

- [X] T003 [P] [US1] features/tests/unit/test_nick_cross_features.py: 手計算 fixture テスト — 小さな決定的データで `expected = p_sire × p_damsire / p_overall`、`shrunk = (w + λ·expected)/(n + λ)`、`nick_lift_log = log(shrunk) − log(expected)`、`nick_obs_count` を厳密値で検証(contracts §列契約 / data-model §算出の定義)。期待値ちょうどのセルで残差 ≈ 0 になることも固定
- [X] T004 [P] [US1] features/tests/unit/test_nick_cross_features.py: 入れ子部分プーリングのテスト — (a)L0 の観測が増えるほど `mu_L0` が L1 推定値から離れて生の交差率へ近づく(**閾値ではなく連続的**であること) (b)**leave-child-out**: L1 の推定に当該 L0 セルの観測が含まれていないこと(L0 セルの実績だけを変えても mu_L1 が動かない) (c)L0 も L1 も観測ゼロなら `mu_L0 = expected` → 残差 0・`obs_count=0` (d)**系統×系統の階層が存在しないこと**(research D5a の設計決定を退行から守る)
- [X] T005 [P] [US1] features/tests/unit/test_nick_cross_features.py: リーク境界テスト — (a)**自馬除外**: 対象馬自身の過去実績を変えても出力不変(INV-N2/SC-002) (b)**同日除外**: 同一開催日の他馬の実績を足しても出力不変 (c)**strictly-before**: 未来のレースを足しても出力不変 (d)対象レースの結果・オッズを変えても出力不変(INV-N1/SC-001)
- [X] T006 [P] [US1] features/tests/unit/test_nick_cross_features.py: 欠損と決定性 — (a)父名または母父名が欠損 → NaN (b)観測が薄い行は NaN でなく親縮約値+`obs_count=0`(INV-N9・**0 埋めでも全 NaN でもない**ことを両方向で固定) (c)同一入力から同一出力(INV-N4) (d)全列 float64

### Implementation for User Story 1

- [X] T007 [US1] features/src/horseracing_features/nick_cross_features.py: 算出本体 `build_nick_cross_features(frames, *, target_race_ids: frozenset | None = None) -> DataFrame` を実装 — `pedigree_features._other_offspring(targets, runs, key="sire_name", extra=["damsire_name"])` を再利用して交差セルの strictly-before + 自馬除外カウントを得る。3 つの marginal(父・母父・全体)は**それぞれ独立に as-of 取得**する(INV-N3)。縮約は **L0 → L1 → 独立性期待値の入れ子部分プーリング**で、L1 は **leave-child-out**(当該 L0 セルの観測を除く)。硬い閾値による段の切り替えはしない(data-model §2)。**072 投影に対応**: `target_race_ids=None` は full build(parity の基準)、指定時は cross-entity キー(父名・母父名)で source を絞る(026 と同型)。**ただし `p_overall`(全体 as-of 勝率)は race-level/global primitive であり投影時も絞らず全履歴から計算する** — 絞ると full build と値が変わり INV-N6 の parity が破れる(026 には global 集計が無いため「026 と同型」では埋まらない穴)。他の as-of ブロックは全て投影対応済みで、未対応だと単一レース予測が全履歴計算になる。T003-T006 が緑になること
- [X] T007a [P] [US1] features/tests/unit/test_projection_blocks.py の cross-entity 群(pedigree / debut_pedigree の隣)に追加: **072 投影 parity** テスト — `projected == full.loc[target_keys]` を `check_exact=True` で検証(投影が値を変えないこと)。エッジ(デビュー馬・父名欠損・母父系統欠損・同日複数レース)と**投影時に `p_overall` が full build と一致すること**を含める
- [X] T008 [US1] features/src/horseracing_features/registry.py: 2 列(`nick_lift_log` / `nick_obs_count`)を group `nick_cross` として登録し、`FEATURE_VERSION` を features-018 → **features-021** に更新(019 は焼却済み・020 は 088 予約)。compat pin を 058/061 方式で追加し、運用モデル(features-018)の serving を維持する。**あわせて FEATURE_VERSION を pin している既存テスト 8 ファイル・8 箇所(実測)を更新する**: `features/tests/unit/` の test_closing_figure:98 / test_feature084_leak_guard:47 / test_past_market_leak:144 / test_speed_figure:201 / test_feature023_leak_guard:30 / test_rating_features:221 / test_raw_column_leak:178(以上 `FEATURE_VERSION == "features-018"`)+ test_materialize_core:44(`manifest.feature_version`)。更新しないと bump と同時に赤化する。**一方 `COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-018"]` を読む参照(11 箇所)は 018 エントリを残す限り壊れないので触らない**(消すと 018 の compat 履歴を壊す)
- [X] T008a [US1] serving/tests: **features-018 が features-021 の下で serve 可能**であることのテストを新規作成(061/069 の compat テストと同型)。T001 で実測した active モデルの `feature_hash` を pin する
- [X] T009 [US1] features/src/horseracing_features/materialize.py: `build_asof_features` に nick ブロックを **1 箇所だけ**結線する(025 の単一 as-of 源。事前生成経路と逐次計算経路が同じ関数を通ることが parity の前提)
- [X] T010 [P] [US1] features/tests: 純加算検証 — nick 列の追加が既存列の値を 1 ビットも変えないこと(左結合・キー一意・列名重複なしを機械検証。058 の additive-merge 検証と同型・INV-N8)
- [X] T011 [US1] materialize parity を実 DB で確認: `python -m horseracing_features materialize` 後、事前生成と逐次計算が `check_exact=True` かつ `check_dtype=True` で一致すること(INV-N6)。あわせて `source_fingerprint` が変化していないことを確認(INV-N7・新規ソース列ゼロ)
- [X] T012 [US1] features/src/horseracing_features/cli.py: `nick-coverage-audit` サブコマンドを新規追加(`--from/--to/--json/--database-url`)— **年別 × `nick_obs_count` の帯別**(0 / 1-19 / 20-99 / 100+)の件数と割合、および欠損率を出力(FR-015/SC-004)。連続的な部分プーリングでは「どの段に落ちたか」という離散区分が無いため観測数の分布で代替する。**training 側の既存 `coverage-audit` は 069 の過去市場専用で `--group` を取らない(実査確認済み)ため変更しない**

**Checkpoint**: 特徴の供給は完成 — 単体緑・parity 一致・カバレッジ監査が出力できる

---

## Phase 4: User Story 2 - 事前登録したゲートで採否を決着させる (Priority: P2)

**Goal**: 実装前に凍結した設定で本番相当の同一条件比較を 1 回だけ実行し、3 値の判定を得る。

**Independent Test**: 凍結設定のハッシュと実行結果が機械照合され、判定が「採用」「不採用」
「判定不能」のいずれか 1 つに定まる。

- [X] T013 [US2] specs/090-nicks-cross-features/gate-config.json を作成し**凍結**する — **必須キーを漏らさないこと**: `evaluation_contract_version`(現行契約版。**欠けると `--confirmatory` が即 fail-closed**)/ `primary` / `top_noninferior` / `calibration` / `subgroup_guard` / `eval_window` / `bootstrap`。`top_noninferior` と `calibration` は**トップレベル**に置く(069 の注記)。**この時点で評価数値は一切見ていないこと**を担保するため T015 より前に実施し、canonical ハッシュを記録する
- [X] T014 [US2] カバレッジ監査(T012)の結果を確認する — 実効カバレッジを把握し、`nick_obs_count` が**全行ゼロ**(候補と基準の入力が実質同一)でないことを確認する。**この確認は判定の省略を許すものではない**(診断結果を理由に判定段を飛ばすことは禁止)。カバレッジが「薄い」ことは NO_DECISION の理由にならない(contracts の 3 分岐定義を参照)
- [X] T015 [US2] 本番相当 pl_topk paired 評価を **1 回**実行する。**コマンドは contracts/feature-columns.md §判定コマンド を正本として転記**すること。`--from/--to`(gate-config の eval_window と同値)・`--seed`・`--bootstrap-b`・`--gate-config`・`--subgroups` は**すべて必須**: 窓を渡さないと `assert_confirmatory` が eval_window 照合を丸ごとスキップし凍結が無効化される / seed・B は gate-config から読まれず CLI 既定になる / `--gate-config` 無しでは confirmatory が必ず失敗する / `--subgroups` 無しでは判定式の片翼が出ない(いずれも実装を実査して確認)。**簡易目的関数(binary)での打ち切りは採否に用いない**(FR-012)
- [X] T016 [US2] 判定の確定: **contracts/feature-columns.md §判定式(3 分岐)を唯一の正本**として ADOPT / REJECT / NO_DECISION の 1 つに機械的に定める。**NO_DECISION は「評価が実行不能」な場合に限る**(完走しない・対象レース皆無・カバレッジゼロ)。ボーダーの数値を理由に NO_DECISION と宣言してはならない。harness の `report.decision` は参考値であり正本ではない。判定と根拠数値を research.md に転記する

**Checkpoint**: 軸が決着した — 以後この feature でニックスを再検討しない

---

## Phase 5: User Story 3 - 判定後の後始末と記録 (Priority: P3)

**Goal**: 判定に応じて運用への影響をゼロにする(不採用)か、更新する(採用)。

**Independent Test**: 不採用の後始末後、運用モデルの予測が判定前とバイト一致する。

- [X] T017 [US3] **不採用の場合**: FEATURE_VERSION の bump(T008)と build 結線(T009)**のみ**を revert する。**T008 で更新した版 pin テスト 7 ファイル(19 箇所)と T008a の compat テストも features-018 側へ戻す**。`nick_cross_features.py` と単体テストは**非結線で保全**し、テストが直接呼び出しで緑のままであることを確認(062/070 同型・負の結果の記録)
- [X] T018 [US3] **不採用の場合**: 実 DB E2E で運用モデルの予測が判定前と**バイト一致**することを確認(SC-006)。materialize 済み parquet を features-018 相当に戻す手順も実施する
- [~] T019 [US3] **該当なし(REJECT のため実施せず)** — 採用の場合: 昇格手順を 061/069 と同型に分解して実施する — (a)`train-evaluate` で本学習(paired-eval は fold ごと再学習で保存モデルを作らないため、昇格には別途本学習が必要) (b)判定に用いた全指標の非悪化を記録(SC-007) (c)モデルを register (d)**ユーザー承認を得てから** active 昇格(自動昇格はしない) (e)serving の compat と予測経路を実 DB で確認
- [X] T020 [US3] 判定結果(採否・指標差・カバレッジ実測)を spec.md と research.md に転記する(FR-014)

**Checkpoint**: 判定と後始末が完了 — 運用状態が確定

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T021 [P] 全パッケージ検証: `uv run --project features pytest features/tests -q`・`uv run --project training pytest training/tests -q`・`uv run --project serving pytest serving/tests -q` と ruff(変更ファイル)がすべて緑
- [X] T022 quickstart.md の全手順を通しで実施し、記載どおりに再現することを確認(乖離があれば quickstart を実態に合わせて修正)
- [X] T023 CLAUDE.md の SPECKIT 区間の 090 エントリを実測値つきで更新(採否・カバレッジ・テスト数・残作業。agent-context スクリプトは使わず Edit で手動)

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1(T001)→ Phase 2(T002・**定数凍結**)→ US1(T003-T012)→ US2(T013-T016)→
  US3(T017-T020)→ Polish
- **T013(ゲート凍結)は T015(評価実行)より前でなければならない**(数値を見てから条件を
  作ることを構造的に防ぐ)
- US3 は T016 の判定結果で分岐する(T017/T018 は不採用時、T019 は採用時)

### Within-Story Order

- US1: T003-T006(テスト・並列可)→ T007 →(T007a 並列可)→ T008 → T008a → T009 →(T010・T012 並列可)→ T011
- US2: T013 → T014 → T015 → T016(直列。凍結 → 確認 → 実行 → 判定の順序が契約)
- US3: T016 の分岐に従い T017 → T018(不採用)または T019(採用)→ T020

### Parallel Opportunities

- T003/T004/T005/T006(同一ファイルだが独立したテストケース群)
- T010(純加算検証)と T012(カバレッジ監査 CLI)は T009 後に並列可
- T007a(投影 parity)は T007 直後に並列可 / T008a(compat)は T008 直後
- T021 は他の Polish タスクと並列可

## Implementation Strategy

**MVP = US1**: 特徴の供給が完成すれば、単体テスト・parity・カバレッジ監査で独立に検証でき、
「作れた」ことが確定する。US2 で初めて「効くか」が決まり、US3 で運用状態が確定する。

**撤退を先に設計してある**: 不採用時の手順(T017/T018)は判定前から確定しており、結果を見て
から「どう扱うか」を考える余地がない(憲法 III)。

## Notes

- **定数(λ / 閾値 / clip)は T002 で凍結し、以後変更しない**。評価結果を見た調整は憲法 III 違反
- **既存の血統特徴(026/032/056)は削除も変更もしない**(同時変更は寄与の帰属を不能にする)
- 新規のネットワーク取得は全フェーズで **0 件**(SC-008)
- codex は 3 回目で取得成功(research D10)。**指摘により λ(5.0 → 350)と縮約方式
  (単段 → 入れ子部分プーリング + leave-child-out)を撤回済み**。analyze は 2 周実施
