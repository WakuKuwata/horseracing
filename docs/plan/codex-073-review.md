# Codex 設計レビュー — spec 071 (Evaluation Contract Correctness)

**取得**: 2026-07-15 `codex exec --sandbox read-only` (codex-cli 0.144.1)。実リポジトリ読解に基づく指摘。

### 1. 日単位 split は active モデルへ即時適用しない

現在の本番学習経路は明確に `split_train_by_time` を呼んでいます。[predictor.py](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/predictor.py:164)

split を変更すると、calibration 行だけでなく、TE encoder の fit 母集団、booster、calibrator がすべて変わります。[predictor.py](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/predictor.py:172)

一方、serving は保存済み artifact をロードするだけなので、コードを変更しても既存 active の予測は直ちには変わりません。ただし同じ `model_version` で再学習すると破壊的です。

推奨方針は次です。

- split を `calibration_split_unit=race_count_v1|race_day_v1` として recipe の明示的な意味論にする
- 既存 active は `race_count_v1` として digest ごと凍結
- 新規学習だけ `race_day_v1` を必須にする
- 071 では再学習、昇格、active artifact の書き換えを禁止
- 日単位 split のモデル再学習・候補評価は別 feature にする

`FEATURE_VERSION` は不変でもよいですが、`recipe_hash` と `model_version` は必ず変わるべきです。

また、active が本当に `lgbm-062` かは feature 開始時に DB で確定させる必要があります。068 文書には DB active が 063 だったとの記載があります。リポジトリ上の 062/063 は model・calibrator・preprocessor の SHA-256 が同一でしたが、version を推測で固定してはいけません。

### 2. 現在の recipe と artifact は immutable 契約を満たしていない

現在の `ModelRecipe` には split 戦略、ordered feature hash、booster params、データスナップショット、後段確率変換が含まれていません。[recipe.py](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/recipe.py:30)

さらに artifact 保存は既存ディレクトリとファイルを上書きでき、DB 登録も upsert です。[artifacts.py](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/artifacts.py:106)

したがって、`base_model_version + recipe_hash + feature_version` だけでは不十分です。最低限、次を content-addressed manifest に含めるべきです。

- split 単位と全学習パラメータ
- feature_version と順序付き feature hash
- base model、preprocessor、calibrator の checksum
- OOF sample の race集合・生成モデル・結果データ hash
- fit cutoff/window
- two-gamma、stage discount の順序、対象確率、パラメータ
- code SHA、schema version

disk artifact だけで migration ゼロは実現可能です。ただし条件は以下です。

- create-only と atomic rename
- 同じ key・同じ内容は冪等成功
- 同じ key・異なる内容は conflict
- 欠損、checksum 不一致、世代不一致、未知 schema は fail-closed
- `latest` 解決を禁止
- identity calibrator も明示的 artifact とする

### 3. 「immutable 化」だけでは校正リークは直らない

two-gamma 校正は現在、race ごとの latest PredictionRun をモデル世代で絞らず取得しています。[model_calibration.py](/Users/kuwatawaku/workspace/horseracing/probability/src/horseracing_probability/model_calibration.py:232)  
呼び出し側も `base_model_version` を渡していません。[betting CLI](/Users/kuwatawaku/workspace/horseracing/betting/src/horseracing_betting/cli.py:133)

stage discount にも同様の latest-run 問題があります。

さらに重大なのは、過去レース用の prediction が「そのレース結果まで含めて学習した full-history model」で生成されていれば、単に artifact を凍結しても OOS にはならない点です。

したがって校正 sample は、必ず recipe-faithful な walk-forward OOF prediction から作る必要があります。これは新しい市場データや DB migration なしでも可能ですが、単なる manifest 化より大きい仕事です。

### 4. gate は boolean AND ではなく三値の単一判定にする

現状は main gate と subgroup guard が別々に返され、CLI が別表示しています。[paired.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/paired.py:343)

`eval_window` と `no_decision_min_days=10` は gate config にありますが、実判定に十分結線されていません。空の recent window が実質通る経路もあります。

最終結果は単一の enum が安全です。

- `ADOPT`: main PASS かつ全 critical subgroup PASS
- `REJECT`: 主指標または十分な標本を持つ critical subgroup が FAIL
- `NO_DECISION`: 期間不足、開催日不足、critical subgroup の標本不足、必須データ欠損

confirmatory mode では、未知・欠落 config、評価期間不一致、gate hash 不一致を即時エラーにすべきです。

### 5. started-all と監査 artifact がまだ未完了

通常 harness は依然として finished horses のみを対象にしています。started-all は paired 側に限定されています。[harness.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/harness.py:99)

071 で正しさを主張するなら、068 の未完了事項に加えて、report へ次を必須化する必要があります。

- evaluation_contract_version
- canonical gate-config hash
- source/result/race-set hash
- candidate/base recipe hash
- artifact checksum
- started-all 集合と除外理由
- deterministic rerun 証跡

## bootstrap の扱い

現実装は「race-day cluster bootstrap」です。各日を独立に再標本化しており、moving block ではありません。[bootstrap.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/bootstrap.py:32)

推奨は以下です。

- 現実装を `race_day_cluster_bootstrap_ci_v1` に改名し、数値を完全維持
- 2/3/4 開催日、開催週、開催単位を v2 sensitivity として追加
- block の重複、端点、休催日、複数場同時開催の定義を事前固定
- sensitivity を全部 gate の AND 条件にしない
- 将来の primary estimator を一つだけ事前登録し、残りは diagnostic

068/069/070 の既存判定は `evaluation_contract_version=v1` の immutable な履歴として残し、v2 再計算は「参考再生」に限定してください。過去 verdict の上書き・再分類は禁止です。

## ECE の定義をもう一段具体化する必要がある

評価対象を次の段階に分けるべきです。

1. raw booster score
2. model 内部 calibration + race normalization 後の win probability
3. two-gamma 後の win probability
4. stage discount 後の top2/top3 probability

stage discount は win ECE の評価対象ではありません。top2/top3 ECE として測る必要があります。

また、tail を candidate 自身の bet 対象で定義すると、arm ごとに評価集合が変わります。confirmatory な比較では、事前登録した共通 mask、または active/base policy 由来の result-blind mask を使うべきです。arm 固有 tail は diagnostic に降格してください。

確率帯、odds帯、q帯には固定境界、欠損 bucket、最低件数・最低開催日数、`NO_DECISION` 規則が必要です。

## realized 改名は公開 API の破壊的変更

`realized_return` と `realized_roi` は API schema に露出しています。[backtest.py](/Users/kuwatawaku/workspace/horseracing/api/src/horseracing_api/backtest.py:22) [schemas.py](/Users/kuwatawaku/workspace/horseracing/api/src/horseracing_api/schemas.py:331)

front だけでなく、front/admin 双方の OpenAPI snapshot と生成 TypeScript 型へ波及します。したがって「表示文言だけ」ではなく API contract migration です。

名称も一つに潰さず、意味を分ける方が安全です。

- `counterfactual_snapshot_gross_return`
- `counterfactual_snapshot_net_return`
- `counterfactual_snapshot_recovery_rate`
- `valuation_basis`
- `outcome_known_count` または `n_scored`

特に favorite 側は現在の `race_horses.odds` を参照しており、decision-time snapshot と保証できません。[recommendations.py](/Users/kuwatawaku/workspace/horseracing/api/src/horseracing_api/routers/recommendations.py:99)  
capture timestamp がないものを一律 `snapshot` と呼ぶのは避け、`current_odds` 等の provenance を明示すべきです。

DB write を増やさない限り read-only 境界は維持できます。ただし API・front・admin・OpenAPI drift check は原子的に変更する必要があります。

## 070 と prospective holdout

070 は単に「F03 REJECT」ではなく、現状の正確な status matrix を凍結してください。registry では F03/F04/F05 が rejected/unwired とされています。[registry.py](/Users/kuwatawaku/workspace/horseracing/features/src/horseracing_features/registry.py:207)

過去文書を書き換えず、commit・verdict artifact の hash を参照する append-only な supersession 記録を追加するのがよいです。

prospective holdout は、データ収集が始まっていない以上 `STARTED` ではなく `DORMANT` または `AWAITING_CAPTURE` にします。時計を開始するのは、以下が揃った後の最初の対象イベントです。

- immutable recipe
- gate と停止規則
- capture 稼働
- contamination/invalidation 規則
- 最初の対象レース

なお、現行憲法は market odds を履歴保存せず上書きするとしています。[constitution.md](/Users/kuwatawaku/workspace/horseracing/.specify/memory/constitution.md:77)  
将来 ROI 台帳を実装する feature では、先に憲法改定が必要です。

## 推奨する feature 分割

1. **071: Evaluation Contract v2 and Historical Freeze**
   - split 戦略の recipe 化
   - active legacy recipe/digest 凍結
   - started-all、E2E、決定論
   - 三値 gate、期間・最低日数の実結線
   - bootstrap v1 改名と v2 sensitivity
   - 070 freeze、development evidence、dormant preregistration
   - 再学習・昇格なし

2. **072: Immutable Probability Pipeline Artifact**
   - base model artifact 自体の create-only 化
   - OOF-only two-gamma/stage discount
   - content-addressed manifest
   - evaluation/serving の final-probability parity

3. **073: Counterfactual Return API Terminology**
   - API schema、front、admin、OpenAPI、fixture の一括 migration
   - provenance と gross/net/recovery の意味分離

4. **別 feature: Day-split Retraining and Promotion**
   - `race_day_v1` で新 model_version を学習
   - accuracy 変化と契約変更を分離して評価
   - 採用後に新 baseline 化

最終確率 ECE を 071 に残すなら、072 を先行依存にする必要があります。そうでなければ 071 の ECE は model-internal calibrated probability までに限定してください。

## 必須テスト

最低限、次を acceptance criteria に入れるべきです。

- active model/version が一意で artifact digest が前後不変
- legacy split の mask 再現、day split の同一日跨ぎゼロ
- split 戦略変更で recipe hash が必ず変化
- 同じ model_version・異なる bytes の保存拒否
- started-all の real DB paired E2E を2回実行し一致
- `ADOPT/REJECT/NO_DECISION` の真理値表
- 9日と10日の境界、空 window、critical subgroup 不足
- bootstrap v1 golden、v2 block membership golden、seed 決定論
- OOF sample 全件で `model_fit_through < race_date`
- 別モデルの latest prediction を追加しても校正 artifact が不変
- manifest 欠損・改竄・世代不一致の fail-closed
- serving と evaluation の各確率段階が一致
- odds 変更で予測不変、結果変更で特徴・予測不変
- rename 前後で数値完全一致
- API/front/admin/OpenAPI 型の同期
- GET 経路の no-write
- 070 exact status matrix と unwired 状態の固定

したがって、071 の現在案は「削るべき」よりも、**契約修正部分だけを残して三分割する**のが最も安全です。特に active モデルを日単位 split で再学習する作業は、071 から明示的に排除するのがよいです。
tokens used
279,967
結論は **Request Changes** です。方向性は妥当ですが、現状の (a) を単一 spec にすると「評価契約の修正」「確率パイプラインの変更」「公開 API の破壊的変更」「モデル再学習」が交絡します。

独立レビューとも突き合わせましたが、特に split の recipe 化、OOF 校正、三値ゲート、API 分離が共通の主要指摘でした。実装・ファイル変更は行っていません。

## 主要な設計上の穴

### 1. 日単位 split は active モデルへ即時適用しない

現在の本番学習経路は明確に `split_train_by_time` を呼んでいます。[predictor.py](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/predictor.py:164)

split を変更すると、calibration 行だけでなく、TE encoder の fit 母集団、booster、calibrator がすべて変わります。[predictor.py](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/predictor.py:172)

一方、serving は保存済み artifact をロードするだけなので、コードを変更しても既存 active の予測は直ちには変わりません。ただし同じ `model_version` で再学習すると破壊的です。

推奨方針は次です。

- split を `calibration_split_unit=race_count_v1|race_day_v1` として recipe の明示的な意味論にする
- 既存 active は `race_count_v1` として digest ごと凍結
- 新規学習だけ `race_day_v1` を必須にする
- 071 では再学習、昇格、active artifact の書き換えを禁止
- 日単位 split のモデル再学習・候補評価は別 feature にする

`FEATURE_VERSION` は不変でもよいですが、`recipe_hash` と `model_version` は必ず変わるべきです。

また、active が本当に `lgbm-062` かは feature 開始時に DB で確定させる必要があります。068 文書には DB active が 063 だったとの記載があります。リポジトリ上の 062/063 は model・calibrator・preprocessor の SHA-256 が同一でしたが、version を推測で固定してはいけません。

### 2. 現在の recipe と artifact は immutable 契約を満たしていない

現在の `ModelRecipe` には split 戦略、ordered feature hash、booster params、データスナップショット、後段確率変換が含まれていません。[recipe.py](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/recipe.py:30)

さらに artifact 保存は既存ディレクトリとファイルを上書きでき、DB 登録も upsert です。[artifacts.py](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/artifacts.py:106)

したがって、`base_model_version + recipe_hash + feature_version` だけでは不十分です。最低限、次を content-addressed manifest に含めるべきです。

- split 単位と全学習パラメータ
- feature_version と順序付き feature hash
- base model、preprocessor、calibrator の checksum
- OOF sample の race集合・生成モデル・結果データ hash
- fit cutoff/window
- two-gamma、stage discount の順序、対象確率、パラメータ
- code SHA、schema version

disk artifact だけで migration ゼロは実現可能です。ただし条件は以下です。

- create-only と atomic rename
- 同じ key・同じ内容は冪等成功
- 同じ key・異なる内容は conflict
- 欠損、checksum 不一致、世代不一致、未知 schema は fail-closed
- `latest` 解決を禁止
- identity calibrator も明示的 artifact とする

### 3. 「immutable 化」だけでは校正リークは直らない

two-gamma 校正は現在、race ごとの latest PredictionRun をモデル世代で絞らず取得しています。[model_calibration.py](/Users/kuwatawaku/workspace/horseracing/probability/src/horseracing_probability/model_calibration.py:232)  
呼び出し側も `base_model_version` を渡していません。[betting CLI](/Users/kuwatawaku/workspace/horseracing/betting/src/horseracing_betting/cli.py:133)

stage discount にも同様の latest-run 問題があります。

さらに重大なのは、過去レース用の prediction が「そのレース結果まで含めて学習した full-history model」で生成されていれば、単に artifact を凍結しても OOS にはならない点です。

したがって校正 sample は、必ず recipe-faithful な walk-forward OOF prediction から作る必要があります。これは新しい市場データや DB migration なしでも可能ですが、単なる manifest 化より大きい仕事です。

### 4. gate は boolean AND ではなく三値の単一判定にする

現状は main gate と subgroup guard が別々に返され、CLI が別表示しています。[paired.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/paired.py:343)

`eval_window` と `no_decision_min_days=10` は gate config にありますが、実判定に十分結線されていません。空の recent window が実質通る経路もあります。

最終結果は単一の enum が安全です。

- `ADOPT`: main PASS かつ全 critical subgroup PASS
- `REJECT`: 主指標または十分な標本を持つ critical subgroup が FAIL
- `NO_DECISION`: 期間不足、開催日不足、critical subgroup の標本不足、必須データ欠損

confirmatory mode では、未知・欠落 config、評価期間不一致、gate hash 不一致を即時エラーにすべきです。

### 5. started-all と監査 artifact がまだ未完了

通常 harness は依然として finished horses のみを対象にしています。started-all は paired 側に限定されています。[harness.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/harness.py:99)

071 で正しさを主張するなら、068 の未完了事項に加えて、report へ次を必須化する必要があります。

- evaluation_contract_version
- canonical gate-config hash
- source/result/race-set hash
- candidate/base recipe hash
- artifact checksum
- started-all 集合と除外理由
- deterministic rerun 証跡

## bootstrap の扱い

現実装は「race-day cluster bootstrap」です。各日を独立に再標本化しており、moving block ではありません。[bootstrap.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/bootstrap.py:32)

推奨は以下です。

- 現実装を `race_day_cluster_bootstrap_ci_v1` に改名し、数値を完全維持
- 2/3/4 開催日、開催週、開催単位を v2 sensitivity として追加
- block の重複、端点、休催日、複数場同時開催の定義を事前固定
- sensitivity を全部 gate の AND 条件にしない
- 将来の primary estimator を一つだけ事前登録し、残りは diagnostic

068/069/070 の既存判定は `evaluation_contract_version=v1` の immutable な履歴として残し、v2 再計算は「参考再生」に限定してください。過去 verdict の上書き・再分類は禁止です。

## ECE の定義をもう一段具体化する必要がある

評価対象を次の段階に分けるべきです。

1. raw booster score
2. model 内部 calibration + race normalization 後の win probability
3. two-gamma 後の win probability
4. stage discount 後の top2/top3 probability

stage discount は win ECE の評価対象ではありません。top2/top3 ECE として測る必要があります。

また、tail を candidate 自身の bet 対象で定義すると、arm ごとに評価集合が変わります。confirmatory な比較では、事前登録した共通 mask、または active/base policy 由来の result-blind mask を使うべきです。arm 固有 tail は diagnostic に降格してください。

確率帯、odds帯、q帯には固定境界、欠損 bucket、最低件数・最低開催日数、`NO_DECISION` 規則が必要です。

## realized 改名は公開 API の破壊的変更

`realized_return` と `realized_roi` は API schema に露出しています。[backtest.py](/Users/kuwatawaku/workspace/horseracing/api/src/horseracing_api/backtest.py:22) [schemas.py](/Users/kuwatawaku/workspace/horseracing/api/src/horseracing_api/schemas.py:331)

front だけでなく、front/admin 双方の OpenAPI snapshot と生成 TypeScript 型へ波及します。したがって「表示文言だけ」ではなく API contract migration です。

名称も一つに潰さず、意味を分ける方が安全です。

- `counterfactual_snapshot_gross_return`
- `counterfactual_snapshot_net_return`
- `counterfactual_snapshot_recovery_rate`
- `valuation_basis`
- `outcome_known_count` または `n_scored`

特に favorite 側は現在の `race_horses.odds` を参照しており、decision-time snapshot と保証できません。[recommendations.py](/Users/kuwatawaku/workspace/horseracing/api/src/horseracing_api/routers/recommendations.py:99)  
capture timestamp がないものを一律 `snapshot` と呼ぶのは避け、`current_odds` 等の provenance を明示すべきです。

DB write を増やさない限り read-only 境界は維持できます。ただし API・front・admin・OpenAPI drift check は原子的に変更する必要があります。

## 070 と prospective holdout

070 は単に「F03 REJECT」ではなく、現状の正確な status matrix を凍結してください。registry では F03/F04/F05 が rejected/unwired とされています。[registry.py](/Users/kuwatawaku/workspace/horseracing/features/src/horseracing_features/registry.py:207)

過去文書を書き換えず、commit・verdict artifact の hash を参照する append-only な supersession 記録を追加するのがよいです。

prospective holdout は、データ収集が始まっていない以上 `STARTED` ではなく `DORMANT` または `AWAITING_CAPTURE` にします。時計を開始するのは、以下が揃った後の最初の対象イベントです。

- immutable recipe
- gate と停止規則
- capture 稼働
- contamination/invalidation 規則
- 最初の対象レース

なお、現行憲法は market odds を履歴保存せず上書きするとしています。[constitution.md](/Users/kuwatawaku/workspace/horseracing/.specify/memory/constitution.md:77)  
将来 ROI 台帳を実装する feature では、先に憲法改定が必要です。

## 推奨する feature 分割

1. **071: Evaluation Contract v2 and Historical Freeze**
   - split 戦略の recipe 化
   - active legacy recipe/digest 凍結
   - started-all、E2E、決定論
   - 三値 gate、期間・最低日数の実結線
   - bootstrap v1 改名と v2 sensitivity
   - 070 freeze、development evidence、dormant preregistration
   - 再学習・昇格なし

2. **072: Immutable Probability Pipeline Artifact**
   - base model artifact 自体の create-only 化
   - OOF-only two-gamma/stage discount
   - content-addressed manifest
   - evaluation/serving の final-probability parity

3. **073: Counterfactual Return API Terminology**
   - API schema、front、admin、OpenAPI、fixture の一括 migration
   - provenance と gross/net/recovery の意味分離

4. **別 feature: Day-split Retraining and Promotion**
   - `race_day_v1` で新 model_version を学習
   - accuracy 変化と契約変更を分離して評価
   - 採用後に新 baseline 化

最終確率 ECE を 071 に残すなら、072 を先行依存にする必要があります。そうでなければ 071 の ECE は model-internal calibrated probability までに限定してください。

## 必須テスト

最低限、次を acceptance criteria に入れるべきです。

- active model/version が一意で artifact digest が前後不変
- legacy split の mask 再現、day split の同一日跨ぎゼロ
- split 戦略変更で recipe hash が必ず変化
- 同じ model_version・異なる bytes の保存拒否
- started-all の real DB paired E2E を2回実行し一致
- `ADOPT/REJECT/NO_DECISION` の真理値表
- 9日と10日の境界、空 window、critical subgroup 不足
- bootstrap v1 golden、v2 block membership golden、seed 決定論
- OOF sample 全件で `model_fit_through < race_date`
- 別モデルの latest prediction を追加しても校正 artifact が不変
- manifest 欠損・改竄・世代不一致の fail-closed
- serving と evaluation の各確率段階が一致
- odds 変更で予測不変、結果変更で特徴・予測不変
- rename 前後で数値完全一致
- API/front/admin/OpenAPI 型の同期
- GET 経路の no-write
- 070 exact status matrix と unwired 状態の固定

したがって、071 の現在案は「削るべき」よりも、**契約修正部分だけを残して三分割する**のが最も安全です。特に active モデルを日単位 split で再学習する作業は、071 から明示的に排除するのがよいです。
