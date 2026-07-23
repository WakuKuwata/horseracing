# Contract: Exotic Edge Measurement (pre-registered)

**Modules**: `betting/exotic_divergence.py`・`betting/exotic_backtest.py`(既存、実データで検証/NO_DECISION 規約確認)
**CLI**: `betting exotic-divergence` / `betting exotic-backtest`
**Pre-registration doc**: 結果前に固定(append-only 監査)

## 前提

- 入力=蓄積された実 `exotic_odds`(確定配当)+ 対応レースの結果(採点用)+ モデル p(009 joint EV 計算用)。
- probability は常に P_model(009 on model p)。q はモデル確率に使わない(p≠q)。実配当が無い券種は 010 推定(double-pseudo)で**分離ラベル**。

## exotic-divergence(推定 vs 実)

- 券種別に 010 推定オッズ vs 実配当の乖離(coverage_rate・signed log(real/est) median/MAE/P90)。
- **DIAGNOSTIC のみ**(採否バーでない)。推定オッズがどれだけ実配当を近似できるかの健全性チェック。

## exotic-backtest(009 joint EV vs 実配当)= 採否ゲート

pre-registration で結果前に固定:

| 要素 | 規約 |
|---|---|
| bet_types | place/quinella/wide/exacta/trio/trifecta を**個別**に測る(束ねない) |
| 採点 | 券種別 hit 規則(exacta/trifecta=ordered・quinella/trio=set・wide/place=inclusion+009 field ルール)、place/wide の複数当選は bet-level |
| payout | 実配当優先、無ければ O_est(double-pseudo・別ラベル) |
| baseline | 「最低 O_est(人気)」+「uniform」同条件 |
| success | baseline 超過(市場超過が真のバー)。ROI>1.0 単独では ADOPT 不可 |
| **n_min** | 券種別最小サンプル(組合せ数で trifecta 最大)。**n<n_min → verdict=NO_DECISION** |
| CI | 開催日クラスタ bootstrap・seed 固定(i.i.d. 禁止) |
| 多重比較 | 6 券種×窓の偽陽性補正を事前固定 |
| OOS | in-sample の見かけ edge を walk-forward/OOS で確認、崩れれば REJECT |

## verdict(三値・遡及変更しない)

- **NO_DECISION**: 実配当 n が n_min 未満(前向き収集初期の既定状態)。edge を主張しない。
- **REJECT**: baseline 超過せず、または OOS で崩壊。
- **ADOPT候補**: 事前登録条件を全満+OOS 維持。**それでも実運用ベッティングは別 feature**(本 feature は測定のみ)。

## リーク境界

- selection 生成は結果を読まない(009/010/011 既存)。実配当は採点のみ。
- edge 派生値(divergence/ROI)をモデル特徴・校正に戻さない(憲法 II leak-guard)。

## logic_version 記録(憲法 V)

控除率(馬連/ワイド22.5%・馬単/三連複25%・三連単27.5%)・評価窓・seed・n_min・baseline 種別・多重比較補正法・収集系列(prospective/cache)。
