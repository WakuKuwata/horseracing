# Data Model: 推定市場オッズ変換

新テーブルは作らない。変換核は純関数(単勝オッズ → 推定オッズ)。検証のみ race_horses.odds / race_results を読む。

## 入力

| 用途 | 取得元 |
|---|---|
| 単勝オッズ | `race_horses.odds`(確定=closing-oracle 疑似)or 008 前売り(実運用寄り)。欠損/0/負は除外 |
| 母集団 | 出走(取消・除外を除外)+ 有効オッズ馬 |
| 検証採点 | `race_results`(確定着順/勝馬) |

## 論理エンティティ

- **WinOdds**: `{horse_id: odds}`(入力、有効値のみ)。
- **MarketImpliedWinProbs (q)**: `{horse_id: q}`、`q_i=(1/odds_i)/Σ(1/odds_j)`。**市場投票シェア**(真の勝率/モデル p
  ではない)。
- **EstimatedOdds**: 券種別の推定市場オッズ(009 の `JointProbabilities` と同じキー構造で、確率の代わりに推定オッズ)。
  **推定フラグ付き**。`P_market(c)→0` は cap/None。
- **推定市場オッズ変換器**: WinOdds → q → `joint_probabilities(q)`(009)→ P_market → `(1−takeout_b)/P_market` →
  EstimatedOdds。
- **検証レポート**: 単勝復元誤差(レース単位)+ q 校正(NLL/Brier)。疑似評価。

## 変換順序(不変、INV-M)

```
0. (前提) 入力 = 単勝オッズ。モデル確率 p は一切使わない
1. 有効オッズ(odds>0)かつ出走(取消・除外除外)の馬のみを母集団に
2. q_i = (1/odds_i) / Σ_{母集団}(1/odds_j)   (Σ1/odds<=0 / 残存不足なら推定不能)
3. q を Feature 009 joint_probabilities に入力 → P_market(各券種)
4. 推定オッズ_b(c) = (1 − takeout_b) / P_market(c)
5. P_market(c) → 0 のとき推定オッズを上限 cap / None(確率本体は cap しない)
```

## 不変条件

- **INV-M1**: 入力は市場オッズのみ。p を参照しない。q と p は別オブジェクト/命名。
- **INV-M2**: `q_i=(1/odds_i)/Σ(1/odds)`、`Σq=1`。q は投票シェア(真の勝率でない)。
- **INV-M3**: 推定単勝オッズ `=(1−takeout_win)/q_i=R·S·odds_i`。`R·S=1` で実 odds を厳密復元。
- **INV-M4**: q を 009 に通した出力は 009 の整合性(Σ=1・無順序=順序和・wide=Σ_k trio)を満たす。
- **INV-M5**: 推定オッズ `=(1−takeout_b)/P_market(c)`。控除率は券種別・設定可能・logic_version に記録。
- **INV-M6**: 欠損/0/負オッズ・取消・除外を母集団から除外して再正規化。推定不能を明確に返す。
- **INV-M7**: `P_market(c) <= eps` で推定オッズ=None、それ以外は `min(R_b/P_market, odds_cap)`(既定 10000)。
  **確率本体 P_market は cap しない**(Σ=1 等を維持)。
- **INV-M8**: 決定論。推定オッズは「推定(is_estimated_odds)」明示、実オッズと区別。実 exotic 価格と乖離しうる(疑似)。

## 控除率(payout_rate R_b = 1 − takeout)

| 券種 | takeout | R_b |
|---|---|---|
| 単勝 win / 複勝 place | 20% | 0.80 |
| 馬連 quinella / ワイド wide | 22.5% | 0.775 |
| 馬単 exacta / 三連複 trio | 25% | 0.75 |
| 三連単 trifecta | 27.5% | 0.725 |

既定(平成26年6月7日以降)。時点依存・設定可能。複勝は粗い近似(プール分配・払戻対象頭数依存)。

## 検証(R7)

```
単勝復元: per-race |log(R_win·S_r)|, mean_i|hat_odds_i/odds_i − 1|, max_i|hat_odds_i − odds_i|
q 校正:  実勝馬に対する q の NLL/Brier(eval.metrics)
全出力 = 疑似評価(推定市場オッズ、実 exotic 価格ではない)
```

## スコープ外(将来)

- exotic EV/推奨(p×推定オッズ)、推定オッズの永続化(recommendations.estimated_market_odds_used/is_estimated_odds)。
- 実 exotic オッズ取得・価格復元評価、favorite-longshot bias 補正、複勝払戻の厳密モデル。
