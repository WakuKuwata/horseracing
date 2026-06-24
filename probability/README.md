# horseracing-probability

各馬の単勝確率(Feature 006)から **Plackett-Luce / Harville** で JRA 全 7 券種の的中確率を導出する
**結合確率エンジン**。`db` / `eval` にパス依存。スキーマ変更なし。憲法 P0「結合確率エンジン」の本実装。

## 確率手法

`p_i` を 1着確率とし、復元なし逐次サンプリングで導出:

- **馬単** `exacta(i,j) = p_i·p_j/(1−p_i)`
- **三連単** `trifecta(i,j,k) = p_i·(p_j/(1−p_i))·(p_k/(1−p_i−p_j))`
- **馬連** `quinella{i,j} = exacta(i,j)+exacta(j,i)`、**三連複** `trio = 6 順序の和`
- **ワイド** `wide{i,j} = Σ_k trio{i,j,k}`(= 2頭がともに top3。**独立積 `top3_i×top3_j` ではない**)
- **複勝** `place` = 頭数依存(5–7=top2 包含, 8+=top3 包含, ≤4=なし)。top2/top3 は `harville_topk` を採用
- **単勝** `win_i = p_i`(パススルー)

## 整合性(最重要)

**固定順序**: 呼び出し側が取消・除外を除外 → エンジンが `Σ=1 再正規化 → [eps,1-eps] clip → 再正規化 → PL 派生`
(**再正規化を PL 分母計算より先に**)。`harville_topk` の分母 skip は本計算に継承しない(clip で端点処理)。

`consistency.check_joint_consistency` が次を fail-fast 検査:
`Σ馬単=1`・`Σ三連単=1`・`無順序=順序和`・`wide≥quinella`・**`joint の周辺(top2/top3)= harville_topk`**(独立実装の
一致 = 正しさの最強の証拠)・包含∈[0,1]・単調。golden(N=3/4, codex 検証済み)で値を固定。

## 評価先行(校正)

確率導出は**結果/オッズを参照しない**(リーク境界)。`calibration` は過去レースの実現組み合わせに対する NLL/Brier を
計測し、**独立積 baseline**(`exacta∝p_i·p_j` / `trifecta∝p_i·p_j·p_k` を Σ=1 再正規化、復元なしを無視した誤近似)と
同一レース集合・同一条件で比較する。

## CLI

```bash
cd probability
uv sync
export DATABASE_URL=postgresql+psycopg://...
# 券種別 上位 K 組み合わせ確率
uv run python -m horseracing_probability show --race-id 200805030401 --top 10
# 校正評価(Plackett-Luce vs 独立積)
uv run python -m horseracing_probability calibrate --from 2008-01-01 --to 2008-12-31 --bet-type exacta
```

## テスト

```bash
cd probability
uv run pytest tests/unit      # golden(N=3/4)・整合性(周辺=harville)・端点/再正規化/決定論/複勝N依存・校正(Docker 不要)
uv run pytest -m integration  # 実 DB で prediction_run→確率→校正評価・CLI show
```

最重要テスト: `test_golden.py`(手計算値)、`test_consistency.py`(周辺=harville)、`test_edge_renorm.py`(再正規化/端点)。

## スコープ外(将来)

exotic オッズ取得・推定オッズ変換・exotic EV/推奨(別 P0）、結合確率の永続化、同着確率モデル、PL 以外の手法。
