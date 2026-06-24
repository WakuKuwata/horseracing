# Quickstart: 結合確率エンジンの検証

実装後に「単勝確率 → 全券種確率 → 整合性 → 校正評価」が動くことを確認する手順。

## 前提

- Feature 003(`harville_topk`)・006(予測保存)が適用済みの PostgreSQL(校正評価用)。
- `probability/` の依存をインストール(`uv sync`、db/eval にパス依存、numpy)。

## セットアップ

```bash
cd probability
uv sync
export DATABASE_URL=postgresql+psycopg://...   # 校正評価のみ(エンジン核は DB 不要)
```

## 確率導出(ライブラリ / CLI)

```python
from horseracing_probability.engine import joint_probabilities
jp = joint_probabilities({"A": 0.5, "B": 0.3, "C": 0.2})
# jp.exacta[("A","B")], jp.trio[frozenset({"A","B","C"})], jp.place["A"] ...
```

```bash
# prediction_run/レース指定で券種別 上位 K 組み合わせ確率を表示
uv run python -m horseracing_probability show --race-id 200805030401 --top 10
```

## 校正評価(過去データ、評価先行)

```bash
uv run python -m horseracing_probability calibrate --from 2008-01-01 --to 2008-12-31 --bet-type exacta
```

期待: Plackett-Luce と independent_product baseline の NLL/Brier が同一レース集合で並ぶ。PL が baseline を悪化させない。

## テスト

```bash
cd probability
uv run pytest tests/unit      # golden(N=3/4)・整合性(Σ=1/無順序=順序和/周辺=harville/範囲/単調)・端点/再正規化・決定論・複勝N依存
uv run pytest -m integration  # 実 DB で prediction_run→確率→校正評価 baseline 比較
```

検証する受け入れ基準:

- **SC-001**: N=3/4・一様で全券種が手計算 golden と許容内一致。
- **SC-002**: Σ馬単=1・Σ三連単=1・無順序=順序和・joint 周辺=`harville_topk`・包含∈[0,1]・単調 が成立。
- **SC-003**: 取消・除外除去後に残存馬で再正規化、取消馬の確率 0。
- **SC-004**: 端点でゼロ割/範囲逸脱なし、決定論。
- **SC-005**: 校正評価が NLL/Brier を算出、独立積 baseline と同一条件比較、PL が悪化させない。
- **SC-006**: 複勝の頭数依存・小頭数縮退・該当なし。
- **SC-007**: 確率導出が結果/オッズ非参照。

## 整合性の考え方(SC-002 の核)

joint(PL 列挙)から計算した各馬の周辺(top2/top3 包含)が、独立に実装された `harville_topk`(eval)と一致することが
正しさの最強の証拠。両者が同じ値に収束しなければエンジンにバグがある(fail-fast)。
