# Research: 評価ハーネスと baseline

Phase 0 調査。NEEDS CLARIFICATION (窓スキーム・許容誤差) と評価設計を確定する。

## R1. walk-forward 窓スキーム

- **Decision**: **expanding-window train + 年次 valid**。valid 年 Y の train = `race_date < Y-01-01` の全
  レース、valid = 暦年 Y。2007 は初期 train 専用、評価 (valid) は 2008 年から年単位で進める。fold ごとの
  集計 (将来の累積統計) は train 期間のみで行うことを契約とする。
- **Rationale**: 全履歴を使う時系列標準。実装間で窓が揺れず比較が安定 (codex 指摘)。baseline は状態なし
  だが、同じ fold 構造を将来モデルが使うため今固定する。
- **Alternatives considered**: sliding (固定長 train) → データ量減・N 依存で結果が動く。ランダム CV →
  憲法 II/III に反するため禁止。

## R2. Predictor 抽象

- **Decision**: 最小 Protocol。`fit(train_races)` (baseline は no-op) と
  `predict_race(race) -> {horse_id: (win, top2, top3)}`。母集団は started 馬 (取消・除外を除く)。
- **Rationale**: LightGBM・校正器・baseline を同一契約で評価。feature 生成・永続化は持たせない (責務分離)。
- **Alternatives considered**: バッチ predict(all) → レース単位の整合性検証がしにくい。

## R3. 予測品質指標の実装

- **Decision**: LogLoss・Brier・AUC・NDCG は scikit-learn (`log_loss`, `brier_score_loss`,
  `roc_auc_score`, `ndcg_score`) を使用。ECE は自前実装。すべて label (win/top2/top3) 別に算出。
- **Rationale**: 標準指標は実績ある実装で誤り低減・決定論的。ECE は競馬固有 (頭数別・母集団除外) のため自前。
- **Alternatives considered**: 全自前 → 検証コスト増。

## R4. ECE (校正) の定義

- **Decision**: label 別に、予測確率を bin (既定 10、等幅、設定可能) に分け、各 bin の平均予測確率と実測
  頻度の差の加重平均を ECE とする。母集団は finished のみ (非完走・非出走を除外、R8)。全体 ECE に加え
  **頭数別の診断 ECE** も算出する (頭数で確率水準が変わるため)。
- **Rationale**: 標準 ECE 定義 + 競馬特有の頭数依存を可視化 (codex 指摘)。
- **Alternatives considered**: 等頻度 bin → 実装可だが既定は等幅。adaptive は将来拡張。

## R5. 確率整合性の許容誤差

- **Decision**: 各馬 `0<=win<=top2<=top3<=1` は厳格 (違反即 fail)。レース内合計は label 別の**設定可能な
  絶対誤差**で、既定 `|Σwin-1|<=0.05` / `|Σtop2-2|<=0.10` / `|Σtop3-3|<=0.15`。超過は fail-fast。
- **Rationale**: 正規化で Σwin はほぼ 1、Harville の top2/top3 は理論上 2/3 に一致するが数値・少頭数で
  ずれるため label ごとに緩める。違反を黙って通さない (憲法 IV)。
- **Alternatives considered**: 厳格 epsilon (1e-6) → Harville/少頭数で過剰 fail。相対誤差 → 直感性低い。

## R6. 市場 baseline (人気順)

- **Decision**: win = `1/odds` を母集団内で正規化したインプライド確率。top2/top3 は **Harville 式**で win
  から導出 (`P(i∈top2)=p_i+Σ_{j≠i}p_j·p_i/(1-p_j)`、top3 は三重和、N<=18 で O(N^3) 可)。odds が
  null/<=0 の馬は母集団最小の微小ウェイトを割り当ててから正規化 (確率 0 を避ける)。「結果確定時値ゆえ
  リークあり・参照線専用」と明示。
- **Rationale**: 市場効率の標準近似。Harville は win→順位分布の自然な導出で Σtop2≈2/Σtop3≈3 を満たす。
- **Alternatives considered**: popularity 順位を直接確率化 → 情報量が落ちる。odds 無効馬を除外 →
  母集団が baseline ごとに変わり比較不能。

## R7. 一様 baseline

- **Decision**: win=1/N、top2=min(2/N,1)、top3=min(3/N,1) (N=母集団頭数)。単調性 (win<=top2<=top3) と
  Σ (N>=3 で 1/2/3) を満たす。少頭数 (N<3) は cap により Σ が崩れるが診断で許容。
- **Rationale**: truly leak-free な床。市場 baseline がこれを上回ることを妥当性チェックに使う (SC-004)。

## R8. 評価母集団とラベル

- **Decision**: 評価母集団 = 各 valid レースの started 馬 (entry_status から取消・除外を除外、残りで
  再正規化)。ラベルは `labels.derive_labels` (finished のみ、同着は finish_order 共有) に従い、
  win=(finish_order==1)、top2=(<=2)、top3=(<=3)。started だが DNF (stopped/disqualified) は label を
  持たないため採点母集団から除外 (予測は出すが採点しない)。全馬非完走のレースは評価から除外。
- **Rationale**: 憲法・spec の母集団規約に一致。derive_labels を単一の正本に使い二重実装しない。
- **Alternatives considered**: DNF に win=0 を付与 → derive_labels と不整合。

## R9. 評価結果の保存

- **Decision**: MVP は baseline を `model_versions` に 1 行 (`model_family='baseline'`,
  `model_version` 例 `baseline-market-2026...`) で登録し、`metrics_summary` (jsonb) に label 別・全体・
  fold 別サマリ + 評価条件 (窓スキーム・許容誤差・bin 数) を格納。スキーマ変更なし。fold 別の正規化保存
  (`eval_runs`/`walkforward_window_results`) は P2 (非破壊拡張)。
- **Rationale**: 既存契約で比較に十分。検索・多モデル比較 UI が要るまで正規化を遅らせる (codex)。

## R10. 決定論

- **Decision**: 乱数を使わない。sklearn 指標は決定論的。馬の順序は (race_id, horse_id) で安定ソート。
  同一入力・同一分割で完全一致 (SC-006)。
- **Rationale**: 評価の再現性は採用判定の前提 (憲法 V)。
