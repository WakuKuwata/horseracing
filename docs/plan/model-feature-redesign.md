# モデル特徴量 再制定書

**作成日**: 2026-07-12  
**状態**: Proposal / Feature Constitution  
**対象**: `features-017` の既存128列と、次期モデルで評価する候補特徴

## 1. 目的

特徴量を「追加した列の一覧」ではなく、意味・利用可能時点・母集団・欠損・縮約・リーク境界・採否履歴を持つ契約として再制定する。

本書は次を決める。

1. 現行128列を `KEEP / REFORM / REPLACE / AUDIT / BLOCKED / REJECTED / FORBIDDEN` に分類する。
2. 誤定義・重複・coverage driftを、新特徴追加より先に是正する。
3. 次期候補を、独立して評価可能なbundleに分割する。
4. 対象レース自身の市場情報を使わず、過去オッズだけを履歴情報として使う境界を固定する。
5. OOS結果を見た後に列を選び直すことを禁止し、再現可能な採用手順を定める。

## 2. 非交渉の特徴量原則

### 2.1 利用時点

- 対象レース自身の結果、オッズ、人気はモデル入力に使用しない。
- 過去レース由来特徴は `history_race_date < target_race_date` のみを使用する。
- 同日レースは、順番にかかわらず対象行の履歴から除外する。
- 馬体重・馬体重差は `POST_WEIGHT` 特徴として維持する。馬体重後の予測更新は手動運用であり、本書では自動化を扱わない。
- 過去日の馬場差を計算する際は、その過去日全体の確定結果を使用できる。ただし対象レース日の結果は使用しない。

### 2.2 市場情報

- 対象レース自身の発走前オッズ・人気・市場確率は使用禁止とする。
- 過去レースの確定オッズ・人気は、対象時点で既知の履歴情報として利用可とする。
- 過去オッズ量は、原則として完全な過去出走母集団から控除率を除いた市場share `q` に変換する。
- 過去レースで一部馬のオッズが欠ける場合、そのレースの`q`を部分母集団で再正規化しない。
- 市場特徴の欠損を能力不足・新馬と混同しないよう、観測件数と履歴有無を必ず併記する。

### 2.3 Unknownと0

- 回数が存在しないこと自体が事実なら0を使用する。
- 計測不能、履歴不足、source欠損、定義不能はNaNとする。
- 少数標本rateを0や生rateで返さず、事前固定した縮約またはNaNを使用する。
- 欠損原因が複数ある場合、必要に応じて `no_history / source_missing / insufficient_count` を区別する。

### 2.4 評価単位

- 1列ずつOOSを見て選別しない。
- 意味と依存関係が同じ列を事前登録bundleとして評価する。
- coverage、値域、相関、定数性などラベルを見ない監査はOOS前に実施可とする。
- 一度OOS結果を見たbundleを変更する場合、新bundle名・新feature versionで再登録する。

## 3. FeatureDefinition契約

今後、全特徴は少なくとも次のメタデータを持つ。

| 項目 | 必須内容 |
|---|---|
| `name` | 安定した列名。単位・母集団が曖昧な名前は禁止 |
| `bundle` | 採否を同時に判定する単位 |
| `status` | KEEP / REFORM / REPLACE / AUDIT / BLOCKED / REJECTED / FORBIDDEN |
| `grain` | race / runner / horse-history / entity-history / field-relative |
| `dtype` / `unit` | float、count、category、秒、kg、確率等 |
| `sources` | raw table・raw column・派生親列 |
| `availability` | PRE_ENTRY / POST_FRAME / POST_WEIGHT |
| `population` | started / finished / valid measurement等 |
| `history_boundary` | `< race_date`、同日除外規則 |
| `formula` | 分子・分母を含む数式 |
| `window` | last-N / N日 / expanding / EWM |
| `shrinkage` | prior、lambda、最低件数、階層backoff |
| `missing_policy` | 0 / NaN / fallbackと欠損理由 |
| `valid_range` | 理論範囲、単位、異常値guard |
| `coverage_floor` | 全体・年度別・直近期間の最低coverage |
| `drift_check` | 年度別分布、PSI、未知category率等 |
| `leak_tests` | 今走・同日・未来不変、過去変更のpositive test |
| `parents` | 決定的派生元と相関監査対象 |
| `adoption_history` | baseline、fold、指標、採否、再検討条件 |
| `definition_version` | 値変更を検出する定義版 |

現行 `FeatureMeta(source, timing, missing_policy)` はこの契約の部分集合として残し、上記項目を別catalogまたは拡張registryで管理する。

## 4. 状態の定義

| 状態 | 意味 |
|---|---|
| KEEP | 現定義を維持。定期coverage/drift監査のみ |
| REFORM | 情報軸は維持するが、母集団・式・縮約・命名を変更する |
| REPLACE | 現列を新定義へ置換する。新旧を同時採用しない |
| AUDIT | 直ちに削除しないが、重複・低寄与・値域をbundle ablationする |
| BLOCKED | source品質・coverage・時点契約を直すまで採用評価禁止 |
| REJECTED | OOS不採用済み。再検討条件を満たすまで再試行しない |
| FORBIDDEN | リークまたは製品方針により使用禁止 |

## 5. P0: 新特徴追加前に直す問題

### 5.1 2025–2026 coverage drift（改訂: raw CSV入手不可が確定）

`artifacts/features.parquet` 956,409行を年別に監査した結果、Feature 056由来の重要列が直近年で消失している。

| bundle | 2024 coverage | 2025 coverage | 2026 coverage | 改訂判定 |
|---|---:|---:|---:|---|
| テン3F・pace balance | 約88.5% | 約68.9% | **0%** | 直近窓ablationでdrop-or-keep |
| owner win/place | 約96.4% | 約74.2% | **0%** | **REMOVE** |
| breeder win | 約97.5% | 約74.9% | **0%** | REMOVE寄りablation |
| `asof_prize_avg` | 約89.5% | 約69.7% | **0%** | 直近窓ablationでdrop-or-keep |
| speed figure | 約88.9% | 約78.8% | 約87.3% | KEEP/監視 |
| past market | 約89.5% | 約79.1% | 約88.2% | REFORM/ID別監視 |

**確定事実（2026-07-12）**: 2025–2026のJRA-VAN raw CSV（73列）は入手不可能。該当期間の行はnetkeiba scrape由来であり、netkeibaはテン3F・owner_name・breeder_name・prize_moneyを供給しない。scrapeは製品方針でブロック中。したがってこれらの欠損は「修復可能な穴」ではなく**serving体制の恒久的なregime shift**である。

この事実は判定を反転させる。056 bundleの導入時OOS改善（AUC +0.0044・19/19 fold）は2007–2024の歴史fold由来であり、本番serving（2025年以降）では該当列が常に全NaNのため、その情報は本番予測に存在しない。全fold平均の採用ゲートは候補モデルの成績を歴史foldで膨らませて見せるが、それはserving性能ではない。

実施事項（改訂）:

1. owner 2列（`asof_owner_win_rate` / `asof_owner_place_rate`）は撤去する。二重根拠（5.6のlast-write-wins時点リーク + source恒久欠損）でablation不要。
2. 全NaN化したbundle（pace_first3f / race_level のprize系 / breeder）は、**直近窓（2025–2026 fold）のpaired ablation**でdrop-or-keepを判定する。全fold平均では判定しない。全NaN列を残す害（train=informative / serve=常時missing分岐の分布不一致）と消す害を実測する。
3. `prize_money_log` 自体が2025+で欠損する場合、race_level 3列とも撤去対象に含める。
4. materialize再生成・bit parity・source fingerprint更新は、撤去後のschemaに対して行う（欠損列の復元は行わない）。

drop-or-keep判定に直近窓paired評価が必要なため、本節はP0の中でも[モデル予測精度向上 提案書](model-accuracy-improvement-proposal.md) のPhase 0（評価契約整備）完了後に実施する。

### 5.2 完全重複・定数列

実データ上、次が完全一致または定数である。

- `career_starts == past_race_count`
- `is_debut == 1 - has_past_race`
- `exclude_count == 0`
- `prev_was_exclude == 0`

再制定方針:

- 出走履歴量の正本を `career_starts` とする。
- `is_low_history` は運用上の明示gateとして残す。
- `past_race_count`、`has_past_race`、`is_debut` は同一bundleで削除ablationする。
- `exclude_count`、`prev_was_exclude` はsource異常で常時0なのか、本当に事象がないのかを確認する。source未取得なら削除ではなくBLOCKEDとする。

単一artifactのgain=0だけでは削除しないが、完全重複は情報量を増やさないため整理対象とする。

### 5.3 脚質の欠損誤定義

現行のrunning-style処理は、生値欠損を0へ変換し、「非逃げ・非追込」と同じ意味にしている可能性がある。その結果、`field_style_coverage` も真のstyle coverageになっていない。

再制定方針:

- raw style欠損はNaNのまま保持する。
- `front_runner_rate` / `closer_rate` の分母はstyle観測済み過去走だけにする。
- `style_obs_count` と `style_coverage = observed_style_runs / prior_started_runs` を追加する。
- pace_scenario 7列は修正済みstyle特徴から全再生成し、旧値と混在させない。

### 5.4 Target Encodingの意味と名前

`jockey_id` / `trainer_id` はregistry上はIDだが、production学習ではOOF smoothed win-rateへ列自体が置換される。

再制定方針:

- 生IDと数値TEを別概念にする。
- 数値列名を `jockey_te_win` / `trainer_te_win` とする。
- TE母集団、prior、smoothing、fold方式をFeatureDefinitionに記録する。
- 次期候補では、学習行より前だけを使うordered/prequential TEを現行OOF TEと比較する。
- 生IDをcategoricalとして残す案とTE置換案を同時に変えず、別modeling experimentとする。

### 5.5 rate母集団の不一致

現行の多くの勝率特徴はfinishedのみを分母とするが、モデルのwin教師はstarted-allでDNF等を0として扱う。

再制定方針:

- モデル教師と合わせる第一候補を `started_win_rate` とする。
- finishedだけを使う場合は `finished_win_rate` と明記する。
- avg finish等、finishedでなければ定義不能な指標はfinished母集団を維持する。
- rateには必ず観測件数またはposterior uncertaintyを併記する。
- 母集団変更は値変更を伴うためfeature versionを上げ、旧モデルを互換扱いしない。

### 5.6 owner時点問題

`horses.owner_name` はlast-write-winsであり、現在ownerを過去走へ遡及付与する。馬主変更がある馬では、historical backtest行に後のowner情報が入る可能性がある。

再制定方針:

- 時点別owner履歴を取得できるまでowner特徴をBLOCKEDとする。
- breederは原則不変属性として継続できるが、ID/name normalizationとcoverageを監視する。
- 時点別ownerを取得しない場合、owner列は次期モデルから外す方向でablationする。

### 5.7 値域異常

監査対象:

- `prev_last3f`: 最大99.8秒
- `rel_last3f_avg`: 最大54.76秒
- `rel_time_avg`: 最大117.75秒
- `finish_diff_avg`: 最大126.6秒
- `dist_short_x_speed`: 最大絶対値約26,138
- `asof_mkt_rank_norm_avg`: 最大1.375
- `venue_win_rate / venue_place_rate`: coverage約12%
- `dist_band_place_rate`: coverage約37.7%

再制定方針:

- sourceのsentinel値、単位、取消・中止・異常時計を先に分類する。
- 理由不明の外れ値をwinsorizeして隠さない。
- invalid sourceはNaNと品質フラグへ分離する。
- interactionは親列のvalid range確定後に作る。
- `rank / field_size` は理論範囲を破っているため、後述のrank percentileまたは市場shareへ置換する。

## 6. 現行25群の棚卸し

| 群 | 現行列数 | 状態 | 再制定方針 |
|---|---:|---|---|
| race/runner base | 15 | KEEP/AUDIT | category正規化、単位、POST_WEIGHT境界を固定 |
| history base | 16 | REFORM | 重複整理、started/finished母集団明記、件数正本化 |
| recent_form | 2 | KEEP/REFORM | 頭数正規化着順、時間減衰trendを別bundleで追加 |
| aptitude | 3 | KEEP/REFORM | 条件件数、Bayes縮約、連続距離適性を追加候補化 |
| race_condition | 2 | KEEP | class mappingとcoverageを継続監視 |
| human_form | 2 | REFORM | started分母、件数、期間減衰 |
| pace_time | 5 | KEEP/AUDIT | 異常時計guard、valid measurement母集団を固定 |
| position_style | 3 | REPLACE | 欠損をNaNへ戻し、観測件数を追加 |
| sire_aptitude | 5 | KEEP/REFORM | shrunk rate、log count、条件階層 |
| damsire_aptitude | 2 | AUDIT | 低寄与ablation、条件特徴は別bundle |
| handicap | 4 | KEEP | kg単位、馬体重欠損、post-weight境界を固定 |
| season | 2 | REPLACE/AUDIT | month categoryまたはsin/cos。monthとseasonの重複評価 |
| place_rate | 3 | AUDIT/REFORM | started分母と件数。低coverage列を縮約 |
| human_form_plus | 6 | KEEP/REFORM | trainer recent、全rateのcount・shrinkage |
| course_aptitude | 2 | REPLACE | coverage12%。venue→surface/globalの階層縮約 |
| pace_scenario | 7 | REBUILD | style欠損修正後に全再生成 |
| debut_pedigree | 5 | AUDIT | 高相関gate列をbundle整理、posterior uncertainty追加 |
| condition_change | 7 | REFORM | other surface transition分離、距離単位とinteraction値域修正 |
| corner_trajectory | 4 | KEEP/REFORM | 観測countとrecent trend候補 |
| pace_first3f | 3 | 直近窓ablation | 2026 coverage 0%が恒久。drop-or-keepを実測 |
| owner_breeder | 3 | REMOVE/AUDIT | owner 2列は撤去（時点リーク+恒久欠損）。breederはREMOVE寄りablation |
| race_level | 3 | 直近窓ablation | `asof_prize_avg`（+`prize_money_log`が欠損なら3列）恒久欠損。drop-or-keep |
| sire_line | 2 | AUDIT | 年度別unknown/new category監視 |
| relative_ability | 13 | KEEP | 採用実績あり。親特徴修正後に全再生成 |
| past_market | 4 | REPLACE/EXPAND | rank定義を修正し、過去オッズshareへ拡張 |
| speed_figure | 5 | KEEP/REFORM | 階層基準、historical track variant、pace補正 |

### 6.1 重要な相関・決定的派生

以下は削除の即時根拠ではないが、bundle ablation対象とする。

- `asof_mkt_rank_avg` と `asof_mkt_rank_norm_avg`: 相関約0.956
- `asof_spdfig_avg` と `asof_spdfig_recent3`: 相関約0.943
- jockey win/place rate: 相関約0.965
- trainer win/place rate: 相関約0.929
- `surface_win_rate` と `win_rate`: 相関約0.916
- `rel_time_avg` と `rel_time_avg_vs_field`: 相関約0.930
- debut/low-history sire interaction群: 相関約0.966
- `dist_change = dist_extension - dist_shortening`
- `pace_imbalance = field_front - field_closer`
- `prize_rel = prize_money_log - asof_prize_avg`

relative列やinteraction列には浅い木の学習を補助する意味があるため、相関だけで削除せず、親列と派生列をbundleで比較する。

## 7. 次期候補bundle

### F01: `reliability_counts`

rateの信頼度をモデルへ明示する低コストbundle。

候補列:

- `dist_band_started_count`
- `surface_started_count`
- `venue_started_count`
- `jockey_started_count`
- `trainer_started_count`
- `jt_started_count`
- `breeder_started_count`
- `style_obs_count`

countはrateと同じstrictly-before母集団で作る。初回定義では `log1p(min(raw_count, 10_000))` を使用し、raw count自体は監査artifactに残す。既存rateの式を変更するexperimentとは分離する。

### F02: `pm_core_strength`

過去オッズ量を使う最優先市場履歴bundle。対象レース自身のオッズは使用しない。

過去レース `k` のstarted頭数を `N_k`、馬 `i` のオッズを `O_ik` とする。started全馬の有効オッズが揃う場合だけ、次を定義する。

```text
q_ik = (1 / O_ik) / Σ_j(1 / O_jk)
s_ik = log(q_ik × N_k)
```

`s=0` は一様支持、正は一様以上の支持、負は一様未満の支持を表す。

候補列:

- `asof_pm_support_last`
- `asof_pm_support_mean3`
- `asof_pm_support_mean5`
- `asof_pm_support_best5`
- `asof_pm_support_career`
- `asof_pm_support_trend`
- `asof_pm_support_sd5`
- `asof_pm_obs_count`
- `asof_pm_has_history`

縮約平均は次を正本とする。

```text
shrmean(x; K, λ) = Σ recent-K(x) / (n + λ)
```

`s` の中立priorは0。recentは `λ=2`、careerは `λ=5` を実験前に固定する。件数0では連続値NaN、count=0、has_history=0とする。

### F03: `pm_rank_robust`

odds provenanceに比較的鈍感な人気順位bundle。現行rank3列の置換候補。

```text
u_ik = 1 - (rank_ik - 1) / (N_k - 1)
```

`u=1` は最上位人気、`u=0` は最下位人気。取消等によりraw popularityがstarted頭数を超える場合は、valid oddsを持つstarted馬内で再rankする。

候補列:

- `asof_pm_rankpct_last`
- `asof_pm_rankpct_mean5`
- `asof_pm_favorite_rate5`
- `asof_pm_top3fav_rate5`

現行rank列と新rank列は同時採用しない。

### F04: `pm_expectation_residual`

市場が織り込んだ強さを超えた過去実績を表すbundle。

```text
finish_strength v_ik = 1 - (finish_order_ik - 1) / (N_k - 1)
finish_residual e_ik = v_ik - u_ik
win_residual w_ik = I(win_ik) - q_ik
```

候補列:

- `asof_pm_finish_resid_mean5`
- `asof_pm_finish_resid_career`
- `asof_pm_win_resid_mean10`
- `asof_pm_win_resid_career`
- `asof_pm_resid_sd5`
- `asof_pm_result_obs_count`

DNF等はfinish residualから除外するが、win residualではstarted非勝利として0を使用する。2つの母集団を混ぜない。

### F05: `pm_conditioned`

F02–F04の採用後だけ評価する条件別市場履歴。

- `asof_pm_support_surface`
- `asof_pm_support_distband`
- `asof_pm_support_venue`
- `asof_pm_finish_resid_surface`

all-prior + `λ=5` の階層縮約を用いる。surface×distance等の二重条件は初版では作らない。

### F06: `rotation_form_state`

既存の `days_since_last` とrecent-3だけでは表現できない疲労・上昇下降を表す。

- `starts_last_28d`
- `starts_last_90d`
- `distance_last_28d`
- `distance_last_90d`
- `speedfig_ewm`
- `speedfig_recent_minus_career`
- `speedfig_trend`
- `speedfig_volatility`
- `layoff_bucket`

debutでは回数0、trend/volatilityはNaN。時間窓はrace_date strictly-beforeで作る。

初回定義:

- `speedfig_ewm`: race_date差に対する半減期90日のEWM
- `speedfig_trend`: 直近3走のspeed figureを時間順に単回帰した傾き。2走未満はNaN
- `speedfig_volatility`: 直近5走の標本標準偏差。2走未満はNaN
- `layoff_bucket`: `0–7 / 8–14 / 15–28 / 29–56 / 57–120 / 121日以上 / 履歴なし`

### F07: `speed_hierarchical_variant`

現行speed figureの条件セル不足と過去日の馬場差を補正する。

1. venue × surface × exact distance × going のas-of基準を作る。
2. 少数セルは `goingなし → 距離帯 → venue×surface` の順に階層縮約する。
3. 過去開催日について、各レース自身を除外した同日同場レースの時計残差からhistorical track variantを作る。
4. 対象レース日ではなく、過去走のfigureだけを補正する。

候補列:

- `asof_spdfig_v2_last`
- `asof_spdfig_v2_ewm`
- `asof_spdfig_v2_best`
- `asof_spdfig_v2_trend`
- `asof_spdfig_v2_uncertainty`
- `asof_spdfig_v2_count`

同日結果を対象レースの予測に直接使うPRE_RACE特徴は本bundleに含めない。

階層平均は次を正本候補とする。

```text
mu_shrunk = (n_cell × mu_cell + lambda × mu_parent) / (n_cell + lambda)
```

初回は `lambda=50 race`、historical track variantは自レースを除く同日同場同surfaceの有効レース3件以上で算出し、3件未満は0でなくNaNとする。lambdaと最低件数はOOS前に固定する。

### F08: `speed_pace_adjusted`（BLOCKED: source恒久欠損）

走破時計からレースペースの影響を除いた能力残差。

本bundleは入力に race first3f・pace balance を必要とするが、これらは2025年以降のserving体制（netkeiba由来）に存在しない（5.1参照）。過去走の歴史行では算出できても、対象レース（未来）側の期待走破時計推定に必要なrace-level first3fが得られないため、serving時に成立しない。first3fの代替sourceが得られるまでBLOCKEDとし、次期候補から外す。closing-3F単独は再導入しない。

### F09: `human_recent_pair`

- `trainer_recent_win_90d`
- `trainer_recent_win_365d`
- `trainer_recent_count_90d`
- `horse_jockey_started_count`
- `returning_jockey`
- `jockey_upgrade_score`

rateはBeta-Binomial縮約、同日全除外とする。条件別trainer率は疎になるため次段階へ分離する。

`returning_jockey` は今回騎手が前走騎手と同一なら1、前走ありかつ異なるなら0、前走なしはNaN。`jockey_upgrade_score` は対象日より前だけで作った `current_jockey_started_win_posterior - previous_jockey_started_win_posterior` とする。

### F10: `aptitude_hierarchical`

低coverageのcourse/distance適性を階層化する。

- 距離を固定4binだけでなく、近傍距離へkernel weightingする。
- venue rateは `venue → surface → overall` に縮約する。
- going適性は件数を併記し、going不明・未経験を0にしない。
- rate、count、posterior uncertaintyを同じbundleにする。

初回距離kernelは `weight = exp(-abs(past_distance - target_distance) / 400)` とし、weighted countが3未満ならNaNとする。venueは `venue → surface → overall` の順に同じEmpirical Bayes式で縮約する。

### F11: `sectional_lap_shape`（DEFERRED）

race-level 200mラップの全期間backfill後に制定する。

- 前傾度
- 中盤緩急
- 加速区間数
- 最速ラップ位置
- pace shape cluster
- 過去のpace shape別runner performance

coverageが年度別floorを満たすまで採用評価しない。現行のfirst/last3Fだけでフルラップ相当を推測しない。

## 8. 過去オッズの追加データ契約

### 8.1 source coverageとID断層

raw odds/popularity自体はstarted行の約99.99%で存在する。一方、対象馬の過去履歴連結には2025–2026のID断層がある。

- 2025 started馬の約23%が `nk:` ID
- 2026 started馬は `nk:` IDが中心
- 2026 `nk:` 馬の過去5走coverageは約15%

したがって全体coverageだけで採用してはならない。

必須レポート:

- year / fold
- canonical ID / `nk:` ID
- age / career-start band
- surface / class
- 過去市場履歴1走以上、3走以上、5走以上
- 完全過去odds field率

ID未連結によるNaNを「市場評価がない新馬」と解釈させない。`asof_pm_has_history` とID source別診断を必須とする。

### 8.2 odds provenance

現在の `race_horses` には odds source、odds as-of、final confirmedを区別する列がない。過去情報なのでtarget leakageではないが、JRA-VAN finalとnetkeiba single-latestの量品質が混在する可能性がある。

量特徴の採用前に次を確認する。

- source別odds分布
- 年度別overround分布
- odds=1.0、999.9等の境界値頻度
- popularityとq-rankの不一致率
- complete field判定

provenanceを復元できない場合、M1の`q`とM2のrankを別bundleとして評価し、量特徴の不安定性を隠さない。

## 9. bundleの優先順位

| 順位 | bundle | 前提 | 理由 |
|---:|---|---|---|
| P0 | 評価契約整備 | なし | serving体制での性能を測る物差し（提案書Phase 0） |
| P0 | 恒久欠損bundleの撤去判断 | 評価契約 | owner撤去 + 全NaN列の直近窓ablation |
| P0 | base/style/rate/TE定義修正 | 評価契約 | 誤定義・母集団不一致を除去（1件ずつpaired） |
| P0 | 重複・定数bundle ablation | 定義修正 | 128列のschema整理 |
| P1 | F01 reliability_counts | rate定義固定 | 低コストでrate信頼度を追加 |
| P1 | F02 pm_core_strength | 067 ID解決 + provenance診断 | ユーザー方針に合う新情報量 |
| P1 | F06 rotation_form_state | speed figure稼働 | 既存データだけで新しい時系列状態 |
| P1 | F07 speed_hierarchical_variant | 時計異常guard | 過去最大級に効いた軸の改善 |
| P2 | F03/F04 market rank/residual | F02結果 + 067 | 市場特徴を段階分離 |
| P2 | F09 human_recent_pair | TE定義固定 | trainer recent等の未使用情報 |
| P2 | F10 aptitude_hierarchical | count bundle | 低coverage条件率の再建 |
| P3 | F05 conditioned market | F02–F04採用 + 067 | 疎性・重複リスク |
| — | F08 speed_pace_adjusted | first3f代替source | **BLOCKED**: source恒久欠損 |
| — | F11 sectional laps | full backfill | **BLOCKED**: netkeiba取得ブロック |

## 10. REJECTED / FORBIDDEN

### REJECTED

- Elo / Bradley-Terry: `pl_topk`直近foldで悪化。新しい対戦情報源が増えない限り再試行しない。
- closing-3F単独: speed figureと高相関で、フルgateの限界寄与が消失。
- 単純draw bias: 単独OOSで不活性。historical track variant等の新情報ができた場合だけ別bundleで再検討。
- 既存能力列の単純race rank/gap追加: race-softmaxと重複。
- binary/PL確率の単純平均: 校正悪化・再校正で利得消失。

### FORBIDDEN

- 対象レース自身のオッズ、人気、市場share
- 対象レースの結果・着順・当日未来レース結果
- 同日別レースのオッズを対象行の過去市場履歴として使うこと
- target valid/test labelを使ったTE、縮約、閾値選択
- OOS結果を見て同一bundle内の列を削ること
- 欠損オッズから部分fieldの市場shareを捏造すること
- 利用時点を証明できない情報を「発走前既知」と仮定すること

## 11. 採用手順

1. P0修正前後のcoverage・値域・重複snapshotを保存する。
2. 現active相当を修正済みschemaで再学習し、新baselineを凍結する。
3. 各bundleについて、列名・式・lambda・窓・欠損・採用条件をOOS前に固定する。
4. ラベル非参照のcoverage・相関・値域チェックを通す。
5. 直近foldのde-riskはgo/no-goだけに使い、列を変更しない。
6. full walk-forwardを現activeと同一race集合でpaired実行する。
7. PRIMARY winner NLL、started-all LogLoss/Brier、top2/top3、校正、直近foldを評価する。
8. 開催日単位block bootstrapの信頼区間を記録する。
9. 採用bundleだけを次baselineへ積み上げる。
10. 不採用bundleは結果と再検討条件をcatalogへ記録する。

## 12. 完了条件

特徴量再制定は次を満たして完了とする。

- 現行128列すべてがFeatureDefinition契約または削除理由を持つ。
- 2025–2026 coverage driftが説明・修復され、直近coverage floorを通る。
- 完全重複・定数・誤定義列が整理される。
- rate母集団とTE意味が列名・定義上明確になる。
- 対象レース市場不使用がbehavioral leak testで固定される。
- 過去オッズbundleがID source別・年度別に評価される。
- feature versionが値変更を正しく表し、旧モデルを誤って互換扱いしない。
- 各採用列についてOOS採用履歴と再現artifactが残る。

## 13. 関連資料

- [モデル予測精度向上 提案書](model-accuracy-improvement-proposal.md)
- [Feature Registry](../../features/src/horseracing_features/registry.py)
- [過去市場特徴](../../features/src/horseracing_features/past_market_features.py)
- [スピード指数](../../features/src/horseracing_features/speed_figure_features.py)
- [materialization](../../features/src/horseracing_features/materialize.py)
- [raw-column feature結果](../../specs/056-raw-column-features/spec.md)
- [past-market feature結果](../../specs/058-market-history-features/spec.md)
- [speed figure結果](../../specs/061-speed-figure-features/tasks.md)
- [Elo不採用結果](../../specs/062-rating-features/tasks.md)

## 14. Appendix A: 現行128列

### race / runner static（15）

`venue_code`, `distance`, `track_type`, `going`, `weather`, `race_class`, `race_number`, `age`, `sex`, `frame`, `horse_number`, `jockey_id`, `trainer_id`, `weight`, `weight_diff`

### history base（16）

`career_starts`, `days_since_last`, `prev_finish`, `prev_last3f`, `avg_finish`, `win_rate`, `cancel_count`, `exclude_count`, `stop_count`, `prev_was_cancel`, `prev_was_exclude`, `prev_was_stop`, `has_past_race`, `is_debut`, `past_race_count`, `is_low_history`

### recent_form（2）

`avg_last3_finish`, `recent_win_rate`

### aptitude（3）

`dist_band_win_rate`, `dist_band_avg_finish`, `surface_win_rate`

### race_condition（2）

`class_transition`, `field_size`

### human_form（2）

`jockey_win_rate`, `trainer_win_rate`

### pace_time（5）

`rel_last3f_avg`, `rel_last3f_best`, `rel_time_avg`, `finish_diff_avg`, `finish_diff_best`

### position_style（3）

`rel_corner_pos_avg`, `front_runner_rate`, `closer_rate`

### sire_aptitude（5）

`sire_win_rate`, `sire_avg_finish`, `sire_starts`, `sire_dist_band_win_rate`, `sire_surface_win_rate`

### damsire_aptitude（2）

`damsire_win_rate`, `damsire_avg_finish`

### handicap（4）

`carried_weight`, `carried_weight_ratio`, `carried_weight_rel`, `carried_weight_change`

### season（2）

`race_month`, `race_season`

### place_rate（3）

`place_rate`, `show_rate`, `dist_band_place_rate`

### human_form_plus（6）

`jockey_place_rate`, `trainer_place_rate`, `jockey_recent_win_rate`, `jockey_surface_win_rate`, `jt_combo_win_rate`, `jockey_change`

### course_aptitude（2）

`venue_win_rate`, `venue_place_rate`

### pace_scenario（7）

`field_front_rate_ex_self`, `field_closer_rate_ex_self`, `pace_imbalance_ex_self`, `front_pressure`, `closer_setup`, `style_mismatch`, `field_style_coverage`

### debut_pedigree（5）

`sire_debut_win_rate`, `debut_x_sire_win_rate`, `debut_x_sire_dist_band_win_rate`, `lowhist_x_sire_win_rate`, `lowhist_x_sire_dist_band_win_rate`

### condition_change（7）

`dist_change`, `surface_switch`, `going_change`, `dist_extension`, `dist_shortening`, `dist_ext_x_closing`, `dist_short_x_speed`

### corner_trajectory（4）

`asof_late_gain_avg`, `asof_late_gain_best`, `asof_early_pos_avg`, `asof_mid_move_avg`

### pace_first3f（3）

`asof_rel_first3f_avg`, `asof_rel_first3f_best`, `asof_pace_balance_avg`

### owner_breeder（3）

`asof_owner_win_rate`, `asof_owner_place_rate`, `asof_breeder_win_rate`

### race_level（3）

`prize_money_log`, `asof_prize_avg`, `prize_rel`

### sire_line（2）

`sire_line`, `damsire_line`

### relative_ability（13）

`win_rate_vs_field`, `recent_win_rate_vs_field`, `place_rate_vs_field`, `show_rate_vs_field`, `dist_band_win_rate_vs_field`, `surface_win_rate_vs_field`, `rel_time_avg_vs_field`, `rel_last3f_avg_vs_field`, `finish_diff_best_vs_field`, `jockey_win_rate_vs_field`, `trainer_win_rate_vs_field`, `win_rate_field_rank`, `rel_time_avg_field_rank`

### past_market（4）

`asof_mkt_rank_avg`, `asof_mkt_rank_norm_avg`, `asof_mkt_rank_best`, `asof_beat_mkt_avg`

### speed_figure（5）

`asof_spdfig_avg`, `asof_spdfig_best`, `asof_spdfig_recent3`, `asof_spdfig_last`, `asof_spdfig_count`

## 15. Appendix B: 現行model診断

`lgbm-062` のsplit gain集計では、上位groupは次のとおりだった。

| group | gain share |
|---|---:|
| relative_ability | 約36.95% |
| base | 約24.46% |
| past_market | 約9.19% |
| recent_form | 約7.96% |
| race_level | 約4.34% |
| speed_figure | 約3.03% |

gain=0だった列は `going`, `exclude_count`, `stop_count`, `prev_was_cancel`, `prev_was_exclude`, `prev_was_stop`, `has_past_race`, `is_debut`, `past_race_count`。

これは1つの最終boosterにおける診断であり、削除の因果的根拠にはしない。完全重複・定数性・paired bundle ablationと合わせて判断する。

## 16. マルチエージェントsecond opinion

本制定書は3系統の独立監査を統合した。

- 現行feature監査: 128列のcoverage、重複、値域、gain、実装定義を検査し、2026年の056特徴全欠損、脚質欠損誤定義、owner時点問題を指摘。
- 過去オッズ設計: raw odds平均ではなく、complete past fieldの`q`と`log(q×N)`、ID source別coverage、段階bundleを提案。
- 新情報候補監査: speed figure拡張、ローテーション、人系recent、個体状態を比較し、既存不採用案との重複を除外。

相違点は次のように裁定した。

- 同日途中までの結果を直接使うPRE_RACE track variant案は、全レース共通の利用時点を崩すため初版不採用。過去開催日の確定結果で過去走figureを補正するhistorical variantへ変更した。
- 馬体重後の自動再予測は手動運用済みのため対象外。既存POST_WEIGHT列は維持するが、新しい体重bundleは初期優先候補から外した。
- raw odds平均は頭数・控除率・source差の影響を受けるため主特徴にせず、normalized `q`を採用候補とした。
- gain=0列の即時削除案は採らず、完全重複・定数性・paired ablationを削除条件とした。
