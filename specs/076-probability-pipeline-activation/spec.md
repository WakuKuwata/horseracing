# Feature Specification: Probability Pipeline Activation & Parity

**Feature Branch**: `076-probability-pipeline-activation`

**Created**: 2026-07-17

**Status**: Draft

**Input**: 074(OOF-faithful Calibration Evidence)が **immutable calibration manifest**(two-gamma
+ stage λ を full 精度で凍結・content-addressed・fail-closed 検証)を作ったが、**production は一切結
線していない**(FR-015 evidence-only)。その結果、betting 推薦の two-gamma・serving 表示の stage
discount・066 dispersion の model_delta は今も **runtime で leaky loader**(`load_p_samples` →
`_latest_run_predictions` = 最新 full-history PredictionRun = 対象レース結果を見た **非OOS**)から
校正器を fit している。076 はこの 3 経路を **immutable manifest を読む**ように activate し、win バイ
ト不変・joint λ=1・allowed-change matrix を parity ゲートで担保する。**表示 top2/top3 と win 推薦の
値が新 run で変わる**のはこの feature。

## 概要 (Why)

073 の codex レビューで判明した校正リークは 3 段で是正される: 074(evidence + immutable
artifact)/ **076(production activation & parity=本 feature)**/ 077(global content-addressed
registry)。074 は「正しい校正の証拠と凍結 artifact」を作ったが、production は依然として:

1. **betting 推薦 two-gamma**: [`betting/cli.py::_fit_product_p_calibrator`](../../betting/src/horseracing_betting/cli.py) が
   `load_p_samples`(leaky)+ `fit_p_calibrator(method="two_gamma")` を毎回 runtime fit → win 選択・
   pseudo odds/ROI・Kelly stake に影響。
2. **serving 表示 stage-discount**: [`serving/pipeline.py::_fit_stage_discount`](../../serving/src/horseracing_serving/pipeline.py) が
   `fit_product_stage_discount(...)` を runtime fit → 永続化 / API の top2/top3(連対率・複勝率)にの
   み影響(win は不変)。
3. **066 dispersion model_delta two-gamma**: [`training/cli.py::_dispersion_pcal`](../../training/src/horseracing_training/cli.py) が
   `load_p_samples`(leaky)→ 表示専用 `H(校正済み p) − H(q)`。074 で診断併記済み(research D7)。

いずれも「凍結された OOF-faithful 校正」ではなく「その日の全履歴から都度 fit した非OOS 校正」を使
う=074 が是正した leak が production にそのまま残っている。076 はこの 3 経路を、074 の manifest
(`full_precision_params.two_gamma{gamma_lo,gamma_hi,pivot}` と `stage_lambdas{top2,top3}`)から
**読む**ように結線し、値の変わり方を allowed-change matrix で明示・parity ゲートで固定する。

**win はバイト不変**(two-gamma は推薦時のみ・stage discount は top2/top3 のみに作用し、
`race_predictions.win_prob` / API `horses[].win` には元々入っていない)。**API `?bet_type=` joint と
exotic betting joint は λ=1 の現行契約を維持**(betting は win p から joint を都度再計算し、永続化
top2/top3 を読まない)。

## 前提の重要な限界(fixture-first / real manifest 生成は follow-up)

074 の manifest **生成ステップは実際には未完成**(2026-07-17 確認、[[feature-074-manifest-unwired]]):

- [`training/calib_manifest.py::build_manifest`](../../training/src/horseracing_training/calib_manifest.py) に
  **production caller が無い**(schema/verifier だけ定義)。
- [`probability/oof_calibration.py::calibrate_oof`](../../probability/src/horseracing_probability/oof_calibration.py) は
  **`two_gamma_win` stage のみ** → **stage_lambdas(表示 λ)の OOF fit が存在しない**。
- 実 manifest を produce する CLI が無い。

**ユーザー決定(2026-07-17)**: 076 は **fixture manifest** の上で activation(loader / betting /
serving / dispersion / parity)を **full 実装**する。テストは `build_manifest` で組んだ fixture
manifest を使い、loader は **実 manifest を読む契約**を守る。**実 production manifest の生成**(stage-λ
OOF fit の新規実装 + `build_manifest` 結線 + full 2008–2026 OOF job 実行)は **本 spec のスコープ外=
follow-up** に分離する。

**正直な位置づけ(codex 指摘=最重要)**: fixture-first 076 は **「activation plumbing」であって
「leak closure」ではない**。fixture manifest は param マッピング・適用・永続化・fail 挙動を実証するが、
**校正の正しさ・採否・時間的妥当性・eval↔production 統計パリティは実証しない**。したがって:

- **実 leak 是正は、real(activation-eligible)manifest が存在してから**成立する。それまで 076 を「リー
  ク是正完了」と表現してはならない(plumbing のみ)。
- fixture が **production loader に誤って受理されない**よう、manifest に `artifact_scope=fixture|
  production` と `activation_eligible` を持たせ、**production profile の loader は fixture/未 eligible を
  拒否**する(FR-016)。
- したがって real evidence 修復(stage-λ OOF fit)+ deployment manifest + 必須 cutover は 076 の
  **blocking follow-up** とする。

076 は **fail-closed**: activation を要求したのに有効 manifest が無ければ activate しない。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 推薦 two-gamma を immutable manifest から読む (Priority: P1)

オペレータが「凍結された OOF-faithful 校正で推薦を作りたい」とき、betting 推薦の two-gamma p 校正器
を、runtime の leaky fit ではなく 074 manifest の `full_precision_params.two_gamma` から読む。

**Why this priority**: 3 経路のうち唯一 **production の意思決定(買い目・pseudo ROI・Kelly stake)に影
響する**経路。ここを activate することがリーク是正の本体で、単独で「正しい校正で推薦を作る」価値を成
す。

**Independent Test**: manifest path を渡した推薦生成が、manifest の γ_lo/γ_hi/pivot と**厳密に一致**す
る two-gamma を適用し(共有 loader が返す calibrator が evaluator の calibrator と param 一致)、manifest
を渡さない既定経路は現行 runtime fit とバイト同等であることを検証できる。改竄/世代不一致 manifest は
apply 前に fail-closed。

**Acceptance Scenarios**:

1. **Given** 有効な fixture manifest(base_model_version=active)、**When** `--calib-manifest <path>` で
   推薦生成する、**Then** two-gamma は manifest の full 精度 γ から作られ、`load_p_samples` を呼ばな
   い(leaky fit 経路に入らない)。
2. **Given** manifest を渡さない、**When** 推薦生成する、**Then** 現行の runtime fit 経路のままで出力は
   バイト同等(後方互換)。
3. **Given** base_model_version が active と異なる manifest、**When** activate を要求する、**Then** apply
   前に fail-closed(typed error)。runtime fit に**黙って fallback しない**。
4. **Given** manifest 由来で推薦を生成した、**When** `logic_version` を見る、**Then** manifest digest が
   token として記録され(`;calib=<digest12>` 等)、runtime-fit 由来と監査上区別できる。

---

### User Story 2 - serving 表示 stage-discount を manifest から読む (Priority: P1)

オペレータが予測を serving するとき、top2/top3(連対率・複勝率)の Benter stage discount λ を、runtime
fit ではなく manifest の `full_precision_params.stage_lambdas{top2,top3}` から読む。

**Why this priority**: 049 で serving 既定 ON の表示校正。win を触らず top2/top3 のみを変える境界が明確
で、US1 と独立に「表示 λ を凍結値にする」価値を成す。

**Independent Test**: manifest path を渡した serving が、`stage_lambdas{top2,top3}` を
`StageDiscount(lambda2=top2, lambda3=top3)` にマップして top2/top3 に適用し、**win 予測はバイト不変**、
eval と serving で同一 λ を使う(`display_topk_parity`)ことを検証できる。manifest 無し既定は現行
runtime fit とバイト同等。

**Acceptance Scenarios**:

1. **Given** 有効な manifest、**When** `--calib-manifest` で serving する、**Then** top2/top3 は manifest
   の λ で割引かれ、`race_predictions.win_prob` は manifest 有無で**バイト不変**(`model_internal_win_parity`)。
2. **Given** manifest の `stage_lambdas` を `{top2,top3}` にマップ、**When** `StageDiscount` を作る、
   **Then** `lambda2=top2 / lambda3=top3`(キー名の取り違え禁止=回帰テストで固定)。
3. **Given** manifest 由来で serving した、**When** `logic_version` を見る、**Then** manifest digest が
   token 記録され、既存の `;sdisc=...` と併記で由来が判別できる。
4. **Given** 同一 manifest・同一レースを 2 回 serving、**When** backfill 冪等を評価、**Then** 既存 run と
   同一 manifest なら skip、**異なる manifest なら別 run**(digest が冪等キーに入る=silent skip しない)。

---

### User Story 3 - 066 dispersion model_delta を manifest から読む (Priority: P2)

荒れ度読み(表示計器)の `model_delta = H(校正済み p) − H(q)` の two-gamma を、leaky loader ではなく
manifest から読み、074 の 3 つ目のリーク面を閉じる。

**Why this priority**: 表示専用・fail-open(欠けても field が消えるだけ)で production 意思決定に影響し
ないため P2。074 で「診断併記のみ」に確定した経路の最終是正。

**Independent Test**: dispersion pcal artifact を manifest の two-gamma から生成でき、API の
`model_delta` が manifest 由来の校正 p を使うこと、manifest 無しでは従来どおり fail-open で `model_delta`
を省略することを検証できる。

**Acceptance Scenarios**:

1. **Given** 有効な manifest、**When** dispersion を読む、**Then** API は **manifest を直接 consume**して
   two-gamma を得る(**別の派生 pcal JSON を生成しない**=codex 指摘: US3 が「直接 manifest 読込」と
   「弱い派生 JSON 生成」の 2 設計を混在させていた→**直接 consume に一本化**)。非OOS の disclosure は
   「manifest 由来=OOF」に更新。
2. **Given** manifest 無し、**When** dispersion を読む、**Then** 従来どおり fail-open で `model_delta` を
   省略。dispersion band は q のみの関数なので不変。
3. **Given** API が candidate model の run を選択、**When** `model_delta` を計算、**Then** 比較対象は
   **選択された run の model**(active model ではない・FR-020)。
4. **Given** manifest が存在するが無効(改竄/世代不一致/scope 不適/時間違反)、**When** dispersion を読む、
   **Then** API は loader 例外を捕捉して **`model_delta` を省略し 200 を維持**(read-only 表示経路は
   fail-**open**=予測エンドポイントを壊さない)。無効は log に記録するが betting/serving の
   `manifest-required` fail-closed とは非対称(表示計器のため・FR-003)。

---

### User Story 4 - fail-closed loader + allowed-change matrix を parity で固定する (Priority: P1)

3 経路が共有する **manifest 読み込み loader**(`probability/calib_activation.py::load_calibration`)を作
り、074 の verifier で fail-closed(改竄/partial/未知 schema/世代不一致/digest 不一致 → apply 前に拒
否)にし、**allowed-change matrix** を parity テスト群として固定する。

**Why this priority**: US1–US3 の共有基盤かつ「値が変わってよい/絶対に変えてはいけない」の境界を機械
的に守る安全装置。ここが無いと activation は silent regression を招く。

**Independent Test**: loader が有効 manifest から two-gamma / stage_lambdas を返し、無効 manifest は
`load_calibration` が例外(fail-closed)で、活性化しても win がバイト不変・joint が λ=1 のままである
ことを、allowed-change matrix の各行に対応する parity テストで検証できる。

**Acceptance Scenarios**:

1. **Given** 改竄/partial/未知 schema/base_model_version 不一致/digest 不一致 の manifest、**When**
   `load_calibration` する、**Then** apply 前に typed error で拒否(runtime fit に fallback しない)。
2. **Given** activation ON、**When** win 予測を比較、**Then** manifest 有無で `win_prob` / API `win` が
   **byte-identical**(`model_internal_win_parity`)。
3. **Given** activation ON、**When** API `?bet_type=` joint と exotic betting EV を比較、**Then** どちら
   も λ=1 の現行契約のまま(`api_joint_legacy_parity` / `betting_joint_identity_stage_parity`)。
4. **Given** activation ON、**When** eval の two-gamma / stage λ と production 純関数を比較、**Then**
   同一 manifest で一致(`betting_two_gamma_parity` / `display_topk_parity`)。

---

### Edge Cases

- **manifest 欠如**: activation を要求しない(既定)→ 現行 runtime fit のまま(後方互換)。activation を
  要求した(path/flag 指定)のに manifest が無い/読めない → **fail-closed**(黙って runtime fit しない)。
- **世代不一致**: active model が lgbm-063 でない、または manifest の `base_model_version` が active と
  異なる → fail-closed(`verify_manifest` の generation check)。077 の registry 化前は active=lgbm-063
  前提。
- **manifest 更新で値が変わる**: 既存 run/recommendation 行は**書き換えない**(append-only)。新 manifest
  は**新規 run**として値を変える(digest が冪等キー/`logic_version` に入るため silent skip しない)。
- **stage_lambdas のキー**: `{top2,top3}`(`lambda2/lambda3`/`l2/l3` ではない)→ `StageDiscount` への
  マップを回帰テストで固定([[feature-074-manifest-unwired]])。
- **identity fallback**: manifest 内の校正が identity(under-sampled 由来)でも明示 artifact として扱い、
  activation は成立(no-op 校正を適用)。
- **同日レース/backfill 冪等**: serving backfill(044)と betting recommend(043/045)の冪等キーに
  **manifest digest** を含める。含めないと別 manifest 再実行が「既存 run あり」で skip=leak 未是正のま
  ま古い値が残る。
- **dispersion の表示影響(UX)**: dispersion は `fit_through` 以前のレースで時間検証に落ち fail-open する
  ため、recent manifest が active だと**過去(閲覧対象の多く)のレースでは `model_delta` が出ず、q のみの
  band のみ**が表示される。意図的だが分かりにくいので、`fit_through` 後のレースでのみ model_delta が出る
  ことを明示する。
- **historical backfill × 時間検証**: `manifest-required` で `fit_through` 以前のレンジを backfill する
  と、対象日 `<= fit_through` の全レースが FR-021 で fail-closed する(それらは校正 fit 窓内=適用すれば
  非OOS になるため**意図的に拒否**)。manifest-required backfill は `fit_through` より後(prospective/
  直近)のレンジに適用する運用。過去レンジへの vintage(複数世代)manifest 選択は**本 spec 外**(拒否が
  正しい挙動)。backfill タスクはこの制約を明示する。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: システムは betting 推薦の two-gamma p 校正器を、指定された manifest の
  `full_precision_params.two_gamma`(full 精度 γ_lo/γ_hi/pivot)から**読めなければならない**。指定時は
  `load_p_samples` による runtime fit 経路に入ってはならない。
- **FR-002**: システムは serving の表示 stage-discount λ を、manifest の
  `full_precision_params.stage_lambdas{top2,top3}` から読み、`StageDiscount(lambda2=top2,
  lambda3=top3)` にマップして top2/top3 にのみ適用しなければならない。
- **FR-003**: システムは 066 dispersion の two-gamma を manifest から読めなければならない(表示専用)。
  **dispersion は read-only 表示経路のため fail-open を維持**: manifest 欠如**および present-but-invalid
  (改竄/世代/scope/時間)いずれでも loader 例外を捕捉して `model_delta` を省略し 200 を返す**(予測
  エンドポイントを壊さない)。betting/serving の `manifest-required` fail-closed とは意図的に非対称。
- **FR-004**: activation は **明示 mode**(boolean の opt-in ではなく)でなければならない:
  `legacy-runtime`(現行 runtime fit=移行互換・既定)/ `manifest-required`(manifest 必須・
  無効/欠如は致命的で **runtime fit に fallback しない**)。`legacy-runtime` は現行挙動と**バイト同等**。
  real manifest が存在してからは production は `manifest-required` を要求する(それまでは plumbing)。
- **FR-005**: activation 指定時に manifest が欠如/改竄/partial/未知 schema/世代不一致/digest 不一致な
  ら、システムは apply 前に **fail-closed**(typed error)でなければならず、runtime fit に**黙って
  fallback してはならない**。検証は 074 の `verify_manifest` を再利用する。
- **FR-006**: 3 経路は単一の共有 loader(`probability/calib_activation.py::load_calibration`)を経由し
  なければならない(two-gamma / stage_lambdas の解釈を 1 箇所に集約=ドリフト防止)。
- **FR-007**: `race_predictions.win_prob` / API `horses[].win` は manifest 有無・activation の有無にかか
  わらず **byte-identical** でなければならない(`model_internal_win_parity`)。
- **FR-008**: API `?bet_type=` joint と exotic betting joint は **λ=1 の現行契約**を維持しなければならな
  い(betting は win p から joint を再計算し、永続化 top2/top3 を読まない)。
- **FR-009**: manifest 由来で生成した推薦 / serving 出力は、その **manifest digest を `logic_version`
  に token として記録**しなければならない(監査 + 冪等キー、既存 `;sdisc=`/`;pcal=`/`;mkt=`/`;oddscap=`
  と同じ規律)。
- **FR-010**: serving backfill(044)と betting recommend(043/045)の冪等キーは manifest digest を含
  め、**異なる manifest での再実行は別 run** として値を更新できなければならない(silent skip 禁止)。既
  存 run/recommendation 行は書き換えない(append-only)。
- **FR-011**: allowed-change matrix は parity テスト群として固定しなければならない:
  `model_internal_win_parity`(byte 不変)/ `display_topk_parity`(eval==serving・値更新は許可)/
  `betting_two_gamma_parity`(evaluator と推薦純関数が一致)/ `betting_joint_identity_stage_parity`
  (betting joint は λ=1 構造)/ `api_joint_legacy_parity`(API joint も λ=1)。**加えて US3 側で
  `model_delta` 変更可・`band`/raw q 不変を固定**(data-model §5 の 7 行が正本=この 5 つ + US3 の 2 つ)。
- **FR-012**: 校正の派生値(γ・λ・digest)を**モデルの特徴量に還流してはならない**(リーク境界 II、
  token grep + behavioral leak-guard)。
- **FR-013**: スキーマ変更ゼロ・migration なし。manifest は 074 の content-addressed disk artifact のま
  ま(prediction_runs に入れない=API/serving/model-selector を汚染しない)。
- **FR-014**: 本 spec は **実 production manifest を生成してはならない**(stage-λ OOF fit の実装・
  `build_manifest` の production 結線・full OOF job は follow-up)。テストは fixture manifest を使い、
  loader は実 manifest を読む契約を守る。
- **FR-015**: activation を既定 ON に昇格してはならない(既定 mode=`legacy-runtime`=FR-004 と対。FR-004
  は mode 定義・FR-015 は昇格禁止で意図的に別義務)。既定 ON への昇格は実 manifest 生成 + full parity
  実証後に別途判断する。
- **FR-016**: manifest は `artifact_scope=fixture|production` と `activation_eligible` を持ち、
  **production loader profile は fixture/未 eligible を拒否**しなければならない(fixture が production 経路
  に誤って受理される false-parity trap を防ぐ)。
- **FR-017**: activation は **全 entry path** に結線しなければならない。betting/serving の CLI だけでな
  く、[`live/orchestrate.py`](../../live/src/horseracing_live/orchestrate.py)(range refresh・prospective
  collect)、[`ops/runner.py`](../../ops/src/horseracing_ops/runner.py)(subprocess 起動)を含む。一部
  経路だけ結線すると leak が残る。
- **FR-018**: manifest path は **絶対パス/immutable artifact URI** で解決しなければならない(パッケージ
  ごとに cwd が異なり相対パスが壊れる=[[weights-uri-relative-path-ops-bug]] の前例)。解決規則は単一。
  manifest は **1 invocation につき 1 回ロード**し、全レースが同一 digest を使う。
- **FR-019**: 世代束縛は **model_version 名(lgbm-063)だけでは不十分**。`save_model_version` が同名を上書
  きしうるため、**strong binding** は resolved model dir + `manifest.code_sha` から 074 の
  `attestation_from_model_dir` で attestation を再計算し `manifest.attestation_digest` と一致比較する
  (不一致=fail-closed)。074 の digest は full recipe payload の `stable_hash` であり 4-key checksum
  mapping では再構成不能(C1)ため、実関数で再計算する。
  **ただし再計算は training 固有(ModelRecipe 再構築)であり、loader は probability に置かれるため
  training を import できない(D11: training→probability の既存依存 + uv workspace で probability の env
  に training が無い)。よって strong binding は loader が受け取る `attestation_verifier` として注入する**
  (`training/calib_binding.py::model_dir_attestation_verifier`)。
  **076(fixture-first)の実効レベル**: fixture manifest の `attestation_digest` は合成値で実 lgbm-063
  artifact とは原理的に一致しないため、**076 の全経路は `attestation_verifier=None` で動作し、束縛は
  「base_model_version 名一致 + content-addressed manifest digest(改竄検知)+ scope + 時間」に留まる**。
  strong binding は **実 manifest 生成(follow-up)で verifier を注入した時点**で有効になり、全経路への
  強制と `save_model_version` 上書き廃止(loader checksum enforcement 全般)は **077**。
  → この段階差は既定 OFF(opt-in)・read-only 運用を前提とした**期限付き waiver**であり、production 既定
  ON の根拠にはならない(codex 条件付き採用)。
- **FR-020**: 比較・照合対象は経路ごとに正しい run でなければならない: betting は実際の
  `PredictionRun.model_version`、serving は解決済み `ServingModel`、**dispersion は API が選択した run の
  model**(057 model-switching で candidate model を選べるため、単に active model と比較してはならない)。
- **FR-021**: manifest 由来の校正は **時間的妥当性**を検証しなければならない(`target_date <=
  manifest.fit_through` の manifest は拒否)。この結果、`manifest-required` backfill は `fit_through`
  より後のレンジにのみ適用でき、過去レンジは fail-closed する(意図的=非OOS 回避)。vintage(複数世代)
  manifest 選択は**本 spec 外**(拒否が正しい挙動)。
- **FR-022**: fail-closed は **backfill の per-day/per-race 例外隔離より前**に置かなければならない
  (無効 manifest がループ内 error count に飲まれて処理継続=silent leak を防ぐ)。無効時は **0 行書込・
  非 0 CLI 終了コード**。冪等は **advisory lock + 厳密 logic-token 一致**で担保する。

### Key Entities

- **calibration manifest**(074 既存): `schema_version` / `artifact_kind="oof_calibration"` /
  `base_model_version="lgbm-063"` / `attestation_digest` / `bundle_digest` / `evaluation` /
  `checksums` / `probability_stage_order` / `full_precision_params.{two_gamma{gamma_lo,gamma_hi,
  pivot}, stage_lambdas{top2,top3}}` / `code_sha` / `seed` / `num_threads` / `manifest_digest`。
  **076 で追加(v2 additive・top-level)**: `artifact_scope{fixture|production}` / `activation_eligible`
  / `fit_through`(date・時間検証)。詳細は data-model §1。
- **load_calibration**(新・共有): manifest path → 検証済みの `{two_gamma: PCalibrator params,
  stage_discount: StageDiscount}`。fail-closed。3 経路が唯一経由する解釈点。
- **activation logic_version token**: `;calib=<manifest_digest[:12]>` 等。監査 + 冪等キー。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `model_internal_win_parity` — activation の有無で `race_predictions.win_prob` / API
  `win` の mismatch が **0**(実 DB 1 レース spot-check 16 頭を含む)。
- **SC-002**: `betting_two_gamma_parity` — 同一 manifest で、推薦純関数が適用する two-gamma と
  evaluator が使う two-gamma の γ params が**厳密一致**。
- **SC-003**: `display_topk_parity` — 同一 manifest で eval と serving の top2/top3 λ が一致(値の更新
  は許可、eval↔serving の不一致は 0)。
- **SC-004a**: `api_joint_legacy_parity` — activation ON でも API `?bet_type=` joint が **λ=1** のまま
  activation OFF と**バイト一致**(API joint は不変の `win_prob` から再計算するため p 自体が変わらない)。
- **SC-004b**: `betting_joint_identity_stage_parity` — exotic betting joint は **λ=1 の構造**を維持
  (marginals == λ=1 の harville_topk)。ただし two-gamma が betting p を変える(win/exotic 両群に適用)
  ため exotic joint は activation OFF と**バイト一致ではない**(λ=1 構造は保つが p は変わる)。
- **SC-005**: fail-closed — 改竄/partial/未知 schema/世代不一致/digest 不一致 manifest が **100%**
  apply 前に拒否され、runtime fit に fallback しない。
- **SC-006**: 冪等 — 同一 manifest 再実行は skip、異なる manifest は別 run で値更新(silent skip **0**)。
- **SC-007**: 後方互換 — manifest 未指定の推薦 / serving / dispersion 出力が現行と**バイト同等**。
- **SC-008**: production 実 manifest 生成の変更 **0**(本 spec は fixture のみ、follow-up に分離)。
- **SC-009**: leak-guard — `manifest-required` 経路が `load_p_samples` / `_latest_run_predictions` / 任意
  の fit 関数 / `RaceResult` クエリを**呼ばない**(behavioral + import-graph)。
- **SC-010**: fixture 拒否 — `artifact_scope=fixture`/未 eligible の manifest が production loader profile
  で **100%** 拒否される。
- **SC-011**: 全 entry path 一致 — CLI / live serve / range refresh / prospective collect / ops subprocess
  が同一 immutable digest を解決する(一部経路の未結線 leak が **0**)。
- **SC-012**: 時間的妥当性 — `target_date <= manifest.fit_through` の manifest が **100%** 拒否される。

## Assumptions

- 現 active は **lgbm-063**(features-017、073/074 の parity oracle)。077 の registry 化前は active=
  lgbm-063 前提で、manifest の `base_model_version` 一致を fail-closed の世代境界とする。
- 074 の `build_manifest` / `verify_manifest`(schema/generation/digest/checksum を fail-closed 検証)
  はそのまま再利用する。076 は生成器を作らず **loader と結線**に集中する。
- two-gamma は **win_prob に入っておらず**推薦生成時のみ、stage discount は **win を触らず** top2/top3
  のみ、という 074 codex レビューの契約([docs/plan/codex-074-review.md](../../docs/plan/codex-074-review.md))
  を前提とする。
- schema-zero を維持(content-addressed disk artifact + 既存 JSONB、migration なし)。
- 校正 activation は既定 `legacy-runtime` mode(現行挙動保存)。opt-in 前例(046 p-calibrator / 060
  market offset)に倣うが、codex 指摘により boolean でなく明示 mode とする。
- 実 manifest 生成(stage-λ OOF fit + `build_manifest` 結線 + full 2008–2026 OOF job)は follow-up。

## 依存・後続 feature・スコープ外

**依存**: 074(manifest schema/builder/verifier・OOF bundle・attestation)。

**スコープ外(follow-up)**:

- **実 production manifest の生成(= 実 leak closure の前提・BLOCKING follow-up)**: `stage_lambdas` の
  OOF fit 新規実装 + `build_manifest` の production caller 結線 + full 2008–2026 OOF job 実行(十数時間級
  long job)+ deployment manifest + 必須 cutover。これが無い限り 076 は plumbing に留まる。
- **artifact authenticity**: checksum は改竄検知であって authenticity ではない。artifact の read-only 化
  /署名は脅威範囲に含めるかを plan で判断(077 の registry 化と併せて検討)。
- **betting の opt-in `--stage-discount`**: betting が明示 `--stage-discount` を渡した場合の exotic λ は
  今も runtime fit(`_fit_product_stage_discount`, leaky)であり、076 は **serving の表示 stage λ のみ**を
  manifest 化する。既定は OFF(λ=1)なので既定でリークは無い。この opt-in 経路の manifest 化は本 spec 外。
- **`dispersion-pcal` の派生 JSON 生成経路**: US3 で API が manifest 直読になった後、この CLI は
  **verify/inspect 用途に縮退**(派生 pcal JSON は read path から外す)。artifact を新規生成する運用は
  廃止(D10)。
- **077 Global Content-addressed Model Registry**: `save_model_version` 上書き廃止・atomic publish・
  loader checksum enforcement・active model の registry 化(active≠lgbm-063 への一般化)。
- **activation の既定 ON 昇格**: 実 manifest + full parity 実証後に別途判断。
- スキーマ変更・既存 run/recommendation 行の書き換え・ROI 台帳・new odds source。

**憲法**: II(校正リーク是正・OOF strict-past・派生値を特徴に還流しない)/ III(評価先行・parity ゲー
ト・事前登録の allowed-change matrix)/ IV(win バイト不変・joint Σ 整合・順位保存)/ V(content-
addressed immutable artifact・logic_version 監査・append-only 冪等)/ VI(契約先行・スキーマ不変・
API `?bet_type=` joint 契約維持)。

**codex レビュー**(gpt-5.6-sol, xhigh, 実コード読解, [[codex-env-recovery]]): full 総括を取得・**全指摘を
採用して本 spec に反映済み**。converge した blocker(実質的な反対意見なし):

1. **opt-in→明示 mode**(`legacy-runtime`/`manifest-required`)。fixture-first 076 は **plumbing であって
   leak closure ではない**=real manifest 前は「リーク是正完了」と称さない → FR-004/概要 に反映。
2. **fixture が production loader に受理されない**よう `artifact_scope`/`activation_eligible` → FR-016。
3. **全 entry path**(live orchestrate・range refresh・prospective・ops subprocess)を結線しないと leak
   が残る → FR-017。
4. **絶対パス解決 + load-once**([[weights-uri-relative-path-ops-bug]] 前例)→ FR-018。
5. **世代束縛は model_version 名だけでは不十分**(`save_model_version` 上書き)→ **model dir から
   `attestation_from_model_dir` で attestation を再計算し `attestation_digest` を照合**(4-key checksum
   mapping では 074 の full-payload digest を再構成できない=C1)→ FR-019。
6. **candidate model 選択**(057)→ dispersion/betting/serving は選択 run の model と比較 → FR-020。
7. **時間的妥当性**(`target_date <= fit_through` 拒否)→ FR-021。
8. **fail-closed は backfill 例外隔離より前**(無効 manifest がループ error count に飲まれる)+ advisory
   lock + 厳密 logic-token 一致 → FR-022。
9. **manifest 世代一致**(→ FR-005/`verify_manifest`)+ **digest を logic_version/冪等キーに token 化**
   (→ FR-009/FR-010)。
10. **checksum は改竄検知であって authenticity ではない** → artifact を read-only にするか署名(脅威範囲
    に含めるなら)。plan で判断。

**codex の scope 勧告**: fixture-first 076 を残すなら「plumbing・既定 off・production-eligible でない」と
**明示**し、real evidence 修復 + stage fitting + deployment manifest + 必須 cutover を **blocking
follow-up** にする。あるいはその作業を 076 に含めて初めて「production activation」と呼ぶ。**本 spec は
前者(plumbing 明示 + follow-up)を採用**。
