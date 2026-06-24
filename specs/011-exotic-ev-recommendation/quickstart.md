# Quickstart: exotic EV 推奨と疑似ROIバックテストの検証

実装後に「モデル p × 推定市場オッズ → exotic EV 推奨 → 疑似ROIバックテスト → baseline 比較」が動くことを確認する手順。

## 前提

- Feature 006(serving、race_predictions に win_prob)・009(結合確率)・010(推定市場オッズ)が適用済み。
- 取込済み(odds 含む)+ 結果のある PostgreSQL に活性モデル(prediction_run)が存在。
- `betting` パッケージが `probability` に依存(009 joint + 010 estimate)。

## セットアップ

```bash
cd betting
uv sync   # horseracing-probability(path 依存)を含む
export DATABASE_URL=postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing
```

## exotic EV 推奨生成(ライブラリ / CLI)

```python
from horseracing_betting.exotic_ev import canonical_field, exotic_ev_bets
field = canonical_field({1: 0.4, 2: 0.3, 3: 0.2, 4: 0.1}, {1: 2.0, 2: 3.5, 3: 6.0, 4: 12.0})
bets = exotic_ev_bets(field, threshold=1.0, top_k=3)
# bets[i].bet_type / .selection({"horses":[...], "ordered":...}) / .ev = p_model*o_est / .pseudo_roi
```

```bash
uv run python -m horseracing_betting exotic-recommend --race-id 200805030401 --run-id 1 \
    --threshold 1.0 --top-k 5 --stake 100
```

期待: 券種別(複勝/馬連/馬単/ワイド/三連複/三連単)に EV≥閾値 の上位 K 買い目が表示・保存され、全行に
**「推定オッズ使用(is_estimated_odds=true)/ market_odds_used=null」** が明示される。

## 疑似ROIバックテスト(過去データ、評価先行 III)

```bash
uv run python -m horseracing_betting exotic-backtest --from 2008-01-01 --to 2008-12-31 \
    --threshold 1.0 --top-k 5 --stake 100
```

期待: EV 戦略 と baseline(最低 O_est / 均等)の券種別 回収率/的中率/見送り率/最大DD/最大連敗が同一条件で並び、
冒頭に **「二重疑似(推定オッズ + PL 外挿)評価」** が明示される。成功は EV が baseline を上回ること(>1.0 ではない)。

## テスト

```bash
cd betting
uv run pytest tests/unit         # canonical 母集団整合・EV/上位K・selection JSONB・券種別的中・複数当たり・baseline・決定論・二重疑似
uv run pytest -m integration     # 実 DB で推奨生成→保存→監査、バックテスト→baseline 比較
```

検証する受け入れ基準:

- **SC-001**: 各(レース,券種)で `EV=P_model(009 on p)×O_est(010 on q)` を計算し、EV≥閾値 上位 K を推奨。
- **SC-002**: P_model と O_est を**同一 canonical 母集団**(p と odds 両方有効)で算出。片方欠損は除外+再正規化。
- **SC-003**: selection は JSONB 安全配列(順序券種=順序付き、無順序=整列、frozenset 非保存)。往復一致。
- **SC-004**: 券種別的中(exacta/trifecta=順序、quinella/trio=集合、wide/place=包含+field 規則)。複勝/ワイド複数当たりはベット単位。
- **SC-005**: baseline(最低 O_est / 均等)と同一条件比較し、成功=baseline 超え。
- **SC-006**: 全推奨・全評価が is_estimated_odds=true / market_odds_used=null / 二重疑似ラベルを持つ。決定論。append-only。
- **SC-007**: 買い目決定はレース結果を一切参照しない(p+odds+entry_status のみ)。

## 核心の考え方(リーク境界 / p≠q)

的中確率は**モデル確率 p**(009)、推定オッズは**市場 q**(010、win オッズ由来)で、`EV=P_model(p)×O_est(q)`。p と q を
混同しない。実 exotic オッズが無いため払戻は推定オッズで代用し、評価は**二重疑似**(推定オッズ + PL 外挿)。買い目は結果を
見ずに決め、結果は採点でのみ使う。実 exotic オッズ取得・Kelly・bias 補正は将来 feature。
