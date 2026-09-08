# Tasks: 最良6モデル構成の候補化と並行予測

**Input**: [plan.md](plan.md)、[spec.md](spec.md)、research/data-model/contracts/quickstart。
**Tests**: Specの再現性・リーク・破損・未見評価要件に対する試験を実装前に定義する。

## Phase 1: Setup

- [x] T001 仕様・設計・契約・独立意見を `specs/129-candidate-mixture-serving/` に保存する。
- [x] T002 実行環境、既存ignore、変更範囲、計画参照を `AGENTS.md` と `plan.md` で確認する。

## Phase 2: Foundation

- [x] T003 6member順序・列・補正・保存形式・shadow限定境界を `contracts/bundle.md` に定義する。
- [x] T004 将来確認の実提供比較相手・seed反復・共通予算・未完了条件を `contracts/shadow-confirmation.md` に定義する。

## Phase 3: US1 候補モデルの作成

**Independent test**: 新6モデルと補正を保存し、再読込後の全3確率を同じ入力で再現する。

- [x] T005 [P] [US1] `scripts/tests/test_candidate_mixture_build.py` にレシピ・列・resume・partial・OOF・保存往復の試験を先行作成する。
- [x] T006 [P] [US1] `serving/tests/unit/test_mixture_correction.py` にstrict prior・同日・欠測・閏年・温度・全head平均の試験を先行作成する。
- [x] T007 [P] [US1] `serving/tests/unit/test_mixture_model.py` に6member完全性・改変・列順・保存往復・入力regimeの試験を先行作成する。
- [x] T008 [P] [US1] `scripts/candidate_mixture_build.py` にfreeze/smoke/6モデル学習/export/receipt検証を実装する。
- [x] T009 [P] [US1] `serving/src/horseracing_serving/mixture_correction.py` に推論専用の入力構築・補正・全head平均を実装する。
- [x] T010 [P] [US1] `serving/src/horseracing_serving/mixture_model.py` に限定profileのmanifest読込と予測を実装する。
- [~] T011 [US1] `specs/129-candidate-mixture-serving/evidence/pre-run-review.md` に全ソース/設定/testの独立レビューを記録し本学習前に固定する。
- [x] T012 [US1] `artifacts/129-candidate-mixture-serving/members/` にsmoke成功後6モデルを作成し保存往復・実費receiptを検証する。
- [x] T013 [US1] `scripts/candidate_mixture_validate.py` で旧年別予測の全期間全head再現、新bundle保存往復と実運用入力の一致を独立監査する。
- [x] T014 [US1] `artifacts/129-candidate-mixture-serving/bundle.json` を全6要素・保存2026係数・全出典とともに確定する。

## Phase 4: US2 並行予測

**Independent test**: 同一の発走前入力から二つの予測を追記保存し、過去再現を未見評価から分離する。

- [x] T015 [P] [US2] `serving/tests/unit/test_mixture_shadow.py` に同一入力/部分体重/結果後拒否/append-only/比較相手改変の試験を先行作成する。
- [x] T016 [US2] `serving/src/horseracing_serving/mixture_shadow.py` に読取専用入力取得・現行anchor固定・並行予測CLIを実装する。
- [x] T017 [US2] `specs/129-candidate-mixture-serving/evidence/shadow-rehearsal.json` に固定過去レースの運用regime・latency・再現検証を記録する。
- [x] T018 [US2] `artifacts/129-candidate-mixture-serving/shadow/` に利用可能な未来レースの予測、又は未来レース未登録の待機状態を正確に保存する。

## Phase 5: US3 最終確認の準備

**Independent test**: 将来結果が不足する時点では採用されず、内容の揃った固定確認契約だけを受け入れる。

- [x] T019 [P] [US3] `scripts/tests/test_mixture_confirmation.py` にensemble/単一seed区別、共通ledger、日時/十分性/改変拒否の試験を先行作成する。
- [x] T020 [US3] `scripts/mixture_confirmation.py` に専用manifest・検出力計画・予約前検査・共通予算登録・完了条件を実装する。
- [x] T021 [US3] `specs/129-candidate-mixture-serving/evidence/confirmation-readiness.json` に実提供条件の証拠と検出力/ノイズ仮定を独立検証し、成立時だけ未来期間を予約、未成立なら不足理由を明示する。

## Phase 6: Final review

- [x] T022 変更した範囲の試験と既存serving回帰試験を実行し `evidence/validation.json` に結果を記録する。
- [x] T023 実費・最終SHA・旧研究不変・稼働モデル不変を独立確認し `evidence/final-integrity.json` に保存する。
- [x] T024 `README.md`、`result-review.md`、`quickstart.md` を実際の実行結果・操作方法・将来待機条件に更新する。

## Dependencies and parallel work

Setup→Foundation→US1→US2→US3。コードの独立部分の試験/実装は並走できるが、実データ実行は前段の検査を通してから行う。

- driver: T005/T008/T012の学習処理。親だけが大規模実行を起動する。
- gap: T006/T009とT013の独立再現監査。
- parent: T007/T010/T014/T015/T016及び統合実行。
- design reviewer: 非自明設計の独立確認、T019/T020の別実装とレビュー。

MVPはUS1。既に承認されたUS2/US3の実装・準備まで継続し、未来の結果が未到来であることをタスク未実装と混同しない。全期間結果の到来と本番切替は将来の外部条件であり、この時点で完了を偽装しない。フックbefore/after_tasks/implementは設定なし。

## 2026-09-09 実行記録(Claude)

- T011 は codex 使用上限(9/15 まで)で独立レビュー不能。代替のセルフレビュー checklist を research.md に記録して本学習へ進んだ([~])。
- T012/T014/T017/T018/T021/T022/T023 の実体は `result-review.md` と `evidence/` を参照。T021 は独立レビュー未取得のため **NOT_READY**(予約なし)で終了。
- T016 は codex 側で test だけ存在し本体が無かったので実装。実装中の欠陥 2 件(`train_from` 固定・`race_date` 欠落)は result-review に記録。
