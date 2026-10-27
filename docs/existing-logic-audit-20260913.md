# 現行の予測・学習・ゲート監査（2026-09-13）

新しい特徴量やモデルの追加を前提にせず、現在のコードにある不具合と条件の不一致を調べた。**修正すべき問題はある。特に採用ゲート、取消後の確率提供、脚質の供給元間の表現差を優先する。PL目的関数の計算が壊れているという証拠は得られなかった。**

基準は131の昇格記録にある `mix-129-nj6`。129の6 member（125列／138列、各seed42/43/44、900木、8 OOF blocks、PL top3、TE、体重mask0.5）と本番経路を追った。各memberの学習末日は2026-08-23。稼働DBのactive行を今回再照会したわけではない。

HEADは `a275e85fd2268d4d985bbc94f7040f75837a4fbb`。既存の未コミット変更を含む作業ツリーを調査した。学習、ゲート、servingを3人の独立エージェントへ分担し、親が特徴量・保存入力の検査と証拠の突合を実施した。実データはSHA固定111素材（2026-08-23まで）のみ新たに集計した。DB読み書き、本番切替、全体の再学習は実施していない。

## 1. 採用ゲートが探索結果・別候補の結果を受け入れる【P1・再現済み】

入口は `train-evaluate --verdict` と `promote-model --verdict`。後者では既登録候補という理由でlegacy gateを通過済みとして扱う。

- [adoption.py:197](../training/src/horseracing_training/adoption.py#L197) は `artifact_kind` が存在するときだけ `eligible_for_verdict` を検査する。kindが無い標準reportでは `eligible_for_verdict=false` を明示しても通過し、`can_adopt` は読まない。
- [adoption.py:206](../training/src/horseracing_training/adoption.py#L206) は重要部分群の保証が欠落していても通過する。
- [adoption.py:133](../training/src/horseracing_training/adoption.py#L133) の正規化はcandidate／activeのrecipe hashを捨てる。[promote.py:117](../training/src/horseracing_training/promote.py#L117) も昇格対象のID・artifact hashを検査関数に渡さない。評価対象と配布対象を結び付けていない。
- [training/cli.py:2025](../training/src/horseracing_training/cli.py#L2025) は `--confirmatory` のときだけ凍結条件等を検査する。標準の探索経路もv4の `ADOPT` を出せる。

**再現:** 実 `paired_eval` に合成データ、gate_configなし（非confirmatory）、採点2開催日、seed変動未評価、部分群未評価を渡すと `ADOPT`。その出力を実 `evaluate_promotion` に渡すと `promotable=true` になった。手作りJSONだけの問題ではない。別recipeを指すreport、明示的に採用不可を含むreportも通過した。

**影響:** 数値ゲートの正しさとは別に、探索・動作確認の良い結果が本番昇格の根拠になり得る。**現行6モデルがこの穴で誤採用されたという意味ではない。** 131にはユーザー指示による理由付きoverrideの昇格記録がある。

**最小修正方向:** 探索出力を常に昇格不可とし、昇格側で明示的な適格性・確認設定・必要な部分群保証を要求する。対象候補、比較基準、recipe、学習済み本体をreportへ結び付け、昇格時に照合する。改善閾値をさらに厳しくする話ではない。

## 2. 予測後の取消で、APIが合計不足の確率を正常として返す【P2・再現済み】

[queries.py:91](../api/src/horseracing_api/queries.py#L91) は現在STARTEDの馬だけを残すが、[predictions.py:138](../api/src/horseracing_api/routers/predictions.py#L138) は取消前の保存win/top2/top3をそのまま返す。[selection.py:55](../api/src/horseracing_api/selection.py#L55) のモデル確率も未正規化である。一方、市場確率qだけは正規化され、馬番号集合が一致すると `canonical_consistent=true` になる。

実routerと確率関数に、DB戻り値だけを模したfixtureを入力した。4頭の勝率 `[0.4, 0.3, 0.2, 0.1]` から0.4の馬を取消すると、残る3頭は次の出力になった。

| 出力 | 実際の合計 | 現在の3頭で必要な合計 |
|---|---:|---:|
| win | 0.600000 | 1 |
| top2 | 1.284127 | 2 |
| top3 | 2.077778 | 3 |
| 市場q | 1.000000 | 1 |

3頭のtop3が全員1にならず、モデルと市場の比較も異なる確率尺度になる。再予測完了までこの状態を許す経路上の不具合であり、実際の発生件数は未集計。

**最小修正方向:** 保存runの出走集合が現在と違えば失効させ、再推論したrunを使う。取消後の条件付けで対応するなら、正規化winから適用校正係数を含めてtop2/top3を導出する。top2/top3の単純な定数倍では修復できない。

## 3. 脚質の供給元が変わると、同じ位置取りでも別の入力になる【P2・実データで表現差確認】

[pace_features.py:41](../features/src/horseracing_features/pace_features.py#L41) の後方型集合は `差し・追込・ﾏｸﾘ・マクリ` で、旧データの主要語彙 `後方` が含まれない。[同:104](../features/src/horseracing_features/pace_features.py#L104) で `後方` は `is_closer=0` になる。一方、[scrape/upsert.py:295](../scrape/src/horseracing_scrape/upsert.py#L295) の現在の導出は最初のコーナー位置から `差し・追込` を生成するため、その出力は1になる。

SHA固定111素材の958,011出走行を独立集計した。

- `後方` は247,649行。2024年は12,558行、2026年は0行。
- その247,649行には比較可能なコーナー位置があり、**同じ過去走の位置と頭数へ現在の取得側ルールを適用すると228,156行が1になる**。現行特徴生成では0である。
- これらは過去走の表現差件数であり、最終特徴量の変化行数、誤教師件数、誤予測件数ではない。

旧語彙の公式分類基準と現在のfirst-corner heuristicが完全同一とは証明していないので、`後方` を無条件で1へ置換することを正解とはしない。ただし「同じ語彙に合わせて特徴を一貫させる」という取得側コメントは実現されていない。本人の脚質率と出走メンバーから作る展開特徴へ影響する。

**132との違い:** 132のUnknown修正は `後方` を既知の0扱いとして意図的に維持した。この表現差を直した実験ではなく、132の不採用結果から本件も無効とは結論できない。

**最小修正方向:** 既存のコーナー位置・脚質語彙の定義を揃える限定比較を行う。過去と現在の入力を同じ処理で作り、現行6モデルに対応する学習／予測の両側で評価する。新特徴や新データの追加は不要。

## 4. 現在の馬主masterが過去年の特徴量に入り込む【P1・時点違反を合成入力で再現】

[loader.py:104](../features/src/horseracing_features/loader.py#L104) は日付制限なしで `Horse.owner_name` を読む。[owner_breeder_features.py:53](../features/src/horseracing_features/owner_breeder_features.py#L53) は現在のhorse masterをすべての過去走へ結合する。集計するレース結果が過去だけでも、集計キーが将来変更後の馬主なら過去時点を再現できない。

**再現:** レースと結果を全く変えず、対象馬の現在の馬主だけA→Bへ変更すると、2020-02-01の `asof_owner_win_rate` が1.0→0.0へ変化した。両馬主に20件以上の過去記録を用意した実feature関数の結果である。現行memberにも馬主特徴が入っている。

**影響の限界:** 「変更後のmasterを使う」という時点違反は再現できたが、実際の移籍件数、どの保存学習行が当時値と違うか、精度への大きさは未測定。現在のmasterしかなければ真の過去値を復元できない。コードの「現在の馬主はレース前に分かるのでリークしない」という説明は、過去時点の評価には成立しない。

**最小修正方向:** 既存のレース時点入力で復元可能な場合だけ当時の馬主を使う。復元できない期間は、この2列を外す対照で評価する。特徴追加より先に過去評価の信頼性を直す問題である。

## 5. 本番の上位着順校正が、別モデル・学習内予測を材料にできる【P1・経路確認、実害未測定】

[pipeline.py:181](../serving/src/horseracing_serving/pipeline.py#L181) → [model_calibration.py:516](../probability/src/horseracing_probability/model_calibration.py#L516) → [同:451](../probability/src/horseracing_probability/model_calibration.py#L451)。既定の `legacy-runtime` は、対象日より前のレースについて最新の保存runを集める。モデル世代、体重公表条件、予測作成時点、学習末日・OOSを絞らない。

レース日が過去でも、そのレースを学習したモデルによる後日のbackfillは未学習予測ではない。131には8/23まで学習した6モデルで過去期間をbackfillした実行記録があり、混入し得る経路は存在する。別モデルの再予測だけで校正が変わる点も問題になる。

ただし、**Harville／段階校正の演算自体が間違っているという所見ではない。** 135では同じwinと同じ係数なら現行131と132が一致し、最大差は1.89×10^-15以下。同じ完全結果素材なら校正係数も一致している。保存比較での不完備1レースの損失差も最大6.04×10^-9だった。これらを新たな本番改善として数えない。実運用の素材集合による悪化幅は未測定。

**最小修正方向:** 配布する混合モデル、情報条件、過去OOSの来歴を持つ保存校正を利用し、日次の最新run選択に依存しない経路へ揃える。

## 6. 校正を必須にしても設定欠落で旧経路へ戻る【P2・再現済み】

[ops/runner.py:333](../ops/src/horseracing_ops/runner.py#L333) は `MODE=manifest-required` **かつ** manifestパスがあるときだけCLIフラグを付ける。パス未設定・空文字だと空の引数を返し、下流CLIが既定のlegacy-runtimeを使う。PREDICT／RECOMMENDに加え、[同:783](../ops/src/horseracing_ops/runner.py#L783) のREFRESHにも同様の処理がある。

実関数ASTで「必須モード＋パス欠落→`[]`」を再現した。現在の稼働環境に設定欠落があるかは確認していない。

**最小修正方向:** 必須モードならパス欠落を明示エラーにする。既存の校正必須ゲートを正しく通す修正である。

## 7. 欠損した脚質を既知の0として扱う【P2・既知不具合を再確認】

[pace_features.py:103](../features/src/horseracing_features/pace_features.py#L103) の `isin(...).astype(float)` はNoneも0にする。[pace_scenario_features.py:82](../features/src/horseracing_features/pace_scenario_features.py#L82) は非欠損を既知と数えるため、履歴がUnknownの馬まで充足率に入る。元023 specの「未記録の脚質は分母から除外」に反する。

2頭のうち1頭の過去脚質がUnknownというfixtureで、本人の2脚質率が0になり、レースの脚質充足率は実際の50%に対して100%になった。固定素材でもUnknownの実出走は3,089行と再確認した。

**修正即採用にはしない。** 132ではUnknown保持を再学習して、2026年のwin NLLは全情報+0.001753、公表前+0.003603と悪化方向だった。これは既に試して保留した修正であり、今回新発見の改善策として繰り返さない。3の語彙・定義差とは分ける。

## 誤りと断定しない点、現行では発生を確認できなかった点

| 論点 | 調査結果・扱い |
|---|---|
| OOF校正は体重情報あり、本番は公表前も使う | [calib_split.py:527](../training/src/horseracing_training/calib_split.py#L527) が差を明記。既知の設計・分布不一致。現行memberに該当するが、同条件校正への変更が改善するか未測定。入力整合の検証候補にする。 |
| 結果完全性を馬ID集合でなく件数だけで判定 | `eval/dataset.py:86,151`、`calib_split.py:85,391`。取消馬の結果が残り別started馬が欠けるfixtureでは誤completeを再現。再取込の取消訂正で古い結果を残す経路もある。ただし固定111素材は非started結果0、誤complete0。現在の精度原因としない。 |
| 学習側の不完備レース | 固定素材で全欠損14頭のレースと16頭中1頭欠損のレースを確認。前者はEvalRaceから除外、後者はOOF校正／現行研究採点から除外されるがbooster学習には入り得る。後者は1・2・3着ラベルが揃うため、欠損1頭の0扱いが現実の誤教師とは証明できない。 |
| HPOのTEは自ラベルを含み、最終学習はOOF | `hpo.py:45` と `predictor.py:313` の手順不一致を再現。singleton6行でHPOは0.54545／0.45455、最終学習は全0.5。現行129はHPO未使用、PLでHPO指定も明示例外。現在の原因から除外する。 |
| 自動採用が既存activeを降格しない | `artifacts.py:469` は新activeをupsertするだけ。既存activeを含むSession代替で2activeを再現。servingは複数activeを拒否する。現在の6モデルは手動promote経路なので、学習CLIを再利用するときの運用不具合として扱う。 |
| TEの他foldに時間的に後の行が入る | outer train内で閉じており、outer OOSへの直接リークとは判断しない。 |
| 以前の「末尾30%を学習しない」問題 | 現行129は `calib_frac=0`、最終boosterは全train。修正済み。 |
| 135のCI・seed集約 | 開催日ごとの損失合計／母数、seedごとのloss差平均を独立計算。不均一な頭数・開催日・不完備・同着を含む合成例で点推定とCIの最大差1.11×10^-16。標本数を3倍にする誤りは確認されなかった。 |

## 優先順位と検証

1. **確定した制御の不具合を修正:** 探索reportの昇格防止、候補同一性、取消後run失効、必須manifestの欠落エラー。これらは精度の統計的改善を主張しなくても修正すべき契約違反である。
2. **既存入力の一貫性を比較:** 特に脚質の語彙／位置取り定義差。Unknownだけの再実験と混同せず、既存素材と現行レシピで対照を作る。馬主の過去時点問題も分けて扱う。
3. **校正を同じ条件へ揃えて比較:** 本番topk素材の来歴と、公表前win校正の入力分布を別々に検証する。135の同λ演算等価という陰性結果は維持する。

今回のunit検証は学習60件、serving101件、probability17件、ゲート／135関連200件が成功（実行ごとの件数で重複を含み、全リポジトリのユニーク総数ではない）。別実装のPL有限差分ではgradient最大差2.95×10^-10、対角Hessian最大差8.24×10^-7。5木の小模型で入力順の変更・復元後の予測差は8.33×10^-17だった。既存テストが成功しても、上の未検査境界で不具合が再現する。

証拠と再現手順は [evidence/existing-logic-audit-20260913/README.md](evidence/existing-logic-audit-20260913/README.md) に保存した。今回の調査では「直せば何%精度が上がるか」は測定していない。条件付きの実装不具合、入力の意味不一致、既知の設計差を区別したうえで、上記の順に修正・比較する。
