## 結論

現行値は「締切寄り価格を事後に知る仮想戦略」の診断には使えますが、実現ROIや購入時戦略の利益性を測る物差しとしては壊れています。  
最大の欠陥は、判断時価格と公式払戻を分離しておらず、`policy-gate-eval` にはCI・MDE・独立holdoutもないことです。  
したがって「締切価格基準ではedgeがない」は支持されますが、「締切数分前にもedgeがない」「0.816と0.818は同値」は未証明です。

## 具体的な欠陥

| 重大度 | 欠陥とバイアス |
|---|---|
| **致命** | **選定価格と精算価格の混同**。`policy-gate-eval` は `race_horses.odds` を使って選定し、同じ値を払戻倍率として使います。[cli.py](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/cli.py:434) 一方API/shadow-logは凍結した `recommendations.market_odds_used` をそのまま払戻に使います。[backtest.py](/Users/kuwatawaku/workspace/horseracing/api/src/horseracing_api/backtest.py:36) パリミュチュエルでは判断時表示オッズは約定価格ではなく、精算は公式払戻で行う必要があります。 |
| **致命** | **closing lookahead の符号を「楽観」と決め打ちしている**。[policy_gate.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/policy_gate.py:14) 実際の符号は不定です。早期に見つけた勝ち馬が締切までに売れて `p×odds<1` になれば真のedgeを消して過小評価します。逆に締切情報で悪い買い目を回避したり、締切で初めて閾値を超えた馬を選べば過大・過小どちらにもなります。「同じclosingを使うのでcap対uncappedの相対比較は有効」も、馬がcap=21を跨ぐため実運用には成立しません。 |
| **致命** | **公式単勝払戻が保存されていない**。JRAの最終オッズは「勝馬が1頭と仮定した概算」で、同着等では実払戻と異なります。[JRAの払戻計算規則](https://www.jra.go.jp/kouza/baken/index.html) しかし `win_realized` は dead heat を検出しても凍結オッズをそのまま返します。Plus10、特払い、丸めも再現できません。 |
| **重大** | **オッズの時点・出所が混在**。netkeibaは結果未確定中だけ単一最新値を上書きし、結果行が入った後は既存オッズを保護します。[upsert.py](/Users/kuwatawaku/workspace/horseracing/scrape/src/horseracing_scrape/upsert.py:149) したがってJRA-VANの最終寄り値と、netkeibaの「最後に取得できた発走前値」が同じ列に混在し得ます。odds専用のsource/captured_at/full-field coverageがありません。 |
| **重大** | **prospectiveも実現ROI台帳になっていない**。`collect_prospective` はbetが選ばれたときだけRecommendationを残し、zero-bet、取得失敗、post-time拒否は一時レポートだけです。[orchestrate.py](/Users/kuwatawaku/workspace/horseracing/live/src/horseracing_live/orchestrate.py:80) zero-betでは冪等性確認用の行も残りません。複数policyを同一snapshotで同時生成せず、全馬snapshotも凍結しません。 |
| **重大** | **post-time保証の弱い行がROIに混ざる**。`post_time is None` は `weak_pretime` を付けるだけで生成され、shadow集計では除外されません。結果未取得だが既に発走済み、という行をprimaryに混ぜる余地があります。 |
| **重大** | **064には統計推論がない**。回収率、年別符号、worst yearだけで、CI・MDE・開催日クラスタ・payout concentrationがありません。[policy_gate.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/policy_gate.py:124) CLIには `--out`、model/artifact digest、race-set hashもなく、0.816対0.818を第三者が再構成できません。仕様が要求したmax drawdown・連敗・log growthも出力されていません。 |
| **重大** | **cap=21は独立確認ではない**。同じ2008–2026で約10種類のleverとcap帯を探索してから、その同じ期間に「事前登録」しています。[064 spec](/Users/kuwatawaku/workspace/horseracing/specs/064-odds-cap-betting-policy/spec.md:13) 結果を見た後の固定は新しいconfirmatory holdoutを作りません。19/19年も探索選択を補正しません。 |
| **重大** | **expanding-windowと年多数決のestimandが現運用と合わない**。2008 foldは2007年だけで学習し、さらに最新30%を校正用に除外します。古い小標本foldと現在のfoldを「年1票」で同列に扱い、worst foldをgateにします。079実測でも2009–2011は大幅負、2022–2025はすべて正という明確な時変があります。[evidence.json](/Users/kuwatawaku/workspace/horseracing/specs/079-ev-weighted-training/evidence.json:62) expandingは歴史ストレステストには使えますが、2026年以降のROI推定のprimaryには不適切です。 |
| **重大** | **結果欠損・取消の扱いが経路間で逆**。APIは選択馬に結果行がないだけでvoidにしてROI分母から外します。部分取込でも上方バイアスになります。policy gateは同じ馬を非勝者として損失計上し、下方バイアスになります。取消券が返還されること自体はJRA規則どおりですが、部分取込と取消を区別する必要があります。[JRAの返還規則](https://www.jra.go.jp/kouza/baken/) |
| **中** | **オッズ欠損馬の確率分母が壊れる**。driverは `odds is None` の馬をrows作成前に落とし、その後scorerが残った馬だけで再正規化します。[cli.py](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/cli.py:461) 本番の `select_ev_bets` は「欠損オッズ馬も勝ち得るので確率分母に残す」契約です。欠損があるレースではEVとbet集合が変わります。 |
| **中** | **flat stakeの意味が未固定**。現在は1頭1単位なので、複数頭を選ぶレースほど資金配分が大きくなります。これは「100円/券」のROIとしては正しい一方、「1レース固定予算」のROIではありません。WINのKellyについては推奨時のfractionはありますが、公式払戻による時系列bankroll評価はなく、既存Kelly backtestはexotic用です。またrisk pathは `race_id` 順で、実際の発走時系列ではありません。[kelly_backtest.py](/Users/kuwatawaku/workspace/horseracing/betting/src/horseracing_betting/kelly_backtest.py:239) |

### 自己影響

単勝払戻率を \(R=0.8\)、総プールを \(T\)、馬 \(i\) の既存投票額を \(W_i\)、自分の全投票を \(B\)、その馬への投票を \(b_i\) とすると、単独勝馬時の自己影響込み倍率は概ね

\[
O'_i=R\frac{T+B}{W_i+b_i},\qquad
\frac{O'_i}{O_i}=\frac{1+B/T}{1+b_i/W_i}.
\]

同じ馬への投票は通常その倍率を下げるので、価格受容者として `stake×odds` を払う現実装は上方バイアスです。100円なら多くのJRAレースで小さい可能性がありますが、プール総額・馬別票数を保存していないため定量確認不能です。公式式はJRAも公開しています。[JRAの払戻計算式](https://www.jra.go.jp/faq/pop03/1_17.html)

## 偽陰性リスクの優先順位

1. **早期情報が締切価格に吸収される効果**  
   これが最大です。朝またはT−5分で `p×odds_t>1` だった勝ち馬が締切で売れ、closing基準ではbet対象から消える戦略は、現DBから完全に復元不能です。

2. **混在した時点価格による選択集合の破壊**  
   2025年前後のデータ源変更と、単一最新値の保存規則により、同じfold内でも「final寄り」と「最終取得時点」が混ざり得ます。

3. **現代regimeのedgeを古いfoldが希釈**  
   060の2014年以降13/13改善や079の直近年正方向は、全期間平均・worst-year gateで消えます。ただし2014という境界を結果後に選ぶのも禁止で、次回は未来holdoutで確認すべきです。

4. **zero-bet・取得失敗・弱いpretimeの非永続化**  
   取得できた、betが出たケースだけが残るため、運用可能性とselection failureを過大評価します。

5. **flat per-ticket集計による局所edgeの希釈**  
   本当にedgeがあるレースでも、同じレースの弱い複数買いが混ざれば馬単位ROIで消えます。固定レース予算との併記が必要です。

### 締切数分前との乖離の定量化

固定時点、例えばprimaryをT−5分として、全馬について `O_T-5`、`O_close`、公式払戻を保存し、次を出すべきです。

- \(S_t=\text{policy}(p_t,O_t)\) を固定し、`ROI_t = 公式払戻 / at-risk stake`。
- `early-only / both / closing-only / neither` の選択遷移表。
- 全馬と選択馬について `log(O_close/O_t)` の分布。
- `ROI(S_T-5; official payout)` と `ROI(S_close; official payout)` の差。
- odds帯、field size、capture quality別のdrift。ただし結果を見て採用segmentを選ばない。

判断時オッズで仮想精算した値はdrift診断であって、経済的ROIではありません。

## MDEと検出力

064単体は分散を保存していないため、0.816対0.818の厳密なMDEは算出不能です。最も近い実測分散は、同じcap21・flat stake・開催日ペアbootstrapを使う079です。[ev_weight_gate.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/ev_weight_gate.py:229)

実測はbaseline 107,211 bets、candidate 106,876 bets、61,745レース、2,004開催日、ΔROI −0.00536、95% CI `[−0.01376, +0.00306]` です。

\[
SE \approx \frac{0.00306-(-0.01376)}{2\times1.96}=0.00429
\]

\[
MDE_{80\%,\,95\%CI}\approx(1.96+0.842)\times SE=\mathbf{0.0120}
\]

- 同程度のペア構造なら、真の差 `+0.02` の検出力は約 **99.7%**。全期間に持続する2ポイント改善を見逃している可能性は低いです。
- ただし観測差 `0.002` はMDEより小さいため、「同値」ではなく「優越性を検出できない」です。
- 直近525–600開催日だけなら、単純な平方根換算でMDEは **0.0235–0.0220**、`+0.02` の検出力は約 **66–72%**。現在regimeだけでは検出力不足になり得ます。
- 80% powerで `+0.02` を検出するには、同じ分散なら約 **724開催日**、JRAの開催頻度では概ね7年相当です。
- 単一policyのROIを固定値と比較する非ペア検定はさらに弱いです。079 baselineから導く理論的なiid下限だけでもMDEは **0.0224以上**で、実際はオッズ分散と日内相関により大きくなります。

したがって「ROIが動かない」は、全期間での2ポイント級の恒常効果についてはかなり強い結論です。しかし直近regimeの2ポイント効果や、締切前だけ存在するedgeには言えません。

## 事前登録すべきROI評価契約

### 窓

- 2008–2026はすべてdevelopment evidenceへ降格。
- historical primaryを置くなら直近の完了年2021–2025。2008開始expandingはstress testのみ。
- 学習窓は別軸として、推奨は直近5年の固定rolling window。3年・8年・expandingとの選択はpre-freeze期間内のnested walk-forwardで一度だけ行う。
- confirmatoryは契約freeze後のprospectiveのみ。途中閲覧するならalpha spendingを固定する。

### 計測単位

- Primary stakeは現行運用に合わせて **100円/券のflat stake**。
- Primary ROIは `Σ公式払戻 / Σat-risk stake`。返還券はstake・payout双方から除外するが、attemptとvoid件数は残す。
- 同着は馬別の公式払戻、取消はrefund、DNF・失格はloss、結果取込不完全はrace全体を未精算として除外。
- Secondaryとして「1レース100円を選択馬に等分」を併記。
- Kellyはedge確認後のsecondary。λ、最小100円丸め、race/day exposure cap、初期bankroll、ruin閾値を固定し、実発走順で幾何成長・drawdown・ruinを出す。Kellyだけで負のedgeを正にはできません。

### CI・判定

- 開催日をpaired clusterとして20,000 bootstrap、seed固定。
- 各replicateで両armの `Σpayout/Σstake` を再計算する。bet単位や年別ROI平均は禁止。
- 2–4連続開催日または開催週blockを事前登録した感度分析とする。
- 利益主張：`ROI−1` の95% CI下限 `>0`。
- 損失低減：candidate−currentの95% CI下限 `>0`。意味ある差を2ポイントとするなら下限 `>+0.02`。
- 「差がない」：非有意ではなく、90% CIが `[-0.02,+0.02]` 内に入るTOST、または片側95%上限 `<+0.02`。
- 常にn_bets、n_races、n_days、MDE、selection Jaccard、上位勝ち馬への払戻集中、official-settlement coverageを添付する。

現時点でROI>1を示すアプローチはありません。ただし「T−5分の情報edge」と「実exotic公式払戻」は未測定であり、存在しないとはまだ言えません。ここを上記契約で前向き測定しても下限CIが0を超えなければ、単勝での利益化は「無い」と結論し、価値を損失低減・市場より早い能力評価・意思決定支援に限定するのが妥当です。

コード・ファイルの変更は行っていません。
