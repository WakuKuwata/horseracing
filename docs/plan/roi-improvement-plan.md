# ROI 改善・正規評価計画

**作成日**: 2026-07-12  
**状態**: Draft / 実装前  
**対象**: 単勝の買い目選定、prospective 評価、policy 採否  
**前提**: 本計画は利益を保証しない。現時点で確認できているのは、closing オッズを用いた代理評価上の損失低減だけである。

## 1. 目的

本計画は、次の2段階を明確に分けて進める。

1. **ROI を正しく測れるようにする**
   - 判断時に利用可能だった情報だけで買い目を生成する。
   - 実現回収は公式確定払戻で精算する。
   - 見送りを含む全 decision opportunity を残す。
2. **正規評価で現行 policy より損失が少ない、または利益化できる policy を探す**
   - まず既存の `odds<21` を検証する。
   - 次に市場残差モデル、オッズ変動耐性、新しい情報軸を1案ずつ kill-test する。

実資金投入、利益保証、疑似オッズしかない券種の利益化判断は本計画の目的外とする。

## 2. 現状ベースライン

[Feature 064](../../specs/064-odds-cap-betting-policy/spec.md) の closing オッズ代理評価では、2008–2026 の walk-forward OOS で次が確認されている。

| Policy | 回収倍率 | 100円当たり損失 | 状態 |
|---|---:|---:|---|
| no-bet | 1.000 | 0.0円 | 資金を減らさない基準 |
| 現行 `EV=p×odds≥1.0` | 0.721 | 27.9円 | proxy 実測 |
| `EV≥1.0 & odds<21` | 0.818 | 18.2円 | proxy 実測、19/19 fold 改善 |
| `EV≥1.0 & odds∈[6,21)` | 0.822 | 17.8円 | cap21 比 +0.004 のみ |
| `EV≥1.3` | 0.703 | 29.7円 | 悪化、棄却済み |

`odds<21` は現行比 +0.097、損失率を約34.8%減らす。ただし回収倍率は1未満であり、**利益化ではない**。

また、この評価は binary+isotonic+TE proxy と closing オッズを利用している。production 構成 `pl_topk + features-016` の最終ゲートと既定切替は未完了である。[Feature 064 tasks T028–T029](../../specs/064-odds-cap-betting-policy/tasks.md)

したがって、以降は「現在のROI」という曖昧な表現を使わず、必ず次のいずれかを明示する。

- **closing proxy 回収倍率**
- **prospective 公式払戻回収倍率**
- **疑似回収倍率**

## 3. 最重要の計測是正

### 3.1 判断時オッズと払戻を分離する

現行 [Feature 065](../../specs/065-prospective-shadow-log/spec.md) と `shadow_log_summary` は、判断時に凍結した `market_odds_used` を的中時の払戻倍率として利用している。

しかしJRAはパリミュチュエル方式であり、購入時点の表示オッズは固定約定されない。払戻は投票プールと公式結果から決まり、最終オッズも概算払戻率である。

- [JRA公式: 馬券のルール・払戻金の計算方法](https://www.jra.go.jp/kouza/baken/index.html)
- [JRA公式: オッズ](https://www.jra.go.jp/kouza/yougo/w406.html)

よって、次の意味論に置き換える。

| 値 | 用途 | realized ROIへの使用 |
|---|---|---|
| `decision_odds` | 判断時の選定、EV、cap 判定、監査 | 使用しない |
| `official_payout_per_100` | 公式確定払戻 | 使用する |
| `final_odds` | オッズ変動・CLV相当の診断 | 公式払戻欠損時も primary 精算には代用しない |

現在の「凍結オッズで精算」「real 約定可能オッズ」という表現は停止する。既存集計は **counterfactual snapshot return** としてのみ扱い、realized ROI とは呼ばない。

### 3.2 全 decision attempt を保存する

既存 `recommendations` だけでは、買い目ゼロのレースが残らず、見送り率・対象母集団・policy間のpaired比較を再構成できない。Feature 065 の「スキーマ変更なし」より評価の正確性を優先し、全 attempt のappend-only記録を追加する。

最低監査項目:

- attempt: `attempt_id`, `race_id`, `decision_at`, `odds_asof`, `post_time`, `capture_quality`
- model: `prediction_run_id`, `model_version`, `calibration_version`
- policy: `policy_version`, `policy_params`, `candidate_count`, `bet_count`, `skip_reason`
- bet: `horse_id`, `decision_odds`, `stake`
- settlement: `official_payout_per_100`, `refund_or_void`, `settled_at`, `source_version`

同一の `race_id + snapshot_id + prediction_run_id` から、最低でも次のarmを同時生成する。

- current EV
- cap21
- favorite baseline
- no-bet
- 評価対象のcandidate policy

これにより、時刻・オッズ・結果母集団が揃ったpaired比較を行う。

### 3.3 primary cohort を固定する

primary評価に含めるのは、発走前であることを機械保証できるsnapshotだけとする。

- `odds_asof < post_time`
- fresh scrape成功
- started全頭の有効オッズあり
- 同一snapshotから全arm生成
- official payout取得済み

`post_time`不明、発走後捕捉、部分オッズ欠損、`weak_pretime=1` はprimaryから除外し、別cohortとして件数と結果だけを開示する。

## 4. 評価指標

選定policyのprimary評価は固定100円/買い目で行う。Kelly等の資金配分は、正のedge確認後のsecondary分析へ分離する。

### 4.1 Primary

- 公式払戻回収倍率 = `Σ公式払戻 / Σstake`
- 現行比paired net-profit差（race単位）
- bet数、betレース数、見送り率、turnover
- 開催日cluster bootstrapによる95%信頼区間
- 月別・期間別の改善数
- 最悪期間の差分
- leave-one-winner-out回収倍率

### 4.2 Secondary

- 的中率
- 最大ドローダウン
- 最大連敗
- log bankroll growth
- decision odds → final odds の変化
- odds帯別・モデル別・capture時刻別の診断

CLV相当指標はROIの早期診断に利用できるが、ROIの代用にはしない。

## 5. 採否ゲート

### 5.1 損失低減ゲート

candidateを「現行より損失が少ない」とするには、次をすべて満たす。

1. 現行比paired net-profit差 > 0
2. 開催日cluster bootstrap 95% CI下限 > 0
3. 事前登録した過半の時系列期間で改善
4. 最悪期間が事前許容幅を超えて悪化しない
5. 最低bet数・betレース数・coverageを満たす
6. leave-one-winner-outでも改善方向を維持
7. primary cohortだけで成立

このゲート通過は「より損失が少ない」を意味し、「利益が出る」を意味しない。

### 5.2 利益化ゲート

利益を主張または実資金候補とするには、損失低減ゲートに加えて次を満たす。

1. prospective 公式払戻回収倍率 > 1
2. その95% CI下限 > 1
3. 最大DD・log growthが事前許容範囲
4. 固定終了日またはsequential補正を含む事前登録プロトコルで評価

利益化ゲート通過前は、Kelly sizing・自動購入・攻撃的な資金配分を行わない。

## 6. 施策バックログ

### P0-A: 公式払戻によるprospective精算

**仮説**: 正しい払戻と全attempt記録により、closing-oracle・生存者バイアス・見送り欠落を除去できる。

**作業**:

- 公式単勝払戻の取得・正規化・監査契約
- `decision_odds` と settlement の型分離
- attempt/snapshot/settlement のappend-only永続化
- current/cap21/favorite/no-betの同時生成
- 既存shadow表示の用語訂正と旧集計の明示的な降格

**停止条件**: 公式払戻を取得できない場合、realized ROIの採否判定を停止する。

### P0-B: cap21 production faithful ゲート

**仮説**: proxyで確認した出血低減が、production `pl_topk + features-016` でも再現する。

**作業**:

- [T028](../../specs/064-odds-cap-betting-policy/tasks.md) の長時間ジョブを完走
- currentとcap21を同一fold・同一race集合で比較
- 既存指標にpaired差・cluster CI・coverageを追加
- 結果をretrospective proxyとして保存

**判断**:

- offlineゲート通過: cap21をshadow/opt-inで維持
- corrected prospectiveゲート通過: decision-supportの既定候補
- 利益化ゲート通過前: 自動実資金policyにはしない

cap21は複数policy族を2008–2026結果上で探索した後に選ばれており、同期間は完全なconfirmatory holdoutではない。19/19 fold改善は有望な探索結果として扱う。

### P1-A: 市場残差モデル + cap21

**仮説**: 市場qをoffsetとする `lgbm-060-mkt` は、十分な学習量がある近年に市場へ追加情報を持つ可能性があり、cap21と組み合わせると偽edgeを減らせる。

**根拠**: 全19foldゲートは不合格だが、2014年以降13/13 foldで市場qのwin LogLossを改善している。ただしこれはpost-hoc所見で、ROI改善は未確認である。

**実験**:

- baseline: active production p + cap21
- candidate: market-residual p + cap21
- expert切替を行う場合、選択は対象年より前の完了OOSだけで決定
- corrected prospectiveで公式払戻を直接比較

**停止条件**:

- ROI差のCI下限≤0
- coverageが事前下限未満
- ほぼ全見送り
- closing入力なしの発走前snapshotで効果が消失

### P1-B: 固定時刻snapshotとodds drift耐性

**仮説**: 一時的な見かけ上のedgeではなく、複数の事前固定時点で持続する候補に限定すると、最終払戻との乖離を減らせる。

**実験規律**:

- primary時点と補助時点を実験前に固定
- 時点、cap、閾値を結果を見て変更しない
- 全時点の全馬snapshotを保存
- 精算は公式払戻

### P2-A: 不確実性下限policy

**仮説**: active p、市場残差p、bootstrapモデルの合意と保守的確率下限を使うことで、tail過信による偽edgeを減らせる。

単純なEV閾値引上げやp−q判定とは別実験とし、cap21内でのみkill-testする。ほぼ全見送りになる場合は棄却する。

### P2-B: 当日動的track variant

**仮説**: 対象レースより前に終了した同日同会場レースから、当日の馬場速度・上がり傾向・内外傾向を推定すると、現行speed figureにない情報を追加できる。

**ガード**:

- event-time replayで対象レース直前に利用可能な結果だけを使用
- `race_number`ではなく実発走時刻・確定時刻を優先
- 対象レース・後続レース・未確定結果を不使用
- 情報不足はNaNでfail-closed
- LogLoss/ECE通過後にだけcap policy ROIをsecondary評価

### P2-C: expanding対rolling/time-decay

市場残差モデルの近年改善が非定常性に由来するかを検証する。rolling幅・減衰率は少数候補を事前固定し、outer foldの内側だけで選択する。

## 7. 再実施しない探索

次は既存証拠により優先度を上げない。

- EV閾値引上げ、一律edge haircut
- 単純p−q、p/q、逆張り
- モデル本命集中、人気帯、頭数だけのfilter
- `[6,21)`への即変更
- plain p×q blend
- Elo/相手品質の再実装
- closing 3F単独特徴の再探索
- race dispersion表示の即買いシグナル化
- 実払戻がないexotic券種の利益化判断
- 直接ROI目的の高配当重み学習

複数案の同時グリッド探索は行わず、事前登録した1候補ずつkill-testする。

## 8. 実行順序

1. 既存Feature 065のrealized表現を停止・訂正
2. 公式単勝払戻の取得契約を作る
3. odds snapshot・decision attempt・settlementを永続化する
4. capture品質とproduction経路parityをテストする
5. cap21 production proxyゲートを完走する
6. current/cap21のcorrected prospective paired A/Bを開始する
7. 採否期間終了までpolicy条件を固定する
8. 損失低減ゲート判定
9. market-residual+cap21を1本だけ次候補として評価する
10. その後に新情報軸を1案ずつkill-testする

計測基盤が完成する前にpolicy探索を広げない。

## 9. 成果物とDefinition of Done

### 成果物

- official payout ingest契約と監査可能な保存先
- odds snapshot / decision attempt / settlementのデータ契約
- paired prospective集計
- current/cap21/favorite/no-bet比較レポート
- cluster bootstrap CI・期間別安定性・leave-one-winner-out
- corrected shadow UI/API表示
- 採否記録とpolicy version

### DoD

- 判断時情報と精算情報が型・保存先・命名で分離されている
- 公式払戻以外をrealized ROIに使わない
- zero-betを含む全attemptが再現可能
- 全armが同一snapshotから生成される
- primary cohortに弱保証データが混入しない
- production policyとprospective policyのparity testが通る
- 採否ゲート、観測期間、終了条件が実行前に固定されている
- 結果が「損失低減」「利益化」「不採用」のいずれかで明示される

## 10. ガードレール

- cap21の既存期間選択バイアスを開示する
- closing oddsを選定入力に使った評価を実運用証拠と呼ばない
- IID bet bootstrapを使わず、開催日単位でclusterする
- 高オッズ1発依存をleave-one-winner-outで確認する
- 途中結果を見て時刻・cap・閾値・期間を変更しない
- multiple testingを避けるため候補数を制限する
- no-bet=1.00を常に基準として表示する
- 負のedgeをKellyで利益化しようとしない
- 疑似ROIとrealized ROIを同一表現・色・ランキングで混在させない

## 11. 未決事項

- 公式単勝払戻の一次データソースと取込頻度
- snapshotのprimary時点と補助時点
- 最低観測期間、最低bet数、最低coverage
- worst periodの許容幅
- decision attemptの新テーブル設計とmigration分割
- 既存 `market_odds_used` の表示・API互換方針
- decision-support既定化と実資金policyの権限分離

これらは実装specで、データを見る前に事前登録する。

## 12. 関連資料

- [Feature 064: odds-cap policy](../../specs/064-odds-cap-betting-policy/spec.md)
- [Feature 064 tasks](../../specs/064-odds-cap-betting-policy/tasks.md)
- [Feature 065: prospective shadow log](../../specs/065-prospective-shadow-log/spec.md)
- [Feature 047: segment diagnostics](../../specs/047-segment-diagnostics/spec.md)
- [Feature 060: market-residual model](../../specs/060-market-residual-model/spec.md)
- [Market-aware betting policy 提案](../market-aware-betting-policy-proposal.md)

## 13. Second opinion反映

本計画はモデル/特徴量、買い目policy/資金配分、評価リーク/統計検定の独立レビューを突き合わせて作成した。レビューから次を採用した。

- Feature 065の凍結オッズ精算をrealized ROIとして扱わない
- decision oddsとofficial payoutを分離する
- schema変更なしよりzero-betを含む全attempt記録を優先する
- cap21は即時default化せず、production proxyとcorrected prospectiveを別ゲートにする
- ROI改善と利益化を別の採否条件にする
- 市場残差モデルはLogLoss実績ではなく直接ROIで判定する
- 計測基盤完成前に新しいpolicy探索を広げない
