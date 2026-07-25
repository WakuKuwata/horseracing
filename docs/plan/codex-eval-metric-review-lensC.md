## 結論

PRIMARY の race-level winner NLL 自体は壊れていません。式、1レース1標本、candidate−active の paired 差は妥当です。「一つも採用されない」主因を指標定義だけに帰す根拠はありません。むしろ residual probe はフル特徴再学習より楽観的になり得るため、probe の改善が本採用に再現しないことは十分あり得ます。

ただし実装には、評価窓・母集団・三値判定に関する具体的な契約違反があります。特に最初の2件は再評価が必要です。

### 1. 評価開始日が学習履歴まで切ってしまう

- 疑義: [cli.py:1131](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/cli.py:1131) が `--from` を `load_eval_races()` の下限に使用し、[splits.py:32](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/splits.py:32) はその集合だけから outer-train を作ります。
- 例: `--from 2019-01-01 --first-valid-year 2019` では2019 foldは train=空で消え、2020 foldも2019年だけで学習されます。本来の2007–2019履歴ではありません。
- 加えて confirmatory は [cli.py:1127](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/cli.py:1127) から `eval_window` を渡しておらず、[decision.py:136](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/decision.py:136) の期間照合が実行されません。
- 影響方向: 不定。candidateを不安定にして特徴を隠す可能性も、activeを弱くして偽の改善を大きくする可能性もあります。
- 最小確認: 2018–2020の3年fixtureで「評価開始2019、学習開始2018」を指定し、2019がvalidに入り、2018がtrainに入ることをassert。実DBでは「全履歴load＋valid開始2019」と現行artifactを同一race集合で比較します。

これは metric の問題ではなく、estimand を作るfold実装の問題です。

### 2. partial-ingest は実際には除外されていない

- 疑義: [dataset.py:71](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/dataset.py:71) の eligibility は、STARTED集合内に `win=1` がちょうど1頭いるかだけです。コメントはpartial-ingestを除外するとしていますが、勝者行だけ取得済みなら通ります。
- `RaceResult` の stopped/disqualified を含む全STARTED馬の結果行充足を確認していません。また [dataset.py:159](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/dataset.py:159) は全馬DNFレースを評価集合から消し、その集合を [foldfit.py:57](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/foldfit.py:57) が学習集合にも使うため、productionのstarted-all学習母集団ともずれます。
- 影響方向: arm選択自体は対称ですが、欠損馬を含むfield size・枠順・field composition特徴には系統誤差になります。特徴を隠す方向にも偽改善方向にもなり得ます。
- 最小確認:
  1. STARTED 4頭、winner結果1行＋2着結果1行、残り2頭の結果行欠損というfixtureが現在 `eligible=True` になることを確認。
  2. 実DBで `n_started == n_result_rows(FINISHED/STOPPED/DISQUALIFIED)` を満たさないrace数と、その除外前後のpaired ΔNLLを比較。
  3. all-DNF raceをwinner NLL scoringからは除外しつつ、outer-trainには残した再計算と比較。

### 3. critical subgroup を実行せず ADOPT できる

- 疑義: CLIの `--subgroups` は既定offです（[cli.py:755](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/cli.py:755)）。`subgroups=None` の場合、[decision.py:82](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/decision.py:82) はconfigにcritical subgroupがあっても検査を丸ごと飛ばします。既存テストもこのADOPT経路を正として固定しています。
- また `no_decision_min_days=10` は全体CIにしか効かず、subgroupは [subgroups.py:65](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/subgroups.py:65) で2日以上あればPASS可能です。
- 影響方向: 特徴を隠す方向ではなく、偽ADOPT方向。
- 最小確認:
  - critical subgroupあり、`subgroups=None`、main gate全PASS → 本来NO_DECISIONだが現在ADOPT。
  - critical subgroup 2～9日、CI上限がmargin未満 → 現在PASSだが本来NO_DECISION。

### 4. paired-eval が確率整合性を検査していない

- 疑義: harnessは [harness.py:163](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/harness.py:163) で `check_consistency()` を呼びますが、paired経路の [paired.py:315](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/paired.py:315) にはありません。
- candidateが全馬にwin=0.9を返しても、winnerの0.9だけで低NLLを得られます。
- 影響方向: 偽改善・偽ADOPT方向。
- 最小確認: 4頭全てにwin=0.9を返すfake candidateを投入し、現在は採点されることを確認。
- 現行 `LightGBMPredictor` はrace-normalizeしているため、lgbm-065の既知結果がこれで汚染された証拠はありません。契約防御の欠落です。

### 5. calibration gate の指標名と計算が一致しない

- gate-configは `metric="mean_ece"` ですが、[paired.py:150](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/paired.py:150) は pooled started-all のequal-mass ECEを計算し、[paired.py:176](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/paired.py:176) が常にそれをゲートします。`metric` キー自体は読まれていません。
- mean fold ECE、pooled equal-width ECE、equal-mass ECEは別の量です。
- 影響方向: データ依存で、特徴を隠す／偽通過の両方。
- 最小確認: 同じ予測から3定義を算出し、`noninferior_width=0.001` の判定差を比較。
- なお、事前登録済みのodds/p/q/tail ECEは `tasks.md` でも未実装と明記されています。PRIMARYには影響しませんが、v2契約は未完了です。

### 6. bootstrap本体は概ね正しいが、クラスタ定義に相反する偏りがある

欠陥でない点:

- [bootstrap.py:51](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/bootstrap.py:51) は開催日を丸ごと再標本化し、各replicateで全raceをpoolします。race-equal meanというPRIMARYと整合しています。
- seed固定はバイアスを生みません。再現性のために妥当です。

疑義:

- keyはISO日付だけです（[paired.py:327](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/paired.py:327)）。同日複数場を一クラスタにするため、場間相関が弱ければCIを広げ、特徴を隠します。
- 一方、土日・同一開催の系列相関は保存しないため、正の系列相関があればCIを狭め、偽改善を通します。
- meeting感度はconfigにありますが未実装です。[bootstrap.py:90](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/bootstrap.py:90) は2/3/4日とweekだけです。
- [paired.py:330](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/paired.py:330) はconfigの`alpha`を渡していません。現行値0.05はdefaultと同じなので現在の数値影響はありません。

最小実験は、固定済みper-race差に対し以下のSE/CIを並べることです。

- 暦日
- venue×date
- ISO week
- 実meeting
- B=20,000以上、複数seed

方向は実測まで確定できません。

### 7. residual probe とフル特徴paired-evalは別estimand

これは最も重要な解釈上の点です。

- probeは「固定したactive pに、低次元のlog-linear residual headを後付けした効果」です。
- フル評価は「特徴を木に追加し、booster・split・正則化・校正を全て再学習した効果」です。
- 単調変換や既存特徴の再表現では、probeの線形headは滑らかな補正を直接表現できますが、木への冗長列追加はほぼ同じsplit候補にしかなりません。したがって今回の `log(1+days_since_last)` のようなケースでは、probeがフル特徴利得を過大評価する方向が強いです。
- 逆に、非線形・条件付きinteractionが本質なら、単一γは過小評価します。全候補に共通する一方向のバイアスではありません。

具体的な実装上の問題もあります。

- [residual_probe.py:149](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/residual_probe.py:149) の `n_races` とcoverageは全foldを数えますが、primary ΔNLLは [residual_probe.py:161](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/residual_probe.py:161) でfold 0を除外します。報告標本数とprimaryの標本数が不一致です。
- NaN→0（[residual_probe.py:58](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/residual_probe.py:58)）は「完全に中立」ではありません。デビュー馬のgap欠損をgap=0相当として扱い、観測済み馬との相対確率を動かすため、傾きとmissingness効果を混同します。
- OOF cache生成はmissing predictionを黙ってskipします（[folklore_probe.py:77](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/folklore_probe.py:77)）。その後 `_clean()` が残った馬だけでpを再正規化するため、partial fieldが採点され得ます。
- `--reuse-cache` は [folklore_probe.py:212](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/folklore_probe.py:212) でspec/window/race-set/cache digestを検証しません。
- `fit_gamma()` は説明上「damped Newton」ですが、実際はline searchなしのfull Newtonです。収束状態も報告されません。信号を隠す方向の数値失敗があり得ます。

最小確認:

1. `n_all_races` と `n_scored_races` を分け、2019 foldを含む／除く値を比較。
2. NaN=0、明示missing indicator、観測値中心化＋missing indicatorの3probeを比較。
3. 各raceでSTARTED数＝cache行数、Σp=1、winner=1をassert。
4. 同じheld-out foldでoffset headと実際のfull-feature refitを直接比較。

## baselineのcalib_frac=0.3について

これは単独では欠陥ではありません。

- activeの実recipeが0.3なら、activeを各foldで0.3として再現するのが正しいbaselineです。
- feature candidateも同じ0.3なら、非効率は両arm共通です。
- activeだけ0.1/全履歴に強化すると、もはや「activeへの特徴追加」ではなく別の複合仮説になります。

ただし、前述の「評価窓が学習履歴を切る」問題は別です。これは0.3以前にbaselineを本来と異なるモデルへ変えます。

## シンプルな指標改善案

PRIMARYは変えないことを推奨します。将来の事前登録で、次の診断指標を1つ追加するのが最も安全です。

- `ΔNLL_all`: 現行の全eligible race平均。採用PRIMARYのまま。
- `ΔNLL_opportunity`: 結果を見ずに固定した「候補hが利用可能で、レース内変動があるrace」だけのpaired winner NLL。
- 同時に `opportunity_coverage` を報告。

これで、

- 条件付きでも効果なし
- 条件付き効果はあるがcoverageで希釈
- 全体効果も十分

を分離できます。`ΔNLL_opportunity` 単独でADOPTしてはいけません。

現行結果を見た後に95%両側CIを片側CIへ変更したり閾値を緩めることは勧めません。まず上記の実装欠陥を別contract versionで修正・事前登録し、同じ固定仮説を再評価するのが妥当です。
