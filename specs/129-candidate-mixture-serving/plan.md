# Implementation Plan: 最良6モデル構成の候補化と並行予測

**Branch**: `codex/129-candidate-mixture-serving` | **Date**: 2026-09-08 | **Spec**: [spec.md](spec.md)

## Summary

**実行結果(2026-09-09)**: 6 member 本学習・bundle・anchor 凍結・rehearsal 144 レース・検出力計画まで完了。rehearsal の候補−anchor は +0.0015(4 日・符号未定)、98.75% 枠で確認可能な効果は 0.01 級のみ(24 日)。確認は独立レビュー未取得で NOT_READY。詳細は [result-review.md](result-review.md)。

125のnew_joint_mixed6をshadow専用の6member bundleとして新規構築する。学習は旧111 snapshot/Framesの2007〜2026-08-23、900本/8OOF/weight mask0.5/TE10を維持、raw125/raw138各seed42/43/44。補正は125/118の保存2026係数を固定し、2019〜2025の外側heldによる学習来歴を保持する。新モデルと旧年別モデルの予測一致は要求せず、旧研究演算の再現と新モデルの保存前後一致を分離する。

serving新モジュールと新scriptsだけを追加する。旧training/features/db/eval/probability src及び110〜128 scripts/specsを変更せず研究hashを維持。現行DBモデル登録・既存predict経路を変更しない。bundleは明示パスでshadow専用に読み、現行anchorは通常の実提供単一モデル＋固定した表示校正を使う。成果物を通常のactiveモデルとして誤登録しない。

## Technical Context

**Language/Version**: Python3.12、既存training/.venvの固定runtime。
**Primary Dependencies**: 既存LightGBM、pandas、numpy、scipy、SQLAlchemy、training OofCalibratedPredictor、serving ServingModel。
**Storage**: artifacts/129-candidate-mixture-servingに追記ファイル。運用DBはshadow入力・現行anchorの読取専用。旧112共通confirmation ledgerは正式登録時のみ更新。
**Testing**: pytest、純粋補正/境界検査、保存往復、旧全期間予測再現、実データ運用regime照合、独立監査。
**Target Platform**: macOS/ローカル24GiB、将来CLIから再現可能な保存物。
**Project Type**: 既存monorepoのserving拡張とoperator scripts。新UI/サービス/DB schemaなし。
**Performance Goals**: 学習6outer計48boosters、旧実費から2workerで約50〜60分＋保存/検証。実推論latency/RSSは測定し未測定値を断定しない。
**Constraints**: 最大2worker/各1thread、同時研究学習なし。失敗/partialを上書きして隠さず、完了receiptのhash照合後だけresume。未知schema/部分bundleはfail closed。
**Scale/Scope**: 6finalモデル、6保存補正、23,030過去raceの演算再現、別の将来shadow記録、ensemble専用確認契約。

## Constitution Check

- [x] I PASS: 12桁raceId/2007以降、source間ID推測結合なし、ラベル3確率。
- [x] II PASS: 既存asof特徴とstartedのstrict prior履歴。対象日除外、OOF校正、保存年別補正。発走前記録を過去replayと別に扱う。
- [x] III PASS: 125のwalk-forward研究と新export/parityを先行。採用は別の未使用固定期間＋baseline/ECE、過去の採用判定を偽装しない。
- [x] IV PASS: 全6同一started集合、有限/順序/和、取消除外、missing offset0と未知値保持。
- [x] V PASS: source/runtime/input/member/補正SHAと時刻、追記shadow。市場オッズを候補入力にしない。疑似ROI・購入判断は今回対象外。
- [x] VI PASS: API/DB変更・UIなし。候補型/予測/確認をcontractsに事前固定。
- [x] 品質gate PASS: stack_design_review、gap_season_recheck、stack_driverの独立意見をresearch.mdへ記録。親が採用案を照合。

## Project Structure

```text
specs/129-candidate-mixture-serving/
  spec.md plan.md research.md data-model.md quickstart.md tasks.md
  contracts/bundle.md contracts/shadow-confirmation.md checklists/requirements.md
  evidence/
scripts/
  candidate_mixture_build.py
  candidate_mixture_validate.py
  mixture_confirmation.py
  tests/test_candidate_mixture_build.py
  tests/test_mixture_confirmation.py
serving/src/horseracing_serving/
  mixture_correction.py
  mixture_model.py
  mixture_shadow.py
serving/tests/unit/
  test_mixture_correction.py test_mixture_model.py test_mixture_shadow.py
artifacts/129-candidate-mixture-serving/
  run-freeze.json members/ receipts/ bundle.json shadow/ confirmation/
```

## Design and Execution

1. Freeze research source evidence and build recipe. Compile tests before real fits; source revisions are final before training. Runtime serving work may continue in unrelated files while each training job's own dependency hashes remain fixed.
2. Build 6 new final models in isolated member directories. Serialize booster/calibrator/preprocessor/metadata without save_model_version (which commits to DB). Native single-model ServingModel roundtrip proves same raw model predictions before completion receipts are written. Fully review receipts before bundle assembly.
3. Correction helper receives current target features and separate full started history. Currentgap comes from days_since_last, priorgap from D1−D2 using same ID/strict dates. Leap-year/sex/missing behavior matches119/125. Base calibration/clip/race-normalization occurs once, then correction, eps0 assembly, ordered per-head mean.
4. Bundle profile validates exact125/138 column list/order and raw representation against the original138 source plus13 registered drops. It does not extend legacy globalfeature compatibility. Hash/path/seed/recipe/OOF/6member completeness and positive temperature are checked before inference.
5. Parity A replays all original annual raw forecasts and annual correction vectors; B compares final in-memory vs serialized inference; C verifies preweight/full/partial input processing on a fixed regression population. Prior reports remain immutable. Actual performance of final models is future-only.
6. Shadow loads current production anchor and its actual display-calibration behavior, freezes a concrete anchor calibration artifact for confirmation, and applies the same asof input to both. Candidate has no downstream stage discount or re-Harville. Records carry full input snapshots/hashes, model hashes, UTC commit time and pre-result status; after-result runs are explicitly rehearsal only.
7. Confirmation uses a new ensemble schema and common112 budget accounting. No fake selected_seed, independentbundle count remains1, old7fold sqrt reduction is not transferred. Power planning uses exact-regime historical development evidence and scenarios before choosing future window. If data or justified noise/power evidence is insufficient, finish the available candidate/shadow work and record an honest NOT_READY confirmation plan; no artificial reservation or adoption.
8. Evaluate only after frozen end and enough prospective days. Fail closed on missing/revised evidence. Production switch is excluded until an eligible final confirmation and deployment review exist; it cannot be completed before future outcomes arrive.

## Independent decisions

Chosen over single final booster: six-member behavior is the tested gain. Chosen over save_model_version: file-only staging avoids early DB exposure and allows a125-column profile without weakening legacy loader checks. Chosen over automatically refreshed coefficient fit: preserve saved2026 coefficients for the first deployment candidate; later cutoff refresh is a new bundle. Chosen over injecting seed42 into112 manifest: ensemble metadata/noise stays explicit. Chosen over applying currenttopk discount to candidate: that would change125's tested head probabilities. Anchor keeps its own real serving behavior.

No constitutional exceptions. Hooks before_specify/before_plan are absent. Optional after_specify/after_plan agent-context hook is satisfied by updating the managed plan reference; no additional external action is required.
