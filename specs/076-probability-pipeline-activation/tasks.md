---
description: "Task list for Probability Pipeline Activation & Parity (076)"
---

# Tasks: Probability Pipeline Activation & Parity

**Input**: Design documents from `specs/076-probability-pipeline-activation/`

**Prerequisites**: plan.md, spec.md, research.md (D0–D10), data-model.md, contracts/{loader,cli}-contract.md, quickstart.md

**Tests**: **含める**。本 feature の成果物は parity / leak-guard / fail-closed / 冪等の不変テスト群そのもの
(SC-001–012)。契約ゲートであり値改善ゲートではない。

**Organization**: user story ごとにフェーズ分割。US1/US2/US4=P1、US3=P2。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 別ファイル・依存なしで並列可
- 各タスクに正確なファイルパス

## Path Conventions

既存マルチパッケージ(`probability/` `betting/` `serving/` `api/` `live/` `ops/` `training/`)。各パッケージ
配下に `src/horseracing_<pkg>/` と `tests/{unit,integration}/`。**db/front/admin/migration には触れない**。

---

## Phase 1: Setup

- [X] T001 [P] fixture manifest ビルダ helper を追加(`training/tests/support/fixture_manifest.py`): `build_manifest`(v2)で `artifact_scope`/`activation_eligible`/`fit_through` を指定し、既知 γ/λ の fixture(fixture / production-eligible / tampered / expired の 4 種)を絶対パスに書く。全 parity/fail-closed テストが共有。
- [X] T002 [P] gate 定数を 1 箇所に(`probability/src/horseracing_probability/calib_activation.py` の module 定数として `ActivationMode`(`legacy-runtime`/`manifest-required`)・`Profile`(`production`/`fixture`)・`ActivationError`(ManifestError の別系統)を宣言。中身は Phase 2 で実装)

---

## Phase 2: Foundational (BLOCKING — 全 user story の前提)

**目的**: manifest v2 schema と単一共有 loader。US1/US2/US3 はすべてこの loader を注入するため先行必須。

- [X] T003 manifest schema を v1→v2 に additive 拡張(`training/src/horseracing_training/calib_manifest.py`): `SCHEMA_VERSION=2`・`_REQUIRED_FIELDS` に `artifact_scope`/`activation_eligible`/`fit_through` を追加・`verify_manifest` に scope 集合値 / bool / date 妥当性 **+ `stage_lambdas` の `{top2,top3}` key presence**(欠如は raw KeyError でなく `ManifestError`=A1)の検証を追加。旧 v1 manifest は `unknown schema_version` で fail-closed(data-model §1)。`build_manifest(...)` は 3 新引数を **`artifact_scope="fixture"`/`activation_eligible=False`/`fit_through=<必須>`** の既定付きで追加(back-compat=既存呼出は fixture 既定)。
- [X] T003b 既存 074 manifest テストを v2 に移行(`training/tests/unit/test_calib_manifest.py` ほか `test_074_boundary.py`/`test_074_leak_guard.py` 等の `build_manifest`/`verify_manifest` 呼出): v1 前提を v2 に更新(新フィールド付き fixture)し緑化。**T030 は緑確認のみ=移行を別途行わないと回帰が落ちる**ため本タスクで先に移行(C1)。
- [X] T004 [P] `verify_manifest` v2 の単体テスト(`training/tests/unit/test_calib_manifest_v2.py`): v2 受理・v1 拒否・artifact_scope 不正値拒否・activation_eligible 非 bool 拒否・**`stage_lambdas` に `{top2,top3}` 欠如で `ManifestError`(raw KeyError でない=A1)**・manifest_digest 改竄拒否・同 payload 同 digest(冪等)。
- [X] T005 共有 loader `load_calibration` を実装(`probability/src/horseracing_probability/calib_activation.py`): loader-contract.md の順序固定フロー = `verify_manifest` → 世代照合(base_model_version 一致 + **`active_model_dir`[必須引数] から 074 の `attestation_from_model_dir(dir, code_sha=manifest.code_sha)` で attestation を再計算し `attestation_digest` を `manifest.attestation_digest` と一致比較**=FR-019 の上書き耐性)→ scope 照合(production profile は fixture/未 eligible 拒否)→ 時間照合(`target_date > fit_through`)→ `two_gamma→PCalibrator` / `stage_lambdas{top2,top3}→StageDiscount(lambda2,lambda3)` マッピング → `Activation` 返却。全失敗は例外(fallback しない)。1 invocation 1 回ロード。**`Activation` に per-target_date の `applies_to`/`assert_applies` メソッドを実装**=単一レースは `load_calibration(target_date=)` で判定、backfill は load-once した Activation を日毎 `assert_applies(day)`(ファイル再読込しない=FR-018×FR-021 両立・I1)。**絶対パス検証は loader 内に集約**(各 entry path は継承=F4)。**4-key checksum mapping では 074 の full-payload digest を再構成できないため、model dir + code_sha を渡して 074 の実関数で再計算する**(C1 是正)。
- [X] T006 [P] `load_calibration` の fail-closed 単体テスト(`probability/tests/unit/test_calib_activation.py`): 欠如/改竄/partial/未知 schema/世代不一致(base 名不一致 **+ 再計算 `attestation_digest` 不一致=同名上書きシナリオ**)/scope=fixture を production で/`target_date<=fit_through` の 100% 拒否(SC-005/010/012・FR-019)。マッピングの `top2→lambda2`/`top3→lambda3` 固定。**identity 校正も有効 Activation**=identity manifest でも `;calib=<digest>` を logic_version に記録し **manifest run 経路を通る**(silent legacy fallback にならない)ことを assert(no-op 適用だが監査上 manifest 由来と判別可能)。**profile positive**=`profile="fixture"` で fixture-scope manifest を**受理**する分岐も 1 件 assert(fixture profile はテストハーネス専用・production は拒否と対）。
- [X] T007 [P] leak-guard 単体テスト(`probability/tests/unit/test_calib_activation_leakguard.py`): `load_calibration` が `load_p_samples`/`_latest_run_predictions`/任意 fit/`RaceResult` クエリを呼ばない(import-graph + monkeypatch トリップワイヤ)。校正派生値(γ/λ/digest)を特徴に還流しない token grep(SC-009・FR-012)。

**Checkpoint**: loader が有効 fixture から Activation を返し、無効は全て fail-closed。US1–US3 着手可。

---

## Phase 3: User Story 1 — 推薦 two-gamma を manifest から読む (P1)

**Goal**: betting 推薦の two-gamma を runtime fit でなく manifest から読む。win 選択/pseudo/Kelly に反映。

**Independent Test**: `--calib-manifest <prod-fixture> --calib-mode manifest-required` の推薦が manifest γ を
使い(`load_p_samples` 非呼出)、manifest 無しは現行とバイト同等。

- [X] T008 [US1] `_fit_product_p_calibrator` に manifest 経路(`betting/src/horseracing_betting/cli.py`): `calib_manifest`/`calib_mode` 指定時は、選択 run(`PredictionRun.model_version`)を `load_calibration(..., active_model_version=<run model>, target_date=<race date>, attestation_verifier=None)` に渡し `.two_gamma` を返す(FR-020/021)。**betting は training 非依存なので attestation_verifier=None**=name+content-address binding(strong binding は 077・D11)。`load_p_samples`+`fit_p_calibrator` の leaky 経路に入らない。既存 `generate_recommendations(p_calibrator=)`(046)にそのまま注入。**target_date=対象レース日**(backfill は per-day)。
- [X] T009 [US1] betting CLI に `--calib-manifest`(絶対パス必須)/`--calib-mode` を追加(`betting/src/horseracing_betting/cli.py` の recommend / recommend-backfill サブコマンド): 相対パス=typed error・`--calib-manifest` かつ `legacy-runtime`=矛盾エラー(cli-contract.md)。
- [X] T010 [US1] logic_version + 冪等キーに manifest digest(`betting/src/horseracing_betting/cli.py` / recommend.py): 出力 lv に `;calib=<digest12>;calibmode=manifest`。recommend 冪等の「既存 run あり?」判定に `;calib=<digest>` を含め、別 digest は別 run(FR-009/010, data-model §4)。
- [X] T011 [P] [US1] betting activation の parity テスト(`betting/tests/integration/test_calib_activation_betting.py`): `betting_two_gamma_parity`(推薦純関数の γ == evaluator の γ、SC-002)・manifest OFF はバイト同等(SC-007)・`betting_joint_identity_stage_parity`(exotic joint は **λ=1 構造維持**だが p は two-gamma で変わる=OFF と byte 不一致、SC-004b)・別 digest で別 run(SC-006)。**recommend-backfill の fail-closed-before-loop**=無効 `manifest-required` manifest が per-race 例外隔離ループ**前**に検出され 0 行・非 0 終了(serving T017 と対の betting 側、FR-022)。

**Checkpoint**: betting が単独で manifest 由来 two-gamma を使い、他経路と独立にテスト可能。

---

## Phase 4: User Story 2 — serving 表示 stage-discount を manifest から読む (P1)

**Goal**: serving の top2/top3 stage-discount λ を manifest から読む。**win はバイト不変**。全 entry path 結線。

**Independent Test**: `--calib-manifest <prod-fixture>` の serving が manifest λ で top2/top3 を割引き、
win_prob は manifest 有無で 16 頭 byte-identical。

- [X] T012 [US2] `_fit_stage_discount` に manifest 経路 + `run_serving`/`run_serving_backfill` に calib 引数(`serving/src/horseracing_serving/pipeline.py`): 指定時、`training.calib_binding.model_dir_attestation_verifier(<ServingModel dir>)` を作り `load_calibration(..., active_model_version=<serving model>, target_date=<race date>, attestation_verifier=<verifier>)` を呼び `.stage_discount` を返す(strong binding=FR-019・serving は training を持つ・D11)。既存 stage_discount 注入経路(predict_race)をそのまま使う。**win を触らない**。**`run_serving`/`run_serving_backfill` に新 param `stage_discount: StageDiscount|None=None` を追加**し優先順位を明示: 注入された `StageDiscount` は `_fit_stage_discount`(runtime fit)を**置換**する / `apply_stage_discount=False` は従来どおり割引なし(注入より優先=OFF)/ 両 None かつ ON=現行 runtime fit。**target_date=対象レース日**(`run_serving`=単一レース日、backfill=per-day ループの当日)。
- [X] T013 [US2] serving CLI に `--calib-manifest`/`--calib-mode`(`serving/src/horseracing_serving/__main__.py` の predict / predict-backfill): backfill は **ループ(per-day/per-race 例外隔離)の前に 1 回検証**(FR-022)・無効 manifest は 0 行・非 0 終了。**target_date は per-day**(backfill の各当日)= `fit_through` 以前の日は fail-closed(FR-021・historical backfill 制約)。
- [X] T014 [US2] logic_version + backfill 冪等キーに digest(`serving/src/horseracing_serving/pipeline.py`): 既存 `;sdisc=…` に `;calib=<digest12>` 追記。冪等キーに digest を含め別 digest は別 run(FR-010)。
- [X] T015 [P] [US2] `live refresh` **と `live collect-prospective`(065)** へ結線(`live/src/horseracing_live/orchestrate.py` + `live/src/horseracing_live/cli.py`): `refresh_range`/`_refresh_one` は Activation の **`p_calibrator`(two_gamma)+ `stage_discount` 両方**を通す。**`collect-prospective` は推薦(win)中心のため `p_calibrator`(two_gamma)のみ結線**し stage_discount は通さない(表示 top2/top3 専用=prospective win には無関係、no-op を混ぜない)。両 CLI に `--calib-manifest`/`--calib-mode`。load-once。FR-017 が両経路を名指し=どちらか未結線だと two-gamma leak 残存。
- [X] T016 [P] [US2] ops subprocess argv へ伝播(`ops/src/horseracing_ops/runner.py`): serving/recommend subprocess の argv に `--calib-manifest <abs> --calib-mode <mode>` を足す(ML 非 import 境界維持)。ops job payload に manifest path/mode(未設定=現行挙動)。
- [X] T017 [P] [US2] serving activation の parity テスト(`serving/tests/integration/test_calib_activation_serving.py`): `model_internal_win_parity`(win 16 頭 byte-identical、SC-001)・`display_topk_parity`(eval==serving の λ、top2/top3 更新可、SC-003)・backfill fail-closed-before-loop(0 行/非 0)・冪等(別 digest=別 run)。**advisory-lock は既存 backfill 冪等 infra(043/044/053)を継承**=同 digest 並行実行で重複 run を作らないことを assert(FR-022、新規ロック実装なし=継承の確認)。
- [X] T018 [P] [US2] 全 entry path 一致テスト(`live/tests/integration/test_entry_path_calib.py` + `ops/tests/integration/test_ops_calib_argv.py`): CLI / live refresh / **live collect-prospective** / ops subprocess が同一 fixture で同一 `manifest_digest` を解決し lv の `;calib=` 一致(SC-011=全経路)。ops 境界(ML 非 import)不変。**load-once assertion**=1 invocation で manifest が丁度 1 回読まれる(spy/call-count トリップワイヤ、FR-018)。

**Checkpoint**: serving が win 不変で表示 λ を manifest 化。全経路で digest 一致。

---

## Phase 5: User Story 3 — 066 dispersion model_delta を manifest から読む (P2)

**Goal**: dispersion の model_delta two-gamma を manifest 直読に(3 つ目のリーク面)。派生 JSON 廃止。

**Independent Test**: manifest 指定で model_delta が manifest 由来 two-gamma を使い、band/raw q は不変、
manifest 無しは fail-open で model_delta 省略。

- [X] T019 [US3] API dispersion を manifest 直読に(`api/src/horseracing_api/dispersion.py`): `load_p_calibrator`(派生 JSON)を `load_calibration(...).two_gamma` 直読に置換。manifest 無しは従来どおり fail-open(model_delta 省略)。band は q のみ=不変。
- [X] T020 [US3] 選択 run の model と照合(`api/src/horseracing_api/routers/predictions.py`): dispersion に **API が選択した prediction run の model_version**(057 candidate 選択)と対象レース日を渡し、`load_calibration(active_model_version=<selected>, target_date=<race date>, attestation_verifier=None)` で照合(api は training 非依存=name+content-address binding・D11)。active model 固定にしない。**target_date=そのレースの race_date**。
- [X] T021 [US3] `dispersion-pcal` CLI を verify/inspect 用途に縮退(`training/src/horseracing_training/cli.py`): API が manifest 直読(T019)になったため、派生 pcal JSON を read path から外す。CLI は `--calib-manifest` を検証・γ を表示する inspect ツールに縮退(新規 artifact 生成運用は廃止)・非OOS NOTE は「model_delta は manifest 由来=OOF」に更新(D10・spec スコープ外注記)。
- [X] T022 [P] [US3] dispersion activation テスト(`api/tests/integration/test_dispersion_calib.py`): model_delta が manifest 由来(派生 JSON 非生成)・band/raw q 不変・candidate run の model と照合・manifest 無しで fail-open。**present-but-invalid manifest(改竄/世代/scope/時間)でも loader 例外を捕捉し model_delta 省略・200 維持**(read-only fail-open=予測エンドポイントを壊さない、FR-003)。

**Checkpoint**: 3 経路すべて manifest 由来。074 の 3 リーク面が(fixture 上で)閉じる。

---

## Phase 6: User Story 4 — allowed-change matrix parity を固定する (P1・cross-cutting)

**Goal**: 「変えてよい/絶対変えない」の境界を機械ゲート化。US1–US3 の経路が揃った後に横断検証。

**Independent Test**: activation ON でも win byte 不変・API/exotic joint λ=1・fixture 拒否が 100%。

- [X] T023 [P] [US4] `api_joint_legacy_parity`(`api/tests/integration/test_joint_legacy_parity.py`): activation ON で `GET …?bet_type=&top=K` の joint が λ=1 のまま・activation OFF とバイト一致(SC-004a、不変 win_prob 由来)。exotic betting joint 側は λ=1 構造のみ(SC-004b、T011)。
- [X] T024 [P] [US4] allowed-change matrix の集約テスト(`probability/tests/integration/test_allowed_change_matrix.py`): data-model §5 の 7 行を 1 スイートで固定(win byte / top2-3 更新可 / two-gamma 変更可 / exotic λ=1 構造 / api joint byte / model_delta 変更可 / band 不変)。参照実装として各 SC テストを呼び出す。
- [X] T025 [P] [US4] fixture-rejection E2E(`serving/tests/integration/test_fixture_rejection.py` + betting 同型): `artifact_scope=fixture` を production profile + `manifest-required` で **非 0 終了・0 行**(SC-010)。fallback 痕跡なし。
- [X] T026 [P] [US4] 時間検証 E2E(`probability/tests/integration/test_temporal_reject.py`): `target_date<=fit_through` の manifest が全経路で 100% 拒否(SC-012)。**historical backfill 相互作用**=`manifest-required` で `fit_through` 以前レンジを backfill すると当該レースが fail-closed(0 行/非 0)し、`fit_through` より後のレンジのみ適用可であることを assert(FR-021・意図的挙動)。

---

## Phase 7: Polish & Cross-Cutting

- [X] T027 [P] artifact read-only 運用ノート(`deploy/README` または該当 deploy 手順 + `specs/076-.../quickstart.md` 参照更新): manifest artifact ディレクトリを read-only にする(D9・authenticity は署名でなく read-only+content-address で担保、署名/registry は 077)。
- [X] T028 [P] メモリ/CLAUDE 整合更新(`.claude` 外 memory は別途): [[feature-074-manifest-unwired]] / [[calibration-leak-fixes-status]] に 076 実装状況を反映(fixture-first・real manifest は follow-up)。
- [X] T029 実 DB E2E スモーク(quickstart.md の手順 2–9 を実 DB lgbm-063 で通す): win byte-parity 16 頭 mismatch 0・betting lv `;calib=` 記録・fail-closed 非 0・冪等 別 run・全経路 digest 一致を手動確認し結果を plan/summary に記録。
- [X] T030 [P] ruff / 各パッケージ既存テストの回帰確認(`probability`/`betting`/`serving`/`api`/`live`/`ops`/`training`): activation OFF 既定で全既存スイート緑(後方互換 SC-007)。**FR-015 assertion**=各 CLI の既定 `--calib-mode` が `legacy-runtime` であり、既定経路が `load_calibration` を呼ばないことを 1 行 assert(既定 ON 昇格していない)。**FR-013/SC-008 assertion**=migration head 不変(alembic head 変化なし)・db/front/admin/migration ディレクトリ差分ゼロを確認(schema-zero 構造ガード)。**FR-014 assertion**=`build_manifest`/`calibrate_oof` に新たな production caller が増えていない(grep/import-graph=実 manifest 生成・stage-λ OOF fit を 076 で実装していない negative 確認)。

---

## Dependencies & Execution Order

- **Phase 1 Setup** → **Phase 2 Foundational**(loader)が全 US をブロック。
- **Phase 3 US1(betting)/ Phase 4 US2(serving)/ Phase 5 US3(dispersion)** は Foundational 後は**相互独立**(別パッケージ)→ 並列可。各々単独で MVP 価値。
- **Phase 6 US4(matrix parity)** は US1–US3 の経路が揃ってから(横断検証)。
- **Phase 7 Polish** は最後。
- 依存の要: T003(schema v2)→ T005(loader)→ 各 US の T008/T012/T019。T005 が済むまで US 着手不可。

## Parallel Opportunities

- Phase 2 内: T004 / T006 / T007 は別テストファイルで並列(T003/T005 実装後)。
- **Phase 3/4/5 は別パッケージ = フェーズ間並列可**(betting / serving+live+ops / api+training)。
- Phase 6: T023–T026 は別ファイルで並列。
- Phase 7: T027 / T028 / T030 並列(T029 は実 DB 直列)。

## Implementation Strategy

- **MVP = US1(betting two-gamma activation)**: 唯一 production の意思決定(買い目・Kelly)に影響する経路。
  Foundational(loader)+ US1 だけで「凍結校正で推薦を作る」価値が独立に成立。
- 増分: US1 → US2(serving 表示・全経路)→ US3(dispersion)→ US4(matrix 固定)→ Polish。
- **fixture-first**: 全タスクは fixture manifest で完結。実 manifest 生成(stage-λ OOF fit + build_manifest
  結線 + full OOF job)は本 tasks の**スコープ外=blocking follow-up**。それが済むまで 076 は plumbing。

## Notes

- スキーマ変更ゼロ・migration なし・db/front/admin 非変更。
- 既定 mode=`legacy-runtime`(現行挙動保存)。既定 ON 昇格は実 manifest + full parity 後に別判断。
- codex spec レビュー全採用(research D0)。plan 判断は codex bless 済みの忠実実装。
