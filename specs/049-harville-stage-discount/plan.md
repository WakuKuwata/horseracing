# Implementation Plan: Harville stage 割引 — top2/top3(連対・複勝)確率の校正改善

**Branch**: `049-harville-stage-discount` | **Date**: 2026-07-02 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/049-harville-stage-discount/spec.md`

## Summary

lgbm-042 の win ECE 0.00057 に対し top2 ECE 0.00735・top3 ECE 0.01944 と、Harville 逐次導出層の校正だけが系統的に悪い(既存 reliability bins で高帯 +9〜10pt 過大を確認済み)。2・3 着ステージの逐次分母に Benter 型冪割引 p^λ_j を導入し、λ_2/λ_3 を walk-forward 条件付き NLL MLE でフィットして是正する。win 確率はバイト不変(λ_1=1・明示分岐)。実装は eval の新モジュール(依存方向 probability→eval のため)+ engine/predictor への opt-in 透過、採否は事前登録ゲート(18-fold A/B・単一学習パス・exotic 非悪化 MUST)で機械判定。スキーマ・API 変更なし、λ は logic_version 記録。詳細は [research.md](research.md)(D1〜D8)・[contracts/stage-discount.md](contracts/stage-discount.md)。

## Technical Context

**Language/Version**: Python 3.12(既存 uv workspace)

**Primary Dependencies**: numpy(導出・NLL)、SQLAlchemy 2.0/psycopg3(フィットサンプル読取)、LightGBM(既存 predictor、変更なし)

**Storage**: PostgreSQL 16 — **スキーマ変更なし**(migration なし、head=0008 不変)。読取: prediction_runs/race_predictions/race_results/races。書込: 既存カラムのみ(top2_prob/top3_prob の値、logic_version 文字列)

**Testing**: pytest(+ 既存 testcontainers/実 DB スモーク)。契約の INV-S1〜S9 を単体テスト化

**Target Platform**: ローカル CLI(Linux/macOS)— 既存 training/betting CLI に評価・比較コマンドを追加

**Project Type**: 既存マルチパッケージ monorepo への層内拡張(eval/probability/training/serving/betting)

**Performance Goals**: 導出は現行 harville_topk と同じ O(n³)/レース(n≤18)— serving レイテンシ影響は無視可能。λ フィットは 1 次元 golden×2(数百レースで <1s)

**Constraints**: win 確率バイト不変(INV-S1/S2/S9)・確率整合性不変量(INV-S3〜S5)・決定論(INV-S6)・リーク境界(INV-S8、厳密前フィット)

**Scale/Scope**: 評価は 18-fold/86.6万頭(既存ハーネス)。製品フィットは永続化予測(550+ レース、日々増加)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.* — **Phase 1 後 再評価済み: 全 PASS(品質ゲートのみ正当化付き deviation)**

- [x] **I. データ契約**: PASS — raceId 12 桁・2007+・id_mappings 既存規律のまま。ラベルは 1着率/2着以内率/3着以内率(win/top2/top3 は内部識別子)。新結合なし。
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: PASS — λ フィットは対象より厳密前(race_before、race_id タイブレーク)/評価は前 fold pooled OOS のみ(D3)。結果はフィットのラベルのみ(選定・特徴に不使用)。オッズ・市場 q 不使用(p≠q)。λ・割引後値はモデル特徴に還流しない(INV-S8 leak-guard テスト)。新特徴なし。
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: PASS — 採否は事前登録ゲート(spec US2: PRIMARY/MUST/ガード、λ 範囲・min_races 事前固定)で機械判定。baseline=λ=1(現行)、ECE/LogLoss/reliability を fold 別+overall で比較。ハーネスは既存 expanding_folds 流用+A/B 拡張を実装より先に用意(tasks で評価タスクを実装タスクに先行させる)。
- [x] **IV. 確率整合性**: PASS — win≤top2≤top3・Σ≈1/2/3 は構成的に保存(contract INV-S3/S4)、joint marginal 整合は consistency チェッカを λ 対応に拡張して検証(INV-S5)。取消・除外は既存 canonical field 規律不変。Unknown/0 区別は非該当(特徴なし)。
- [x] **V. 再現性・監査**: PASS — λ・フィット件数・fallback を logic_version に記録(`sdisc=...`、046/048 の pcal と同一規律)。未指定経路の lv はバイト不変。推定オッズの疑似ラベル既存規律に変更なし。
- [x] **VI. feature 分割規律**: PASS — UI 変更なし(既存表示に校正済み値が透過)。API/openapi/スキーマ契約不変(drift-check で確認)。P0 未決なし(方式は本 plan で固定)。
- [x] **品質ゲート(codex second opinion)**: **DEVIATION(正当化)** — codex CLI は本セッションで 3 回起動不可(プラグインが CLI 未検出)。044/045/046/048 の見送り前例に従い single-opinion。代償措置: (a) 手法は Henery/Stern/Benter の確立された文献補正で新規発明なし、(b) research.md D1〜D8 に却下代替案を明記(自己レビュー)、(c) 実装前に実測 reliability で仮説を確認済み(仮説先行でない)、(d) 事前登録ゲート+exotic 非悪化 MUST で誤採用をデータで遮断。CLI 復旧時は implement 前に再試行する。

## Project Structure

### Documentation (this feature)

```text
specs/049-harville-stage-discount/
├── spec.md              # 仕様(事前登録ゲート含む)
├── plan.md              # This file
├── research.md          # Phase 0 — D1〜D8 決定記録
├── data-model.md        # Phase 1 — 値オブジェクト・lv 記録形式(スキーマ変更なし)
├── quickstart.md        # Phase 1 — 検証手順
├── contracts/
│   └── stage-discount.md  # 導出・フィットの数学契約 + INV-S1..S9
├── checklists/requirements.md
└── tasks.md             # Phase 2 (/speckit-tasks — 未作成)
```

### Source Code (repository root)

```text
eval/src/horseracing_eval/
├── stage_discount.py        # 新規: 割引導出コア・fit_stage_discount・局所 golden(D2/D8)
├── baselines.py             # harville_topk に lambda2/lambda3 追加(λ=1 明示分岐=バイト一致)
├── stage_discount_eval.py   # 新規: evaluate_stage_discount(18-fold A/B・単一学習パス、D6)
└── tests/                   # INV-S1..S7・フィット決定論・同着除外

probability/src/horseracing_probability/
├── engine.py                # joint_probabilities(..., stage_discount=None) — 逐次分母に w2/w3
├── consistency.py           # 同一 λ での joint↔marginal 整合検証(INV-S5)
└── tests/                   # 割引時の Σexacta/Σtrifecta・place/wide/trio 整合

training/src/horseracing_training/
├── predictor.py             # assemble_predictions(..., stage_discount=None) 透過
└── cli.py                   # stage-discount-eval コマンド(LightGBMPredictor 注入)

serving/src/horseracing_serving/
├── pipeline.py / persistence.py  # (採用時) product λ walk-forward フィット結線・lv 記録
├── pyproject.toml           # (採用時) horseracing-probability 依存を追加(load_topk_samples 用。
│                            #  probability は serving に依存しないため非循環 — analyze U1)
└── tests/                   # win バイト不変・top2/top3 変化・lv 記録

betting/src/horseracing_betting/
├── (新) stage_discount_compare.py  # exotic 非悪化ゲート(D7: 複勝/ワイド/三連複 λ=1 vs λ̂)
├── cli.py                   # stage-discount-backtest-compare コマンド
└── (採用時) recommend 経路の stage_discount opt-in(046 の pcal 結線と同型)

probability/src/horseracing_probability/model_calibration.py
└── (新 loader) load_topk_samples — (win ベクトル, 1〜3着) 厳密前サンプル(load_p_samples 不変)
```

**Structure Decision**: 共有導出コアは依存方向(probability→eval、eval は training/probability 非依存)の制約から eval に置く(research D2)。既存パッケージへの opt-in 拡張のみで新パッケージ・新サービスは作らない。全 opt-in 引数の既定値は「現行挙動とバイト一致」(INV-S9)。**新規依存エッジは serving→probability の 1 本のみ**(US3 採用時、`load_topk_samples` のため。probability は serving を import しないため非循環)。λ の fit/apply は分布一致原則(research D4: serving=素の p、betting=two_gamma 後の p')に従う。

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| codex second opinion 未取得(品質ゲート deviation) | codex CLI がセッション内 3 回起動不可(環境要因) | 「実装を止めて CLI 復旧を待つ」は前例(044〜048)に反しユーザー指示(開始)とも不整合。文献裏付け+事前登録ゲート+exotic MUST+research の却下代替案明記で単一視点リスクを緩和。CLI 復旧時 implement 前に再試行 |
| golden-section の局所再実装(fl_bias._golden_min と重複) | eval は probability を import できない(循環) | `_golden_min` の probability→eval 移設は 013/017/048 の安定モジュールに波及するリファクタで、~15 行の決定論関数の重複より高リスク |
