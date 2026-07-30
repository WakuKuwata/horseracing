結論は次のとおりです。

- pseudo-R² は Bolton–Chapman / McFadden の  
  \[
  R^2=1-\frac{LL_{\text{model}}}{LL_{\text{uniform}}}
  =1-\frac{\sum_r-\log p_{r,w_r}}{\sum_r\log N_r}
  \]
  で正しいです。null はレースごとの一様確率 \(1/N_r\) です。
- ただし採否に使うべき中心指標は、原典どおりの `combined − raw market` だけでなく、`combined − market-only recalibrated` です。後者でないと、市場 \(q\) 自身の power 補正をモデル \(p\) の寄与と誤認します。
- 現在の 2019–2026 データは既に分析済みなので、歴史的評価は prequential、正式な採否証拠は将来の固定 prospective holdout、と役割分担すべきです。
- ΔR² は winner NLL の代替主指標にはせず、「市場に対する増分情報」の別軸ゲートに置くのが製品目的に合います。

## 1. 推奨定義

レース \(r\)、最終的な started field \(S_r\)、頭数 \(N_r=|S_r|\)、単独勝者 \(w_r\) とします。正の損失を

\[
D(s)=\sum_{r\in\mathcal E}-\log s_{r,w_r}
\]

とし、一様 null は

\[
D_0=\sum_{r\in\mathcal E}-\log(1/N_r)
   =\sum_{r\in\mathcal E}\log N_r
\]

です。したがって

\[
R^2(s)=1-\frac{D(s)}{D_0}.
\]

これは Bolton–Chapman が McFadden の likelihood-ratio index として示し、Benter がそのまま利用した定義です。Cox–Snell や Nagelkerke ではありません。[Bolton–Chapman (1986)](https://gwern.net/doc/statistics/decision/1986-bolton.pdf)、[Benter (1994)](https://gwern.net/doc/statistics/decision/1994-benter.pdf)

重要な点は以下です。

- \(D_0\) は必ず `Σ log(N_r)`。
- `log(mean N)` ではない。
- 各レースの \(R^2_r\) を計算して平均してもいけない。
- 全モデルを完全に同一の eligible race set で計算する。
- OOS では一様より悪ければ \(R^2<0\) になり得る。Benter の「0〜1」は、null を含む in-sample MLE に近い説明です。

既存コードも一様 winner NLL を `mean(log N_r)` としており、この部分は正しいです。[metrics.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/metrics.py:117)

### Benter の二段目

\[
c_{ri}(\alpha,\beta)=
\frac{\exp(\alpha\log p_{ri}+\beta\log q_{ri})}
{\sum_{j\in S_r}\exp(\alpha\log p_{rj}+\beta\log q_{rj})}
=
\frac{p_{ri}^{\alpha}q_{ri}^{\beta}}
{\sum_j p_{rj}^{\alpha}q_{rj}^{\beta}}.
\]

Benter は fundamental probability が別サンプルからの OOS 予測であることも明記しています。[Benter (1994)](https://gwern.net/doc/statistics/decision/1994-benter.pdf)

原典忠実な指標は

\[
\Delta R^2_{\text{literal}}
=R^2(c)-R^2(q)
=\frac{D(q)-D(c)}{D_0}.
\]

ただし、この定義では \(\beta\neq1\) による市場自身の再較正も「fundamental model の寄与」に入ります。そこで正式レポートでは次の reduced model も必須にすべきです。

\[
m_{ri}(\gamma)=
\frac{q_{ri}^{\gamma}}{\sum_j q_{rj}^{\gamma}}
\]

\[
\boxed{
\Delta R^2_{p\mid q}
=R^2(c)-R^2(m)
=\frac{D(m)-D(c)}{D_0}
}
\]

これが「市場だけを最適に再較正した後にも、モデル \(p\) が情報を足すか」を直接測ります。full model は \(\alpha=0,\beta=\gamma\) で reduced model を含むので、こちらも in-sample では構造的に非負です。

推奨出力は最低でも次の5つです。

- \(R^2_F=R^2(p)\): supplied model probability 単独
- \(R^2_P=R^2(q)\): raw devig market
- \(R^2_{P,\mathrm{cal}}=R^2(m)\): market-only power calibration
- \(R^2_C=R^2(c)\): full combined
- `delta_r2_literal` と `delta_r2_model_given_market`

### 報告値との整合性

Benter の表は 3,198レース、32,877頭なので、平均頭数は約10.28頭です。平均14頭ではありません。14頭は論文中の例示レースです。

正確な \(\operatorname{mean}\log N_r\) は表だけでは復元できませんが、仮に \(\log(10.28)=2.330\) を使うと、

- public NLL ≈ \((1-0.1218)\times2.330=2.046\)
- combined NLL ≈ \((1-0.1396)\times2.330=2.005\)
- NLL 改善 ≈ \(0.0178\times2.330=0.0415\)

となり、報告値は十分整合的です。

現在の bundle は全26,049レースの平均頭数13.745、`mean(log N)=2.59558` でした。提示された NLL を同じ eligible set と仮定した概算では、

- \(R^2_F \approx 0.20250\)
- \(R^2_P \approx 0.25538\)
- \(R^2_C \approx 0.25548\)
- literal ΔR² ≈ \(0.00027/2.59558=0.000104\)
- market power 補正後のモデル固有分 ≈ \(0.00001/2.59558=0.0000039\)

です。後者は丸め誤差水準なので、現在の in-sample 結果は実質的に `ΔR²_model_given_market ≈ 0` と見るべきです。なお正式値は dead heat 等を除いた共通 eligible set の \(D_0\) で再計算する必要があります。

## 2. α・β のフィット手続き

### 歴史的評価

現在の OOF bundle には2019〜2026の年次 fold があるため、まず次を primary historical estimator にするのが自然です。

- 2019: 二段目係数の初期 fit 専用
- 2020: 2019だけで \((\alpha,\beta)\) と \(\gamma\) を fit
- 2021: 2019–2020で fit
- …
- 2026: 2019–2025で fit
- 評価値は2020–2026だけを集計

同日の途中までの結果を使わないよう、切断単位は必ず開催日です。年次更新でも内部条件は

\[
\max(\text{fit race date}) < \min(\text{held-out block date})
\]

とします。既存の `residual_probe.py` には、これとほぼ同型の strictly-earlier prequential fit が既にあります。[residual_probe.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/residual_probe.py:140)

最適化対象は凸です。

\[
\mathcal L_T(\alpha,\beta)
=\sum_{r\in T}
\left[
\log\sum_{j\in S_r}
 \exp(\alpha\log p_{rj}+\beta\log q_{rj})
-\alpha\log p_{r,w_r}
-\beta\log q_{r,w_r}
\right].
\]

推奨条件は次です。

- intercept は不要。レース内 softmax で消える。
- primary は無正則化 MLE。
- \((0,1)\) から開始。
- 数値実装上は `logsumexp` を使う。
- α、βは原則 unconstrained。広い数値境界に当たった場合は fit failure として記録する。
- Hessian condition、gradient norm、反復数、収束状態、fit through date を保存する。
- p と q がほぼ同じ場合、係数自体は識別不良でも combined probability は安定し得る。係数の大きさではなく OOS loss を判定対象にする。

### 正式な採否

現在の2019–2026は既に ROI、NLL、最適重みを見た後なので、新たに手続きを固定しても confirmatory holdout には戻りません。

正式な採否には次を推奨します。

1. cutoff \(T_0\) までの全 strict-past OOF で \((\alpha,\beta,\gamma)\) を固定。
2. \(T_0\) より後の未閲覧期間では係数を更新しない。
3. 期間または必要開催日数を事前の power 設計で固定。
4. 終了前に結果を見て打ち切らない。

つまり、prequential は歴史的推定・運用監視、固定 prospective holdout は採否確認です。

## 3. CI と判定

開催日クラスタ bootstrap は基本方針として妥当です。ただし既存 helper をそのまま ΔR² に使うのは厳密には誤りです。

ΔR² は平均差ではなく比率なので、bootstrap replicate \(b\) ごとに

\[
\Delta R^{2(b)}
=
\frac{
\sum_{r\in b}\left(\ell_{P,r}-\ell_{C,r}\right)
}{
\sum_{r\in b}\log N_r
}
\]

を再計算する必要があります。`mean(per-race ΔR²)` や、元データの固定 \(D_0\) で全 replicate を割る方法は避けるべきです。

したがって既存の開催日 resampling ロジックは再利用できますが、`bootstrap.py` に ratio statistic 対応 helper を追加する設計がよいです。現在の helper は各 replicate で単純な平均を計算しています。[bootstrap.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/bootstrap.py:38)

推奨設定は次です。

- primary: 開催日クラスタ percentile CI
- gate 用は 10,000〜20,000 replicate、固定 seed
- two-sided 95% CI
- 2日、3日、4日、週、開催単位を感度分析
- bootstrap 中に α、βを無秩序に再fitしない
- 「過去データから学習済みの prequential policy に条件づけた OOS loss CI」であることを明記

判定は、効果の有無と実用性を分けます。

- `evidence_positive`: CI lower \(>0\)
- `material_positive`: CI lower \(>\delta_{\min}\)
- `harmful`: CI upper \(<0\)
- その他: `NO_DECISION`

Benter の 0.0178 は閾値にすべきではありません。市場、控除率、平均頭数、モデル開発過程、in-sample/OOS 条件が違い、Benter 自身も収益との関係を経験的 heuristic として述べています。参考線として出力するのはよいですが、採否閾値ではありません。

\(\delta_{\min}\) は

\[
\delta_{\min}
=
\frac{\text{最小限意味のある winner NLL 改善}}{\operatorname{mean}(\log N_r)}
\]

または prospective policy simulation / 製品価値から事前に決めるべきです。根拠をまだ置けないなら、初版は \(\delta_{\min}=0\) の evidence-only instrument とし、hard gate 化しないのが安全です。

## 4. 主な落とし穴

### q と母集団

\[
q_{ri}=\frac{1/O_{ri}}{\sum_{j\in S_r}1/O_{rj}}
\]

でよいです。ただし全 started 馬に有限・正の odds があるレースだけを使います。

現在の `_market_q` は欠損馬を除いた部分集合で再正規化するため、この用途には使えません。[market_edge.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/market_edge.py:27) `MarketBaseline` も欠損 odds を floor 補完するので不適切です。[baselines.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/baselines.py:81)

一方、`market_offset.q_from_odds` と `segment_attrs.sql` は完全 field を要求する設計で、こちらが望ましい契約です。[market_offset.py](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/market_offset.py:45) [segment_attrs.sql](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/segment_attrs.sql:15)

ルールは以下です。

- p、q、winner label の horse ID 集合が完全一致。
- 一頭だけ落として再正規化しない。
- cancelled/excluded は final retrospective 評価では双方から除外。
- prediction mismatch は race exclusion ではなく原則 run failure。
- market missing はレース単位 exclusion とし、理由別 ledger を残す。
- prospective では「判断時点の出走集合」を別途固定し、後日の取消を final started で遡及修正しない。

### closing odds の時間的意味

現在の `race_horses.odds` は結果確定・closing-leaning な参照線です。既存 README も serving 入力とは明確に分離しています。[eval/README.md](/Users/kuwatawaku/workspace/horseracing/eval/README.md:10)

したがって現段階の ΔR² は、

> closing market に対する retrospective information increment

です。判断時点の市場を超える能力ではありません。将来 hard gate にする前に、判断時刻と一致する odds snapshot が必要です。

### log(0) と極端値

- p、q をまず同一 field で正規化。
- 非有限、負値、1超過は fail closed。
- 0だけは事前固定した \(\varepsilon\)、例えば `1e-15` に floor して再正規化。
- standalone、reduced、combined の全計算で同じ前処理を使う。
- floor された件数・最小値を出力。
- `1e-12` と `1e-15` の感度分析を diagnostic として持つ。

### 同着・勝者不在

既存 `population_masks` と同じく、単独勝者がないレース、partial ingest はレース全体を除外します。[dataset.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/dataset.py:65)

除外後の共通母集団で \(N_r\)、全 R²、CI を再計算します。

### isotonic p

Benter の \(f\) は「OOS fundamental probability」の log であり、別の isotonic 前 score ではありません。したがって製品・採用対象の実確率を測るなら、074 bundle の post-internal-isotonic p を使うのが正しいです。現在の bundle は「model-internal calibrated、pre-two-gamma」と明示されています。[segment_accuracy_run.py](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/segment_accuracy_run.py:263)

ただし isotonic は非可逆で、単純な power scaling と違って二段目 αだけでは元の順位解像度を回復できません。必要なら pre-calibration OOF score を別診断として出してよいですが、primary と混ぜず probability stage を明記すべきです。

### subgroup

ΔR² は race-grain 指標です。既存の `nk` / `2026_nk` は horse-level started-all LogLoss subgroup なので、そのまま ΔR² subgroup に転用できません。

利用可能なのは結果非依存な race subgroup、例えば以下です。

- `2026_only`
- `field_has_nk`
- `2026_field_has_nk`

「勝者が `nk:` のレース」で切るのは outcome-conditioned selection なので禁止です。subgroup ごとに α、βを再fitせず、全体の prior fit をそのまま適用して subgroup loss だけ集計します。

## 5. winner NLL との関係とゲート位置

ΔR² の正規化部分自体には新しい情報はありません。

\[
\Delta R^2=\frac{\Delta\mathrm{NLL}}{\operatorname{mean}\log N}
\]

だからです。新しい意味を持つのは、「比較相手が active model ではなく、market-only reduced model であること」と「二段目を OOS fit すること」です。

- 既存 paired winner NLL: candidate が active より正確か
- conditional ΔR²: candidate が市場にない情報を持つか

将来 candidate と active を比較する場合は、各モデルの市場増分を別々に求めるだけでなく、

\[
I(p;q)=R^2(C(p,q))-R^2(P_{\mathrm{cal}})
\]

\[
I(p_{\mathrm{cand}};q)-I(p_{\mathrm{active}};q)
=
\frac{D(C_{\mathrm{active}})-D(C_{\mathrm{cand}})}{D_0}
\]

も直接 paired bootstrap すべきです。candidate の \(I>0\) だけでは、active より市場増分が大きいとは限りません。

製品目的が「正直な意思決定支援」なら、推奨位置づけは次です。

- winner NLL + calibration + top2/top3 + subgroup: 一般的なモデル採用の主ゲート
- ΔR²: `market complementarity` の独立ゲート／ラベル
- betting・EV・市場超過を目的とした変更: ΔR² を必須条件にできる
- 一般的な確率表示・説明・早期予測改善: ΔR²≈0だけでは拒否しない

したがって、現ドキュメントの「ΔR² が動かない特徴は LogLoss を下げても採らない」という強い一般化は、製品目的に対して過剰です。[accuracy-roi-decoupling-investigation.md](/Users/kuwatawaku/workspace/horseracing/docs/plan/accuracy-roi-decoupling-investigation.md:429)

## 6. 最小実装設計

提案された配置は妥当です。

### `eval/src/horseracing_eval/delta_r2.py`

pure / predictor-agnostic に限定します。

- `DeltaR2Race`
  - race_id、day、fold、winner_idx、p、q
- probability validation / normalization
- `fit_market_power`
- `fit_combined`
- prequential scoring
- aggregate R²
- literal / conditional ΔR²
- candidate-vs-active ΔΔR²
- fit diagnostics
- subgroup score aggregation

### `eval/src/horseracing_eval/bootstrap.py`

既存 day resampling を一般化した

- `race_day_cluster_ratio_bootstrap_ci_v1`

を追加するのがよいです。numerator と denominator を日別に受け、replicate ごとに ratio を再計算します。

### `training/src/horseracing_training/delta_r2_run.py`

- OOF bundle の checksum/digest 検証
- DB の started field、winner、odds との join
- full-field q 作成
- exclusion ledger
- fold構築
- provenance / snapshot hash
- JSON artifact 出力

OOF bundle は予測と fold 証跡だけを持ち、勝者・開催日・odds は含まないため、この orchestration は training 側が自然です。[oof_bundle.py](/Users/kuwatawaku/workspace/horseracing/probability/src/horseracing_probability/oof_bundle.py:100)

保存すべき provenance は最低限、

- bundle / attestation / prediction checksum
- scored race-set hash
- p stage
- q source、temporal class
- sorted odds/q snapshot hash
- winner label snapshot hash
- started-field hash
- fold schedule
- gate-config hash
- code SHA
- floor、optimizer、bootstrap 設定

です。

### training CLI

例としては以下の責務で十分です。

```text
delta-r2-eval
  --bundle
  --active-bundle optional
  --from
  --to
  --fit-schedule annual-expanding
  --gate-config
  --bootstrap-b
  --seed
  --json
  --database-url
```

`paired.py` は OOF bundle ではなく recipe の再学習ハーネスなので、直接呼ぶより population/hash/思想だけを再利用すべきです。

## 7. 事前登録すべき項目

- literal ΔR² と conditional ΔR² のどちらを primary にするか
- candidate-vs-market か candidate-vs-active increment か
- p の probability stage
- q の odds source と観測時刻
- started field・取消の定義
- missing odds / prediction mismatch / dead heat の扱い
- common eligible population
- epsilon と再正規化順序
- fit schedule、warm-up fold、更新頻度、same-day exclusion
- reduced/full model、係数制約、正則化、収束失敗時処理
- eval window と first-fold exclusion
- ratio bootstrap の block、B、seed、alpha
- primary CI と感度分析
- \(\delta_{\min}\) と判定規則
- subgroup と grain
- 最低開催日数・最低レース数
- prospective holdout の終了条件
- snapshot/config/code hash
- 歴史的結果を screening と表示すること

## 8. 必要なテスト

最低限、次が必要です。

- 手計算できる可変頭数レースで `Σlog N` を検証
- 一様予測で \(R^2=0\)、完全予測で \(R^2\to1\)
- OOS の悪い予測で \(R^2<0\)
- combined が in-sample で raw q と reduced q-only を悪化させない
- p=q のとき conditional ΔR²=0
- p がレース内一様のとき conditional ΔR²=0
- synthetic data から既知 α、βを回収
- distribution shift で OOS ΔR²が負になれる
- first fold が評価から除外される
- same-day label を fit に使わない
- held-out label変更で当該 fold の係数が変わらない
- partial odds でレース全体を除外
- cancelled horse、prediction key mismatch、winner missing
- dead heat / no winner / partial ingest の ledger
- p=0、極小 p/q、tie、logsumexp の有限性
- optimizer の決定性・非収束・識別不良・境界到達
- ratio bootstrap が replicate ごとに denominator を再計算
- 同一 seed で bit-identical CI
- candidate=active で ΔΔR²=0
- subgroup が winner identity に依存しない
- bundle、q、label、config hash の改変検知
- 現 bundle を使った acceptance check で既報 NLL・母集団件数を再現

設計レビューのみ行い、コードやファイルは編集していません。
