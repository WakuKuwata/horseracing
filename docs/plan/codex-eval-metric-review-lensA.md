## 結論

現 PRIMARY は「壊れている」のではなく、**勝確率という目的には正しいが、局所効果の採用には狭すぎる**物差しです。

started-all LogLoss や全順位指標への単純な置換で検出力が上がる根拠はありません。最もシンプルで有効な改善は、winner NLL を全体指標として維持しつつ、**事前登録した対象レースでの winner NLL 優越性＋全体非劣性**という targeted track を追加することです。

ファイル変更はしていません。

## (a) 現 PRIMARY 定義の穴

1. **高分散性は実在する**

winner NLL は低確率の勝者で大きな損失が出るため裾が重く、局所的な改善は全体平均で希釈されます。実装も勝者確率だけから race-level 差を作り、開催日 bootstrap しています。[paired.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/paired.py:318)

ただし「非勝者を使わないから統計情報を捨てている」は半分だけ正しいです。`-log p_winner` は一勝者カテゴリ分布に対する厳密に proper な listwise log score です。レース内合計が1なら、非勝者への確率配分は `p_winner` を通じて間接的に効いています。非勝者の着順を見ないのは、目的が「勝確率」であって「全着順」ではないためです。[metrics.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/metrics.py:101)

2. **started-all の馬数は独立標本数ではない**

現 started-all は全馬を平坦化した micro-average で、頭数の多いレースほど重くなります。[paired.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/paired.py:116) [metrics.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/metrics.py:129)

保存済み081 OOF cacheを同じ開催日 bootstrap で再採点した結果：

- 平均頭数は13.75頭。
- started-all micro LogLoss の見かけのSEは NLLの **8.4–9.0%**まで縮小。
- しかし効果量もほぼ同率で縮むため、標準化検出力は概ね **0.90–1.09倍**。実質改善なし。
- レースごとに全馬binary lossを合計してから等重み平均すると、SEは逆に NLLの **1.16–1.24倍**。

つまり「13頭あるから約√13倍強くなる」ことはありません。同じ一勝者ラベルを13行に展開しただけです。

3. **本当の穴は局所効果の全体希釈**

現 subgroup は2026/nk等に対する非劣性安全ガードであり、特徴仮説の対象セグメントにおける優越性検定ではありません。[gate-config.json](/Users/kuwatawaku/workspace/horseracing/specs/073-eval-contract-correctness/gate-config.json:7) [subgroups.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/subgroups.py:65)

したがって「牝馬×季節」のような局所仮説は、正しくても全体平均で薄まります。これは指標の誤りではなく、**estimandが全体平均しかないこと**の不足です。

## (b)(c) シンプルな代替定義

### 案1・推奨：事前登録 targeted winner-NLL track

全体の canonical PRIMARY は現 winner NLL のまま維持します。局所特徴だけ、結果非依存の適用マスク `A_r` を事前登録し、

\[
\Delta_T=
\frac{\sum_r A_r\{
-\log p^{cand}_{w,r}+\log p^{active}_{w,r}
\}}{\sum_r A_r}
\]

を対象セグメントの confirmatory 指標にします。

採用条件は例えば以下です。

- 対象 `ΔT` のCI上限 `< 0`。
- 全体 winner NLL の点推定 `≤ 0`。
- 全体CI上限 `< m_global` の非劣性。
- 必要なら対象外レースも非劣性。
- 現行top2/top3・校正ガードは維持。

マスクは「勝者が牝馬」ではなく、「結果を見る前に牝馬対象馬が存在し、特徴にレース内変動がある」等にします。

セグメント比率を `f` とすると、局所効果の検出効率は概算で全体集約より `1/√f` 改善します。

- `f=10%`：約3.2倍。局所効果MDEは、全体集約なら0.040–0.060、targetedなら約0.013–0.019。
- `f=25%`：約2倍。0.016–0.024から約0.008–0.012。

開催日の偏りやクラスタ相関で変動しますが、metric置換より効く可能性が高いです。

重要な代償は、全体CI `<0` を必須から外して非劣性にする点です。全体優越性を引き続き必須にすれば、局所希釈は解消できません。

### 案2：race-level multiclass Brier

全馬ベクトルを使う、 bounded な proper scoreです。

\[
B_r=\sum_i (p_{i,r}-1[i=w_r])^2,\qquad
PRIMARY=\operatorname{mean}_r B_r
\]

一レース一ベクトル・一標本を保ちながら、非勝者確率も明示的に評価できます。

081 OOF再採点では、生のSEは winner NLLの **28–35%**まで縮みました。ただし尺度と信号も縮みます。

- `current_gap_shape`: z値 5.42 → 6.04
- `prior_gap_log`: 3.21 → 1.95
- `seasonal_sex`: 2.70 → 1.15

したがって検出力向上は一貫せず、現時点で winner NLL の置換は勧めません。Brierは低確率勝者の重大な過小評価を有限ペナルティにするため、正直な確率という目的ではNLLより鈍いリスクもあります。

全着順NLLやNDCGは、勝確率から着順分布へestimandを変更し、NDCGは確率のproper scoreでもないため PRIMARY 候補には勧めません。

## (d) 憲法IIIを破らない導入条件

- 現 v2や過去の036/042/059/061/070/081 verdictは再判定しない。別の評価契約v3として追加する。
- 2019–2026は設計・power study専用。採否は未使用のprospective windowで行う。
- OOS前に、式・集約単位・対象マスク・全体非劣性幅・baseline・窓・bootstrap seed/B・多重性処理をhash固定する。
- targeted trackは「1 feature仮説につき1 segment」。結果後に季節・性別・距離等から通ったものを選ばない。
- 複数セグメントを同時検定するなら、固定familyにHolm等を事前登録する。
- NLL/Brierの「どちらか通れば採用」は禁止。採用指標を一つに固定する。

最終提案は、**winner NLLを捨てず、局所仮説だけ targeted superiority＋global non-inferiority にする**ことです。現状の不採用群から直ちに「物差しが悪い」とは言えませんが、局所効果を全体優越性だけで採る契約は確かに狭すぎます。
