## 結論

現行データ源・単勝市場・現行モデル族のままでは、ROI>1.0へ到達する証拠のある道はありません。既定解は no-bet、cap<21 は賭ける場合の損失を約35%減らす施策です。  
残る検証価値は、060市場残差を判断時点オッズ＋公式払戻で一度だけ前向き確認することと、実配当が蓄積した exotic の検証です。両方失敗なら「金融的エッジなし」と結論してよいです。

## 数学的分解

### 1. パリミュチュエルの利益条件

レース \(r\)、馬 \(i\) について、

- \(t_{ri}\): 真の勝率
- \(p_{ri}\): モデル確率
- \(q_{ri}\): 市場確率
- \(O_{ri}\): 払戻倍率
- \(a_{ri}\): 賭け金

とすると、期待回収率と純ROIは

\[
R(A)=
\frac{\sum_{r,i}a_{ri}t_{ri}O_{ri}}
     {\sum_{r,i}a_{ri}},
\qquad
ROI(A)=R(A)-1.
\]

表示オッズから

\[
q_{ri}=\frac{1/O_{ri}}{\sum_j1/O_{rj}},\qquad
c_r=\frac1{\sum_j1/O_{rj}}
\]

と devig すれば、定義上

\[
O_{ri}=\frac{c_r}{q_{ri}}.
\]

したがって

\[
ROI(A)
=
\mathbb E_A\!\left[c_r\frac{t}{q}\right]-1
=
\mathbb E_A\!\left[c_r
 \left(1+\frac{t-q}{q}\right)\right]-1.
\]

単勝の \(c\simeq0.8\) なら、利益条件は

\[
\frac{t}{q}>\frac1{0.8}=1.25,
\qquad
\frac{t-q}{q}>25\%.
\]

つまり「市場が少し過小評価している」だけでは不十分です。市場より真の勝率が相対25%以上高い対象を、賭ける前に選別しなければなりません。breakage があれば \(c<0.8\) となり、必要幅はさらに大きくなります。JRAの払戻率とプール按分の構造もこの形です。[JRAの払戻率・計算式](https://www.jra.go.jp/news/other/20140303.html)

実測との対応は明瞭です。

- cap 方策 \(R=0.816\) は、概算 \(E_A[t/q]=0.816/0.8=1.02\)。
- 必要な1.25に対して、現在は1.02にすぎません。
- 現在の選択集合からさらに約22.6%の相対リフトが必要です。
- q≥0.30帯も \(0.413/0.405=1.020\) で、ほぼ同じ値です。[047所見](/Users/kuwatawaku/workspace/horseracing/specs/047-segment-diagnostics/spec.md:46)

これは「favorite-longshot bias の符号はあるが、控除率を越えるほど大きくない」という状態です。一般にもFLBは長穴の過大評価・本命の過小評価を意味しますが、takeoutと空売り不能の下では、符号が存在しても利益化できるとは限りません。[FLBとtakeout・空売り不能の関係](https://doi.org/10.1016/S0165-1765(96)00870-1)

### 2. LogLossとの関係

race-level winner NLLなら

\[
L(p)=\mathbb E_t[-\log p_W]
    =H(t)+\mathbb E[D_{\mathrm{KL}}(t\Vert p)].
\]

市場との差は

\[
L(p)-L(q)
=
\mathbb E_t\!\left[\log\frac{q_W}{p_W}\right].
\]

さらに、全馬に資金比率 \(p_i\) で賭ける完全ポートフォリオの期待対数成長率は

\[
G(p)
=
\mathbb E_t\!\left[\log\left(c\frac{p_W}{q_W}\right)\right]
=
\log c+L(q)-L(p).
\]

これはLogLossと賭けの最も直接的な関係です。市場と同じ \(p=q\) でも

\[
G(q)=\log0.8=-0.2231
\]

であり、正の対数成長には

\[
L(p)<L(q)-0.2231
\]

が必要です。

保存済み2019+ OOSを年加重すると winner NLL は概算で

- モデル: 2.0694
- 市場: 1.9319

です。[082 OOS artifact](/Users/kuwatawaku/workspace/horseracing/out/082-segment-accuracy.json)

したがって、この完全ポートフォリオで正の成長に必要なのは

\[
L(p)<1.9319-0.2231=1.7087.
\]

現状から必要な改善は約0.361 winner NLLで、F02の改善幅0.00575の約63倍です。これは現実的な「あと少し」ではありません。

ただし、これは全馬比例賭けの式です。選択的な固定額ベットでは、単一のLogLossからROIを一意に逆算できません。まれな部分集合だけ当てれば、全体で \(p\) が \(q\) に負けていても利益は論理上可能です。

### 3. LogLossが下がってもROIが動かない条件

方策を \(A(p,q)\) とすると、固定額ベットのROIは選択集合だけで決まります。したがって以下ではLogLoss改善がROIへ伝わりません。

- 改善した馬が賭け対象でない。
- \(pO=1\) の境界を跨がず、選択集合が不変。
- 改善の実体が「pをqへ近づけた」だけで、独立した市場残差を発見していない。
- モデル誤差と市場誤差が同じ公開情報由来で強く相関している。
- 全体平均では改善しても、高オッズの \(1/q\) 加重領域では悪化している。
- 閾値超過をノイズの最大値から選ぶため、winner’s curse が発生する。

qをcontrol variateとして使うなら、推定すべき対象は絶対勝率より

\[
Z_i=\frac{Y_i-q_i}{q_i}
\quad\Rightarrow\quad
E[Z_i\mid I]=\frac{t_i-q_i}{q_i}
\]

です。必要なのは \(E[Z\mid A]>0.25\) という条件であり、単なる \(p>q\) ではありません。ただし \(Y/q\) は穴で分散が爆発するため、offset・縮約・capが不可欠です。

### 4. 方策の幾何学

\[
x=\log(p/q),\qquad
y=\log(t/q),\qquad
h=-\log c\simeq0.223
\]

と置くと、

- モデルEV方策: \(x\ge h\)
- 真に利益が出る領域: \(y\ge h\)

です。

現在は低い \(q\) ほどモデルのtail過大で \(x\) だけが大きくなり、\(y\) は上がっていません。そのためEV閾値を上げるほど右へ進むだけで、利益領域の上側へは進まず悪化します。

一方、odds capは \(q\to0\) の領域を切り落とします。これは正のエッジを発見したのではなく、誤差分散と過信が最大の領域を削除した操作です。実測の

- EV≥1: 0.721
- cap<21: 0.818
- EV≥1.3: 0.703

と完全に整合します。[064実測](/Users/kuwatawaku/workspace/horseracing/specs/064-odds-cap-betting-policy/spec.md:13)

したがって、現状ではpolicyが損失低減の支配的レバーです。しかし正の期待値を作るには、policyだけではなく「市場残差を条件付きで当てる情報」が必要です。

## 残っている論理的経路

期待値の高い順です。

| 経路 | 必要条件 | 評価 |
|---|---|---|
| 1. 060市場残差＋直接edge policy | 判断時qに対して残差の符号・大きさを再現し、選択集合で \(E[t/q]>1.25\) | 唯一、予測面で具体的な正の兆候あり。ただし利益の証拠ではない |
| 2. qの系統バイアスだけを狙う | pがqより正確である必要はないが、事前定義した部分集合で過小評価が25%以上必要 | 論理上可能。既存の広いq帯・新馬・頭数ではほぼ棄却済み |
| 3. exoticのcross-pool edge | 高い控除率を越える組合せ価格の歪み、正しいjoint確率、公式配当での再現性 | 未検証。win p由来jointなので事前確率は低いが、論理的には開いている |
| 4. 独占情報・手数料低下 | 公開市場にない情報、または18%以上のrebate等 | 現在のデータ制約・JRA通常条件の外 |
| 5. 無リスク裁定 | 全状態を固定価格で被覆でき、総費用が最低払戻以下 | 変動するパリミュチュエル、空売り不能では通常成立しない |

pがqに全域で負けていても、まれな集合 \(S\) で \(t/q>1.25\) を識別できればROI>1は可能です。したがって「pがqに勝つこと」は必要条件ではありません。しかし「qのバイアスの符号を当てる」だけでは足りず、控除率を越える大きさまで必要です。

別の価値として確立しているのは以下です。

- capによる損失率の28.1%→18.4%への低減、すなわち損失額約34.5%削減。
- オッズがまだ無い時点の能力予測、説明、異常検知。
- qを最終確率として提示する正直な意思決定支援。
- 「賭けない方がよい」を定量的に示すリスク教育。

## 060市場残差モデルの評価

060は「単なる偽エッジ」とも「本物」ともまだ断定できません。

確認値は、

- 全19 fold: candidate 0.2026707、q 0.2025912、差 +0.0000795でFAIL
- 2008–2013: 平均 +0.00152
- 2014–2026: 平均 −0.000636、13/13でqに勝利
- 近年差の範囲: −0.00029～−0.00108

です。[060結果](/Users/kuwatawaku/workspace/horseracing/specs/060-market-residual-model/tasks.md:34)

13年連続の符号は、単なるfoldノイズとしては強い形です。市場qは学習不要、残差モデルは初期ほど高分散なので、「十分な学習量に達してから優位になる」という説明にも整合します。現在の運用estimandが十分な履歴を持つモデルなら、2008年のstartup性能を同じ重みで含める必然性もありません。

一方で、

- 2014という境界が結果後に発見された。
- expanding foldは学習集合を共有し、独立した13試行ではない。
- 同じ過去OOSが繰り返し研究に利用されている。
- 使用qがclosing寄りで、実運用の判断時qと分布が違う。
- 改善幅は控除率の壁に比べ非常に小さい。

ため、これは「本物らしい小さなforecast edge」であって「tradable edge」ではありません。

## 精度の弾力性

lgbm-061→065では、

\[
\Delta L=0.215021-0.214529=0.000492
\]

改善した一方、cap回収は0.818→0.816です。[061結果](/Users/kuwatawaku/workspace/horseracing/artifacts/061-lgbm061-train.log:5) [065 metadata](/Users/kuwatawaku/workspace/horseracing/artifacts/model_versions/lgbm-065/metadata.json:21)

観測弾力性は

\[
\frac{\Delta R}{-\Delta L}
=
\frac{-0.002}{0.000492}
\simeq -4.1
\]

で、符号すら逆です。統計的にはほぼゼロと扱うべきです。

仮に、このノイズの絶対値4.1を極端に楽観的な正の弾力性として使っても、0.816→1.0には

\[
\Delta L\simeq\frac{0.184}{4.1}=0.0452
\]

が必要で、目標LogLossは約0.1693です。

これは、

- 061→065改善の約92倍
- 0.2388→0.2145という4年間の全改善の約1.86倍
- しかも過去の改善はROIへほぼゼロ変換

です。したがって「精度をもう少し上げれば届く」という規模ではありません。正確には、flat-bet ROIに対応する一意のLogLoss目標は存在せず、必要なのはglobal精度ではなく選択領域の相対残差です。

## 決着をつける最小実験

### 1. 060のforecast edge確認

- 必要データ: 各レース1回の固定時点q、予測前のmodel/recipe hash、結果。
- 期間: 2026-08以降の未使用12か月、最低1完整シーズン。途中で再学習・閾値変更をしない。
- Primary: 同一q snapshotで candidate−q のpaired winner NLL。
- 判定: 開催日cluster CI上限<0なら「近年forecast edgeあり」。跨げばREJECT。

過去データでrolling/expandingを再比較するのは機序診断にはなりますが、同じ開発集合なので決着にはなりません。

### 2. 公式払戻による単勝ROI確認

- 必要データ: 固定時点q、全decision attempt、bet/skip、固定100円、公式最終払戻。
- Arm: current EV、cap21、q-only favorite、060 residual policy、no-bet。
- 期間: 最低24か月または事前power計算で定めた必要bet数の大きい方。途中閲覧で停止しない。
- 利益判定: per-opportunity net profitの開催日cluster CI下限>0。
- 損失低減判定: candidate−cap21のCI下限>0。
- Guard: leave-one-winner-out、月別符号、zero-bet込み。

判断時表示オッズは約定価格ではないため、精算は必ず公式払戻です。既存075も凍結snapshot収益をcounterfactualへ降格しており、この区別は正しいです。[075整理](/Users/kuwatawaku/workspace/horseracing/specs/075-counterfactual-return-api/spec.md:9) [公式払戻が必要な理由](/Users/kuwatawaku/workspace/horseracing/docs/plan/model-accuracy-roi-redesign-proposal.md:108)

### 3. exoticの前向きkill-test

- 必要データ: 結果前に生成・凍結した買い目、判断時win q、券種別公式配当。
- 期間: 最低18–24か月。現在の500–1,500 betはcoverage下限であり、配当分散に基づくpower保証ではないため、盲検pilotで分散だけ推定して必要nを固定する。
- 判定: 券種ごとに公式払戻ROIのCI下限>1、かつ人気筋baselineとの差CI下限>0、Holm補正後も通過。
- 注意: 発走前exotic価格を保存しない場合に証明できるのは「win市場から作ったcross-pool policyの利益」であって、「exotic市場価格の非効率」そのものではありません。

また、080の「前向き配当なのでclosing biasなし」は、買い目自体も判断時qから結果前に保存されている場合にだけ成立します。[080事前登録](/Users/kuwatawaku/workspace/horseracing/specs/080-exotic-dividend-edge/pre-registration.md:66)

## 潰した道と未決着の道

### 潰したとみなしてよい

- 同じ公開情報で独立pのglobal LogLossを上げ続けることをROIレバーとみなす。
- 単純EV≥1全馬買い、EV閾値引き上げ、haircut。
- p−q乖離、逆張り、モデル本命集中、広い人気帯・頭数ルール。
- p/qの単純混合。
- 新馬・少頭数なら市場が弱いという仮説。
- EV加重学習。[079 null](/Users/kuwatawaku/workspace/horseracing/specs/079-ev-weighted-training/pre-registration.md:240)
- 叩き2走目・前走着順を既存モデルへ足せば市場残差を取れるという仮説。
- 推定exoticオッズだけによる利益主張。

### まだ潰していない

- 060残差が固定判断時qでも再現するか。
- 060のNLL改善が、控除率を越える狭い部分集合へ集中しているか。
- 判断時q→公式払戻のdriftを含めた前向きROI。
- 実公式配当によるexotic cross-pool policy。
- 現在存在しない独占データ、または大幅なrebate。

この3実験でも正の結果が出なければ、現在の情報集合では「市場超過収益なし」と言い切ってよいです。その場合の合理的な製品価値は、no-betを基準にした損失抑制、q中心の正直な予測、説明・異常検知に限定されます。
