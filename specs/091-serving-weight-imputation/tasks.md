# Tasks: 馬体重欠損時の serving 入力是正 (Serving Weight Imputation)

**Input**: Design documents from `/specs/091-serving-weight-imputation/`

**Prerequisites**: [plan.md](./plan.md) / [spec.md](./spec.md) / [research.md](./research.md) / [data-model.md](./data-model.md) / [contracts/](./contracts/) / [gate-config.json](./gate-config.json)

**Tests**: 必須。憲法の品質ゲートが leakage test・時系列 split test・パリティ test を要求するため、テストは省略可能項目ではない。

**Organization**: ユーザーストーリー単位。plan.md の Phase A–E に **codex レビューで D0 を追加**した順序に従う。

| plan Phase | 本 tasks | 内容 |
|---|---|---|
| A | Phase 3 前半 | `prev_weight` 構築とパリティ実測 |
| B | Phase 3 後半 | mask 純関数と training への配線 |
| C | Phase 4 前半 | 評価契約(serving regime)の実装 |
| **D0(新設)** | Phase 4 中盤 | FEATURE_VERSION bump・compat 受入・**配線 E2E smoke**・**outcome-blind 受入** |
| D | Phase 4 後半 | gate-config 凍結 → 本評価 |
| E | Phase 5 / 6 | registry timing 是正(独立)と verdict 分岐 |

### codex tasks レビューで変えたこと(重要)

1. **screening を「効果で止める中断点」から「outcome-blind な配線受入」に変更した。** 当初案は直近 fold で候補が現行を上回るかを見て継続判断する設計だったが、**その fold は最終評価窓にも含まれるため選択リークになる**(codex #4)。効果で中断したいなら当該 fold を最終 verdict から除外するか二段階継続規則を事前登録する必要があり、どちらも本 feature の射程を広げる。よって受入は**効果を見ない項目だけ**(mask 件数・両アーム一致・カバレッジ・指標が有限・provenance 一致)に限定した。
2. **D0 を新設した。** bump 前パリティは必要だが十分でない(codex #2)。version keyed cache による旧 parquet 再利用・FEATURE_GROUPS の未登録/重複/列順・candidate recipe が新列を選ばない・compat pin した active への列順・loader/CLI が新 version を選ばない、はいずれも **bump 後にしか検出できない**。
3. **配線 E2E smoke を追加した。** 最も危険な失敗は「A/B の単体テストが緑なのに実モデル入力に `prev_weight` が入っていない」ことである(codex #1)。
4. **assertion の kill-test を追加した。** 配線を 1 箇所ずつ外して assertion が必ず落ちることを確認する(codex #3)。呼び出し回数ではなく**変換後の行列**を検査する。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 並列実行可(別ファイル・未完タスクに依存しない)
- **[Story]**: US1 / US2 / US3

## Path Conventions

複数 Python パッケージ構成: `features/` `training/` `eval/` `serving/`。すべてリポジトリルートからの相対パス。

---

## Phase 1: Setup(基準値の捕捉)

**Purpose**: 変更前の状態を測って保存する。これが無いと後段のパリティ主張(INV-W7/W8/W9)が検証できない。

- [X] T001 実 DB で `features-018` の特徴量行列をフルビルドし、共有列の基準スナップショットと `source_fingerprint` を `specs/091-serving-weight-imputation/evidence/baseline_features018.json` に保存(INV-W7/W8 の比較基準。ビルドは実測 72 秒)
- [X] T002 [P] 実 DB で active `lgbm-064-f02acc` の予測を代表 3 レース(通常・少頭数・デビュー馬含み)について採取し、全頭の win 確率を full 精度で `specs/091-serving-weight-imputation/evidence/baseline_lgbm064_predictions.json` に保存(INV-W9 のバイト比較基準)
- [X] T003 [P] 現行 `artifacts/features.parquet` が stale(fingerprint 不一致で fail-closed)であることを確認し、`specs/091-serving-weight-imputation/evidence/baseline_materialize_state.md` に記録。再 materialize は D0(T039)で行う

---

## Phase 2: Foundational(全ストーリーの前提)

**Purpose**: 手計算 fixture とテスト足場。実装前に赤で置き、実装が「たまたま通る」ことを防ぐ。

**⚠️ Phase 3 以降の前提**

- [X] T004 `features/tests/unit/test_weight_history_features.py` に手計算 fixture を作成(実装前=赤)。ケース: 通常の前走参照 / 同日レース 2 本での同日除外 / 前走が取消 / 前走の体重が NULL / 範囲外体重 / 同一馬同日に複数候補 / 供給元なし / **順延で race_date が動いた場合 / 結果が後から訂正された場合 / 個体統合で履歴が繋がった/繋がらない場合**(FR-036: 遡及参照は当時 serving が知り得た値と一致しないことがある。タイブレークを決定論的に固定する)。**期待値は手計算で表に書き下ろす**(実装出力をコピーしない)
- [X] T005 [P] `features/tests/unit/test_weight_mask.py` にテスト足場を作成(実装前=赤)。ケース: `spec=None` のバイト同一 / レース単位性 / **行順・batch 分割に対する不変** / 決定論 / m=0 と m=1 の端 / 対象 3 列以外が不変 / `prev_weight` が不変 / 対象列欠如で fail-closed / **結果・オッズを変えても mask 選択集合が不変**(FR-016: mask は結果に依存してはならない。構成上自明でも機械検証を置く)
- [X] T006 [P] `features/tests/unit/test_weight_leak.py` に leak-guard 足場を作成(実装前=赤)。対象レースの結果・当日オッズ・同日他レースを変更しても `prev_weight` が不変であること(INV-W3)
- [X] T007 [P] `features/tests/unit/test_weight_mask_golden.py` に**プロセス横断の golden vector** を固定(実装前=赤)。Python 組込みの `hash()` は `PYTHONHASHSEED` で変わるため**使用禁止**とし、`stable_hash` 由来の選択結果を固定ベクタとして凍結する。別プロセス起動でも同一であることを確認(codex #5)

---

## Phase 3: US1 — 発走前の予測が体重欠損で劣化しない (Priority: P1) 🎯 MVP

**Goal**: 体重が未公表でも、前走体重を織り込んだ入力でモデルが予測できる状態にする。

**Independent Test**: 体重未公表のレースで予測を生成し、モデル入力に `prev_weight` が渡っていること、デビュー馬では欠損のまま渡ること、当日体重が存在する場合は上書きされないことを確認する。

### plan Phase A: `prev_weight` の構築とパリティ

- [X] T008 [US1] `features/src/horseracing_features/weight_history_features.py` を新規作成。供給元 = `entry_status == started` かつ体重非 NULL かつ `200 <= weight <= 800`、照合 = `merge_asof(on="race_date", by="horse_id", direction="backward", allow_exact_matches=False)`(既存 `features/src/horseracing_features/lowcost_features.py` の `_prev_started` と同一規約)。同一馬同日に複数候補があれば NaN。出力は `[race_id, horse_id, prev_weight]` の**キー付きフレーム**(位置結合しない)
- [X] T009 [US1] `features/tests/unit/test_weight_history_features.py` の全ケースを緑にする。**`prev_weight` が float64 であることを assertion する(FR-009)**。特に**同日除外**が `allow_exact_matches=False` で効いていることを、同日 2 レース fixture で明示的に確認
- [X] T010 [US1] `features/src/horseracing_features/registry.py` に `prev_weight` を登録: source=`race_horses`、availability_timing=`pre_entry`、missing_policy=`null`、FEATURE_GROUPS=`weight_history`。**この時点では FEATURE_VERSION を上げない**(D0/T039 まで据え置き)
- [X] T011 [US1] `features/src/horseracing_features/materialize.py` の `build_asof_features` に `weight_history_features` を 1 箇所結線(025 の単一 as-of 源規律)。additive left-merge で既存列を perturb しないことをコード上で保証
- [X] T012 [US1] `features/tests/unit/test_weight_leak.py` を緑にする(INV-W3)
- [X] T013 [US1] `features/tests/integration/test_features020_parity.py` を新規作成し、実 DB で **INV-W7**(既存 137 列が T001 の基準とバイト一致・`check_exact=True, check_dtype=True`)と **INV-W8**(`source_fingerprint` が不変)を実測
- [X] T014 [US1] `features/tests/integration/test_features020_parity.py` に **INV-W4** を追加: 過去に出走がある行では `prev_weight` が非欠損。**破れたら fail-closed** とし、エラーメッセージに「research D1 の列縮小の前提が崩れた = `weight_age_days` / `has_prev_weight` の追加を再検討せよ」と明記する
- [X] T015 [US1] カバレッジ監査を `specs/091-serving-weight-imputation/evidence/coverage_audit.json` に出力: 年別 / レース内カバレッジ(全馬・一部・0 頭)/ 鮮度帯(≤45, 46-120, 121-365, >365 日)/ **真デビュー vs 履歴断絶(`nk:` 由来)** / ID 名前空間(FR-028)。**加えて「結果未確定レース」コホートを別軸として測り、前走体重の供給率が 85% を超えることを assertion する(SC-005)** — SC-005 の母集団は確定済レースではなく、これから予測する行である

**Checkpoint A**: `prev_weight` が正しく作れており既存列に一切影響していないことが実データで確定する。**パリティが赤なら D0 の FEATURE_VERSION bump に進んではならない。**

### plan Phase B: mask 機構

- [X] T016 [US1] `features/src/horseracing_features/weight_mask.py` を新規作成。`apply_weight_mask(frame, *, spec)` 純関数。`spec=None` は**入力とバイト同一**を返す(INV-W5)。mask 対象列 `weight` / `weight_diff` / `carried_weight_ratio` を**モジュール定数の表**として持ち、対象列が入力に無ければ fail-closed。`prev_weight` は決して mask しない。選択は `stable_hash((race_id, seed))` を [0,1) に写して `rate` と比較(**組込み `hash()` 禁止**)。契約は [contracts/weight-mask.md](./contracts/weight-mask.md)
- [X] T017 [US1] `features/tests/unit/test_weight_mask.py` と `features/tests/unit/test_weight_mask_golden.py` を緑にする。**mask 対象列の表そのものをテストで検証**する(列名のタイポで静かに mask 漏れが起きるのを防ぐ)。行順を入れ替えても、フレームを分割して個別に適用しても同一結果になることを含む
- [X] T018 [US1] `training/src/horseracing_training/recipe.py` に mask spec フィールドを追加。**既定 `None` は `recipe_hash` から除外**して既存 recipe の hash をバイト不変に保つ(073 の `calibration_split_unit` / 079 の `ev_weight` と同じ back-compat canonicalization)
- [X] T019 [US1] `training/src/horseracing_training/recipe.py` で **`m=0.0` と `spec=None` を provenance 上で区別**する(値としては同一だが、「mask を意図的に無効化した実験」と「mask 機構を使わない既存 recipe」は別物として記録されなければならない・codex #5)
- [X] T020 [US1] `training/src/horseracing_training/predictor.py` の `fit()` に fit-scope mask を、`raw_win_probs()` に predict-scope mask を通す。**キャッシュ行列(`_ensure_data`)は無改変**とし、各利用箇所の行部分集合に純変換として適用する。両 spec は独立に指定できること
- [X] T021 [US1] `training/src/horseracing_training/predictor.py` で校正 holdout にも model-fit と**同一 spec・同一 seed**の mask を適用する(research D4)。model-fit 行と校正行で mask されたレース集合が一致することを assertion で担保
- [X] T022 [US1] `training/src/horseracing_training/artifacts.py` の metadata に mask spec(rate / seed / unit / columns)とその hash を記録。mask 無しなら `null`
- [X] T023 [US1] `training/tests/unit/test_weight_mask_wiring.py` を新規作成。**変換後の行列を検査する**(呼び出し回数を数えない): estimator 入力と校正 holdout 入力を捕捉し、同一 spec/seed で選ばれたレースの 3 列が最終行列で NaN になっていること、artifact metadata の spec hash が一致すること
- [X] T024 [US1] `training/tests/unit/test_weight_mask_wiring.py` に **`spec=None` のバイト同一性**を追加: 旧 artifact の読み込み・列順・active 予測が変わらないこと。既存 training テストが無改修で緑であること
- [X] T025 [US1] `training/tests/unit/test_weight_mask_wiring.py` に **assertion の kill-test** を追加: training 配線 / 校正 holdout 配線 を 1 箇所ずつ無効化し、上記 assertion が**必ず落ちる**ことを確認(assertion が実際に何かを守っていることの証明・codex #3)

- [X] T026 [US1] `serving/src/horseracing_serving/predictor.py` に**レース単位の可用性正規化**を実装(FR-034): started 馬に一頭でも当日体重が未公表の馬がいれば、そのレースの**全馬**について `weight` / `weight_diff` / `carried_weight_ratio` を欠損として扱う。**発動はモデルが `prev_weight` を入力に持つ場合に限る(FR-034a)** — 代替値を持たない現行モデルから当日体重を取り上げるのは補償のない劣化で SC-004 にも反するため、`prev_weight` を持たないモデルでは完全な no-op にする。**ライブと backfill の両方に同一条件で効かせる(FR-034b)**(確定済レースにも 0.3〜0.4% の体重欠損が残るので backfill でも混在は起きる)。確率 mask ではなく**可用性の二値化**である(research D12・[contracts/weight-mask.md](./contracts/weight-mask.md) §5a)
- [X] T027 [US1] `serving/tests/unit/test_weight_availability_normalization.py` を新規作成し(**T026 が返す発動情報をその場で検査する。永続化は T067 の役割**)、 **INV-W11 / INV-W12** を検証。全馬計測済み → full-info のまま / 一頭でも未計量 → 全馬欠損 / **`prev_weight` を持たないモデルでは発動しない**(混在レースでも現行モデルの入力が 1 ビットも変わらない=SC-004 の全入力条件版)/ 取消馬の未計量は判定に影響しない / ライブと backfill の両経路で同一挙動 / 発動回数と (計測済み頭数 / 出走頭数) が観測できる(FR-035)

**Checkpoint B (US1 完了)**: 体重未公表のレースで `prev_weight` を含む入力が組め、mask 機構が学習側に配線され、`spec=None` では現行とバイト同一。予測経路の可用性正規化も入り、**現行モデルでは発動しない**ことが確認済み。

---

## Phase 4: US2 — 採否を serving 条件で判定する (Priority: P2)

**Goal**: 実際に予測が行われる条件で候補と現行を比較し、事前登録した基準で機械的に採否を決める。

**Independent Test**: 候補と現行を同一レース集合・同一 fold で paired 再評価し、serving regime と full-info regime の両方のレポートが出て、verdict が単一式から一意に決まることを確認する。

**Dependencies**: Phase 3 完了

### plan Phase C: 評価契約の実装

- [X] T028 [US2] `eval/src/horseracing_eval/foldfit.py` の `predict_over_folds` に predict-scope mask spec を通す(既定 `None` で現行と挙動同一)
- [X] T029 [US2] `eval/src/horseracing_eval/paired.py` に regime 別評価を追加。`serving_regime`(両アームに serving spec)と `full_info_regime`(両アームに `spec=None`)を別々に算出し `PairedReport` に**純追加**する。既存フィールドは不変。**subgroup は regime ごとに算出する**(トップレベルの `subgroups` だけだと verdict 式が既定=full-info を読んでしまい一意に評価できない)。`eval` は spec を**中身を見ない不透明な値として受け取り転送するだけ**にし、構築は training / CLI が行う(`eval` は `horseracing-features` に依存していない=020 の predictor-agnostic 境界を壊さない)
- [X] T030 [US2] `eval/src/horseracing_eval/paired.py` に `full_info_guard` と、`serving_regime.gate.adopted` への**最小効果 δ**(点推定 < −δ)を追加。**両方の閾値は gate-config から読む**(既存コードが `cfg.get("top_noninferior")` 等を読むのと同じ方式)。定数をハードコードすると凍結 hash による事前登録が拘束力を失い、「テストは緑だが要件を満たさない」形になる(FR-025)。**config の値を変えるとゲート判定が変わる**単体テストを同時に追加する。verdict の正本は `serving_regime.gate.adopted AND full_info_guard AND serving_regime.subgroups.subgroup_guard` を評価した**単一真偽値 `verdict.adopt`**([contracts/adoption-gate.md](./contracts/adoption-gate.md))
- [X] T031 [US2] `eval/src/horseracing_eval/paired.py` に**校正前(race-softmax 直後)winner NLL** を診断として追加(校正器由来の反転を識別する)
- [X] T032 [US2] `eval/tests/unit/test_paired_regime.py` を新規作成。**serving では両アームの mask レース集合と hash が完全一致**、**full-info では両アームとも無変換**であることを検証する。不一致なら fail-closed(片側だけ適用されると数値は出るが比較の意味が消える)
- [X] T033 [US2] `eval/tests/unit/test_paired_regime.py` に、両アームの**レース集合・順序・winner ラベルの一致**検証を追加(既存の model-blind race set 契約 C8 の regime 版)
- [X] T034 [US2] `eval/tests/unit/test_paired_gate_boundary.py` を新規作成。**境界値テスト**: δ=0.002 のちょうど上下、差分の符号、full-info 非劣化幅 0.003 のちょうど上下。PRIMARY が `regime=serving` かつ両アーム適用証跡が一致しなければ fail すること、GUARD が `full_info` 固定であること
- [X] T035 [US2] `eval/tests/unit/test_paired_regime.py` に **assertion の kill-test** を追加: active アーム / candidate アームの mask 配線を 1 箇所ずつ外し、T032 の assertion が必ず落ちることを確認
- [X] T036 [US2] `training/src/horseracing_training/cli.py` の `paired-eval` に `--weight-regime {serving,full_info,both}` と、受入実行用の `--acceptance-recent-folds N` を追加。`both` を既定とする。**`--confirmatory` 指定時は subgroup 計算を暗黙 ON にする(または未指定を fail-closed にする)** — 現行の `subgroups` は opt-in で、gate-config が critical_subgroups を宣言していると未計算のまま NO_DECISION に落ちる。**`artifact_kind` と `eligible_for_verdict` をレポートに出力する**のもここで行う(T044/T048/T050-T052 が刻む値の受け皿・T049 の loader ガードが読む)

### plan Phase D0(新設): bump・compat 受入・配線 E2E

**Purpose**: bump 後にしか検出できない不具合を、本評価に入る前にまとめて潰す。

- [X] T037 [US2] **T013/T014 が緑であることを確認したうえで** `features/src/horseracing_features/registry.py` の `FEATURE_VERSION` を `features-021` に上げ、`COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-021"] = {"features-018": "263ef6b7ac5eccf45faf90005a5904de91adfed639b8d3f14a04c4d20f141a3f"}` を追加。**`features-019` は焼却番号のため使用禁止**(070 revert)
- [X] T038 [US2] `features/tests/unit/test_registry_features020.py` を新規作成。`prev_weight` が **FEATURE_GROUPS にちょうど 1 回**登録され、重複が無く、列順が決定論であることを検証(bump 後にしか出ない登録漏れ・重複・列順変更の検出・codex #2)
- [X] T039 [US2] `uv run --project features features materialize` で parquet を**新規に**再生成し、manifest の `feature_version` が `features-021` になることを確認。**version keyed cache による旧 parquet の再利用が起きていない**ことを、manifest の content hash が T001 の基準と異なることで確認
- [X] T040 [US2] 実 DB E2E で **INV-W9** を検証: `features-021` のもとで active `lgbm-064-f02acc` の予測が `specs/091-serving-weight-imputation/evidence/baseline_lgbm064_predictions.json` と**全頭 1 ビットも違わない**こと。compat 経路を通ったことが `logic_version` から読めること。compat pin した active に**新しい列順が渡っていない**ことも確認
- [X] T041 [US2] `serving/tests/integration/` に artifact loader / CLI が `features-021` を正しく選ぶことの検証を追加(loader が新 version を選ばない・recipe hash が非互換になる、を bump 後に検出する)
- [X] T042 [US2] **配線 E2E smoke**: `specs/091-serving-weight-imputation/evidence/wiring_smoke.json` に以下を出力して全項目を確認(codex #1)。**ここが緑にならない限り学習(T045)に進まない**
  - candidate の最終 feature list に `prev_weight` が**ちょうど 1 回**存在する
  - `weight` が欠損かつ `prev_weight` が非欠損のモデル入力行が実在する
  - fit / 校正 holdout / predict それぞれの mask 対象レース ID hash と件数
  - mask 後の最終行列で対象 3 列が NaN になっている
  - **`carried_weight_ratio` が後段で再計算されて復活していない**
  - artifact の feature version・recipe hash・mask spec hash
  - **`days_since_last` と `has_past_race` が candidate の最終 feature list に存在する** — FR-003/FR-004 は鮮度と有無をこの既存 2 列で満たす設計なので、片方でも落ちていれば `prev_weight` だけあっても要件を満たさない
- [X] T043 [US2] **配線の診断値**を `specs/091-serving-weight-imputation/evidence/wiring_diagnostics.json` に出力: `prev_weight` の feature importance と、`prev_weight` を shuffle したときの予測変化量。**「配線不良」と「正しく学習したが効果なし」を切り分ける**ための診断であり、採用 assertion ではない(codex #1)
- [X] T044 [US2] **outcome-blind 受入**: 直近 3 fold で候補を回し、`specs/091-serving-weight-imputation/evidence/acceptance.json` に **効果を見ない項目だけ**を出力して確認する — mask 件数 / 両アーム同一 mask / `prev_weight` カバレッジ / 指標が有限 / artifact・config の provenance 一致。**winner NLL の大小で継続可否を判断してはならない**(最終評価窓に同じ fold が含まれるため選択リークになる・codex #4)。artifact には `artifact_kind="acceptance"` と `eligible_for_verdict=false` を刻む

### plan Phase D: 本評価

- [X] T045 [US2] [gate-config.json](./gate-config.json) の `eval_window.to` を実行時点の最新確定レース日に確定し、**同時に `to_is_provisional` を `false` にしてから** canonical hash を計算する(実キーなので hash に参加する。`true` のまま凍結すると config が「暫定」と自称し続ける)。**gate-config を凍結**。canonical hash を計算して `specs/091-serving-weight-imputation/evidence/gate_config_hash.txt` に記録
- [X] T046 [US2] `gate-config.json` が `eval/src/horseracing_eval/decision.py` の `assert_confirmatory` を通ることを事前確認(`evaluation_contract_version: "v2"` の存在・`eval_window` の一致・hash 一致)。**070 の gate-config は 073 以前の作でこのキーを持たないため、コピー元にすると起動時に落ちる**
- [X] T047 [US2] 候補モデルを `features-021` + mask spec(rate=0.5, seed=20260810)で学習(長時間ジョブ。`nohup` + 監視。artifact は**絶対パス**の `--artifacts-dir` に出す = [weights-uri-relative-path-ops-bug] の再発防止)
- [X] T048 [US2] **本評価**(artifact に `artifact_kind="full_walk_forward"` / `eligible_for_verdict=true` を刻む): `training paired-eval --confirmatory --gate-config specs/091-serving-weight-imputation/gate-config.json --gate-config-hash <T045 の値> --from 2021-01-01 --to <T045 で確定した終端> --weight-regime both --subgroups` を実行。**`--subgroups` は必須** — gate-config が `critical_subgroups` を宣言している状態で subgroup を計算しないと `assert_confirmatory` が fail-closed で NO_DECISION を返し、verdict 正本の `serving_regime.subgroups.subgroup_guard` パス自体が生成されない(十数時間の学習と本評価のあとで verdict が出ない事故になる)。serving regime(PRIMARY)/ full-info regime(GUARD)/ subgroup guard を出力
- [X] T049 [US2] `eval/src/horseracing_eval/decision.py` に **verdict loader のガード**を追加: `artifact_kind="full_walk_forward"` のみを受理し、`eligible_for_verdict=false` の artifact を読んだら fail-closed。最終 fold 集合の完全一致・重複なし・受入 run ID を含まないことを検証(codex #4)
- [x] T050 [US2] [P] **診断アーム m=0.0** を同条件で実行し `specs/091-serving-weight-imputation/evidence/diagnostic_m0.json` に保存。目的は「mask が本当に必要か」の対照。artifact に `artifact_kind="diagnostic"` / `eligible_for_verdict=false` を刻む。**verdict には使わない**
- [x] T051 [US2] [P] **診断アーム m=1.0** を同条件で実行し `specs/091-serving-weight-imputation/evidence/diagnostic_m1.json` に保存。目的は「当日体重を捨てる設計」の先行測定。artifact に `artifact_kind="diagnostic"` / `eligible_for_verdict=false` を刻む。**verdict には使わない**
- [ ] T052 [US2] [P] bootstrap block 幅感度(2/3/4 日・週)を `specs/091-serving-weight-imputation/evidence/bootstrap_sensitivity.json` に出力(073 の診断枠。`artifact_kind="diagnostic"` を刻む。**ゲートに AND しない**)
- [X] T053 [US2] `eval/tests/unit/test_verdict_isolation.py` を新規作成。**受入(T044)と診断アーム(T050/T051)の数値を改変しても verdict が変わらない**ことを機械的に確認(codex #4/#5)
- [X] T054 [US2] verdict を **`verdict.adopt`(レポートが出力する単一真偽値)**から読み取り、`specs/091-serving-weight-imputation/evidence/verdict.json` に記録。**個別数値の事後読み替えを行わない**。実行不能なら NO_DECISION(ボーダー数値を理由にしない)

**Checkpoint C (US2 完了)**: 採否が事前登録した単一式から一意に決まり、受入と診断が verdict から機械的に隔離されている。

---

## Phase 5: US3 — 特徴の利用可能タイミング宣言を実態に合わせる (Priority: P3)

**Goal**: 宣言と実装の不一致を解消する。

**Independent Test**: 宣言を修正しても特徴量の値・列名・列順・`feature_hash` が変わらないことを確認し、レジストリ全体に同種の不一致が無いことを確認する。

**⚠️ このフェーズは Phase 3 / 4 に依存しない。** 値不変・列不変の宣言修正であり、採否と論理的に独立している。任意のタイミングで並行実施でき、**REJECT でも残す**。

- [X] T055 [P] [US3] `features/src/horseracing_features/registry.py` の `carried_weight_ratio` の availability_timing を `pre_entry` → `post_weight` に是正(実装は `features/src/horseracing_features/static_features.py` で 斤量 ÷ 当日体重、当日体重は `post_weight` 宣言)
- [X] T056 [P] [US3] `features/tests/unit/test_registry_timing.py` を新規作成し **INV-W10** を検証: 是正の前後で特徴量の値・列名・列順・`feature_hash` が完全一致する
- [X] T057 [P] [US3] `features/tests/unit/test_registry_timing.py` に**レジストリ全体の監査**を追加: 宣言された availability_timing より遅い入力に依存する列が他に存在しないこと。存在すれば同一変更セットで是正するか、是正しない理由を [research.md](./research.md) に追記(FR-033)
- [X] T058 [P] [US3] `features/tests/unit/test_registry_timing.py` に、**REJECT rollback を行っても timing 是正が残る**ことのテストを追加(採否非依存であることの機械的担保・codex #5)

---

## Phase 6: verdict 分岐と後始末

**Purpose**: 測定結果に応じた処理と記録。

- [X] T059 測定結果(serving / full-info / subgroup / 診断アーム / カバレッジ / 配線診断)を [spec.md](./spec.md) に転記する。**数値は評価レポートからの転記のみ**で、独自の再計算や再解釈を挟まない
- [x] T060 **ADOPT の場合のみ**: 運用者に昇格の可否を提示する。機械ゲートが通っても**自動昇格はしない**(FR-030)。承認後に `model_versions` の active を切り替え、旧 active を retired にする
- [x] T061 **ADOPT の場合のみ**: `serving/src/horseracing_serving/pipeline.py` の `logic_version` に体重 regime marker を追加(その予測が体重ありで計算されたかを後から絞り込めるようにする)。**監査のためではなくフィルタのため** — 再現性は `feature_snapshots` が per-horse のモデル入力ベクトルを保存していることで既に満たされている。**marker を足すなら予測の冪等キー(`_has_run_for_model` 相当)にも同じ版を参加させる** — さもないと marker 有り無しの実行が「既にある」で取りこぼされる(076 の `;calib=<digest>` が同型の罠を既に踏んでいる)
- [x] T062 **ADOPT の場合のみ**: [contracts/weight-mask.md](./contracts/weight-mask.md) §7 の禁止事項「full-info backfill 予測を live 品質の代理として使わない」を、shadow log / backtest のレポート生成側ドキュメントにも反映する(065 の closing-oracle バイアスと同型)
- [~] T063 **REJECT の場合のみ**: rollback 対象を漏れなく列挙して revert する — (a) `features/src/horseracing_features/registry.py` の FEATURE_VERSION bump と compat pin(T037)(b) `features/src/horseracing_features/materialize.py` の結線(T011)(c) **`serving/src/horseracing_serving/predictor.py` の可用性正規化(T026)**(d) `serving/src/horseracing_serving/pipeline.py` の観測配線(T067)。`weight_history_features.py` と `weight_mask.py` は**単体テストごと非結線で保全**(062/070 の前例)。**training / eval の mask 配線(T018-T022 / T028-T031)は既定 `None` のまま残置する** — `spec=None` はバイト同一なので「非結線」とみなせる(呼び出し箇所を物理削除する必要はない)。parquet を `features-018` で再 materialize
- [~] T064a **REJECT の場合のみ**: **FR-029a の退避先を記録する** — 「serving 経路での既存 `weight` 列への充当」(kill-test が実測した構成そのもの)を検討対象として `specs/091-serving-weight-imputation/research.md` に追記する。採用を約束するものではない。実際に採る場合の最低条件(入力方針の版を `logic_version` に持たせ、**その版を予測の冪等キーにも参加させる** = 076 の `;calib=` と同型)も併記し、別 feature として起こす
- [~] T064 **REJECT の場合のみ**: 負の結果をメモリファイル `body-weight-serving-skew.md` を更新して記録する。「固定モデルの replay では −0.0123 出たが、独立列 + mask の再学習では再現しなかった」という事実と、**配線診断(T043)と診断アーム(T050/T051)から読み取れる原因**(配線不良か、学習はしたが効果が無いか)を書き分ける
- [ ] T065 全パッケージのテストスイートを実行して緑を確認: `uv run --project features pytest features/tests` / `training` / `eval` / `serving`。`ruff` クリーン
- [x] T066 `git diff --stat` で **スキーマ・migration・API・OpenAPI・買い目生成に差分が無い**ことを確認(SC-007)
- [x] T067 [P] **可用性正規化の運用可視化**(FR-035・採否と独立・**早く始めるほど観測が貯まるので Phase 3 完了後いつでも着手してよい**): 開催日の予測実行時に、T026 の正規化が発動した回数と、その際の (計測済み頭数 / 出走頭数) の分布を `serving/src/horseracing_serving/pipeline.py` から記録する。**これは正しさの担保ではない** — 混在は T026 で構造的に起こらなくなっているので、ここで見るのは「どれだけ頻繁に full-info を手放しているか」という運用上の量である。`0` と `N` の二値しか出ないはずの分布に中間値が現れたら、それは T026 の判定が効いていない合図なので調査する

---

> **実装セッション 1 の記録(2026-08-10)**: Phase 1-3(MVP)+ US3 を実装した。**T035(FEATURE_VERSION bump + compat pin)を D0 から前倒しした** — 列を registry に登録した時点から bump までの間、`feature_hash` が変わる一方 version が features-018 のままなので現行 active モデルの serving が fail-closed になる。この中断状態でセッションを終えないため、Checkpoint A(パリティ緑)通過を確認したうえで bump まで進めた。**T038(INV-W9)も同時に実施し、lgbm-064-f02acc の予測が 44 頭すべてバイト一致**することを compat 経路で実測済み。
>
> bump の機械的な帰結として、`features-018` を pin していた既存テスト 8 本を `features-021` に更新した。うち 2 本(`test_speed_figure` / `test_past_market_leak`)は compat map の中身を検証しているので、単純置換ではなく **020→018 の pin と「compat は推移しない(017 は 018 経由で 020 に乗らない)」の assertion を追加**した。
>
> codex agent 3 が書いた `test_registry_timing.py` は `feature_hash` を絶対値で pin していたため、`prev_weight` 追加で落ちた。テストの意図(timing 修正は hash 中立)は正しいので、**絶対値 pin を before/after 比較に置き換えた** — 絶対値のままだと「列が一切増えない」ことまで暗黙に主張してしまい、列を足す feature のたびに落ちる。

> **実装セッション 2(Phase 4 前半)**: 評価契約(regime 対応・verdict 合成・境界値・verdict 隔離)と D0 受入まで完了。**Checkpoint D0 通過**(配線 smoke 14/14)。gate-config を凍結(hash `c3594766…`、窓 2021-01-01..2026-08-09、改竄検知を実測)、候補モデル `lgbm-091-wmask` の学習を開始した。
>
> **配線 smoke が実データの事実を 1 件掘り当てた**: 学習母集団(started かつ確定済 956,099 行)で当日体重が欠損するのは **1 行だけ**で、spec に書いていた「学習側 0.3%」は取消行を含む `race_horses` 全体の数字だった。started 行の欠損 452 行のうち **451 行は未確定レース側**にある。つまりモデルは学習中に「体重なし」をただの一度も見ない。mask 前 0 行 → mask 後 429,399 行という実測が、「mask は機構の本体」を数字で裏づけた。spec を訂正済み。
>
> **T031 実装済み**(セッション 3): `predict_over_folds_multi(collect_raw=True)` が `raw_win_probs` を fold ごとに収集し、regime 別に校正前 winner NLL と CI を出す。raw が取れない predictor には fail-closed(診断が黙って消えるのを防ぐ)。verdict には参加しない。
>
> 残: T015 / T023-T025 / T031 / T037(補助) / T040(済) / T044 / T047(実行中) / T048 以降。

## Dependencies

```
Phase 1 (Setup: T001-T003)
    ↓
Phase 2 (Foundational: T004-T007  ← テスト足場・赤で開始)
    ↓
Phase 3 US1 ─ plan Phase A: T008→T009→T010→T011→T012→T013/T014→T015
             【Checkpoint A: パリティ緑でなければ T037 の bump に進まない】
           ─ plan Phase B: T016→T017→T018→T019→T020→T021→T022→T023→T024→T025→T026→T027
    ↓
Phase 4 US2 ─ plan Phase C: T028→T029→T030→T031→T032→T033→T034→T035→T036
           ─ plan Phase D0: T037→T038→T039→T040→T041→T042→T043→T044
             【Checkpoint D0: T042 の配線 smoke が緑でなければ T047 の学習に進まない】
           ─ plan Phase D: T045→T046→T047→T048→T049→(T050/T051/T052 並列)→T053→T054
    ↓
Phase 6 (T059-T066・T064a 含む)

Phase 5 US3 (T055-T058) ── 他フェーズに依存しない。いつでも並行可
T067 (可用性正規化の運用可視化) ── Phase 3 完了後いつでも。採否と独立
```

**ストーリー間の依存**:

- **US1 → US2**: 必須。列と mask 機構が無ければ評価できない
- **US3**: 独立。US1/US2 の成否と無関係(T058 がそれを機械的に担保する)

## Parallel Opportunities

| 並列群 | タスク | 理由 |
|---|---|---|
| Setup | T002, T003 | 別ファイル・独立 |
| Foundational | T005, T006, T007 | 別テストファイル |
| 診断アーム | T050, T051, T052 | 本評価と独立。ただし計算資源を共有するので実際には逐次が現実的 |
| US3 全体 | T055, T056, T057, T058 | 他フェーズと完全並行可 |

## Implementation Strategy

### MVP スコープ

**Phase 1 + 2 + 3(US1)= T001–T027**。この時点で「体重未公表でも前走体重を織り込んだ入力で予測できる」状態になり、`spec=None` では現行とバイト同一が保たれる。ただし**採否は決まらない**ので、本番へ入れるには US2 が要る。

### 中断点(意図的に置いた 2 箇所)

1. **Checkpoint A(T013/T014 の後)**: 既存列のバイト一致と fingerprint 不変が取れなければ FEATURE_VERSION bump に進まない。ここで止まれば影響はゼロ
2. **Checkpoint D0(T042 の後)**: 配線 E2E smoke が緑でなければ、十数時間かかる学習と本評価に進まない。**この受入は効果を見ない**(効果で止めると選択リークになるため)

### この feature 特有の注意

- **受入(T044)で効果を見ない。** 直近 fold は最終評価窓に含まれるので、そこでの勝敗を継続判断に使うと選択リークになる。効果で中断したいなら当該 fold を verdict から除外するか二段階継続規則を事前登録する必要があり、それは本 feature の射程外
- **診断アームの結果で採用値(m=0.5)を差し替えない**(憲法 III・068 C2)
- **mask が両アームに適用されていることを検証する**(T032)。片側だけだと数値は出るが比較の意味が消える = 「テストは通るが要件を満たさない」型の典型
- **assertion 自体を kill-test する**(T025 / T035)。配線を外して assertion が落ちなければ、その assertion は何も守っていない
- **組込み `hash()` を使わない**。`PYTHONHASHSEED` でプロセスごとに変わり、mask 選択が再現しなくなる(T007 の golden vector で固定)
- **長時間ジョブ**(T047 の学習、T048 の本評価)は `nohup` + 監視で流す。DB 再起動で全損した前例がある(056)
