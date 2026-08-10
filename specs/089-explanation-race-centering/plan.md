# Implementation Plan: 予測根拠の実効寄与化(レース内センタリング)

**Branch**: `089-explanation-race-centering` | **Date**: 2026-08-09 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/089-explanation-race-centering/spec.md`

## Summary

040 の予測根拠は pred_contrib(race-softmax 前 margin・全母集団 base_value 比)の絶対値で
top-5 を選定して保存するため、新馬戦ではレース内定数の寄与(prev_finish 等の NaN 分岐、
レース内 std 0.006〜0.024)が top-5 出現率 29〜99% で根拠上位を占拠する。race-softmax は
レース内定数を相殺するので**順位に効かない特徴が判断根拠として表示される**(利用者が実際に
誤解)。保存形式は top-5 に切り詰め済みで表示側では復元不能なため、**保存時**に全寄与行列
からレース内平均を引いた「センタリング済み寄与」で top-K を選び直す(method_version 2)。
生寄与も併存保存して加法性監査(INV-E1)を維持し、binary objective モデルは v1 のまま
(独立確率ではセンタリングが誤った帰属になるため)。**予測確率はバイト不変・モデル/特徴/
スキーマ/migration 一切不変**。トレーニングの問題ではなく説明の保存意味論の問題である。

## Technical Context

**Language/Version**: Python 3.12(training/serving/api)、TypeScript + React(front)

**Primary Dependencies**: LightGBM(`pred_contrib=True` 内蔵 TreeSHAP・新規依存なし)、
numpy/pandas、FastAPI + pydantic v2、Vite/Vitest

**Storage**: PostgreSQL 16 — `race_predictions.explanation`(既存 JSONB・nullable)。
スキーマ変更なし・migration なし

**Testing**: pytest(training/serving/api)、Vitest + RTL(front)、実 DB E2E(quickstart)

**Target Platform**: ローカル(scripts/stack.sh スタック)

**Project Type**: 既存 monorepo の 4 パッケージ横断(training / serving / api / front)+
admin は生成型の同期のみ

**Performance Goals**: 説明計算の追加コストは列平均+差分のみ(O(n_horses × n_features)
1 パス追加)。040 実測 3-6ms/レースからの体感差なし

**Constraints**: 予測確率(win/top2/top3)バイト不変(INV-E2)・説明失敗は予測を妨げない・
API additive のみ・保存済み v1 行は書き換えない(append-only)

**Scale/Scope**: 変更ファイル実体 4(explanation.py / predictor.py / schemas.py /
ExplanationPanel.tsx)+ openapi snapshot/型同期 + テスト。049/075 級の薄い feature

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution v1.0.0 に基づくゲート:

- [x] **I. データ契約**: PASS — raceId/ID 結合・ラベル定義に触れない。表示ラベルは既存の
  featureLabels 対応表を継続使用。
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: PASS — 特徴量・予測入力は一切不変。explanation
  は表示専用派生値でモデル特徴に還流しない(040 の leak-guard 維持)。センタリングは
  予測時に手元にある X(as-of 特徴)から計算され、結果・オッズ・未来情報を読まない。
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: PASS — モデル・特徴の変更なしのため walk-forward
  評価は非該当。代わりに測定可能な受け入れゲートを事前登録(SC-001: 新馬戦のレース内定数
  特徴 top-5 出現率 0%・SC-002: 確率バイト一致)。
- [x] **IV. 確率整合性**: PASS — 確率経路に非関与(INV-E2 を回帰テストで機械固定)。
- [x] **V. 再現性・監査**: PASS — method_version で保存意味論を行単位に永続・生寄与併存で
  INV-E1 監査式を v1/v2 共通に維持・v1 行は書き換えない(append-only)・再生成は既存
  --force 経路で新 run として append。
- [x] **VI. feature 分割規律**: PASS — API 契約(additive)を front 変更より先に確定
  (contracts/explanation-v2.md)。スキーマ変更なし・P0 未決なし。
- [x] **品質ゲート**: PASS — codex 設計レビューを実施し採否を research.md D10 に記録
  (serving 永続化経路変更=CLAUDE.md MUST トリガー)。

## Project Structure

### Documentation (this feature)

```text
specs/089-explanation-race-centering/
├── plan.md              # This file
├── research.md          # D1-D10(codex 採否込み)
├── data-model.md        # explanation v1/v2 JSONB 形式・不変条件 INV-E1..E5
├── quickstart.md        # 単体/回帰/実 DB E2E/契約検証手順
├── checklists/requirements.md
├── contracts/
│   └── explanation-v2.md  # 計算/呼び出し/API/表示契約
└── tasks.md             # (/speckit-tasks で生成)
```

### Source Code (repository root)

```text
training/src/horseracing_training/explanation.py   # 核: center_within_group フラグ・v2 出力・INV-E4/E5
training/tests/unit/test_explanation.py            # v1 バイト同一・センタリング fixture・決定性・エッジ

serving/src/horseracing_serving/predictor.py       # predict_race: objective 分岐で center=True/False
serving/tests/                                     # 予測バイト不変回帰・v2/v1 保存分岐

api/src/horseracing_api/schemas.py                 # ExplanationItem.contribution_centered 追加(additive)
api/tests/                                         # v1/v2 行のシリアライズ・openapi 整合

front/openapi.json, front/src/api/schema.d.ts      # snapshot/型再生成(committed)
front/src/components/ExplanationPanel.tsx          # method_version 分岐表示
front/src/components/ExplanationPanel.test.tsx     # v1/v2/null の 3 状態表示テスト

admin/openapi.json, admin/src/api/schema.d.ts      # snapshot/型同期のみ(explanation UI なし)
```

**Structure Decision**: 既存パッケージ構成に完全に載る(新規モジュールなし)。説明計算の
正本は training/explanation.py 1 箇所(呼び出し元は serving predictor.py の 1 箇所のみ・
全 tree grep で確認)。DB/migration/betting/ops/live は無改修。

## 設計の核(research.md 要約・codex レビュー反映済)

1. **数学的根拠と限定命名(D3)**: race-softmax では margin へのレース内定数加算が確率に
   無影響(exp の約分)。centered は Σ_f centered_{i,f} = z_i − mean_race(z) =
   log p_i − mean_race(log p) を満たす**レース内相対 logit の正確な加法分解**。ただし
   softmax 後確率への SHAP ではないため、文言は「同一レース内の平均に対する、レース内
   正規化前の相対スコア寄与」に限定(codex #2)。確率空間 exact SHAP は cross-attribution
   の複雑さで不採用(codex 同意)。
2. **候補除外+レース atomic(D5・codex #1/#3)**: センタリングだけでは top-5 混入 0% を
   保証できない(分散は残る)→ **そのレースで value が全馬同値(全馬 NaN 含む)の特徴を
   候補から除外**し K 未満許容で SC-001 を構造保証。平均は softmax 分母と同じ**全 started
   行**で取り、1 行でも検査不能ならレース atomic に全行 NULL(部分平均は「レース内平均
   との差」でなくなるため)。1 頭レースは items 空+「比較対象なし」表示。
3. **v2 形式(D4・codex #6)**: items に生+centered 併存、`score_centered` /
   `other_contribution_centered` / `centering_population_size` を追加保存 → **保存 JSON
   単体から v2 加法性を検証可能**(INV-E4b)。per-feature 総和 0 は生成時検査(INV-E4)。
   base/score/other(生)は v1 同一定義で INV-E1 は共通式。
4. **検査の是正(D5・codex #4)**: 現行 INV-E1 は自己参照(score を Σcontrib から合成)
   → `expected_raw_scores`(serving の既計算 raw)との照合+NaN/Inf 明示検査に強化。
   **追加の booster.predict は不要**(性能見積もり不変)。
5. **objective / offset 分岐(D6・codex #5)**: v2 は「race-softmax かつ offset なし」限定。
   binary は誤帰属、**market-offset は pred_contrib が offset 成分を説明できず主張が虚偽に
   なる**ため v1 維持(offset_centered は deferred)。METHOD_VERSION は V1/V2 定数分離。
   防御 try/except は説明経路全体+呼び出し側の二重化。
6. **API(D7・codex #7)**: router の `Explanation.model_validate` が extra キーを黙って
   落とすため新フィールド追加は表示の前提条件(075 splat-null 罠の同型を plan 段階で検出)。
   additive のみ・「透過」でなく型付き変換・snapshot/型同期。
7. **front(D8・codex #7)**: 分岐は `method_version === 2` 厳密一致(>= 2 禁止)。v2 は
   centered 主表示・「その他」行も centered(意味論統一)・centered 欠落は生値フォール
   バックせず「未提供(形式不整合)」。v1 は現行表示維持。
8. **旧行と backfill(D9・codex #11)**: v1 行は書き換えない。過去分の v2 化は既存
   `predict-backfill --force` の任意運用(新規 CLI なし)。確率バイト一致の主張は「同一
   入力での説明 on/off 比較」に限定(再生成 run 間の一致はデータ訂正等で保証しない)。
9. **契約整理(codex #12)**: 040 の contracts/prediction-explanation.md に「v1 契約・
   v2 は 089 参照」を追記して競合を解消(FR-016)。

## codex 設計レビュー(採否は research.md D10 が正本)

CLAUDE.md 規約(serving 永続化経路=MUST)に基づき `codex exec --sandbox read-only` で実施
済み。codex 総合判定「センタリングの核は妥当・採用推奨、ただし条件付き」— **BLOCKER 1
(SC-001 の 0% 保証不能)+ HIGH 6 + その他 8 を全件トリアージし採用 12/部分採用 1
(ε 除外→全馬同値除外に変更)/不採用 1(v2 その他行の UI 非表示→centered 表示でより
強く)**。当初設計からの主要改訂: 候補除外の導入・レース atomic 母集団・raw 照合検査・
market-offset の v2 除外・centered score/other の保存・version 厳密分岐・SC-002/006 の
主張限定。詳細な対応表は research.md D10。

## Complexity Tracking

違反なし(スキーマ変更なし・新規パッケージなし・API additive のみ)。
