# Research: Conditional-logit (race-softmax) 目的関数 (039)

## R1: 目的関数の選択 — conditional-logit vs lambdarank vs binary

**Decision**: conditional-logit(レース内 softmax、損失 −log p_winner = Plackett-Luce の top-1)を採用。lambdarank は棄却。

**Rationale**: de-risk spike(2019+ 実データ 3 fold、同一特徴・同一 TE jockey/trainer smoothing50):

| 目的関数 | winner-NLL | top1 | AUC |
|---|---|---|---|
| binary(現行) | 2.1160 | 0.2764 | 0.7839 |
| conditional-logit | **2.1066** | **0.2799** | **0.7948** |
| lambdarank | 2.1371 | 0.2736 | 0.7911 |

cond_logit は winner-NLL/top1/AUC すべてで binary を上回り、fold 別も 3/3 で改善。lambdarank は AUC こそ binary 超えだが winner-NLL で負け = ranking(NDCG)最適化が確率校正とズレる典型(codex 事前警告的中)。確率を出す製品なので採るのは cond_logit。

**Alternatives considered**: 完全 Plackett-Luce(top-k 順序全体の listwise)は勾配が重く、まず top-1 で構造利得を確認(deferred)。rank_xendcg も ranking 系で lambdarank 同様の校正ズレが予想され不採用。

## R2: 校正の当て方(最大論点)

**Decision**: 採用評価で **(a) softmax-only(校正なし)** と **(b) softmax→isotonic→009 再正規化** を 18-fold OOS で両方測り、win LogLoss/ECE の良い方を採る。(b) 採用時は metadata に **heuristic**(厳密 isotonic でなく校正風の再配分)と明記。

**Rationale(codex 是正)**: softmax 出力は既に Σ=1 の条件付き確率。per-horse isotonic は各馬を独立に単調変換するため変換後は一般に Σ≠1 で、009 再正規化後は「厳密な isotonic 校正済み確率」でなく校正風の再配分になる。binary(独立確率→009 正規化)とは校正の意味が異なる。よって「isotonic を当てる/当てない」を決め打ちせず OOS で比較する(数値を見てから閾値は動かさないが、事前登録した2経路の良い方を採るのは選択リークでない=どちらも同一 fold・同一基準で評価)。009 の最終再正規化(clip→Σ=1)は binary でも常に行う後段で両者共通。

**Alternatives considered**: Σ=1 を保つ **temperature/power scaling(race-aware calibration)** — 実装は raw softmax の温度 T を winner-NLL で train-only 最適化。infra 追加が要るため本 feature では deferred(まず既存 isotonic infra で 18-fold を通すか確認)。レース内 Dirichlet 校正も複雑で deferred。

## R3: group と OOF TE の相互作用(リーク面)

**Decision**: OOF TE は 036 のまま(model 行で chronological fold OOF、final encoder は model-fit)。cond_logit の group(race_id 整列)は TE と独立に適用。

**Rationale**: TE のリーク安全は「行が自分のラベルで encode されない(OOF)」に依存し、group 整列は行の順序を変えるだけで OOF fold 割当・encoder fit 集合を変えない。group は race_id のみ依存で結果非参照。よってリーク面は増えない。leak-guard test で「今走結果変更 → 他馬予測不変」「group が finish_order を読まない」を担保。

## R4: hessian の対角近似

**Decision**: `hess = max(p*(1−p), 1e-6)`(multinomial の対角近似)。

**Rationale**: LightGBM の custom objective は per-sample の (grad, hess) を要求し、full hessian(group 内 −p_i p_j 非対角)は扱えない。多クラス softmax の標準実装(LightGBM 内蔵 multiclass も)も対角 p(1−p) を使う。newton step は対角近似で安定(実 spike で 300 boost 収束・binary 超え確認済)。eps 下限で 0 割回避。

**Alternatives considered**: full hessian は LightGBM の API 外。定数 hess=1 は収束遅く不採用。

## R5: エッジケース(勝ち馬不在・同着・1頭立て)

**Decision**: y 和 != 1 の group は grad/hess=0(学習から中立化)。1頭立ては softmax 自明で自然処理。評価 winner-NLL は「勝ち馬ちょうど1頭」レース限定(spike 同基準)。

**Rationale**: 勝ち馬不在(全 DNF)や同着は損失定義(top-1)の前提外。中立化で学習を汚さず止めない。同着は実データで稀。

## R6: 後方互換

**Decision**: objective 既定 = binary。未指定時は現行 WinModel と bit 一致(既存モデル・テスト透過)。cond_logit は opt-in(cli `--objective cond_logit`)。

**Rationale**: lgbm-036 の予測不変を保証(憲法 III/V)。採用されるまで main は lgbm-036/features-011 のまま。

## R7: serving

**Decision**: serving predict_race は常に1レース単位 → cond_logit の raw_predict = softmax(booster.raw_score(X)) over X(=そのレース)。後段(calibrator→009)不変。model_loader に objective 記録。

**Rationale**: serving が1レースずつ処理する既存構造が cond_logit の group と自然に一致(X 全行 = 1 group)。feature 列不変 = feature_hash 整合。
