---
description: "Task list for 101-recency-weighting"
---

# Tasks: recency weighting — 学習の時間重み

**Input**: `specs/101-recency-weighting/`(spec / plan / research / data-model / contracts / quickstart / codex-review)

**Tests**: **含む。** 憲法の品質ゲート(leakage test / 確率整合性 test / 評価ハーネス test)が要求し、
spec 側も検証を規定している(FR-002/003/006a/014)。

**Organization**: **Phase 3(US1 の判定)が中断点**。そこを通過するまで Phase 4/5 は詳細化しない。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 並列実行可(別ファイル・未完了タスクへの依存なし)
- **[Story]**: US1 / US2 / US3(Setup・Foundational・Polish には付けない)

## Path Conventions

- `training/src/horseracing_training/` — 重みの計算・配線・レシピ
- `scripts/` — 判定 driver・日付基準の探索
- `training/tests/unit` / `training/tests/integration`
- `eval/` は**読むだけで変更しない**(100 の per-race 証拠がそのまま使える)

---

## Phase 1: Setup(ラベルを見ない準備)

**Purpose**: 重みを計算するために必要な素を揃える。**このフェーズと次のフェーズでは
ラベルにも winner NLL にも触れない。**

- [ ] T001 [P] `training/src/horseracing_training/recency.py` を新規作成し、`RecencyWeightSpec`(scheme / half_life_days / floor / normalize / selection_basis)を定義する(data-model.md §1)
- [ ] T002 [P] `training/tests/unit/test_recency_weight.py` の骨格を作り、契約 [recency-weight.md](./contracts/recency-weight.md) の必須テスト一覧を空のテストとして並べる(何を守るかを先に固定する)

---

## Phase 2: Foundational(重みの計算・**ラベルを一度も見ない**)

**Purpose**: 重みの純関数・正規化・日付衛生・ESS を作り、**半減期を日付だけで決めて凍結する**。

⚠️ **すべての US はこのフェーズの完了に依存する**

> **このフェーズが終わった時点で「選択リークが無いこと」が構造的に確定する。**
> 半減期をラベルを一度も見ずに決めているため、事前登録が「守る約束」ではなく
> 「そもそも破れない構造」になる。

### テスト(先に書く)

- [ ] T003 [P] `training/tests/unit/test_recency_weight.py` に**正規化**の検証を書く: レース単位の平均が厳密に 1(INV-W3)。**正規化を外す変異でテストが落ちる**ことを確認する(FR-006a・codex C1)
- [ ] T004 [P] `training/tests/unit/test_recency_weight.py` に**レース内定数**の検証を書く: `assert_race_constant` を通ること、per-horse 項を足した重みが fail-closed になること(INV-W2・FR-003)
- [ ] T005 [P] `training/tests/unit/test_recency_leak_guard.py` を追加し、**重みが純関数である**ことを挙動で固定する: 着順・オッズ・未来のレースを変えても重みが 1 ビットも動かない(INV-W1・FR-002・憲法 II)
- [ ] T006 [P] `training/tests/unit/test_recency_date_hygiene.py` を追加し、未来 cutoff・負の経過日数・`race_date` 欠損・年月の単位取り違えが**すべて例外**になることを検証する(FR-002a・codex R9)
- [ ] T007 [P] `training/tests/unit/test_recency_weight.py` に単調非増加(INV-W5)・下限 `floor > 0`(INV-W4)・同日同一重み・**fold 間で重みの意味が変わらない**(同じ age なら同じ重み)を追加する(FR-008)

### 実装

- [ ] T008 `training/src/horseracing_training/recency.py` に `build_recency_weights(race_ids, race_dates, *, cutoff, half_life_days, floor)` を実装する。`α̃ = ε + (1−ε)·0.5^(age_days/half_life)` を計算し、**レース単位の平均が 1 になるよう正規化**して返す(contracts/recency-weight.md)
- [ ] T009 `training/src/horseracing_training/recency.py` に日付衛生の fail-closed 検査を実装する(T006 が要求する 4 種)
- [ ] T010 `training/src/horseracing_training/recency.py` に `effective_sample_size(weights) = (Σw)²/Σw²` と、**粒度別 ESS** を返す関数を実装する(カテゴリ別・供給元別・頭数帯別・特徴の有効値/欠損別)(FR-009/010・codex R4)
- [ ] T011 `training/src/horseracing_training/recency.py` に `WeightAudit`(cutoff / 半減期 / floor / ESS 全体と粒度別 / 重み分布 / 新レジーム質量 / 消失カテゴリ)を実装する(data-model.md §3)

### 半減期の決定と凍結(**ここでもラベルを見ない**)

- [ ] T012 `scripts/recency_halflife.py` を作り、**実データの日付分布だけ**から半減期の候補ごとに「新レジームの重み質量」「主要カテゴリの ESS」を算出する。**ラベル・着順・winner NLL を一切読まないことをスクリプト冒頭の契約として明記し、import でも保証する**
- [ ] T013 T012 の出力から、事前登録した日付基準(新レジーム質量 20〜35% かつ主要カテゴリの ESS が下限以上)を満たす半減期を**単一値に決める**。決めた値・基準・その実測値を `specs/101-recency-weighting/gate-config.json` に凍結し、canonical hash を `gate-config.hash.txt` に記録する(FR-007)
- [ ] T014 `specs/101-recency-weighting/gate-config.json` に判定式を凍結する。**評価は無重み**であること(FR-011a)、δ の `derivation_ref`(feature 100 の `delta-derivation.json`)、seed_noise、bootstrap、eval_window を含める。`delta_provenance.assert_delta_provenance` を通ることを確認する

**Checkpoint**: 重みが計算でき、半減期が**ラベルを見ずに**凍結された。ここまでで選択リーク無しが確定

---

## Phase 3: US1 — 時間重みの導入と採否判定(Priority: P1)🎯 MVP ★★★ 中断点 ★★★

**Goal**: 学習の時間重みという未着手のレバーを、1 回の事前登録判定で決着させる。

**Independent Test**: 重みあり/なしの 2 アームを paired 判定に掛け、事前登録式で三値が出れば完了。
**REJECT も正当な完了**である。

### テスト

- [ ] T015 [P] [US1] `training/tests/unit/test_recency_wiring.py` を追加し、**重み無効時に現行とビット一致**することを検証する(SC-001・FR-001)
- [ ] T016 [P] [US1] `training/tests/unit/test_recency_recipe_field.py` を追加し、`ModelRecipe` の新フィールドが arm E builder の `_RECIPE_FIELD_DISPOSITION` に **"forward" として登録**されていることを検証する。未登録だと `_check_recipe_fields_accounted_for` が fail-closed になることも確認する(FR-005・research D8)
- [ ] T017 [P] [US1] `training/tests/unit/test_recency_scope_declared.py` を追加し、**重みの適用範囲が明示的に宣言されていない場合に fail-closed** になることを検証する(INV-S2)。US2 未実装の段階では「booster 限定」を明示宣言する

### 実装

- [ ] T018 [US1] `training/src/horseracing_training/recipe.py` の `ModelRecipe` に半減期フィールドを追加する。既定は無効(None)で、無効時の `recipe_hash` が現行と不変であることをテストで固定する
- [ ] T019 [US1] `training/src/horseracing_training/calib_split.py` の `_RECIPE_FIELD_DISPOSITION` に新フィールドを **"forward"** で登録し、`_make_base` に渡す。`ev_weight` が "reject" である理由(重み源が fit-scope で届かない)が**当てはまらない**ことをコメントで明記する
- [ ] T020 [US1] `training/src/horseracing_training/predictor.py` の fit 経路(079 の `model_weights` の seam)で recency 重みを構築して渡す。`assert_race_constant` を通す。**適用範囲は booster 限定であることを明示宣言**して `fit_info_` に記録する
- [ ] T021 [US1] `WeightAudit` を `fit_info_` に記録する。**cutoff を必ず含める**(再学習日が動けば全重みが動くため・INV-A1)

### 判定

- [ ] T022 [US1] `scripts/recency_gate.py` を作り、凍結 `gate-config.json` の hash を照合してから 2 アーム(重みあり/なし)を回す。**差が時間重みのみであることを実行前後の構造 assert で担保し、差がゼロでないことも確認する**(FR-011・SC-003)
- [ ] T023 [US1] 判定を実行し、結果を `specs/101-recency-weighting/evidence/recency-gate.json` に保存する。**評価は無重み**(FR-011a)。feature 100 の per-race 証拠(`--evidence`)も併せて残す
- [ ] T024 [US1] 事前登録式から ADOPT / REJECT / NO_DECISION を機械的に決め、判定を artifact に記録する。**判定を記録してから**内訳(fold 別・カテゴリ別)を読む(SC-004)

**Checkpoint**: **ここが分岐点。** T024 の結果が出るまで Phase 4/5 に着手してはならない

---

## Phase 4: US2 — 重みの適用範囲(**T024 が正のときのみ・中断点通過後に確定**)

> **意図的に詳細化していない。** TE と校正器に重みを通すのは**中〜大の実装**で、
> US1 が REJECT なら丸ごと不要になる。feature 100 で「理屈のまま spec に載せた US2 が
> 測定で死んだ」前例があるので、同じ形を繰り返さない。

展開時に必ず含めるもの([weight-scope.md](./contracts/weight-scope.md) の全項目):

- `fit_target_encoder` / `oof_target_encode` / `fit_calibrator` に重みを通す
  (現状**いずれも重み引数を持たず**、`prior` も `y_model.mean()` で重みなし)
- TE の**件数・平均・prior をすべて同じ重みで**計算(INV-S4)
- smoothing を単純件数ではなく**カテゴリ別 ESS** で決め、不足カテゴリは親/全体 prior へ縮約
- 校正器の **bin 別 ESS** 監視と不足時の fallback を事前規定
- 各消費者で **per_race / per_horse_row** を明示(INV-S5・多頭数レースの再増幅を防ぐ)
- **TE の as-of 時間境界を動かさない**(INV-S3)
- **booster 限定と一貫適用の両方を測り、測定で決める**(FR-015)

---

## Phase 5: US3 — 交絡の見分け(**T024 が正のときのみ・中断点通過後に確定**)

展開時に必ず含めるもの:

- **疑似 cutoff(2025 年より前で切った窓)**での減衰 = 供給元変更を含まない純粋な時間変化(FR-017a)
- 減衰アームと段差アームの対照(FR-017)
- **H と供給元別重み ρ を同時に自由選択しない**(識別不能に近い・codex R10)
- 段差が同等以上なら「**正体は供給元切替であって時間変化ではない**」と記録(FR-018)
- **完全な識別はできない**(並行取得が無い)ことを結論に明記(FR-017b)

---

## Phase 6: Polish(Phase 3 の結末が出てから)

- [ ] T025 [P] training / eval の既存スイートが緑であることを確認する
- [ ] T026 [P] 変更した `training/src/` と `scripts/` に対して ruff クリーンを確認する
- [ ] T027 [P] `db/` の migration head が不変、`api/` `front/` `betting/` `probability/` `serving/` `features/` に差分ゼロ、**FEATURE_VERSION が不変**であることを `git diff --stat` で確認する(FR-004・SC-007)
- [ ] T028 **REJECT の場合**: 結線・レシピフィールドを revert し、`recency.py` とテストを**非結線で保全**する。production 側に結線が残っていないことを確認する(FR-020・062/070/090/100-US3 同型)
- [ ] T029 実測結果を `specs/101-recency-weighting/spec.md` に転記する。**ADOPT でも REJECT でも、実効標本数と消失カテゴリの実測値を残す**(FR-021)
- [ ] T030 `CLAUDE.md` の SPECKIT 区間を最終結果で更新する(**speckit の agent-context 更新スクリプトは使わず Edit で手動編集**)
- [ ] T031 変更をパスを明示列挙してコミットする(`git add -A` 禁止 — 並行セッションの未コミット変更を巻き込む)

---

## Dependencies

```
Phase 1 (Setup: 骨格)
   ↓
Phase 2 (Foundational: 重みの計算 + 半減期の凍結)   ← ラベルを見ない
   ↓
Phase 3 (US1: 配線と判定) ★中断点★
   │
   ├─ T024 通過 ──→ Phase 4 (US2) ─→ Phase 5 (US3) ─→ Phase 6
   └─ T024 REJECT ──────────────────────────────────→ Phase 6 (T028 保全)
```

- **Phase 2 が全 US のブロッキング前提**(重みが無ければ何も測れない)。
- **US2 / US3 は US1 の結果に依存する**。US1 が REJECT なら両方消える。
- Phase 6 は結末によらず走る(T028 だけが REJECT 時限定)。

---

## Parallel Execution

### Phase 2(テストは全て並列可)
T003 / T004 / T005 / T006 / T007 は別ファイルまたは独立した検証なので同時に着手できる。
実装は T008 → T009 → T010 → T011 が直列(同一モジュールを育てる)。
T012 は T008 の後、T013 → T014 は直列。

### Phase 3(US1)
テスト T015 / T016 / T017 は並列可。
実装は T018 → T019 → T020 が直列(レシピ → builder → fit 経路の順に依存)。T021 は T020 の後。
判定 T022 → T023 → T024 は直列。

---

## Independent Test Criteria(US ごと)

| US | 単独で完了と言える条件 |
|---|---|
| **US1** | 重み無効時のビット一致(T015)+ 2 アームの差が時間重みのみで**ゼロでない**(T022)+ 事前登録式から三値が決まる(T024)。**REJECT も正当な完了** |
| **US2** | 宣言と実適用の一致 + booster 限定と一貫適用の両方を測って測定で決めた記録 |
| **US3** | 疑似 cutoff での減衰と段差アームの対照が出て、どちらがより説明するかが記録される |

---

## Implementation Strategy

**MVP = Phase 1 + Phase 2 + Phase 3(US1)。**

US1 だけで「学習の時間重みは効くのか」に決着がつく。**この feature の価値はそこにある**。

Phase 2 が終わった時点で、**ラベルを一度も見ずに半減期が凍結されている**ことが構造的に
確定する — これは事前登録として最も強い形である。

要求水準は隠していない: 採用には点推定 **−0.0025 かつ δ=0.00352 超**が要り、これは過去に
効いたレバー(−0.0067 / −0.0128 / −0.014)の帯である。**小さい効果では通らない。**
成功条件は「効くこと」ではなく「**一度で決着させること**」である。
