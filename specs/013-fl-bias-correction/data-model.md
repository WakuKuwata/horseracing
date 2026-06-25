# Data Model: 人気-不人気バイアス補正

スキーマ変更なし。`race_horses.odds`/`race_results` を読み、校正器は artifact + logic_version 相当メタに記録。以下はコード上の
値オブジェクトと不変条件。

## 1. FLCalibrator（値オブジェクト・非永続 / artifact 化）

q → q' の単調写像。市場由来、p 非参照。

| フィールド | 型 | 説明 |
|---|---|---|
| `method` | str | power（正準）/ loglog / isotonic |
| `params` | dict | power: `{gamma: float}`、loglog: 係数、isotonic: knots |
| `train_window` | (date|datetime, date|datetime) | 学習窓（対象レース開始より厳密前） |
| `n_races` / `n_samples` | int | 学習レース数・サンプル数（小帯監査用） |
| `odds_range` | (float, float) | 学習で見た q の範囲（外挿ガード） |
| `logic_version` | str | method/params/window/版を含む再現キー |

- 不変: `method∈{power,loglog,isotonic}`。`g` は**単調**（power は γ>0、isotonic は単調制約）。p を一切参照しない。
- 学習: **正規化後 q'** の勝者尤度を最大化（power は γ の 1 次元 MLE）。方式/ハイパラ選択は学習窓内（最終評価未使用）。

## 2. CorrectedMarketProbs（値オブジェクト・非永続）

レース内で Σ=1 に再正規化した補正済み市場勝率。

| フィールド | 型 | 説明 |
|---|---|---|
| `race_id` | str | 対象レース |
| `q` | dict[str,float] | 生の市場含意勝率（010、Σ=1） |
| `q_prime` | dict[str,float] | 補正済み `q'_i=g(q_i)/Σg(q_j)`（Σ=1、エンジン整合） |
| `field_size` | int | 補正後の有効出走集合サイズ（009/010 の field 規則） |
| `excluded` | list | 取消・除外・無効オッズ（監査） |

- 不変: `Σq'=1`、`q'` は q に対し**単調**（順序保持）、母集団は有効出走馬（取消・除外・無効オッズ除外）。
- **エンジン整合**: `q'` は 009 の正規化 + eps clip と整合させ、エンジン再処理が無作用。**評価も q'(エンジン正規化後)** で行う。

## 3. 補正済み推定オッズ（010 拡張・opt-in）

- `estimate_market_odds(win_odds, *, calibrator=None, ...)`: `calibrator` 指定時は q→q'(補正)→009→`O_est=(1−控除率)/P_market(q')`。
  未指定は生 q（後方互換）。`is_estimated=True`（疑似）。
- **オッズ復元の非保存**: 補正済み推定単勝オッズは生オッズを**厳密復元しない**（バイアス除去の意図）。生 q では復元。

## 4. 校正評価レポート（第一指標・非永続）

| エンティティ | フィールド |
|---|---|
| `QvsQpReport` | scope（overall / 人気帯）, n_races, n_samples, nll_q, brier_q, ece_q, nll_qp, brier_qp, ece_qp, reliability_q(list[(mean_pred,emp_rate,n)]), reliability_qp(同), improved(bool), pseudo=True |

- ECE/信頼性: **正規化後 q'**、**固定既定ビン `DEFAULT_BINS`**（10 等幅）、空ビン n=0 明示、サンプル数併記。人気帯は固定境界。同着は除外し件数明示。
- **採否ゲート**: nll_qp/brier_qp/ece_qp が生 q を改善するか（baseline=補正なし）。

## 5. 乖離比較レポート（補助指標・非永続）

| エンティティ | フィールド |
|---|---|
| `DivergenceDeltaReport` | bet_type, coverage_rate, logratio_median_q, logratio_mae_q, logratio_p90_q, logratio_median_qp, logratio_mae_qp, logratio_p90_qp, baseline="estimated raw q", pseudo=True |

- 012 の `exotic_divergence` を生 q / 補正 q' で 2 回回し比較。**診断のみ**（実 exotic は独自の控除/偏り）。採否は §4 で判断。

## 6. 不変条件まとめ

1. 校正は正規化後 q' を学習・評価対象（再正規化で marginal が変わるため、R1）。
2. q' はエンジン整合で「評価=使用」（R2）。field_size は補正後出走集合から（IV）。
3. 学習は walk-forward 厳密前（< 対象レース開始、race_id タイブレーク）、方式選択も学習窓内（II、R3）。
4. q'・オッズは win モデル特徴に一切使わない（II、リーク・ガードテスト）。
5. p≠q（補正は q のみ、p 非参照）。
6. 採否=勝率校正（NLL/Brier/ECE）、乖離は診断（III、R7）。
7. スキーマ変更なし、方式/窓/版を logic_version/artifact に記録（V）。決定論。
8. closing-odds は retrospective 前提、operational は出走前オッズ（限界開示、R4）。
