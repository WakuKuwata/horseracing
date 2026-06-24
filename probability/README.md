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

## 推定市場オッズ変換(Feature 010、`market_odds.py` / `market_calibration.py`)

実 exotic オッズが無い/未来レースのため、**単勝オッズ**から各券種の**推定市場オッズ**を導出する(憲法 P0)。

- `market_implied_win_probs(win_odds)`: `q_i=(1/odds_i)/Σ(1/odds_j)`。これは**市場の投票シェア**で、**真の勝率でも
  モデル確率 p でもない**(favorite-longshot bias を含む)。
- `estimate_market_odds(win_odds)`: `q` を **Feature 009 エンジン**に入力 → 各券種の市場含意確率 → 控除率で
  `推定オッズ = (1−takeout_b)/P_market`。控除率は JRA 既定(単複20%/馬連ワイド22.5%/馬単三連複25%/三連単27.5%、
  **時点依存→設定可能 + logic_version**)。`is_estimated=True`。
- **単勝復元**: 推定単勝オッズ `= R·S·odds_i`、`R·S=1`(控除率=実オーバーラウンド)で実 odds を厳密復元。
- **p/q 分離**: 変換は**市場オッズのみ**(モデル p 非参照)。将来 EV は `p(009 モデル)× 推定オッズ(010 市場)`。
- **数値**: 欠損/0/負・取消・除外を母集団から除外して q 再正規化。`P_market≤eps` は推定オッズ None、それ以外は
  `min(R/P, odds_cap)`。**確率本体は cap しない**(整合性維持)。
- **疑似明示**: PL 外挿の推定 exotic オッズは実 exotic プール価格と乖離しうるため**「推定/疑似」**として明示(憲法 V)。
  実 exotic オッズが無いため評価は単勝復元 + q 校正(NLL/Brier)に限る。

```bash
uv run python -m horseracing_probability estimate-odds --race-id 200805030401 --top 10
uv run python -m horseracing_probability validate-odds --from 2008-01-01 --to 2008-12-31
```

## スコープ外(将来)

exotic EV/推奨(`p×推定オッズ`)、推定オッズの永続化、実 exotic オッズ取得・価格復元評価、favorite-longshot bias
補正、複勝払戻の厳密モデル、同着確率モデル、PL 以外の手法。
