# Implementation Plan: Plackett-Luce top-k 目的関数 (042)

**Branch**: `042-pl-topk-objective` | **Date**: 2026-07-02 | **Spec**: [spec.md](spec.md)

## Summary

039 cond_logit(PL top-1)を **PL top-3 listwise** に拡張する objective `pl_topk` を追加。stage j(j=1..3)は「まだ placed でない馬」の softmax で −log p(j 着馬)、stage 減衰 w=[1.0,0.5,0.25] を grad/hess に加重加算。**予測・校正・serving は cond_logit と完全同一経路**(raw_score→race softmax→校正→009)= 変わるのは勾配計算のみ。rank ラベル(確定着順 1..3/0)を label 側で供給(win と同じ境界、特徴に非流入)。spike(2019+ 3fold)で winner-NLL −0.0095・top1 +0.0064・AUC +0.0035・全 fold 改善(ブレストキュー最大)。採否は 18-fold OOS・校正 A/B(039 同型)。採用なら lgbm-042 active・lgbm-041 retired。**新特徴なし・スキーマ変更なし・FEATURE_VERSION 不変(features-012)**。

## Technical Context

**Language/Version**: Python 3.12 / **Dependencies**: 既存のみ(LightGBM 4.x custom objective)
**Storage**: read-only(スキーマ変更なし)。artifacts=lgbm-042
**Testing**: pytest(objective 単体・後方互換・leak)+ 実 DB 18-fold
**Constraints**: 憲法 II(rank は label のみ)/III(事前登録・A/B)/IV(009 不変)/V(objective 記録・deterministic)
**Scale**: 学習 ~90万行。stage 計算は group ごと O(k·n)

## Constitution Check

- [x] **I**: データ契約不変(PASS)
- [x] **II**: rank は race_results 確定着順から win と同一機構で label 導出、model_input_features 外・特徴に非流入(leak-guard)。group は race_id のみ。odds 非参照(PASS)
- [x] **III**: 18-fold OOS 事前登録・STAGE_WEIGHTS 固定(spike の事前選択値、OOS で調整しない)・校正 A/B(PASS)
- [x] **IV**: 出力はレース内 softmax=win 確率、009 不変(PASS)
- [x] **V**: objective=pl_topk を metadata/metrics_summary 記録、deterministic/seed 固定(PASS)
- [x] **VI**: スキーマ・API/front 不変(PASS)
- [x] **品質ゲート**: codex second opinion を並走取得(結果は research/plan に反映)。039 レビュー済み設計の直接拡張

**Gate result: PASS**

## Design Decisions

### D1: pl_topk objective(cond_logit.py に追加)
- `pl_topk_objective(group_sizes, ranks)` → fobj。group ごと:
  - stage1: 全馬 softmax(max 減算)。`rk==1` が一意でなければ **group 全体中立化**(039 同型、grad=0/hess=floor)。
  - stage j≥2: remaining(未 placed)上で softmax。`rk==j` 非一意 or remaining<2 → **break**(先行 stage 保持)。
  - `grad[remaining] += w_j(p−y_j)`・`hess[remaining] += w_j·p(1−p)`、最後に `hess=max(hess, floor)`。
- STAGE_WEIGHTS=(1.0, 0.5, 0.25) モジュール定数(事前登録)。sample_weight は cond_logit 同様 grad/hess に乗算(None は不変)。

### D2: rank ラベル(dataset)
- `dataset.py` に `RANK_LABEL="finish_rank"` 追加: race_results(finished, finish_order≤3)から (race_id,horse_id)→rank、他 0。win と同じ label 側・feature_cols 不変(=feature_hash 不変)。

### D3: WinModel / predictor の結線
- `WinModel(objective="pl_topk")`: fit は group_ids 必須 + `ranks`(X 行順)必須。stable sort で X/y/ranks/group を同期 → pl_topk_objective(gsizes, ranks_sorted)。predict は **cond_logit と同一 softmax 分岐**(objective∈{cond_logit,pl_topk})。
- predictor.fit: objective=pl_topk 時 model_df[RANK_LABEL] を ranks に。calib 予測・predict_race は cond_logit 経路をそのまま共有(ranks 不要)。fit_info_ postprocess="group_softmax"。
- HPO×pl_topk は非対応(cond_logit 同様 NotImplementedError)。

### D4: serving / artifacts / cli
- serving raw_predict: `objective in ("cond_logit", "pl_topk")` → race softmax(1レース=1 group)。
- artifacts: objective/postprocess 記録は既存機構が透過(fit_info_ 経由)。
- cli: `--objective` choices に pl_topk(model-eval / train-evaluate)。model-eval baseline は binary のままだが 042 の採否は inline 比較(baseline=cond_logit+TE+isotonic vs candidate=pl_topk+TE+{isotonic,none})で行う(039 の校正 A/B と同型)。

### D5: 採用ゲート
- 18-fold expanding OOS(evaluate_feature_adoption、candidate/baseline とも最終 postprocess 後の確率)。PRIMARY win LogLoss+ECE 非悪化+fold ガード。SECONDARY: winner-NLL/top1/AUC/top2/top3。
- 採用: lgbm-042(pl_topk+TE+採用校正+features-012)。不採用: ブランチ保全。

## Project Structure

```text
training/src/horseracing_training/
├── cond_logit.py    # STAGE_WEIGHTS + pl_topk_objective 追加
├── dataset.py       # RANK_LABEL 追加(label 側)
├── win_model.py     # objective=pl_topk 分岐(fit ranks / predict は cond_logit 共有)
├── predictor.py     # ranks 引き回し + HPO ガード
└── cli.py           # --objective pl_topk

serving/src/horseracing_serving/model_loader.py  # softmax 分岐を {cond_logit, pl_topk} に
training/tests/unit/  # pl_topk 単体(stage 勾配/中断規則)・後方互換・leak
serving/tests/unit/   # pl_topk raw_predict softmax
```

## 実装フェーズ概要
1. cond_logit.py: pl_topk_objective + 単体テスト(stage 勾配手計算・中断規則・中立化・weight)
2. dataset: RANK_LABEL + 非特徴テスト
3. win_model/predictor: 結線 + 後方互換テスト
4. serving/cli: 分岐拡張
5. 18-fold OOS(A/B)→ 採否 → lgbm-042 or 保全
6. Polish: lint/テスト・CLAUDE.md・memory・codex 反映確認
