# horseracing-betting

Feature 006 の予測(win 確率)から**単勝 EV 推奨**を生成して `recommendations` に保存し、**疑似ROI
バックテスト**で ROI baseline と比較するパッケージ。`db` / `features` / `eval` / `serving` にパス依存。

## 疑似評価(重要)

`race_horses.odds`(確定単勝オッズ)を EV 入力と払戻の双方に使う **closing-oracle 簡略化**であり、賭け締切時には
存在しない情報を使う。したがって**全評価は疑似評価(pseudo)**で、実運用 ROI ではない。RoiReport は常に
`pseudo=True`、logic_version にも明記。推定オッズ変換(未来レース用)は将来。

## 設計の要点

- **EV 選択**(`ev.py`): 母集団=出走(started)。取消・除外を除外し、残存馬の win_prob を Σ=1 に**再正規化**
  (憲法 IV)。オッズ欠損馬は分母に残すが賭けない。`EV=win_prob×odds`、`EV>=閾値` を全頭買い目に。
  **買い目選択は結果(着順)を一切参照しない**(リーク境界)。内部は float、DB 境界で `Decimal(str(x))`。
- **推奨保存**(`recommend.py`): `recommendations` に append-only。`bet_type='win'`・
  `selection={horse_id,horse_number}`・`market_odds_used=odds`・`is_estimated_odds=false`・
  `pseudo_odds=1/win_prob_renorm`・`pseudo_roi=win_prob_renorm×odds-1`・`logic_version`(式/閾値/stake/除外方針)。
- **戦略 / baseline**(`strategies.py`): `EVStrategy` / `FavoriteROIBaseline`(最低 odds 1 頭)/
  `UniformROIBaseline`(全出走均等)。baseline は ROI 専用(Feature 003 の確率品質 baseline とは別)。
- **疑似ROI 採点**(`roi.py`): 的中=`finished かつ finish_order==1`、払戻=`stake×odds`、外れ=0。DNF=負け、
  取消・除外=母集団除外、同着1着=的中。回収率・的中率はベット単位、**最大DD(絶対額)・最大連敗はレース単位
  (賭けたレースのみ)**、見送り率=見送りレース/評価レース。
- **バックテスト**(`backtest.py`): serving 純部品で **in-memory** 予測(prediction_runs を量産しない)、
  build_feature_matrix を期間で 1 度。同一レース集合で 3 戦略を採点。期間がモデル `train_through` と重なると
  `in_sample=True` を立てる(in-sample の疑似ROI は楽観的)。

## CLI

```bash
cd betting
uv sync
export DATABASE_URL=postgresql+psycopg://...   # 取込済み + active モデル + 予測保存済み DB
# 推奨生成(予測実行 or レース指定: 最新 prediction_run を解決)
uv run python -m horseracing_betting recommend --race-id 200805030401 --threshold 1.0 --stake 100
# 疑似ROIバックテスト(期間)
uv run python -m horseracing_betting backtest --from 2008-01-01 --to 2008-12-31 --threshold 1.0 --stake 100
```

## テスト

```bash
cd betting
uv run pytest tests/unit      # EV 選択/再正規化・疑似ROI 採点(勝/負/DNF/取消/同着)・baseline・決定論(Docker 不要)
uv run pytest -m integration  # 実 DB で推奨生成→保存→監査、バックテスト→baseline 比較
```

最重要テスト: `tests/unit/test_ev_select.py`(除外/再正規化/結果非参照)、`tests/unit/test_roi_scoring.py`
(取消/DNF/同着・DD/連敗)。成功条件=baseline 超え(SC-004)、`回収率>1.0` は参考バー(控除率考慮)。
