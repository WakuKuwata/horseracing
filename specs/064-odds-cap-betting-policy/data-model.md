# Phase 1 Data Model: odds-cap betting policy

**スキーマ変更なし・migration なし**(migration head 不変)。既存テーブルのみ。cap は `recommendations.logic_version` に文字列で記録。

## エンティティ(既存・変更なし)

### recommendations(既存)
- 変更列なし。`logic_version` にオッズ cap 情報を追記(下記文法)。
- win 行: `bet_type=win`・`selection={horse_id, horse_number}`・`market_odds_used=real 単勝オッズ`・`is_estimated_odds=false`・`pseudo_odds=1/p`・`pseudo_roi=EV−1`・`stake_fraction`(Kelly or NULL)。cap は行の**有無**に影響(cap 超馬の行が生成されない)が、生成された行の列意味論は不変。

### race_predictions / prediction_runs / race_horses / race_results(既存)
- 読み取りのみ。cap は race_horses.odds を選定フィルタに使うだけ(モデル特徴に非流入)。

## logic_version 文法(win 経路)

現行(`recommend.py::default_logic_version`):
```
ev=win_prob*odds;thr=<t>;stake=<s>;excl=scratch+nullodds+zeroprob;renorm=started;v=<ver>
[;kelly=lam_real=<..>;cap_bet=<..>;cap_total=<..>;alloc=<..>;bankroll=<..>]
[;<p_calibrator.logic_version>]
```

追加(`odds_cap is not None` のときのみ・条件付き追記=cap 無効時バイト同等):
```
;oddscap=<cap>            # 例 ;oddscap=21.0  (上限のみ・下限なし)
```

- cap 無効(`None`)時は一切追記しない → 既存 lv とバイト同等(R2)。
- 監査: cap 値は lv から一意に復元でき、stake=fraction×bankroll と併せ推奨を完全再現(V, SC-003)。

## エンティティ(feature 内の非永続オブジェクト)

### PolicyGateReport(eval/policy_gate.py・非永続)
- fields: `strategy`(ev / ev_oddscap21 / favorite / uniform / no_bet)・`recovery_rate`・`hit_rate`・`n_bets`・`skip_rate`・`max_drawdown`・`max_losing_streak`・`log_growth`・`by_fold: list[{year, recovery}]`・`by_odds_band: list[{band, n, recovery}]`・`n_folds_improved`・`worst_fold_delta`。
- 採否判定 `adopted: bool` = cap policy が現行 EV policy 比で recovery 改善 かつ 過半 fold 改善 かつ 最悪 fold 非悪化。
- 永続化は本 feature スコープ外(将来 diagnostic_runs へ=054 前例、deferred)。

### 表示派生値(api/backtest.py・read-time 純・非永続)
- `favorite_realized(odds_map, finish_map) -> WinRealized 相当`: レースの最低オッズ started 馬の realized(本命ベタ基準)。
- no-bet ×1.00・odds帯別集計は front 派生(表示中 win 行から)。
- いずれも feature_snapshots・モデル特徴に非流入(II leak-guard)。

## 不変条件

- INV-1: `odds_cap=None` で全出力(recommendation 行・lv)が現行とバイト同等。
- INV-2: cap 有効時、cap 超オッズの馬は win 推奨に現れず、cap 内馬の行は cap 無効時と同一(EV/renorm/Kelly 不変)。
- INV-3: win_prob(race_predictions)は cap の有無で不変(cap は選定段のみ・確率導出に非介入)。
- INV-4: cap 値・realized 基準・odds帯別のいずれもモデル特徴に戻らない(leak-guard test)。
