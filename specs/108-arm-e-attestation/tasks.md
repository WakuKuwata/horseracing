# Tasks: arm E 系モデルの OOF attestation 対応

**Input**: Design documents from `/specs/108-arm-e-attestation/`
**Prerequisites**: plan.md, research.md (D1-D8), data-model.md (INV-A1..A6 / INV-M1..M3), contracts/attestation.md

**組織**: US1(attestation の arm E 対応)が MVP。US2(manifest 生成)は US1 依存で**中断点つき**。
US3(活性化検証)は US2 依存。

## Phase 1: Setup

- [ ] T001 実行前提の確認: postgres 稼働・現行 active(`model_versions` の adoption_status='active')の
  model_version とモデルディレクトリを確定・`artifacts/model_versions/<active>/metadata.json` に
  `calibration_protocol.n_oof_blocks` と `weight_mask` が存在することを確認し、
  値を `specs/108-arm-e-attestation/evidence/preconditions.json` に記録
- [ ] T002 **旧世代 digest の golden 固定**(FR-002 の基準線): 現行コードで
  `attestation_from_model_dir("artifacts/model_versions/lgbm-063",
  code_sha="c8fc3a9cc7b55e6d0ce5da6598d609e4d3a80202")` を実行し(**既存 production manifest が
  記録している code_sha**。任意定数だと activation 経路の不変を示せない・analyze I2)、
  payload と `attestation_digest` を `specs/108-arm-e-attestation/evidence/legacy-attestation-golden.json`
  に保存。**この採取を実装前に行う**(実装後に採ると不変性を検証できない)

## Phase 2: Foundational(US1 の前提・blocking)

- [ ] T003 `training/src/horseracing_training/calib_split.py` の `to_servable()` で
  `info["calibrator_degenerate"]` を **`self.oof_info_` の値から転記**する(D4 の報告バグ是正)。
  ハードコードしない — 真値は既に `oof_info_["calibrator_degenerate"]` にあり、
  権威ある場所を読む原則(INV-A6)と一致させる。**既存モデルの metadata.json は書き換えない**
- [ ] T004 [P] T003 の回帰テスト(`training/tests/unit/test_calib_split_metadata.py` に追記または新規):
  arm E の `to_servable()` が返す `fit_info_` の `calibrator_degenerate` が False であり、
  `calibration`/`calibration_split_unit`/`calibration_protocol` は現行どおりであること

**Checkpoint**: 権威フィールドが信頼できる状態 = attestation 拡張に着手可能

## Phase 3: User Story 1 — attestation の arm E 対応 (P1・MVP)

**Goal**: 現行世代の学習手続きを attestation として記録し、そこから忠実に再構成できる
**Independent Test**: 現行 active から attestation 生成 → 再構成が記録と一致・旧世代 digest 不変

- [ ] T005 [US1] payload 構築を方式別に分岐(`training/src/horseracing_training/legacy_attest.py`
  の `build_attestation`): 校正方式が OOF のとき **出荷ビューと構成ブロックを分離**して組む —
  出荷ビューは metadata の値をそのまま(**欠落を既定値で埋めない**・D2 の fail-open 封鎖)、
  構成ブロックに `protocol_version`(`strict_past_oof_isotonic_v1`)・`n_oof_blocks`・
  mask rate/seed・**容量を含む解決済みパラメータ**を入れる。`calib_frac` は
  **`{requested, effective}` に分けて記録**(codex 指摘 1)。**legacy 方式は payload を
  バイト同一に保つ**(キーを一切足さない・D1)。読み出し元は
  `metadata.calibration_protocol` と `metadata.weight_mask`(top-level `calibrator_degenerate` は
  読まない・INV-A6)
- [ ] T006 [US1] 検証を方式別に分岐(`training/src/horseracing_training/legacy_attest.py` の
  `_validate_payload` / `_INTERNAL_CALIBRATION_FIELDS`):
  **`method` の値域を既知集合に限定し 3 分岐にする**(セルフレビュー観点 2 で検出した fail-open:
  現行は method の値域を見ないため、未知の method が legacy 扱いで素通りする):
  OOF → arm E 検証 / 既知 legacy → 現行検証 / **未知 → 型付きエラーで拒否**(085 の教訓と同型)。
  arm E 検証は `calib_frac == 0.0` **かつ** `split_unit is None` **かつ** `n_oof_blocks` が正整数。
  `weight_mask` は rate ∈ [0,1] と整数 seed が**両方揃うか両方無いか**のみ受理し、
  **metadata に `weight_mask` があるのに読めない場合は型付きエラー**(観点 1: 省略と欠落を区別)。
  違反はすべて型付きエラー(既定値フォールバックを作らない・INV-A4)
- [ ] T007 [US1] 再構成を方式別に分岐(`training/src/horseracing_training/legacy_attest.py` の
  `_recipe_from_payload` / `factory_from_attestation` / `general_factory_from_attestation`): arm E は
  **構成ブロックから** `ModelRecipe(calibration="isotonic", calib_frac=<既定・非挙動>,
  split_unit=<既定>, params=<容量>, weight_mask_rate/seed=<記録値>)` +
  `CalibSplitFactory(method="isotonic", n_oof_blocks=<記録値>)` を組む。
  **出荷ビューの `calib_frac=0.0`/`split_unit=null` から recipe を組んではならない**
  (`ModelRecipe(split_unit=None)` は構築不能・実測済み)。`n_oof_blocks` はコード既定 3 に、
  **容量はコード既定に落とさない**(実モデルは 900・D3)。再構成後に記録された構成との
  **挙動的一致**を検査し不一致は型付きエラー(INV-A5)
- [ ] T008 [P] [US1] **digest 不変の golden テスト**(`training/tests/unit/test_attestation_legacy_parity.py`):
  T002 で採取した golden の payload と digest が、実装後の
  `attestation_from_model_dir("artifacts/model_versions/lgbm-063", ...)` と**完全一致**すること
  (INV-A1・SC-002)。この 1 件が既存 manifest と過去 verdict を守る唯一の防波堤
- [ ] T009 [P] [US1] arm E attestation の単体テスト(`training/tests/unit/test_attestation_arm_e.py`):
  (a) 現行 active から生成成功・payload に `n_oof_blocks`/`weight_mask` が含まれる
  (b) OOF 宣言 + `n_oof_blocks` 欠落 → 型付きエラー (c) OOF 宣言 + 非 null split_unit → 拒否
  (d) OOF 宣言 + `calib_frac != 0.0` → 拒否 (e) mask の rate/seed 片方だけ → 拒否
  (f) legacy 宣言 + calib_frac=0.0 → 現行どおり拒否
  (g) **未知の method 文字列 → 拒否**(legacy 扱いで素通りしないこと・セルフレビュー観点 2)
  (h) **metadata に weight_mask があるのに rate/seed が読めない → 拒否**(観点 1)
  (i) **未知の `protocol_version` → 拒否**(codex 指摘 2)
  (j) **出荷ビューが OOF を名乗るのに構成ブロックが無い → 拒否**(相互整合・FR-005c)
  (k) **欠落値が既定値で補完されていない**ことを assert(現状の捏造の再発防止・FR-005a)
  (fail-open の芽が無いこと・SC-003)
- [ ] T010 [P] [US1] 改竄・世代取り違えの単体テスト(`training/tests/unit/test_attestation_tamper.py`):
  payload 1 バイト改竄で
  digest 不一致・モデルディレクトリ差し替えで再計算照合が拒否(INV-A2/A3・**既存機構を緩めて
  いないことの確認**)
- [ ] T011 [US1] 再構成忠実性のテスト(`training/tests/unit/test_attestation_arm_e_factory.py`):
  現行 active の attestation から再構成した factory の構成(目的関数・校正プロトコル・
  `n_oof_blocks`・mask rate/seed・**解決済みパラメータ(`n_estimators=900` を含む)**・
  特徴列順・seed)が記録された値と**挙動的に一致**すること(SC-001)。
  **`calib_frac` の宣言値は比較対象から除く**(非挙動項目・codex 指摘 1)

- [ ] T011a [US1] **`oof_generate` を非 legacy 対応にする**(analyze G1・着手時点で止まる欠陥):
  `training/src/horseracing_training/oof_generate.py` の `generate_oof_bundle` が
  `factory_from_attestation`(旧世代強制)を呼ぶのを `general_factory_from_attestation` に切替え、
  期待 model/feature version を**必須引数**で受けて fail-closed にする。
  `training/src/horseracing_training/cli.py` の `oof-generate` / `generate-manifest` の
  既定値(`lgbm-063`)と help も現行世代を指せるよう是正。**旧世代での実行結果は不変**であること
- [ ] T011b [P] [US1] **mask 伝播の構造 assert**(analyze U2・FR-006 の機構化):
  `training/tests/unit/test_attestation_arm_e_factory.py` に、再構成した factory で fit したとき
  **weight mask が実際に適用されたこと**を構造的に検査するテストを追加(091 の「mask は
  付随的工夫でなく機構の本体」)。適用されないなら fail-closed であることも固定
- [ ] T011c [US1] **決定論設定の確定**(analyze G4・FR-003a): arm E factory は `num_threads` を
  受け取るが使っていない。伝播させるか、伝播しないことを contract に明記して一致項目から外すかを
  **決めてテストで固定**する。`training/src/horseracing_training/calib_split.py` +
  `specs/108-arm-e-attestation/contracts/attestation.md`。**黙って無視される状態を残さない**

**Checkpoint**: US1 完了 = 「基盤が現行世代を表現できる」が独立に成立

## Phase 4: User Story 2 — 現行世代の manifest 生成 (P2) ★中断点あり★

**Goal**: 現行 active の手続きで OOF を再生成し、校正 verdict を測って production manifest を作る
**Independent Test**: 生成 manifest が production scope・現行 active 束縛・検証通過・digest 再現

- [ ] T012 [US2] **★中断点★ 1 fold のコスト実測**(FR-018): **`--first-valid-year 2026` で
  最終年 1 fold のみ**を実行し所要時間を測る(`--smoke` は `first_valid_year=2024` = 3 fold に
  なるので使わない・analyze M1)。**外挿式を事前固定**: `ETA = 実測秒 × 19 × 0.6`
  (最終年 fold が最も高価なので係数で割り引く)。結果を
  `specs/108-arm-e-attestation/evidence/oof-eta.json` に記録。
  **ETA > 12 時間なら T013 に進まず停止**し、実行方式を見直してから再判断する
- [ ] T012a [US2] **clean-tree 判定の是正**(analyze G3・憲法 V): `training/src/horseracing_training/cli.py`
  の manifest 生成は `"dirty" in sha or sha == "unknown"` で未コミットを検出しようとするが、
  `code_sha()` は `git rev-parse HEAD` の 40 桁 hex を返すため**この判定は決して発火しない**。
  `git status --porcelain` が空かどうかで判定するよう是正し、テストで固定
- [ ] T012b [US2] **attestation 変更をコミットする**(憲法 V・順序が重要): T014 の manifest 生成
  より**前**に本 feature のコード変更をコミットする。production manifest はその manifest を生んだ
  コードの SHA で刻印されなければならない(未コミット状態で生成すると刻印が嘘になる)
- [ ] T013 [US2] OOF 束の生成(数時間・nohup): 現行 active の attestation で全史(2008-2026)の
  OOF bundle を生成。**weight mask が再現されていることを実行前に確認**(再現できないなら
  fail-closed で停止・FR-006)。完了後 bundle digest と fold 数を
  `specs/108-arm-e-attestation/evidence/oof-bundle.json` に記録
- [ ] T014 [US2] manifest 生成(`training generate-manifest`): 074 の凍結 gate-config で校正 verdict を
  測り production scope の manifest を生成。**探索・調整・窓の選び直しをしない**(FR-007)。
  作業ツリーが dirty なら production scope にならない仕様を確認(FR-008・SC-004)
- [ ] T015 [US2] 生成物の検証: `training verify-manifest` が通ること・`base_model_version` が現行 active
  と一致(INV-M1)・同一入力から同一 digest が再現(INV-M3)。verdict(三値 ×2 stage)を
  `specs/108-arm-e-attestation/evidence/manifest-verdict.json` に記録。
  **どの verdict でも次に進む**(FR-010)
- [ ] T016 [P] [US2] 旧世代 artifact の不変確認(SC-007): 既存 manifest(`d9f45bb0…`)の内容と
  digest、および過去 verdict が 1 件も書き換わっていないことを確認(INV-M2)

**Checkpoint**: 現行世代に束縛された production manifest が存在する

## Phase 5: User Story 3 — 活性化の検証と運用手順 (P3)

**Goal**: 生成 manifest で全経路が凍結校正を読むことを実データで確認し、運用手順を残す
**Independent Test**: 既定 OFF でバイト不変・有効化で監査記録に識別子・不整合 3 種が実行前に拒否

- [ ] T017 [US3] 既定設定のバイト不変を実 DB で確認(SC-005): 任意の 1 レースで予測・推薦を実行し、
  本 feature 導入前の出力(md5)と一致すること。両者を
  `specs/108-arm-e-attestation/evidence/activation-parity.json` に記録
- [ ] T018 [US3] 明示有効化の検証(SC-006): 生成 manifest を指定して予測・推薦を実行し、
  監査記録(logic_version)に manifest 識別子が現れること・表示用 top2/top3 が凍結値由来に
  変わること(win はバイト不変・FR-017)を確認し、同じく
  `specs/108-arm-e-attestation/evidence/activation-parity.json` に追記
- [ ] T019 [P] [US3] fail-closed 3 種の実地確認(SC-006): (a)旧世代 manifest を指定 → 世代不一致で
  実行前拒否・0 行 (b)`fit_through` 以前の対象日 → 拒否 (c)fixture scope の manifest を production で
  → 拒否。**いずれも黙って現行動作にフォールバックしない**(FR-013)
- [ ] T020 [US3] 運用手順の文書化(FR-014): `specs/108-arm-e-attestation/quickstart.md` に
  有効化の設定箇所(env)・確認方法・元に戻す方法を追記。**既定の切り替えは行わない**(FR-015)

- [ ] T020a [US3] **runbook を実際に辿る**(SC-008): T020 で書いた手順どおりに
  有効化 → 確認 → 復旧 を一度通し、記載と実際が食い違わないことを確認する
  (文書を書くだけでは SC-008 を満たさない・analyze U4)

## Phase 6: Polish & 記録

- [ ] T021 全体回帰: `training` / `probability` / `serving` / `betting` / `eval` の各スイート緑・
  ruff クリーン。**db/front/admin/features/api に差分が無いこと**を `git status` で確認。
  **attestation の共有消費者 2 経路(`ev_weight_run`=079・`segment_accuracy_run`=082)の
  回帰も確認**し、082 の現行世代での使用可否が改善したか・変わらないかを記録する(analyze G5)
- [ ] T022 [P] spec.md に実測結果を転記: 校正 verdict(三値 ×2 stage)・生成 digest・
  OOF 再生成の実所要時間・活性化検証の結果。**verdict が非採用でも「基盤が活性化した」という
  達成を明記**(FR-010)
- [ ] T023 [P] memory 更新: `calibration-leak-fixes-status.md` を「現行世代で活性化済み」に更新
  (または verdict に応じた正確な状態へ)+ MEMORY.md の行を更新
- [ ] T024 [P] CLAUDE.md の SPECKIT 区間を手動 Edit で完了ステータスに更新
  (agent-context スクリプトは実行禁止)
- [ ] T025 コミット(attestation 拡張・テスト・evidence・spec 転記)

## Dependencies

- Phase 1 → Phase 2 → Phase 3(US1)→ Phase 4(US2)→ Phase 5(US3)→ Phase 6
- **T002(golden 採取)は T005 より前でなければならない** — 実装後に採ると不変性の検証が空回りする
- Phase 3 内: T005 → T006 → T007(同一ファイルの逐次)→ T011a/T011c、
  T008/T009/T010/T011/T011b は別ファイルで並列
- Phase 4 内: **T012(中断点)→ T012a → T012b(コミット)→ T013 → T014 → T015**、T016 は並列可。
  **T012b は T014 より前でなければならない**(未コミットのコードで production manifest を
  刻印しない・憲法 V)
- Phase 5 内: T017 → T018 → T019(T019 は並列可)→ T020 → T020a

## Parallel Opportunities

- T004(Phase 2 のテスト)は T003 後に単独
- **T008 / T009 / T010 / T011**(US1 の 4 テストファイル)は T007 完了後に並列
- T016(旧 artifact 不変確認)は Phase 4 の他タスクと並列
- T022 / T023 / T024(記録系)は並列

## Implementation Strategy

- **MVP = Phase 1-3(US1)**: attestation が現行世代を表現でき、旧世代の digest が不変。
  ここまでで「基盤が今後の世代で使える」価値が独立に成立する(manifest 生成前でも)
- **T012 が資源の中断点**: **5〜8 時間**の再生成に進む前に必ず最終年 1 fold で ETA を確定する
  (外挿式は事前固定・ETA > 12 時間なら停止)。見積の希望的観測で数時間を溶かさない(095 の教訓)
- **verdict は測定**: two_gamma / stage 割引のどちらが非採用でも Phase 5 に進む。
  非採用なら恒等校正が出荷され、表示値は変わらないが「実行時 fit をやめて凍結値を読む」
  監査上の達成は残る(FR-010)
- **既定は最後まで opt-in**: 既定 ON への切替は本 feature のスコープ外(FR-015)
