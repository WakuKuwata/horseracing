# モデル予測精度向上 提案書

**作成日**: 2026-07-12  
**状態**: Proposal  
**対象**: 対象レース自身の市場情報から独立した競走能力予測を主目的とする `features-017 / lgbm-062` 系モデル

特徴量の列単位契約・現行128列の棚卸し・次期bundleの正本は、[モデル特徴量 再制定書](model-feature-redesign.md) とする。

## 1. 結論

次の精度改善は、新しい特徴量を大量に追加する前に、以下の順で進める。

1. 評価母集団・比較方法を揃え、モデル改善を正しく判定できるようにする。
2. 校正のために最新30%をLightGBM本体の学習から外している構造を見直す。
3. 2025–2026に恒久欠損した特徴の撤去判断と、既存列の誤定義・重複の修正を行う（raw CSV入手不可が確定したためcoverage修復は行わない）。
4. `pl_topk` 目的関数に合った時系列HPOとearly stoppingを導入する。
5. 過去レースのオッズ情報を、strictly-beforeの履歴特徴として拡張する（067 entity-identity-resolutionのID断層解消を前提とする）。
6. 効果実績の大きいスピード指数を、馬場差・階層基準タイム方向へ拡張する。

最初に実施する実験は、同一データスナップショット上での次の比較とする。

- A: 現行の70%モデル学習 + 最新30% isotonic校正
- B: 90%モデル学習 + 最新10% isotonic校正
- C: 時系列OOF予測でtemperature校正器を学習 + LightGBM本体を全履歴で再学習
- D: 時系列OOF予測でrace-normalized power校正器を学習 + LightGBM本体を全履歴で再学習

この比較では特徴量・目的関数・seedを固定し、校正と学習データ配分だけの限界効果を測る。

## 2. 今回確定した方針

### 2.1 馬体重更新

馬体重発表後の予測更新は現行運用で手動実施しているため、本提案の対象外とする。自動再予測や予測時点別モデルは今回扱わない。

### 2.2 市場情報の利用境界

対象レース自身の発走前オッズ・人気は、モデル特徴量およびモデル選択に使用しない。市場offsetモデルや対象レースのモデル確率と市場確率のblendも、本提案の本線には含めない。

一方、過去レースの確定オッズ・人気は、その後のレースを予測する時点で既知の履歴情報であるため使用可能とする。ただし次の境界を必須とする。

- 対象レースを `R`、履歴レースを `r` としたとき、`race_date(r) < race_date(R)` のみ利用する。
- 対象レース当日、対象レース自身、未来レースのオッズ・人気は利用しない。
- 過去レースの結果を使う派生量も、対象レース時点では確定済みの過去結果だけから作る。
- 欠損オッズを推定値で補完せず、欠損・利用件数を別特徴として保持する。
- 特徴生成テストで、対象レース・同日・未来のオッズを変更しても出力が変わらないことを検証する。

現在のregistryには、過去人気を使う `asof_mkt_rank_avg`、`asof_mkt_rank_norm_avg`、`asof_mkt_rank_best`、`asof_beat_mkt_avg` が存在する。今回の方針ではこれらを許容し、対象レース自身のオッズを使う `market_offset` とは明確に分離する。

なお本方針は、Feature 058時点の「default意思決定支援モデルにはpast_marketを含めない（p⊥q）」という決定を明示的に上書きする。現行active `lgbm-062` は既に `asof_mkt_*` 4列を含む128列で学習済み（gain share約9.19%）であり、本書はこのde facto状態を正式方針として追認する。040/066のp vs q乖離表示の前提は「pは**対象レース自身の**市場から独立」に限定される（過去レースの市場履歴はpに含まれる）。

## 3. 現状認識

現行artifactは次の構成である。

- feature version: `features-017`
- model version: `lgbm-062`
- objective: `pl_topk`
- calibration: isotonic
- target encoding: jockey / trainer
- win LogLoss: 0.214886
- top2 LogLoss: 0.338216
- top3 LogLoss: 0.428645
- win ECE: 0.000687

根拠は [`artifacts/model_versions/lgbm-062/metadata.json`](../../artifacts/model_versions/lgbm-062/metadata.json) に保存されている。

直近の有効な改善は、スピード指数とclass normalization修正である。一方、Elo、closing-3F単独、単純なモデルblendは既存評価で不採用または効果消失となっている。したがって同じ軸の小変更を繰り返すより、学習データ利用・目的関数整合・新情報量の改善を優先する。

## 4. 現行パイプラインの主要課題

### 4.1 最新30%がモデル本体の学習に使われない

`DEFAULT_CALIB_FRAC = 0.3` により、各walk-forward foldの学習期間を古い70%のmodel-fitと最新30%のcalibration-fitに分割している。LightGBM本体はmodel-fit側だけを学習する。

現行artifactでは次の配分である。

- model-fit: 673,561行
- calibration-fit: 277,841行

校正データを減らす、または時系列OOF予測から校正器を学習できれば、LightGBM本体の学習量、とりわけ直近期間の学習量を増やせる可能性が高い。

また、現在の `train_through` は全training frameの最大日であり、boosterが実際に学習した最終日とは限らない。今後は次を個別に記録する。

- `model_fit_through`
- `calib_from`
- `calib_through`
- `n_model_rows`
- `n_calib_rows`

### 4.2 学習母集団と評価母集団が一致していない

trainingはstarted全馬を対象とし、DNF・失格等もwin=0として学習する。一方、現在のevaluationはfinished馬だけを採点している。

race-softmaxはstarted全馬に確率を配るため、PRIMARY評価も同じ母集団に揃える。今後は次を併記する。

1. race-level winner NLL: 1レース1標本の `-log(p_winner)`
2. started-all LogLoss / Brier: DNF・失格等を0として含む
3. 現行finished-only指標: 過去結果との互換比較専用

### 4.3 現activeとのpaired比較になっていない経路がある

`train-evaluate` は候補モデルを現在DBから再評価するが、baselineは `model_versions.metrics_summary` の保存値を読む。データbackfill、materialization、母集団変更があると、同一race集合での比較にならない可能性がある。

以後の採用判定は、候補と現activeを次の条件で同時評価する。

- 同一DB/source fingerprint
- 同一materialized manifest
- 同一race_id集合とrace_id hash
- 同一fold境界
- 同一評価コードversion
- race単位のpaired loss差

uniform baselineはsanity checkとして残すが、active昇格の比較対象にはしない。

### 4.4 現行校正はrace構造を直接最適化していない

現在は馬単位isotonic変換後にレース内で再正規化する。再正規化後の確率は、isotonicが直接最適化した確率とは異なる。

race-softmaxモデルでは、順位を壊さずレース内合計1を保つ次の方式を比較対象とする。

- identity
- 現行isotonic + race normalization
- temperature scaling
- race-normalized power calibration
- two-gamma calibration

方式選択は外側validを見ず、各外側foldのtrain内だけで行う。

## 5. 提案する改善ロードマップ

### Phase -1: 特徴量の撤去判断・定義修正（改訂: 2025–2026 raw CSV入手不可が確定）

**確定事実（2026-07-12）**: 2025–2026のJRA-VAN raw CSVは入手不可能。該当期間の行はnetkeiba scrape由来であり、テン3F・owner/breeder・賞金は恒久的に欠損する。したがって本Phaseは「coverage修復」ではなく「serving体制のregime shiftへの適応」である。

[モデル特徴量 再制定書](model-feature-redesign.md) のP0項目（改訂版）を実施する。

- owner 2列は撤去する（last-write-wins時点リーク + source恒久欠損の二重根拠。ablation不要）。
- 全NaN化したbundle（pace_first3f / race_level / breeder）は、直近窓（2025–2026 fold）のpaired ablationでdrop-or-keepを判定する。歴史foldでは効くため全fold平均では判定しない。
- running-style欠損、rate母集団、TE命名・時系列定義の修正は従来どおり実施するが、1件ずつ独立bundleとしてpaired評価する（撤去起因の変化と定義起因の変化を帰属可能に保つ）。
- 完全重複・定数列をbundle ablationする。
- 確定済みschemaで現active相当を再学習し、以後のbaselineを凍結する。

判定に直近窓paired評価が必要なため、本Phaseは**Phase 0の評価契約整備の後**に実施する。Phase 0/1/2は特徴coverageと独立であり、本Phaseをブロッカーにしない。新特徴bundle（Phase 3/4）の採用評価は本Phase完了後に開始する。

### Phase 0: 評価契約の是正

#### 目的

0.0001級の改善を誤採用せず、以後の実験を同じ物差しで比較できるようにする。

#### 実施内容

- race-level winner NLLをPRIMARY指標に追加する。
- started-all評価を追加する。
- 候補とactiveのOOF予測を同時生成し、race_id単位で保存する。
- 開催日単位のmoving/block bootstrapでpaired差の95%信頼区間を算出する。
- 全期間と直近期間を分けて報告する。
- ECEは固定10等幅に加え、equal-mass ECE、確率帯別・頭数別校正を報告する。

#### 採用ゲート

- PRIMARY: candidateのwinner NLLがactiveより小さい。
- 統計ガード: paired差の95%信頼区間上限が0未満。
- 直近ガード: 直近3年または5年で悪化しない。
- top2/top3: 事前固定したnon-inferiority幅以内。
- 校正: active比で非劣化。絶対ECE 0.05は非常停止用上限に格下げする。

### Phase 1: 校正分割と全履歴学習

#### 実験候補

| ID | Booster学習 | 校正データ | 校正方式 |
|---|---|---|---|
| A | 古い70% | 最新30% | isotonic（現行） |
| B | 古い90% | 最新10% | isotonic |
| C | 全履歴refit | 時系列OOF予測 | temperature |
| D | 全履歴refit | 時系列OOF予測 | race-normalized power |

C/Dでは、OOFモデルと全履歴refitモデルのraw score分布が変わるリスクがある。校正パラメータの移植可能性をvalidで確認し、悪化する場合はBを採用する。

#### 優先理由

追加データや新特徴なしで、LightGBM本体が利用する直近学習データを増やせるため、費用対効果が最も高い。

### Phase 2: `pl_topk` 対応HPOとearly stopping

現行HPOはsoftmax目的関数に未対応であり、productionの `pl_topk` は固定300 round・31 leaves・learning rate 0.05で学習されている。

#### 初回探索範囲

- `num_leaves`: 15 / 31 / 63
- `min_child_samples`: 50 / 200 / 500
- `reg_lambda`: 1 / 5 / 20
- feature fraction: 0.7 / 0.9 / 1.0
- learning rate: 0.02 / 0.05
- maximum rounds: 1,200
- early stopping: train内の直近inner-valid winner NLL
- PL stage weights: `(1.0, 0.5, 0.25)` を基準に、少数の事前固定候補だけを比較

全組み合わせ探索は行わず、直近foldでsuccessive halvingし、残った候補だけをフルwalk-forwardへ進める。

### Phase 3: 過去オッズ特徴の拡張

**実装・採用状況(2026-07-14)**: F02 pm_core_strength(`s=log(q×N)`)を [specs/069-past-odds-features](../../specs/069-past-odds-features/spec.md) で実装(features-018 純加算・068ゲートを 2026/nk: subgroup 三値 intersection-union へ拡張)。**本番採否 = ADOPT**: pl_topk 2024-2026 walk-forward で winner NLL −0.0057・068ゲート全 PASS・2026/nk: subgroup 全 PASS(067 のID断層下でも非劣化)。フル19-fold で **lgbm-064-f02acc(win LogLoss 0.21406=歴代最良 accuracy-first、−0.00083 vs lgbm-063)を CANDIDATE 登録**(default 意思決定支援モデルは p⊥q 維持で lgbm-063 のまま不変)。067 repair は実質適用済み(2026馬の76.7%連結)で当初懸念(coverage約15%)より良好。F03/F04/F05 は段階別 spec。

対象レース自身のオッズは使用せず、過去レースの確定オッズだけを履歴特徴にする。

2025–2026の `nk:` ID断層（2026年の過去5走coverage約15%）が過去履歴連結を弱めているため、本Phaseは [specs/067-entity-identity-resolution](../../specs/067-entity-identity-resolution/spec.md) によるID解決の進捗を前提条件とする。

列・数式・縮約・ID source別coverage契約は、[モデル特徴量 再制定書](model-feature-redesign.md) のF02〜F05を正とする。

#### 候補特徴

- `asof_mkt_logprob_avg`: 過去オッズの正規化市場確率をlogit/log変換した平均
- `asof_mkt_logprob_recent3`: 直近3走平均
- `asof_mkt_logprob_last`: 前走の市場評価
- `asof_mkt_logprob_best`: 過去最高の市場評価
- `asof_mkt_eval_trend`: 直近評価と長期平均の差
- `asof_mkt_surprise_avg`: 過去の市場期待に対する結果超過の縮約平均
- `asof_mkt_count`: 有効な過去オッズ件数
- 距離帯・surface別の過去市場評価。ただし十分な件数がない場合は全体値へ階層shrinkageする。

人気順位だけでなくオッズ量を使うことで、同じ1番人気でも市場確率25%と60%を区別できる。欠損率・年度別coverage・オッズ定義の一貫性を先に監査し、coverage不足の区間を黙って補完しない。

#### 採用単位

過去オッズ特徴は1つの事前登録bundleとして評価する。OOS結果を見て列を後から選別しない。必要なら次版で新しいbundleを事前登録する。

### Phase 4: スピード指数v2と区間ラップ

スピード指数は直近の特徴追加で最大級の改善を出したため、新特徴軸では最優先とする。

詳細定義は、[モデル特徴量 再制定書](model-feature-redesign.md) のF07を正とする。

#### 候補

1. 過去開催日のtrack variant・馬場差で補正した走破時計
2. going無し、距離帯、venue/surfaceへ順に縮約する階層基準タイム

pace-adjusted speed residual（F08）は入力のrace first3fが2025年以降のserving体制に存在しないため、区間ラップpace-shape特徴（F11）はnetkeiba取得ブロックによりbackfill不能のため、いずれも候補から撤回する。first3f・ラップの代替sourceが得られた場合のみ再検討する。

### Phase 5: 低優先の追加候補

- jockey / trainerの90日・365日recent form
- ordered/prequential target encoding
- TE posterior count・variance
- 異種モデルのOOF score ensemble

ensembleは、単純平均でなく異なる誤差構造を持つモデルが得られた場合だけ再検討する。外側OOF raw scoreを使い、race-softmax前のscore空間でblendする。

## 6. 今回再試行しない案

- Elo / Bradley-Terry ratingの小変更
- closing-3F単独特徴
- 既存特徴の単純な積・race内rankの追加
- binaryとPLモデルの単純確率平均
- 対象レース自身の発走前オッズを使う市場offset・blend
- 馬体重発表後の自動再予測
- LogLoss改善をそのままROI改善とみなす評価

## 7. 実験の実行順

**実装状況（2026-07-13）**: Phase 0（評価契約）+ Phase 1（校正分割 A/B/C/D）は [specs/068-evaluation-contract-calibration](../../specs/068-evaluation-contract-calibration/spec.md) で実装済み。`training paired-eval`（候補↔active を各fold再fit・winner NLL PRIMARY・開催日 block bootstrap CI・採用ゲート）と `training calib-split-eval`（A/B/C/D の screening→confirmation、disjoint窓）が実DBで動作。C/D は全履歴 booster + 開催日単位 strict-past OOF power 校正。実DB検証: SC-001/002/003 通過。後続の Phase 2/3/4 は本評価契約を物差しとして使う。

1. Phase 0の評価修正を実装する。
2. 現activeを新評価契約で計測し、基準OOF予測を凍結する。
3. Phase 1のA〜Dを直近foldで比較する。
4. 勝ち候補だけをフルwalk-forwardへ進める。
5. Phase -1の撤去判断・定義修正を直近窓paired評価で実施し、確定済みschemaでbaselineを更新する。
6. Phase 2のgroup-aware HPOを行う。
7. Phase 3の過去オッズbundleを単独評価する（067のID解決進捗を前提条件とする）。
8. Phase 4のspeed bundleを単独評価する。
9. 最後に採用候補を組み合わせ、現activeとのpairedフル評価を1回だけ実施する。

校正、HPO、特徴追加を同時に変更しない。各Phaseで限界効果を測り、採用済み変更だけを次のbaselineに積み上げる。

## 8. 成果物

各実験は以下を残す。

- 実験前に固定したhypothesis・候補・採用条件
- git SHA、feature version、model version
- DB/source fingerprint、materialized manifest hash
- train/model/calibration期間と行数
- fold別・全体・直近期間の指標
- race-level paired差とbootstrap CI
- OOF prediction artifact
- 採用・不採用理由
- 不採用案の再試行禁止条件、再検討条件

## 9. マルチエージェントsecond opinionの反映

独立した3系統の監査を行った。

- 学習パイプライン監査: 最新30%の校正holdout、uniform baselineとの比較、started/finished母集団差を最重要と判定。
- 検証・校正監査: race-level winner NLL、paired block bootstrap、race-aware calibrationを優先と判定。
- 特徴・モデル監査: `pl_topk` HPO、過去市場履歴、speed figure拡張を次の有力レバーと判定。

3系統とも「新特徴を増やす前に評価契約と校正分割を直す」で一致した。馬体重後の再予測と対象レース自身の発走前市場利用は候補に挙がったが、運用方針・ユーザー方針により本提案から除外した。

## 10. 関連実装・記録

- 校正分割: [`training/src/horseracing_training/calibration.py`](../../training/src/horseracing_training/calibration.py)
- 学習・校正結線: [`training/src/horseracing_training/predictor.py`](../../training/src/horseracing_training/predictor.py)
- `pl_topk`モデル: [`training/src/horseracing_training/win_model.py`](../../training/src/horseracing_training/win_model.py)
- 評価指標: [`eval/src/horseracing_eval/metrics.py`](../../eval/src/horseracing_eval/metrics.py)
- 評価母集団: [`eval/src/horseracing_eval/dataset.py`](../../eval/src/horseracing_eval/dataset.py)
- 過去市場特徴: [`features/src/horseracing_features/past_market_features.py`](../../features/src/horseracing_features/past_market_features.py)
- スピード指数: [`features/src/horseracing_features/speed_figure_features.py`](../../features/src/horseracing_features/speed_figure_features.py)
- 区間ラップ取得: [`specs/034-sectional-lap-ingest/spec.md`](../../specs/034-sectional-lap-ingest/spec.md)
- 市場残差モデルの負・正結果: [`specs/060-market-residual-model/tasks.md`](../../specs/060-market-residual-model/tasks.md)
- スピード指数採用結果: [`specs/061-speed-figure-features/tasks.md`](../../specs/061-speed-figure-features/tasks.md)
