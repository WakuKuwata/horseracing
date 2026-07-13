# Implementation Plan: 評価契約の是正 + 校正分割の見直し

**Branch**: `068-evaluation-contract-calibration` | **Date**: 2026-07-12 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/068-evaluation-contract-calibration/spec.md`（提案書 Phase 0 + Phase 1）

## Summary

採用判定の物差しを是正し、その物差しで校正分割実験の限界効果を測る。2レイヤ:

1. **評価契約（US1/US3, Phase 0）**: `eval/` に predictor-agnostic な新指標と比較基盤を追加する — race-level winner NLL（PRIMARY）、started-all LogLoss/Brier、equal-mass/確率帯別/頭数別 ECE、候補↔active の **paired 評価**（保存 artifact を使わず両 ModelRecipe を各 outer fold で再 fit、baseline 保存値を読まない）、開催日単位 block bootstrap の 95% 信頼区間、直近3年/5年ガード。採用ゲート（winner NLL 勝ち + CI上限<0 + 直近非劣化 + top2/3 non-inferiority + ECE非劣化）をコード化し、閾値・seed・fold境界を実行前固定。`predictor.py` の `fit_info_` に `model_fit_through`/`calib_from`/`calib_through` を追加（provenance）。

2. **校正分割実験（US2, Phase 1）**: 特徴量・目的関数・seed を固定した同一スナップショット上で A（70/30 isotonic 現行）・B（90/10 isotonic）・C（全履歴refit + OOF temperature）・D（全履歴refit + OOF race-normalized power）を比較する CLI を追加。直近fold で go/no-go de-risk → 勝ち候補のみフル walk-forward → active と paired フル評価。

**スキーマ・API・OpenAPI・migration・FEATURE_VERSION・feature_hash・model artifact bit parity は不変**。変更は `eval/`（新指標・比較・bootstrap）と `training/`（実験ドライバ CLI・provenance 記録）に限定。

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: numpy, scikit-learn, lightgbm, pandas（既存）。新規依存なし（block bootstrap は numpy の seeded Generator で自作）。

**Storage**: PostgreSQL 16（read-only 評価）。**スキーマ変更なし・migration なし**。provenance は既存 `model_versions.metrics_summary` JSONB 内で完結。

**Testing**: pytest + testcontainers。合成データで指標の決定論・母集団分類・leak-guard を固定し、実 DB で既存2モデル（lgbm-062 vs lgbm-061）の paired 再評価を SC-001 として検証。

**Target Platform**: CLI（`training` / `eval` パッケージ）。運用者が walk-forward driver を起動。

**Project Type**: ML 評価・学習パッケージ（web/UI なし）。

**Performance Goals**: pl_topk フル walk-forward は perf 改善後 ~20分/回（[perf-training-eval-speedup]、multi-thread）。C/D の全履歴refit + OOF は最も重いので、直近fold の go/no-go de-risk を先に行い、フル評価は勝ち候補のみ。**SC-002 の決定論検証のみ `num_threads=1` 固定**（bit 再現）で、重い A–D screening / フル walk-forward は multi-thread（決定論は単一スレッド run で別途担保・CI が残差を吸収）＝~20分/run の予算と矛盾しない（analyze I1）。block bootstrap は開催日集計後に resample するので実行時間は無視可能。

**Constraints**: 憲法II（評価派生値を特徴に戻さない・対象レース市場/結果を特徴にしない）、III（事前登録ゲート・OOS前固定）、IV（校正後 Σ=1・順位保存）、V（provenance・bootstrap seed 記録・既存行遡及なし）、VI（契約不変）。eval は training 非依存（predictor 注入、020 の循環回避）。

**Scale/Scope**: 956,409 学習行 / 約67k レース。2007–2026。fold は既存 walk-forward 境界を再利用。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- [x] **I. データ契約**: PASS。raceId・年範囲・id_mappings・ラベル定義は既存経路を変更しない。評価は既存 `eval/dataset.py` の entry_status を使い、新たな結合や ID 経路を足さない。
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: PASS。新指標・reliability・paired差・bootstrap CI はモデル特徴に一切戻さない（leak-guard test を追加）。win label は採点専用で特徴非流入（既存規律と同じ）。対象レース自身の市場・結果は評価入力にも特徴にも使わない。started-all/finished-only の母集団は結果ラベルの採点利用であり特徴経路に無関係。
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: PASS。本feature自体が評価契約の強化。採用ゲート閾値・non-inferiority幅・bootstrap seed・fold境界は OOS 前に固定（artifact 化）。直近fold は go/no-go のみ、列/条件をOOSで変えない。
- [x] **IV. 確率整合性**: PASS。校正後もレース内 Σ=1・順位保存（009整合）。C/D の race-normalized power は 013/048 と同じ「marginal でなく race-normalized ベクトルを校正」規律を踏襲（research D3 で固定）。
- [x] **V. 再現性・監査**: PASS。provenance 5項目記録・bootstrap seed 記録・実験条件 artifact。既存モデル行は遡及書換しない（040/050 前例）。
- [x] **VI. feature 分割規律**: PASS。UI なし。スキーマ・API・OpenAPI・migration 不変。予測/推奨テーブル契約に触れない。
- [x] **品質ゲート**: PASS。codex-rescue agent は起動不可だったが、親から `codex exec --sandbox read-only` 直叩きで second opinion を取得（[codex-env-recovery] 方式・codex-cli 0.144.1）。**correctness-critical 4点（C1 recipe-refit / C2 nested inner-valid / C3 market_offset fail-closed / C4 日単位分割）+ 設計明確化6点を全採用**し spec/plan/data-model/contracts/research D7 に反映。両案差分と採否は research D7 に記録。

**判定**: 全ゲート PASS。codex 指摘反映後、NON-NEGOTIABLE 項目（II/III）を含め設計は堅牢化。ブロッキング違反なし → Phase 1 完了。

## Project Structure

### Documentation (this feature)

```text
specs/068-evaluation-contract-calibration/
├── plan.md              # This file
├── research.md          # Phase 0: 指標定義・bootstrap・C/D スコア移植の設計判断
├── data-model.md        # Phase 1: PairedEvalReport / CalibrationSplitExperiment / TrainingProvenance
├── contracts/
│   └── cli.md           # Phase 1: paired-eval / calib-split-eval CLI 契約
├── gate-config.json     # T001: 事前登録ゲート/screening 基準・seed・閾値（OOS前固定, III）
├── quickstart.md        # Phase 1: SC-001（lgbm-062 vs lgbm-061 paired 再評価）検証手順
└── tasks.md             # Phase 2: /speckit-tasks が生成
```

### Source Code (repository root)

```text
eval/src/horseracing_eval/
├── metrics.py           # [EDIT] winner_nll / started_all_* / equal_mass_ece / band別・頭数別 ECE を追加
├── dataset.py           # [EDIT] population_masks（started/finished/winner-NLL-eligible の分類）を追加
├── harness.py           # [EDIT] evaluate() に started-all 母集団と新指標を併記、per-race loss を保持
├── foldfit.py           # [NEW] PredictorFactory Protocol（各fold再fit）。ModelRecipe を import せず factory を注入で受ける
├── paired.py            # [NEW] 候補↔active の同一race集合 paired 比較 + gate 判定（predictor-agnostic）
├── bootstrap.py         # [NEW] 開催日単位 block/moving bootstrap の seeded CI
├── hashing.py           # [NEW] HashContract / SnapshotAudit（型と算出の単一home）
└── splits.py            # [READ] 既存 fold 境界を再利用

training/src/horseracing_training/
├── recipe.py            # [NEW] ModelRecipe（各fold再fit用の処方、market_offset=false fail-closed）+ metadata復元
├── predictor.py         # [EDIT] fit_info_ に model_fit_through/calib_from/calib_through 追加（US3）
├── calibration.py       # [EDIT] split を開催日単位へ（codex C4）。A 再現用に race数ベースをテスト保持
├── calib_split_eval.py  # [NEW] A/B/C/D 実験ドライバ（inner-valid screening・strict-past OOF・predictor 注入）
├── artifacts.py         # [EDIT/VERIFY] provenance 5項目が metadata.json / metrics_summary に透過（既存 pass-through 確認）
└── cli.py               # [EDIT] `paired-eval` と `calib-split-eval` サブコマンド追加

eval/tests/ , training/tests/  # [NEW] 指標決定論・母集団分類・leak-guard・paired・bootstrap・provenance
```

**Structure Decision**: 既存の2パッケージ境界を維持する。**予測不能な指標・比較・bootstrap は `eval/`**（predictor-agnostic、020 で確立した循環回避 — eval は training を import しない、`LightGBMPredictor` は CLI が注入）。**学習配分を変える実験ドライバと provenance は `training/`**（predictor を握る側）。新規パッケージは作らない（薄い増分、051 admin のような新レイヤ不要）。

**eval→training 非依存の担保（analyze C1）**: `ModelRecipe` は training 概念のため `training/recipe.py` に置く。eval は **`foldfit.PredictorFactory` Protocol**（`(train_rows, fold) -> fitted predictor`）だけを知り、`ModelRecipe` 型を import しない。CLI（training 側）が2つの recipe から factory を構築して `eval.paired` に注入する（020 の「CLI が predictor 注入」を factory 単位に一般化）。`PairedEvalReport` は recipe を **plain dict + recipe_hash** で保持し、training 型を持ち込まない。これで eval のクリーンな import 境界（T012 の import 禁止テスト）を守る。

## Complexity Tracking

> ブロッキング憲法違反なし。品質ゲートの PARTIAL は codex 環境起因の正直な限界であり設計複雑性ではないため、表は空。

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| （なし） | — | — |
