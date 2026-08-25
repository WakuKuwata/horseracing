---
description: "Task list for 100-eval-contract-v5"
---

# Tasks: 評価契約 v5 — 判定の証拠保全と、seed 分散の縮小

**Input**: `specs/100-eval-contract-v5/`(spec.md / plan.md / research.md / data-model.md / contracts/ / quickstart.md / codex-review.md)

**Tests**: **含む。** 憲法の品質ゲート(leakage test / 確率整合性 test / 評価ハーネス test)が要求し、
spec 側にも検証を規定した FR がある(FR-015/015b/029a)。**FR-016〜FR-021 は US2 棄却に伴い削除**され、FR-016 のみ US3 の足切り要件として再定義されている(spec の該当注記を参照)。

**Organization**: US ごとにフェーズを分ける。**Phase 5 は中断点**であり、そこを通過するまで
**Phase 6** は詳細化しない(Phase 7 の Polish は結末によらず必要なので詳細化してある)。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 並列実行可(別ファイル・未完了タスクへの依存なし)
- **[Story]**: US1 / US3 / US4(Setup・Foundational・Polish には付けない)

## Path Conventions

- `eval/src/horseracing_eval/` — 評価ハーネス
- `training/src/horseracing_training/` — 学習・driver・CLI
- `serving/src/horseracing_serving/` — 予測(**Phase 5 通過時のみ**)
- `scripts/` — スパイク・測定
- 各パッケージの `tests/unit` / `tests/integration`

---

## Phase 1: Setup(共有基盤)

**Purpose**: 後方互換を守るための土台。**ここを飛ばすと v4 の凍結 verdict を壊したことに気づけない。**

- [X] T001 [P] 094〜099 の gate-config・その canonical hash・verdict.json を golden fixture として `eval/tests/fixtures/frozen_contracts/` にコピーし、出典(spec ディレクトリのパス)を README に記録する
- [X] T002 [P] `eval/tests/unit/test_frozen_contract_parity.py` を追加し、T001 の各 gate-config に対して `decision.gate_config_hash(cfg)` が記録済み hash とビット一致することを検証する(FR-002・D9)
- [X] T003 [P] `training/tests/unit/test_contract_version_lower_bound.py` を追加し、`adoption.py` の契約版比較が**下限比較**であることを、**注入した床に対する**表形式テストで固定する(`floor=3/actual=3,4,5` 許可・`floor=4/actual=3` 拒否)。実際の `MIN_CONTRACT_VERSION` は **3**(`adoption.py:107`)であり、**v5 でも床を上げない**(下限比較なので v5 の verdict は現状のまま受理される)ことを決定として記録する。等値比較への mutation で落ちることを確認する(FR-003・D10・analyze M1)
- [X] T003a `training/src/horseracing_training/` に**凍結 gate-config のレジストリ解決経路**を追加する(`specs/*/gate-config.json` を hash → config で引ける規約を正本とする)。未知 hash は **fail-closed**。これが無いと T026 の解決器は読む先が無い(FR-031a・analyze G2)
- [X] T004 `specs/100-eval-contract-v5/evidence/` に `screen_power_probe` / `screen_track_bias` / `cv_rho_probe` の実行結果を保存し、それぞれが何を確定させたかを 1 行で添える(D1・D2 の証跡固定)

**Checkpoint**: T002/T003 が緑 = v4 の凍結成果物を壊したら即座に気づける状態になった

---

## Phase 2: Foundational(全 US のブロッキング前提)

**Purpose**: 版比較の 2 地点を性質ごとに固定し、**版を上げずに** US1/US4 を載せられる状態を作る。

> **analyze C1 で判明**: `decision.assert_confirmatory` は契約版を**等値比較**しており(`decision.py:223`)、
> ここで `EVALUATION_CONTRACT_VERSION` を v5 に上げると **094〜099 の凍結 config が全て即座に
> `ConfirmatoryContractError` になり、SC-002 が構造的に達成不能**になる。そしてこの等値比較は
> **意図的**である(「別のルールで判断された数値を黙って再判定すると verdict の不変性が壊れる」)。
> よって解は「等値比較を緩める」ではなく「**US1/US4 では版を上げない**」— これらは判定ルールを
> 変えないので、そもそも版を上げる理由が無い。

⚠️ **すべての US はこのフェーズの完了に依存する**

- [X] T005 `eval/src/horseracing_eval/decision.py` の `assert_confirmatory` にある**等値比較は意図的な fail-closed** である旨を契約コメントとして明文化し、`eval/tests/unit/test_confirmatory_version_equality.py` で**下限比較への mutation が落ちる**ことを固定する。**US1/US4 では `EVALUATION_CONTRACT_VERSION` を bump しない**(FR-002b/002c)。版 bump は Phase 6(US3)に属する
- [X] T006 `eval/src/horseracing_eval/decision.py` の gate-config 読み取り経路を **`版を最小限読む → 版別の canonicalizer / 必須チェックを選ぶ → hash 照合`** の順序に明示的に分解する。**`gate_config_hash` の入力に v5 の既定値を注入しないこと**を関数の契約として固定する(FR-002a・R1・D9)
- [X] T007 [P] `eval/tests/unit/test_gate_config_version_boundary.py` を追加し、**v4 config に v5 の既定値が注入されない**ことを検証する。defaults 注入を足す mutation で落ちることを確認する(FR-002a)。**「必須ブロック欠落の fail-closed」は v5 が導入されるまで検証対象が存在しない**ため Phase 6 に送る(v5 の必須ブロックは `ensemble` のみ・FR-002c・analyze U1)
- [X] T008 [P] `eval/src/horseracing_eval/bootstrap.py` の `seed_noise_sd` に、**`k_seeds>1` は seed 間独立を仮定するのでアンサンブルには流用してはならない**旨の契約コメントを追加し、`eval/tests/unit/test_seed_noise_contract.py` でアンサンブル経路からの呼び出しを禁止するテストを置く(FR-026b・R16・D7)

**Checkpoint**: 契約版の境界ができ、v4 の凍結成果物が壊れないことがテストで守られた

---

## Phase 3: US1 — per-race 証拠の保存(Priority: P1)🎯 MVP

**Goal**: 判定 1 回につき、その判定を再現できる生の証拠が残る。

**Independent Test**: 判定を 1 回走らせ、生成された証拠 artifact **だけ**を入力に点推定と CI を
再計算して verdict とビット一致させられれば完了。US3/US4 が未実装でも価値が出る。

### テスト(先に書く)

- [X] T009 [P] [US1] `eval/tests/unit/test_paired_evidence_row.py` を追加し、INV-E1〜E7(行数一致・race_id 一意・`diff` の厳密一致・**符号規約 `候補 − 基準`**・共変量が結果を読まない・行順不変・lossless round-trip)を検証する
- [X] T010 [P] [US1] `eval/tests/unit/test_evidence_recompute_parity.py` を追加し、**証拠 artifact だけから**点推定・sampling CI・total CI を再計算して `PairedReport` とビット一致することを検証する(INV-A1・FR-008)
- [X] T011 [P] [US1] `eval/tests/unit/test_evidence_sign_convention.py` を追加し、`diff` の向きを反転させた mutation でテストが落ちることを確認する(INV-E4・FR-008a・R7)
- [X] T012 [P] [US1] `eval/tests/unit/test_evidence_leak_guard.py` を追加し、共変量に結果由来の量(着順・確定オッズ等)を混ぜた mutation が落ちることを確認する。証拠 artifact が特徴量ビルダから参照されないことを import グラフで固定する(INV-E5/A4・FR-011/012・憲法 II)

### 実装

- [X] T013 [US1] `eval/src/horseracing_eval/paired.py` に `PairedEvidenceRow` を定義し、`paired_eval` の per-race ループ(現行 `diffs_by_day` を組み立てている箇所)で race_id・開催日・両アームの winner NLL・`diff`・事前登録共変量を持つ行を生成する。**オプションにせず必ず返す**(contracts/paired-evidence.md)
- [X] T014 [US1] `eval/src/horseracing_eval/paired.py` の `PairedReport` に `PairedEvidenceArtifact`(rows + bootstrap パラメータ + 版 + 各種 hash + window + artifact_kind)を追加する。既存 `diffs_by_day` は互換のため残し、証拠行から導出できることをテストで固定する(data-model.md §2)
- [X] T015 [US1] `eval/src/horseracing_eval/bootstrap.py` に**証拠 artifact だけを入力とする再計算関数**を追加する。`paired_eval` 本体もこの関数を経由させ、**2 経路が構造的に同じ計算をする**ようにする(同じ契約の二重実装を作らない)
- [X] T016 [US1] 事前登録共変量の生成を `eval/src/horseracing_eval/paired.py` に置く。**結果を読まない**量のみ(頭数・市場エントロピー・市場 max q・基準アームの loss・レース属性)。判定式には入れない(FR-014)
- [X] T017 [US1] `training/src/horseracing_training/cli.py` の `_paired_eval` 出力経路で証拠 artifact をファイルに書き出す。append-only(再実行は新ファイル)(INV-A2・FR-010)
- [X] T018 [US1] `eval/src/horseracing_eval/regime_paired.py` と 097 型の複数窓 driver(`scripts/097_simulated_supply_gate.py` 相当の経路)で、**束ねる前の各窓の証拠を落とさない**ようにする(INV-A3・FR-009・D11)
- [X] T019 [US1] `eval/tests/integration/test_multiwindow_evidence.py` を追加し、複数窓 driver が各窓の証拠を落とす mutation で落ちることを確認する(INV-A3)

### 検証

- [X] T009a [P] [US1] `eval/tests/unit/test_primary_metric_unchanged.py` を追加し、**PRIMARY 指標(race-level winner NLL)の定義と評価母集団が本 feature で変わっていない**ことを固定する。証拠 artifact の追加で指標の計算経路が変わる mutation が落ちることを確認する(FR-001・analyze coverage)
- [X] T019a [P] [US1] `eval/tests/unit/test_ci_components_separated.py` を追加し、verdict が **sampling CI と seed 成分を分離して**報告し続けることを固定する。現状の挙動だが回帰テストが無く、リファクタで静かに死ぬ形(FR-032・analyze G3)
- [X] T020 [US1] 実 DB で判定を 1 回走らせ、証拠の行数が verdict の `n_races` と一致し、証拠だけからの再計算が verdict とビット一致することを確認する。結果を `specs/100-eval-contract-v5/evidence/us1-recompute-parity.txt` に保存する(SC-001・quickstart A-1/A-2)
- [X] T021 [US1] T001 の凍結 config(`eval/tests/fixtures/frozen_contracts/`)で判定を回し、verdict の**既存キーの値がすべてビット一致**することを確認する。結果を `specs/100-eval-contract-v5/evidence/us1-frozen-parity.txt` に保存する(SC-002・quickstart A-3)

**Checkpoint**: US1 完了。**ここで止めても価値が出る。** 以後のすべての解析がこの証拠の上に載る

---

## Phase 4: US4 — δ の再導出(Priority: P3)

**Goal**: 「これ未満は採用しない」の線を、測定ノイズ以外の言葉で説明できる状態にする。

**Independent Test**: `sd_fold` を変えても δ が動かないことで完了。US1/US3 に依存しない。

- [X] T022 [P] [US4] `specs/100-eval-contract-v5/contracts/delta-derivation.md` の方法に従って δ を導出する。入力(N=年間判定回数・許容 net-harm 確率・想定効果量分布)を**導出前に凍結**し、導出計算を `scripts/derive_delta.py` として残す(FR-030)
- [X] T023 [US4] 導出結果を `DeltaDerivation`(data-model.md §4)として機械可読に保存する。`method="multiple_testing_budget"`・入力値・`derived_delta`・`frozen_at` を含める
- [X] T024 [US4] 採らなかった 4 つの導出(測定ノイズ由来・計算コスト対効果・過去採用レバーの 1/N・金銭価値)を**理由つきで** `DeltaDerivation` に記録する(FR-030a)
- [X] T025 [P] [US4] `eval/tests/unit/test_delta_independent_of_seed_noise.py` を追加し、`sd_fold` を変えても δ が動かないことを検証する(INV-D1・SC-007)。**これは安価な回帰でしかない**(δ は config の literal なので現状すでに自明に真)
- [X] T025a [US4] **実効的な歯止め**を入れる: gate-config の `min_effect_delta` に `derivation_ref` を必須化し、`method="multiple_testing_budget"` の `DeltaDerivation` に解決できること・導出入力に `sd_fold` が現れないことを**実行時に fail-closed で検証**する。`eval/tests/unit/test_delta_provenance.py` で `sd_fold` 由来の導出を拒否することを固定する(FR-029a・analyze V1)
- [X] T026 [P] [US4] `training/tests/unit/test_past_verdict_delta_resolution.py` を追加し、過去 verdict の表示に**当時の** δ・provenance が使われ、**解決できなければ fail-closed**(v5 の δ で補わない)ことを検証する(INV-D3・FR-031a・R8)
- [X] T027 [US4] 新しい δ を**新しい gate-config の凍結**として `specs/100-eval-contract-v5/gate-config.json` に発行し、その canonical hash を記録する。過去 verdict の再読み替えには使わない(INV-D2・FR-031)
- [X] T028 [US4] `specs/09*/verdict.json` と `out/*-verdict.json` が 1 つも書き換わっていないことを `git status` で確認する(SC-008・quickstart B-2)

**Checkpoint**: δ が `sd_fold` から独立になった。US3 で `sd_fold` が動いても閾値が動かない

---

## Phase 5: US3 スパイク ★★★ 中断点 ★★★(Priority: P2)

**Goal**: k-seed アンサンブルの利得が**実在するか**を、本実装の前に確かめる。

> **なぜ中断点か**: US2 は「未着手だから削れるはず」という理屈のまま spec に書かれ、
> 測定(R²=0.029)で棄却された。US3 だけ「理屈で効くはず」で通すのは二重基準である。
> 加えて R16 により、**CI 縮小という測定上の利得はほぼ期待できない**ことが判明している。
> US3 は**製品の改善としてのみ**立つので、その改善が実在するかを先に測る。

### 事前登録(実行前・順序厳守)

- [ ] T029 [US3] 足切り値を**スパイク実行前に凍結**する: (a) winner NLL の改善幅の下限、(b) **バンドル間 sd(`bundle_sd`)** の縮小幅の下限。**`sd_fold` という名前を足切り指標に使わない**(FR-026b が禁じた `sd/√k` の代入を名前が誘発する・analyze T1)。凍結値と凍結日を `specs/100-eval-contract-v5/spike-config.json` に書き、その canonical hash を記録する(FR-016)
- [ ] T030 [US3] スパイクの出力規律を `specs/100-eval-contract-v5/spike-config.json` に追記する: 足切り判定に使う数値以外(fold 別の内訳など)を**先に見ない**(097 の smoke redact 規律と同型)

### スパイク

- [ ] T031 [US3] `scripts/ensemble_spike.py` を作る。k=3〜5 で実際に学習し、**レース内 softmax 後の確率を平均**して `log p̄` をスコアとし、アンサンブル OOF で校正器を再 fit したうえで winner NLL を測る(contracts/ensemble.md・D3/D4/D5)
- [ ] T032 [US3] 同スクリプトで **独立な k-seed バンドルを複数作り、バンドル単位の paired 性能差からバンドル間 sd を推定**する。**`sd/√k` を使ってはならない**(FR-026b・R16・D7)
- [ ] T033 [US3] スパイク結果を `specs/100-eval-contract-v5/evidence/ensemble-spike.json` に保存する。凍結 hash の照合結果を含める

### 判定

- [ ] T034 [US3] `specs/100-eval-contract-v5/evidence/ensemble-spike.json` を T029 の凍結足切り値(`spike-config.json`)と照合して判定し、判定結果を同 JSON に追記する。**通過 → Phase 6 へ。不通過 → T035 へ**
- [ ] T035 [US3] **(不通過時のみ)** 非結線保全を行う: `scripts/ensemble_spike.py` とそのテストを残し、**production 側への結線・版 bump・レシピフィールド追加が一切行われていないことを確認**する(スパイク段では元々それらを作らない設計なので、確認であって revert ではない・analyze L1)。結果を `specs/100-eval-contract-v5/spec.md` に転記し、この feature を US1+US4 で閉じる

**Checkpoint**: **ここが分岐点。** T034 の結果が出るまで Phase 6 に着手してはならない(Phase 7 は結末によらず走る)

---

## Phase 6: US3 本実装(**T034 通過時のみ・中断点通過後に確定**)

> **意図的に詳細化していない。** 足切りが落ちたときに捨てるタスクを書かないため。
> T034 通過後に `/speckit-tasks` を再実行するか、本フェーズを手で展開する。

展開時に必ず含めるもの(contracts/ensemble.md の全項目):

- 合成はレース内確率平均、スコアは `log p̄`、校正器はアンサンブル OOF で再 fit(流用禁止)
- 校正後の**レース内正規化** `h(p̄_i)/Σ_j h(p̄_j)` と Σ=1 の不変条件テスト(INV-M4・憲法 IV)
- 評価経路と serving 経路の**演算順序 bit パリティ** + 「校正 → 平均」への入れ替え mutation
- 同一性 hash に member の**順序つき** hash・前処理・校正器・集約演算・dtype・runtime(INV-M3)
- 宣言 `k` と実ロード数の一致検証(**学習時と serving ロード時の両方**)・**部分平均と単一 seed
  フォールバックの禁止**・k 個の hash 照合まで serving を ready にしない(INV-M1/M5)
- `ModelRecipe` に足すフィールドを `_RECIPE_FIELD_DISPOSITION` に登録(FR-028)
- estimand の宣言(再学習手続き)と報告の但し書き(FR-026a/026c)
- **CI 幅の縮小率を毎回報告し、それが昇格根拠として使われていないことが verdict から読み取れる**形(FR-033)
- v5 の必須ブロック `ensemble` が欠けた config の fail-closed(Phase 2 の T007 から送られた分・FR-002a)
- **判定手順の正しさを結果を見ずに検証**: 配線(完全 clone)/ **境界帰無 E[D]=δ での偽陽性率** /
  既知効果注入での被覆率(FR-015・R3)。診断を合否根拠にしない(FR-015b・R9)
- 採否ゲート通過(winner NLL・top2/top3・ECE 非劣化)。**CI が狭いことを昇格根拠にしない**(FR-027)

---

## Phase 7: Polish(**Phase 5/6 の結末が出てから**)

- [X] T036 [P] `eval/tests/` と `training/tests/`(US3 採用時は `serving/tests/` も)の既存スイートが緑であることを確認する
- [X] T037 [P] 変更した `eval/src/` `training/src/` `scripts/` に対して ruff クリーンを確認する
- [X] T038 [P] `db/` の migration head が不変であること、`api/` `front/openapi.json` `betting/` `probability/` に差分が無いことを `git diff --stat` で確認する(FR-005)
- [X] T038a [P] **過去 REJECT の再解析を行わなかった**ことを記録する(spec の実装結果に転記済み)。FR-004 の `eligible_for_verdict=false` 印と FR-034 の診断注記は、再解析を実施する場合にのみ必要であり、本 feature ではスコープ外(D2 により再解析しても 097 は境界上のままで結論が変わらないため)
- [X] T039 実測結果(US2 の棄却・US3 の足切り判定)を `specs/100-eval-contract-v5/spec.md` に転記する(FR-013 の記録規律)
- [X] T040 `CLAUDE.md` の SPECKIT 区間にある本 feature の要約を最終結果で更新する(**speckit の agent-context 更新スクリプトは使わず Edit で手動編集**)
- [ ] T041 `specs/100-eval-contract-v5/` `scripts/` `eval/` `training/` の変更をパスを明示列挙してコミットする(`git add -A` 禁止 — 並行セッションの未コミット変更を巻き込む)

---

## Dependencies

```
Phase 1 (Setup: 凍結成果物の golden fixture)
   ↓
Phase 2 (Foundational: 契約版 v5 の境界)   ← 全 US がここに依存
   ↓
   ├─→ Phase 3 (US1) ────────────┐
   ├─→ Phase 4 (US4) ────────────┤   US1 と US4 は互いに独立・並列可
   └─→ Phase 5 (US3 スパイク) ★中断点★
                                  │
                     T034 通過 ───┴─→ Phase 6 (US3 本実装) ─→ Phase 7
                     T034 不通過 ────→ T035 (保全) ─────────→ Phase 7
```

- **US1 と US4 は互いに独立**。Phase 2 完了後に並列で進められる。
- **US3 スパイクも Phase 2 さえ済めば US1/US4 と並列に回せる**(学習が重いので実時間は長い)。
- Phase 6/7 は T034 の結果に依存する。

---

## Parallel Execution

### Phase 1(全て並列可)
T001 / T002 / T003 を同時に着手できる(別ファイル・相互依存なし)。T004 も独立。

### Phase 3(US1)
テスト群 T009 / T010 / T011 / T012 は別ファイルなので並列可。
実装は T013 → T014 → T015 が直列(同一ファイルの同一構造を触る)、T016 は T013 の後、
T017 / T018 は T014 の後で並列可。

### Phase 4(US4)
T022 → T023 → T024 が直列。T025 / T026 は並列可。

### US 間
**Phase 3 と Phase 4 と Phase 5 は完全に並列に進められる。**
実時間で見ると Phase 5(学習が k 倍)が最長になるので、先に着火して US1/US4 を並行させるのが早い。

---

## Independent Test Criteria(US ごと)

| US | 単独で完了と言える条件 |
|---|---|
| **US1** | 判定を 1 回走らせ、証拠 artifact **だけ**から点推定と CI を再計算して verdict とビット一致する(T020)。かつ凍結 config の verdict が不変(T021) |
| **US4** | `sd_fold` を変えても δ が動かない(T025)。過去 verdict が 1 つも書き換わらない(T028) |
| **US3** | (スパイク段)足切り値との照合で ADOPT / REJECT が機械的に決まる(T034)。**不通過も正当な完了**である |

---

## Implementation Strategy

**MVP = Phase 1 + Phase 2 + Phase 3(US1)。**

US1 だけで「重い判定を回したのに証拠が残らない」問題が解消し、将来のあらゆる事後解析が
可能になる。**この feature で確実に価値があると言い切れるのはここまで**である。

その後は 2 通り:

- **US4 を足す**(安い・δ の自己参照を断つ)
- **US3 スパイクを回す**(重い・落ちる可能性が実在する)

なお plan.md にも記したとおり、**この feature 自体が最優先とは限らない**。T0 が
「評価契約はボトルネックではない」と示した以上、US1 を入れたうえで、次はモデリング側の
未着手レバー(recency weighting)を測るほうが期待値が高い可能性がある。
