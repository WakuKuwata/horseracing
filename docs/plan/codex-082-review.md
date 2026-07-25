結論は「方向性は正しいが、現案のままでは進めない」です。採否から切り離した SECONDARY 計器という骨格は妥当ですが、OOF provenance、正規化、ECE の grain、多重比較ガードを仕様化する必要があります。

## 設計の穴（優先度順）

1. **P0: 「active モデルの精度」という estimand が曖昧**

`predict_over_folds` が測るのは、保存済み active artifact そのものではなく、「active と同じと主張する recipe を各年で再学習した historical OOF」です。しかも 081 は `pl_topk:isotonic:0.3` という文字列から汎用 `RecipeFactory` を作っており、lgbm-065 artifact の feature columns、params、drop list、checksum へ強く結合していません。[cli.py](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/cli.py:963)

したがって名称は、少なくとも以下のどちらかに限定すべきです。

- `active-recipe historical OOF accuracy`
- `deployed-active prospective accuracy`

現案は前者です。実際の deployed artifact の運用品質は、将来レースに発走前保存した prediction で別途測る必要があります。

2. **P0: 081 cache は常設計器の正本にできない**

`--reuse-cache` は parquet の存在だけで再利用し、model、window、race set、checksum を検証しません。[folklore_probe.py](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/folklore_probe.py:217)

また cache 内の `is_winner` は、dead heat・partial ingest 等で `winner_horse_id=None` の場合、全馬が 0 になります。[folklore_probe.py](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/folklore_probe.py:81) したがって、その列を started-all logloss のラベルに流用してはいけません。

074 の content-addressed OOF bundle は prediction checksum、race-set hash、attestation digest を検証済みで再利用できます。[oof_bundle.py](/Users/kuwatawaku/workspace/horseracing/probability/src/horseracing_probability/oof_bundle.py:152) 正本はこの形式を lgbm-065/将来 active に一般化したものにすべきです。

3. **P0: raw NLL と参照線の併記だけでは誤読を防げない**

特に race-level winner NLL は頭数により一様予測でも `log(N)` になります。uniform を隣に表示するだけでは、人は raw NLL の大小を比較します。

race-level の既定表示は次がよいです。

- `winner_nll = mean(-log p_winner)`
- `uniform_nll = mean(log field_size)`
- **`excess_nll_uniform = mean[-log(p_winner) - log(field_size)]`**
- 任意の補助表示: `skill_uniform = 1 - winner_nll / uniform_nll`
- `market_nll` と `excess_nll_market = winner_nll - market_nll`

主表示は加法的な `excess_nll_uniform` を推奨します。skill score は直感的ですが、比率なので分母・集約方法による誤読が増えます。既存にも uniform winner NLL はあります。[metrics.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/metrics.py:117)

horse-level binary logloss も頭数で基礎率が変わるため、`p=1/N` の同一行 baseline に対する paired excess logloss を併記すべきです。

4. **P0: race-level ECE の定義が未確定**

ECE 自体は horse-observation 単位です。race-level mask に対して出すなら、定義は次で固定すべきです。

> race-level 属性でレースを選び、そのレースの started 全馬を使って calibration/ECE を計算する。

これは race-level winner NLL と同じ metric grain ではありません。payload 上も例えば以下を分ける必要があります。

- `winner_nll.grain = race`
- `calibration.grain = started_horse_within_selected_races`

horse-level mask で winner NLL を出さない方針は正しいです。結果として勝者だった馬だけを選ぶ winner-conditioned selection を避けられます。

5. **P0: ECE 単独比較は標本数依存で、021 Wilson CI だけでは不足**

既存 021 reliability は equal-width bins と Wilson CI ですが、Wilson は horse 行を独立 Bernoulli とみなし、同一レース内の依存を扱いません。[harness.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/harness.py:21)

推奨は以下です。

- 固定 probability bins の reliability table
- calibration-in-the-large: `mean(p) - realized_rate`
- 固定定義 ECE
- ECE/NLL/excess score は race-day cluster bootstrap CI
- reliability bin の Wilson は補助表示と明記
- `n_horses` に加え `n_races`、`n_days`、missing/coverage を常時表示

073 の equal-mass ECE と 021 の equal-width ECE は別の量です。どちらを使うか `metric_contract_version` で凍結してください。

6. **P1: freezing は 081 由来マスクの post-selection を消さない**

「081 で結果を見た後に seasonal-sex、rotation 等を v1 に固定」しても、それらは confirmatory な事前登録にはなりません。属性が result-blind であることと、マスクの選択が result-blind であることは別です。

081 由来軸を含めること自体は、仮説生成計器なら問題ありません。ただし `origin=post_081_exploratory` と記録し、081 の独立確認には使えないと明示すべきです。

7. **P1: 「最悪セグメント」を構造的に作らないガードが必要**

注意書きだけでは弱いです。以下を出力契約にしてください。

- 固定順表示。score、gap、ECE 順のソート禁止
- `worst_segment`、rank、赤緑色、PASS/FAIL を payload に持たない
- pointwise CI は「多重比較未調整」と表示
- anomaly/alert を将来付ける場合のみ、軸 family 内 simultaneous CI を要求
- 安定性は重複する run 間ではなく、非重複の年/fold 別値で表示
- 発見後の検証には `discovery_run_id` を持つ新規事前登録を要求
- 082 の値を 073 config、閾値、decision から参照禁止

## 代替案

二層に分けるのが最も明快です。

1. **Historical anchor readout**

   同じ固定評価窓・同じ race/result snapshot 上で、active recipe の OOF 精度を測る。モデル世代間比較用。

2. **Prospective operational readout**

   active artifact が発走前に生成した prediction のみを将来蓄積し、実運用上の drift を測る。過去の `race_predictions` は full-history/backfill と区別できないため使用禁止。`computed_at < post_time` と prospective provenance を満たす将来行だけが候補です。

082 MVP は historical anchor のみに絞って構いませんが、「現在 deploy 中 artifact の常時精度」とは呼ばない方がよいです。

OOF source の優先順位は次です。

1. manifest 検証済み content-addressed OOF bundle を再利用
2. bundle がない、または attestation/window/race-set が違う場合は再生成
3. 081 生 parquet は公式 run では拒否
4. historical `race_predictions` は拒否
5. prospective marker を持つ将来 prediction は別 instrument で採用

payload の structured provenance には最低限、以下が必要です。

- base model version、artifact/attestation digest
- OOF bundle digest、prediction checksum、race/horse set hash
- train floor、eval from/to、fold boundaries
- probability stage
- code SHA、feature/source fingerprint
- label snapshot hash、mask-assignment hash、market snapshot hash
- metric contract version、mask library version/hash

これらを `logic_version` 文字列だけに押し込めない方が安全です。

## マスクライブラリ v1 の推奨

| family | grain | v1 |
|---|---:|---|
| temporal | race | **eval year/fold**。必須 |
| race core | race | surface、distance band、class、field size |
| course | race | `venue_code × track_type`。going との意味を混同しない |
| horse core | horse | sex、debut/history-depth |
| data quality | horse | canonical/nk、past-market coverage 0/1–2/3+ |
| market context | horse | q band + `q_missing/incomplete`。closing-market-conditioned と明記 |
| calibration | horse | p bins は mask library ではなく reliability contract に置く |
| 081 exploratory | horse | sex×season、current/prior rotation、previous finish、draw×venue、body-mass×going、weight gain |

081 exploratory は v1 core と別 family にしてください。連続値は「軸名」だけでは凍結になりません。missing bucket、境界、端点、availability timing、交差セルを固定する必要があります。14/70日、440kg、+11kg 等は 081 を見た後の由来を保持します。

library version は丸ごと比較不能にする必要はありません。

- mask ID と definition hash は不変
- v2 への追加は v1 の superset
- 定義変更は同じ ID を使わず新 ID
- run 間比較は `mask_definition_hash × metric_contract_version × population_hash` が一致する行だけ
- 過去 payload は書き換えないが、旧 source を新 library で再計算することは「新 run」として許可

## 必要なテスト

- train floor と eval start の分離。2007 が初期 train-only なら `--from 2008` が本当に完全履歴か確認
- OOF の `train_through < valid date`、同日除外、fold cadence 固定
- active artifact/attestation、window、race set の不一致で cache reuse を拒否
- 各レースで prediction horse IDs＝started IDs、finite、`Σp=1`
- dead heat、partial ingest、all-DNF、結果欠損の除外数と扱い
- variable field size で uniform model の `excess_nll_uniform=0`
- horse-level uniform excess の golden test
- race mask ECE が選択レースの全 started 馬を使うこと
- horse mask から winner NLL を呼べないこと
- q 欠損・partial odds が model population を変えず、market 指標だけ unavailable になること
- mask の境界・missing bucket・Σn reconciliation・結果変更で割当不変
- cluster bootstrap の決定論、min count/min days、underpowered 表示
- 固定順、rank/worst/verdict/gate field 不在
- payload verbatim round-trip、append-only、`segment_edge` kind との分離
- library v2 追加後も同一 mask hash の比較が可能
- 073 gate-config、FEATURE_VERSION、model/API/schema が不変

## 見落とし制約

- 現 047 は odds 欠損馬を除外し、残った odds だけで q を再正規化します。[market_edge.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/market_edge.py:27) 絶対精度計器ではこれを踏襲せず、model/uniform の母集団と market-complete subset を分離すべきです。
- `load_eval_races` は finished label が一件もないレースを丸ごと落とします。[dataset.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/dataset.py:178) exclusion ledger が必要です。
- yearly expanding fold は strict-past ですが、その年の全レースを前年末までのモデルで予測します。[splits.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/splits.py:24) 特に season 診断では「model age within year」が交絡します。
- q は現在の `race_horses.odds` にある result-time/closing 系参照で、発走前 snapshot ではありません。
- probability stage を固定しないと、model-internal calibrated win と two-gamma 後 win が混ざります。
- `model_versions` には ACTIVE 一意制約が見当たらず、現読取は複数時に version 降順で一件を選びます。[queries.py](/Users/kuwatawaku/workspace/horseracing/api/src/horseracing_api/queries.py:435) 計器は active がちょうど一件でなければ fail-closed が安全です。
- `diagnostic_runs` は kind ごとの append-only 汎用 JSONB なので migration 不要という判断は妥当です。[prediction.py](/Users/kuwatawaku/workspace/horseracing/db/src/horseracing_db/models/prediction.py:136)

## 結論

次の条件を spec 082 に入れれば進めてよいです。

1. 074 型の検証済み OOF bundle を正本にする  
2. raw NLL より `excess_nll_uniform` を主表示にする  
3. race-level ECE の二段階 grain を明記する  
4. 081 軸を post-081 exploratory family とラベルする  
5. 固定順・CI・年別安定性・rank/worst 不在を構造的に保証する  
6. structured provenance と snapshot hashes を payload に保存する  

`segment_edge` とは別 kind `segment_accuracy` が適切です。共通の population/mask/metric primitive は再利用してよいですが、estimand と payload は統合しない方が安全です。

CLI＋persist は計算基盤の MVP としては妥当です。ただし「常時見える常設計器」という目的の完了条件には viewer または定型 read CLI と更新運用が必要であり、現スコープは「常設計器のデータ生成層まで」と明記すべきです。
