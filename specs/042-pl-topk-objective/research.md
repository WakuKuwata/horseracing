# Research: PL top-k 目的関数 (042)

## R1: なぜ top-k が win 予測を改善するか

**Decision**: PL 逐次 top-3(stage 減衰 w=[1.0,0.5,0.25])。

**Rationale**: cond_logit(top-1)は 1 レースあたり 1 bit 相当の順序情報しか使わない。PL 逐次は 2着・3着の相対順序も尤度に入れ、**中位馬のスコア形成**(どの馬が「勝てないがそこそこ強い」か)を教師する → スコア空間全体が滑らかになり winner 確率も改善。spike: winner-NLL −0.0095・top1 +0.0064・AUC +0.0035・全 3 fold(ブレストキュー最大、039 spike と同規模)。lambdarank(NDCG 近似)と違い **PL top-k は確率モデルの尤度そのもの**なので校正と整合的 — codex の事前警告(校正崩し)が spike で非発現だった理由もこれ。18-fold ECE ゲートが最終防衛線。

**Alternatives considered**: 完全 PL(全順序)= 奥の順序はノイズ(不利・流し込み等で 10 着 vs 12 着に情報なし)かつ計算増。k=3 は JRA 複勝圏・009 top3 と整合。margin-aware(着差重み)は校正リスクで deferred。

## R2: stage 減衰重みの正当性

**Decision**: w=[1.0, 0.5, 0.25] 固定(spike の事前選択値)。純 PL は w 均等だが、実データの 2-3 着は 1 着よりノイジー(ゴール前の流し込み・不利)なので減衰が実務的。**OOS を見てから w を調整しない**(憲法 III)。均等 w の併測は SECONDARY 診断のみ(採否には使わない)。

## R3: rank ラベルのリーク境界

**Decision**: `finish_rank`(1..3/0)を dataset の label 側に追加。win ラベルと同一機構(race_results finished の確定着順)・model_input_features 外・feature_hash 不変。

**Rationale**: 「学習行の自レース結果を損失に使う」のは win ラベルと完全に同じ既存境界。予測時は一切使わない(leak-guard: 今走結果変更で予測不変)。

## R4: hess の stage 加算

**Decision**: `hess = max(Σ_j w_j·p_j(1−p_j), floor)`(対角近似の和)。

**Rationale**: 各 stage の PL 項は独立の softmax NLL で、対角 Fisher の加重和は正定・LightGBM の Newton step と整合。stage が深い馬ほど hess が厚くなる(複数 stage に参加)= 学習が安定方向。spike(300 rounds)で発散なし。

## R5: 予測経路の共有(039 最大リスクの回避)

**Decision**: pl_topk の予測は cond_logit と **同一コード経路**(raw_score→race softmax→校正→009)。分岐は `objective in {"cond_logit","pl_topk"}` の集合判定のみ。

**Rationale**: 039 の教訓「predict の意味が objective で変わる→全経路 postprocess 一致」。pl_topk の出力スコアも「レース内相対強さ」で softmax の意味は同一(PL 尤度の stage1 がまさに win 確率)。経路を増やさないことで eval/calib/serving のズレを構造的に排除。

## R6: codex second opinion(反映済)

- **中立化**: grad=0/hess=floor(hess=0 回避)→ 実装済みと一致(最終 `hess=max(Σ, floor)`)。
- **w=[1,1,1] 純 PL の diagnostic 併測**(採用ゲインが「top-k 情報」か「手重み」か分離)→ 採用。**事前固定: 採否候補は w=[1,.5,.25] のみ**、均等 w は 3-fold diagnostic(採否に使わない)。
- **rank ラベル仕様**: result_status=finished + 公式 finish_order≤3 → 1..3、他 0。同着(rank 非一意)は objective 側で stage break/中立化。**結果未確定レースは全 rank=0 → stage1 非一意 → group 中立化**(win ラベルの扱いと一貫、リーク面ゼロ)。rank は objective closure 経由で渡し特徴列に混ぜない。
- **top2/top3 no-regression をゲートに昇格**(事前登録): 採用条件に「lgbm-041 の top2 LogLoss 0.34352 / top3 0.43533 比で非悪化」を追加(Harville 派生は win vector 品質に依存するため)。
- **校正リスク**: spike は ECE 未提示 → 18-fold isotonic/none A/B の ECE ゲートが最終判定(高確率帯 reliability も確認)。


## R7: 採用結果(2026-07-02 確定)

- **18-fold OOS(baseline=cond_logit+TE+isotonic=lgbm-041 相当)= ADOPTED 機械通過**:
  pl_topk+isotonic が win LogLoss 0.21792→**0.21706**(−0.00085、039 の −0.00057 超え=036 以来最大)・
  AUC 0.79091→**0.79340**・**ECE 0.00093→0.00058(改善)**・13/18 fold・worst_dLL +0.00041・worst_dECE +0.00093(全ガード内)。
- **校正 A/B**: none は ECE 0.00427(4.6 倍悪化)→ **isotonic 採用**(039 と同結論)。
- **codex 昇格ゲート(top2/top3 non-regression)= 改善で通過**: top2 0.34352→**0.34156**・top3 0.43533→**0.43220**(2-3着教師が Harville 派生を直接改善、codex 予想どおり)。
- **codex の校正懸念(spike は ECE 未提示)は 18-fold で非発現** — PL 尤度は確率モデルそのもの(R1)が実証された。
- lgbm-042(pl_topk+TE+isotonic+features-012)active 昇格・lgbm-041 retired・serving objective=pl_topk ロード確認。

## R8: 均等 w 純 PL diagnostic(codex 要請のゲイン源分離、2022+ 3fold、採否に不使用)

- decayed w=(1,.5,.25): cand LogLoss 0.22132・AUC 0.79533(採用構成)
- uniform w=(1,1,1): cand LogLoss 0.22172・AUC 0.79364(baseline 0.22295)

**両構成とも baseline に明確に勝つ = ゲインの主因は top-k 順序情報そのもの**(手重みの産物ではない)。減衰はさらに LogLoss −0.0004/AUC +0.0017 を上乗せ(2-3着ノイズ抑制が有効)= 事前登録 w=(1,.5,.25) の妥当性を確認。
