# Quickstart: Harville stage 割引 (049) 検証ガイド

前提: ローカル Postgres(`DATABASE_URL=postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing`、2007–2025 ingest 済み・lgbm-042 active・永続化予測あり)。

## 1. 単体テスト(DB 不要)

```bash
uv run --project eval pytest ../eval/tests -k "stage_discount or baselines" -q
uv run --project probability pytest ../probability/tests -q   # INV-S1..S7 + 既存整合性
uv run --project training pytest ../training/tests -q          # assemble_predictions 透過
```

期待: λ=1 バイト一致(INV-S1/S9)・単調/合計/joint 整合(INV-S3..S5)・フィット決定論(INV-S6)全緑。

## 2. 事前登録 A/B 評価(US2 — 採否判定)

```bash
cd training
DATABASE_URL=... uv run horseracing-training stage-discount-eval
```

期待出力(AdoptionReport 同型):
- fold 別(2008–2025)の baseline(λ=1) vs candidate(λ̂: 前 fold pooled OOS フィット)の top2/top3 LogLoss/ECE、fold λ̂
- **win 指標の diff が全 fold で 0**(stage 1 不変の証明)
- overall reliability(top3 高帯の予測−実現乖離が baseline +9〜10pt から縮小しているか)
- gate 判定: PRIMARY(top2/top3 LogLoss・ECE 改善+strict majority)/ガード(worst-fold top3 dLogLoss ≤ +5e-3)と採否

## 3. exotic 非悪化ゲート(MUST)

```bash
cd betting
DATABASE_URL=... uv run horseracing-betting stage-discount-backtest-compare
```

期待: 複勝・ワイド・三連複の pseudo-ROI(λ=1 vs λ̂、同一レース・同一条件)と差分。MUST: 各差 ≥ −0.005。
実行前にサンプル密度(永続化予測の分布)を確認し、密度不足なら密集窓を採用してから実行(048 教訓 — 結果を見た後の窓変更は禁止)。

## 4. (採用時のみ)製品 E2E — US3

```bash
# (1) 予測の生成/再生成 — race_predictions.top2/top3 は serving 経路が書く
#     (recommend-serve は予測を書かない [043: run 無しは SKIPPED] — analyze Q1)
cd serving
DATABASE_URL=... uv run horseracing-serving predict --race-id <対象レース>   # または predict-backfill --force

# (2) 推奨の生成 — betting 経路(two_gamma 込み)の exotic P_model に割引適用
cd ../betting
DATABASE_URL=... uv run horseracing-betting recommend-serve --race-id <対象レース>
```

期待:
- (1) の lv に `sdisc=harville;l2=...;l3=...;n2=...;n3=...`(素の p でフィット)、(2) の lv にも同形式(two_gamma 後 p' でフィット、値は別)が記録される
- `race_predictions` の win_prob は従来とバイト一致、top2/top3 のみ変化
- API(`/api/v1/races/{id}/predictions`)透過、画面の連対率・複勝率が割引済み値
- 既存 idempotent/後方互換テスト(betting/api/serving スイート)緑

## 5. 回帰確認(全パッケージ)

```bash
for p in eval probability training serving betting api; do (cd $p && uv run pytest -q); done
```

不採用時: 手順 4 は実施せず、負結果を spec/report に記録して終了(opt-in 実装はマージ可、製品既定は λ=1 のまま)。
