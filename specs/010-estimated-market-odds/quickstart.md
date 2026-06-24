# Quickstart: 推定市場オッズ変換の検証

実装後に「単勝オッズ → 推定市場オッズ → 復元/校正検証」が動くことを確認する手順。

## 前提

- Feature 009(`probability` パッケージ・結合確率エンジン)が適用済み。
- 検証は取込済み(odds 含む)+ 結果のある PostgreSQL。
- `probability/` の依存は既存(db/eval)。追加依存なし。

## セットアップ

```bash
cd probability
uv sync
export DATABASE_URL=postgresql+psycopg://...   # 検証のみ(変換核は DB 不要)
```

## 推定オッズ導出(ライブラリ / CLI)

```python
from horseracing_probability.market_odds import estimate_market_odds
eo = estimate_market_odds({"A": 2.0, "B": 4.0, "C": 8.0})
# eo.exacta[("A","B")], eo.trio[frozenset({"A","B","C"})], eo.is_estimated == True
```

```bash
# レース指定で券種別 推定オッズ(上位)+ 控除率・推定明示
uv run python -m horseracing_probability estimate-odds --race-id 200805030401 --top 10
```

## 検証(過去データ、評価先行)

```bash
uv run python -m horseracing_probability validate-odds --from 2008-01-01 --to 2008-12-31
```

期待: 単勝オッズ復元誤差(レース単位)と市場含意 q の校正(NLL/Brier)が表示され、全出力に **「疑似評価(推定市場
オッズ)」** が明示される。

## テスト

```bash
cd probability
uv run pytest tests/unit       # 単勝復元 golden(odds=R/s→復元)・q 整合性・控除率・端点 cap・決定論・p 非参照
uv run pytest -m integration   # 実 DB で odds→推定オッズ、復元誤差・q 校正
```

検証する受け入れ基準:

- **SC-001**: 人工オッズ `odds_i=R/s_i` で `q_i=s_i`、推定単勝オッズ `=odds_i` を厳密復元。
- **SC-002**: q を 009 に通した各券種の推定オッズが整合的(Σ=1 等)、控除率で `(1−takeout)/P`。
- **SC-003**: 欠損/0/負・取消・除外を除外して q 再正規化、推定不能を返す。`P→0` で推定オッズ cap/None、確率は壊れない。
- **SC-004**: 単勝復元誤差 + q 校正が過去データで計測され、全出力が疑似評価明示。
- **SC-005**: 変換が p を一切参照せず、q と p が別物。
- **SC-006**: 決定論。複勝頭数依存・小頭数。控除率が設定可能で logic_version に含まれる。

## 核心の考え方(SC-001/SC-005)

`q` は**市場の投票シェア**(`odds=R/s ⇒ q=s`)であって真の勝率でもモデル確率 p でもない。推定単勝オッズが実 odds を
復元するのは `R·S=1` のとき。推定 exotic オッズは単勝市場から PL で外挿した**推定**であり、実 exotic 価格とは乖離しうる
(疑似)。モデル p は本変換に一切使わない(将来 EV で `p×推定オッズ`)。
