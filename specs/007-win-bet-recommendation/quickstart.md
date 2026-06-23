# Quickstart: 単勝 EV 推奨と疑似ROIバックテストの検証

実装後に「推奨生成 → 永続化 → 監査」と「期間バックテスト → baseline 比較」が動き、疑似評価が明示されることを確認する手順。

## 前提

- Feature 001 適用 + 002 取込済み(odds 含む)+ 005 で active モデル + 006 で予測(prediction_run)保存済みの
  PostgreSQL。
- Docker(testcontainers 用)。
- `betting/` の依存をインストール(`uv sync`、db/features/eval/serving にパス依存)。

## セットアップ

```bash
cd betting
uv sync
export DATABASE_URL=postgresql+psycopg://...   # 取込済み + active モデル + 予測保存済み DB
```

## 推奨生成(US1)

```bash
# 予測実行を指定して単勝 EV 買い目を生成・保存
uv run python -m horseracing_betting recommend --prediction-run <uuid> --threshold 1.2 --stake 100
# レース指定(内部で active モデルの prediction_run を解決/利用)
uv run python -m horseracing_betting recommend --race-id 200805030401 --threshold 1.2 --stake 100
```

期待: EV>=閾値 の馬だけが `recommendations`(bet_type='win')に保存され、各行に market_odds_used・pseudo_odds・
pseudo_roi・selection・logic_version が揃う。保存件数と各 EV を表示。

## 疑似ROIバックテスト(US2)

```bash
uv run python -m horseracing_betting backtest --from 2008-01-01 --to 2008-12-31 \
    --threshold 1.2 --stake 100
```

期待: EV 戦略 / FavoriteROIBaseline / UniformROIBaseline の **回収率・的中率・見送り率・最大DD・最大連敗**が
同一レース集合で表に並ぶ。全行に **「疑似評価(pseudo)」** が明示される。

## テスト

Docker 必須(testcontainers が PostgreSQL を起動し `db/` migration を head まで適用)。

```bash
cd betting
uv run pytest tests/unit      # EV 選択・除外/再正規化・疑似ROI 採点(勝ち/負け/DNF/取消/同着)・baseline・決定論
uv run pytest -m integration  # 実 DB で推奨生成→保存→監査、バックテスト→baseline 比較
```

検証する受け入れ基準:

- **SC-001/002 (推奨)**: EV>=閾値 のみ保存・監査情報あり。odds null/0・取消/除外・win_prob=0 を除外し再正規化。
- **SC-003 (採点)**: 勝ち/負け/DNF/取消/同着を定義どおり扱い、回収率/的中率/見送り率/最大DD/最大連敗を算出。
- **SC-004 (比較)**: EV 戦略と 2 baseline が同一レース集合・同一条件で比較。
- **SC-005 (append-only)**: 再生成が新しい recommendation 群(別 logic_version)。
- **SC-006 (疑似評価)**: 全評価出力が pseudo として明示され、確定オッズ使用の前提が記録される。

## 疑似評価の考え方(SC-006)

`race_horses.odds` は結果確定時オッズで、賭け締切時には存在しない。これを EV 入力と払戻に使う closing-oracle 簡略化は
実運用 ROI ではない。レポートの `pseudo=True`・logic_version・README で明示し、実運用は推定オッズ変換(将来)が要る。
