# Data Model: 結合確率エンジン

新テーブルは作らない。エンジン核は純関数(win 確率 → 結合確率)。評価のみ race_predictions/race_results を読む。

## 入力

| 用途 | 取得元 |
|---|---|
| 単勝確率 (p_i) | Feature 006 `race_predictions.win_prob`(出走母集団で正規化済み)または in-memory |
| 母集団 | 出走(取消・除外を除外、必要なら entry_status で再確認)→ 再正規化 |
| 評価採点(校正のみ) | `race_results`(確定着順、券種的中判定) |

## 論理エンティティ

- **WinProbabilities**: 1 レースの `{horse_id: p}`(正規化済み)。
- **JointProbabilities**: 1 レースの券種別確率:
  - `win: {horse_id: p}`、`place: {horse_id: p}`(N 依存)
  - `exacta: {(i,j): p}`(順序)、`quinella: {frozenset{i,j}: p}`(無順序)
  - `wide: {frozenset{i,j}: p}`、`trifecta: {(i,j,k): p}`(順序)、`trio: {frozenset{i,j,k}: p}`(無順序)
- **結合確率エンジン**: WinProbabilities → JointProbabilities(除外→再正規化→clip→PL 派生)。
- **校正レポート**: エンジン vs 独立積 baseline の NLL/Brier(同一レース集合、結果確定の過去データ)。

## 導出順序(不変、INV-J)

```
0. (呼び出し側の前提条件) 取消・除外を母集団から除外し、出走馬の p のみをエンジンに渡す
1. (エンジン) 受け取った p を Σ=1 に再正規化
2. p を [eps, 1-eps] に clip し、再度 Σ=1 に正規化
3. PL 派生(分母 1-p_i, 1-p_i-p_j はこの正規化済み p で計算)
4. 無順序 = 順序和、wide = trio の第3頭和、place = harville top-N(N 依存)
```
エンジンの入力は出走母集団の win 確率 dict のみ(entry_status を持たない)。取消・除外の除去は校正評価/CLI が担う。

## 不変条件

- **INV-J1**: 再正規化は PL 分母計算より**前**(順序固定)。取消・除外馬の確率は 0。
- **INV-J2**: `Σ_{i≠j} exacta = 1`、`Σ_{順序付き3つ組} trifecta = 1`(許容内)。
- **INV-J3**: `quinella{i,j} = exacta(i,j)+exacta(j,i)`、`trio{i,j,k} = Σ6順序 trifecta`、
  `wide{i,j} = Σ_k trio{i,j,k}`、`wide{i,j} >= quinella{i,j}`。
- **INV-J4**: joint の周辺一致(非退化): `Σ_{i含む} trifecta = harville_topk.top3[i]`、
  `Σ_j(exacta(i,j)+exacta(j,i)) = harville_topk.top2[i]`。`place` は harville top-N を採用。
- **INV-J5**: 包含確率 `place/trio/wide ∈ [0,1]`、単調 `p_i>=p_j ⟹ place_i>=place_j`。
- **INV-J6**: 端点でゼロ割・範囲逸脱なし(clip)。`harville_topk` の分母 skip を継承しない。
- **INV-J7**: 決定論(同一入力で同一出力)。
- **INV-J8**: 確率導出はレース結果・オッズを参照しない(評価採点のみ結果使用)。

## 券種規則

- **複勝(place)**: 8 頭以上=top3 包含、5–7 頭=top2 包含、≤4 頭=該当なし(None)。
- **小頭数**: N<3 で三連系=該当なし、N<2 で全て該当なし。各券種は定義可能な範囲でのみ値を返す。
- **同着**: 確率導出は連続(ties なし)。評価採点で JRA 規則により的中判定(同着 1 着は的中)。

## 校正評価(R5)

```
エンジン: realize した馬単/三連単 組み合わせに対する NLL/Brier(eval.metrics 再利用)
baseline: 独立積 exacta∝p_i·p_j / trifecta∝p_i·p_j·p_k を Σ=1 に再正規化
同一レース集合・同一条件で比較。エンジンが baseline を悪化させない(PL の理論的優位)
```

## スコープ外(将来)

- exotic オッズ取得・推定オッズ変換・exotic EV/推奨(別 P0)。
- 結合確率の永続化(exotic オッズ源が入るまで保留)。
- 同着確率モデル・PL 以外の確率手法。
