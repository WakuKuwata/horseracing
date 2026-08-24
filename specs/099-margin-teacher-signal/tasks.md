# Tasks: margin-aware 教師信号(PL top-k ステージ損失の着差減衰)

**Input**: Design documents from `/specs/099-margin-teacher-signal/`

**Prerequisites**: plan.md・spec.md・research.md(D1-D9)・data-model.md(INV-MT1..9)・
contracts/(teacher-signal / adoption-gate)・gate-config.json(凍結済み hash `d8c479dea834…`)

**Tests**: 含む(spec の FR/SC がビット一致・leak-guard・回帰テストを明示要求)。

**Organization**: US1=production 実装(P1)→ US2=事前登録ゲート(P2)→ US3=verdict 分岐
(P3)。US2 は US1 完了が前提。US3 は US2 の verdict が前提。

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

- [ ] T001 前提確認(実 DB): materialized parquet が最新でコード hash 一致
      (`assert_manifest_compatible` が通る)・active が lgbm-094-cap900・spike evidence
      3 本(`evidence/margin-teacher-spike-*.json`)が参照可能。ずれていれば
      `features materialize` を先に再実行

---

## Phase 2: Foundational(全 US をブロックする土台)

**Purpose**: hash back-compat の一本化と objective 拡張。US1 のレシピ変更より**前**に
入れないと、フィールド追加の瞬間に arm E 系の既存 hash が全滅する(codex P0-1)。

- [ ] T002 変更**前**の現行 hash 実測値を採取して定数化: RecipeFactory 系
      (`ModelRecipe().recipe_hash()` ほか代表レシピ数種)と CalibSplitFactory 系
      (`CalibSplitFactory(recipe, method=...).recipe_hash` 同種)の実測値を
      training/tests/unit/test_margin_teacher_recipe.py に**先に**スナップショット定数として
      書く(このテストは T003/T007 の変更後も緑であり続けることが back-compat の証明)
- [ ] T003 hash 省略規則の一本化 in training/src/horseracing_training/recipe.py:
      「default 値のとき hash payload から省略するフィールド」の単一定義(集合+適用関数)を
      ModelRecipe に置く。**注意**: 現行の 2 系は正準化が既に異なる(recipe_hash() は
      split_unit/ev_weight/weight_mask/params を省略、CalibSplitFactory は meta() 全体)。
      統一するのは**省略規則の置き場**であって payload 形ではない — RecipeFactory 系は現行
      canonical 形を、CalibSplitFactory 系は現行 meta() 全体形を維持し、**新規フィールドの
      default 省略だけを両系に共有適用**する(training/src/horseracing_training/calib_split.py
      の recipe_meta/recipe_hash をこの共有関数経由に変更)。T002 のスナップショットが緑の
      ままであることが受入条件
- [ ] T004 pl_topk_objective + _pl_topk_objective_loop に `stage_scales=None` を追加 in
      training/src/horseracing_training/cond_logit.py: spike
      (scripts/margin_teacher_spike.py::margin_pl_topk_objective)の移植。**既存の
      `offsets=None` を保存**(spike 版は offsets を持たない=素朴な写しは market-offset
      対応を失う・codex P1-4)。発火/中立化/break 規則は不変(INV-MT4)
- [ ] T005 [P] objective 単体テスト in training/tests/unit/test_margin_teacher_objective.py:
      ①None=現行と勾配・ヘシアン**ビット一致**を **offsets 有無 × sample weight 有無の
      4 象限**で(INV-MT1) ②一様 0.5=厳密半分 ③ステージ限定変調 ④dead-heat group が変調下
      でも勾配 0(INV-MT4) ⑤loop↔vectorized 等価テストに stage_scales 有りケース追加

**Checkpoint**: 既存スイート無改修緑+T002 スナップショット緑 = 土台の完全 back-compat

---

## Phase 3: US1 — production の margin-aware 教師信号(P1)

**Goal**: OFF が 1 バイトも変えない production 実装。**Independent Test**: 単体+実 DB
統合のみで完結(ゲート不要)。

- [ ] T006 [US1] margin スケール算出+aux 列 in training/src/horseracing_training/dataset.py:
      SQL は「LEAD を**全完走馬**(時計 NULL の finished 行も window に含める=次馬の時計
      欠損は差分 NULL→中立 1.0)の CTE で計算 → 外側で着順 1..3 に制限」(INV-MT7/MT8・
      codex SQL 指摘)。`MARGIN_SCALE_S2`/`MARGIN_SCALE_S3` 定数と aux 列 2 本(レース内
      定数・float64)を MKT_ODDS と同じ位置で frame に付与。g(m)=clip(m/0.2, 0.25, 1.0)
      の定数はモジュール定数(M0/GMIN)として凍結
- [ ] T007 [US1] ModelRecipe.margin_teacher 追加 in training/src/horseracing_training/recipe.py:
      `str | None = None`・受理 None/"v1" のみ(`__post_init__` ValueError)・T003 の省略
      規則に登録(None=両系 hash 不変)。calib_split.py の `_RECIPE_FIELD_DISPOSITION` に
      `"forward"` を追加し、**`_make_base()` と RecipeFactory.fit の両方で
      `margin_teacher=recipe.margin_teacher` を明示的に渡す**(codex P0-2: disposition は
      会計であって配線ではない)
- [ ] T008 [P] [US1] レシピ単体テスト in training/tests/unit/test_margin_teacher_recipe.py
      (T002 のファイルに追記。[P] は T011/T012 とであり **T002 とは直列**): None で両系 hash 不変(スナップショット継続緑)・"v1" で
      distinct・不正値 ValueError・disposition 未登録状態を模した ArmNotServable の発火確認
- [ ] T009 [US1] WinModel 配線 in training/src/horseracing_training/win_model.py:
      `fit(..., margin_scales=None)`((n_rows,2))。`_fit_softmax` で offsets/weights と同じ
      argsort に追随させ、**group 内 min==max・値域 [0.25,1]・有限を `ValueError` で検証して
      から**先頭行で (n_groups,3)(s1=1.0)を構成し objective へ(codex P1-5)。predict は
      無変更(INV-MT6)。**検証(ValueError・値域・不均一)の単体テストは保全対象の
      test_margin_teacher_objective.py 側に置く**(REJECT 時も win_model の引数は保全される
      ため、その検証が無テスト化しないように = analyze 3 周目 I1)
- [ ] T010 [US1] predictor 配線+監査統計 in training/src/horseracing_training/predictor.py:
      コンストラクタ `margin_teacher: str | None = None`("v1" 以外 fail-closed)。ON のとき
      model_df から aux 列 2 本を取り出し WinModel.fit へ。fit_info["margin_teacher"] に
      **実際の booster fit 行ベース**の統計(variant/m0/gmin・s2/s3 ごとの source_available/
      scale_lt1/fire_and_lt1/fireable_mean・neutral_missing_time/neutral_absent_race 分計=
      data-model の形)。OFF のとき key 不在(INV-MT9)
- [ ] T011 [P] [US1] 整列回帰テスト in training/tests/unit/test_margin_teacher_alignment.py
      (codex 最小テスト 2): レースを交互配置し、レースごとに異なる s2/s3・rank・offset・
      weight を与え、weight mask 適用+calib split 後の sorted group スケールが期待レースと
      一致することを objective 構築引数の spy で確認(**このファイルは配線スコープのみ** —
      ValueError/値域検証のテストは T009 のとおり保全対象の _objective.py 側)
- [ ] T012 [P] [US1] leak-guard テスト in training/tests/unit/test_margin_teacher_leak.py:
      aux 列 2 本が `model_input_features()` に不在・`feature_hash` 不変・feature_snapshots
      への書き込み経路(serving persist の列選択)に現れない(INV-MT2)
- [ ] T013 [US1] 統合テスト(実 DB)in
      training/tests/integration/test_margin_teacher_db.py: ①実データ形状で s2・s3 の
      fireable 平均が**ともに < 0.9**(凍結閾値 = analyze 3 周目 A1。0.66-0.69 帯は参考値。
      run 1 バグ形=s3≈1.0 なら赤・INV-MT7/SC-002) ②ミニレース fixture で s3 手計算一致(4 完走で 3→4 着差 0.1s →
      s3=0.5)・3 完走で s3=1.0・直後馬の時計 NULL→1.0(codex 最小テスト 1) ③ON の fit で
      fit_info 統計が存在し effective(scale_lt1)>0 ④OFF の fit_info/metadata がバイト不変
- [ ] T014 [US1] artifacts 透過 in training/src/horseracing_training/artifacts.py:
      metadata.json / metrics_summary.training に fit_info の margin_teacher ブロックを透過
      (OFF は key 不在=既存 metadata バイト不変・INV-MT9)。既存テストの金型に追記
- [ ] T015 [US1] E2E 確認(実 DB): 既存 active(lgbm-094-cap900)の予測がマージ前後で
      バイト一致(SC-004)+ training/serving/eval/features 全スイート**無改修**緑(SC-001)

**Checkpoint**: US1 単独で出荷可能(OFF 既定なので production 挙動は完全不変)

---

## Phase 4: US2 — 事前登録採用ゲート(P2)

**Goal**: 凍結 gate-config での confirmatory 実行と三値 verdict。**Independent Test**:
smoke(事前登録外の窓)で配線確認 → 本実行。

- [ ] T015a [US2] CalibSplitFactory への snapshot pin 結線 in
      training/src/horseracing_training/calib_split.py + cli.py: `_factory_from_spec` の
      `mat_kwargs` は現在 RecipeFactory にしか渡らず、OOF 分岐の shared matrix は live DB の
      `_ensure_data()` で構築される — `--use-materialized --pin-snapshot` が **OOF アームでは
      黙って無効化**され、十数時間の run 中に DB が動くと凍結 estimand が毀損する
      (analyze 2 周目 C1・091 D16 の実測 4.8% 書換)。CalibSplitFactory に
      use_materialized/materialized_path/pin_snapshot を追加し shared 構築へ配線。
      受入 = pin 有無で shared matrix が同一スナップショットから構築されるテスト+
      「pin 指定が OOF 分岐に届かない」形の回帰テスト
- [ ] T016 [US2] CLI 配線 in training/src/horseracing_training/cli.py:
      `_recipe_from_spec` に `mteach=v1` セグメント(parse→serialize round-trip で保持)。
      **`_factory_from_spec` の OOF 分岐の欠落フィールド carriage を修正**: 同分岐は現在
      `ModelRecipe(objective, calibration="none", drop_features=...)` を再構成しており
      `wmask=`/`params`(rounds)を**今も捨てている**(`drop=` を捨てた過去バグと同じ穴が
      現存 = analyze C1)。margin_teacher と併せて weight_mask_rate/seed・params も base
      recipe から運ぶ。テストで「spec 文字列 → OOF factory の実効 recipe」の全フィールド
      保持を固定
- [ ] T016a [US2] 凍結アーム構成の注入と照合 in training/src/horseracing_training/cli.py:
      paired-eval の --confirmatory 時、gate-config `arms` の n_estimators(900)/
      weight_mask_rate・seed(0.5/20260810)/n_oof_blocks(8)/seed(42)を両アームの
      factory 構築に注入し、**採点開始前に実効 factory recipe_meta が `arms` 凍結値と一致
      しなければ fail-closed**(analyze C1: 現状のまま実行すると両アームともマスク無し・
      既定 rounds・n_oof_blocks=3 で凍結した estimand と別物を測り、両アームが等しくズレる
      ので差分検査は素通りする)。**同じ注入機構は smoke でも使う**: 非 confirmatory +
      `--gate-config` 指定時は `smoke` ブロック(n_estimators=50)を注入(smoke の低容量を
      運用者の手作業に頼ると適用手段が無い — paired-eval に rounds フラグは存在しない)。
      単体テスト付き(凍結値と食い違う config / 注入漏れがエラーになること)
- [ ] T017 [US2] paired-eval 実行前検査 in training/src/horseracing_training/cli.py:
      ①candidate と active の recipe_hash 同一 → 実行前エラー ②--confirmatory 時、
      **実際に fit される recipe(`factory.recipe_meta`)**の canonical payload 差分が
      **厳密に margin_teacher 1 フィールドのみ**でなければエラー(codex P0-3。比較対象を
      spec 文字列の parse 結果でなく factory の実効 recipe にするのは、再構成境界の黙殺
      — T016 の carriage 漏れの型 — を検出するため = analyze H2)。単体テスト付き
      (綴りミス `mteach=v2`/欠落が捕まることを表明)
- [ ] T017a [US2] paired-eval 実行**後**の構造 assert in
      training/src/horseracing_training/cli.py: paired_eval 返却後・verdict 書き出し前に
      ①`diffs_by_day` の非ゼロ差レース数 ≥ gate-config `arm_identity.
      require_nonzero_diff_races`(全ゼロ = アーム同一の故障 → verdict を書かず異常終了)
      ②candidate 側 factory の保持する predictor `fit_info_["margin_teacher"]` で
      s2/s3 の scale_lt1 > 0 かつ fireable_mean < 1.0(変調不発 = 黙殺の故障 → 同上)。
      これで FR-010 の実行時 assert が実装され、`require_nonzero_diff_races` キーが
      実際に消費される(非消費 config キーの罠 = analyze H1)。**注記**: `_pred` は
      CalibSplitFactory が再 fit する単一インスタンスのため、この検査は**最終 fold の統計**に
      対するもの(存在+変調の検査としては十分。private 依存を避けるなら公開アクセサを
      受入条件に含める = analyze M3)。単体テスト付き
- [ ] T018 [P] [US2] gate-config 凍結整合テスト in
      training/tests/unit/test_margin_teacher_gate_config.py: gate-config.json の
      canonical hash を `gate_config_hash()` で**再計算**し contracts/adoption-gate.md 記載の
      完全 64 桁凍結値と一致・
      `margin_teacher_candidate="v1"` が実装の凍結定数(M0=0.2 / GMIN=0.25・dataset.py の
      モジュール定数を直接参照)へ写像されること・eval_window/min_eval_days/seed_noise が
      v4 必須形(gate-config は M0/GMIN キーを意図的に持たない = FR-002 の非露出。
      analyze M1 の文言修正)
- [ ] T019 [US2] smoke 実行(quickstart §3): 事前登録外の窓(2016-2017)・T016a の注入で
      `smoke.n_estimators=50`・効果数値は読まない(redact は運用規律)。確認 = 完走・
      非ゼロ差レース ≥1(T017a 作動)・candidate fit_info の margin_teacher 統計・
      アーム同一 spec の対照実行が実行前エラー。**JSON 出力は `--json`**(標準経路の
      フラグ。`--out` は 091 regime 経路専用で標準経路では未消費 = analyze 2 周目 H1)。
      結果は out/(非追跡)
- [ ] T020 [US2] 本実行(quickstart §4・nohup・十数時間): `--confirmatory
      --gate-config-hash <完全 64 桁値> --json specs/099-margin-teacher-signal/verdict.json
      --use-materialized --materialized-path ... --pin-snapshot` で 2019-01-01..2026-08-23
      (`--json` が標準経路の出力・`--materialized-path` 無しは fail-closed 即終了 =
      analyze 2 周目 H1/H2。hash 照合は完全値・prefix 比較禁止 = M4)
- [ ] T021 [US2] 測定結果の転記: pooled 点推定・標本 CI・総 CI・fold 別・ガード別・subgroup
      状態を spec.md 末尾「実測結果」節に転記(SC-005)。**個別数値による verdict の読み替え
      禁止**(harness 三値が正本)

**Checkpoint**: verdict 確定(ADOPT / REJECT / NO_DECISION)

---

## Phase 5: US3 — verdict 分岐の後始末(P3)

**Goal**: どの verdict でも一貫した閉じ方。**Independent Test**: 分岐ごとの受入条件。

- [ ] T022 [US3] ADOPT の場合: `train-evaluate --register-candidate`(または arm E 登録経路)
      で candidate 登録 → adoption_status=candidate を DB で確認・`load_serving_model` で
      ロード検証・metadata に margin_teacher 統計が載っていることを確認。自動 active 化して
      いないことを確認
- [x] T023 [US3] REJECT の場合: 結線 revert — dataset.py の aux 列生成・recipe field・CLI
      セグメント・predictor 配線を剥がす。**win_model の `margin_scales=None` 引数は
      objective 拡張と同様に保全**(None 既定=現行不変・analyze L3)。`pl_topk_objective` の stage_scales 引数+
      T005 単体テストは**保全**(None 既定は現行とビット一致なのでコードは残る)。revert 後:
      全スイート緑+実 DB E2E で active 予測バイト一致
- [x] T024 [US3] 記録: verdict と数値をメモリ(margin-teacher-spike-go.md の後継 or 追記)と
      CLAUDE.md SPECKIT 区間(手動 Edit)に反映。コミットは**変更ファイルの明示列挙**
      (共有チェックアウト・`git add -A` 禁止)

---

## Phase 6: Polish

- [x] T025 ruff(変更ファイル)+ training/eval/serving/features 全スイート最終確認+
      quickstart の SC 対応表を実施結果で照合

---

## Dependencies

```text
T001 → Phase 2(T002 は T003/T007 より前が必須 — 変更前の hash 採取)
Phase 2(T002→T003→T004→T005)→ US1(T006..T015)→ US2(T015a→T016→T016a→T017→T017a→T018..T021)→ US3(T022 xor T023 → T024)→ T025
並列可: T005 / T008 / T011 / T012 / T018(異なるテストファイル・依存タスク完了後)
```

## Parallel Example(US1)

T006/T007/T009/T010 完了後、T011・T012 は並列可(別ファイル)。T013 は T006-T010 全完了後。

## Implementation Strategy

- **MVP = US1**: OFF 既定で production 完全不変のまま拡張が入る(単独で出荷可能)。
- US2 の本実行(T020)は夜間 nohup。実行中に他作業をしない(DB を動かさない・pin snapshot
  だが安全側)。
- T022/T023 は排他(verdict で片方のみ)。REJECT でも null-is-success として T024 まで完走。
