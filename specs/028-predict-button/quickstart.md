# Quickstart / Validation: 予測生成ボタン (028)

前提: branch `028-predict-button`、DB `horseracing`、024 ops 基盤 + lgbm-026 採用済み。

## 1. ops 単体/結合テスト
```bash
cd ops
uv run ruff check src tests
uv run pytest -q     # enqueue dedup / worker claim / run_predict(過去レース・entries無→skipped・active異常→failed) / endpoint 契約
```
期待: 全緑。

## 2. front テスト
```bash
cd front
pnpm test            # PredictButton: ポーリング→succeeded で predictions invalidate / failed 表示 / 受付中 disabled
pnpm run check-openapi  # ops 型 drift-check（ops-openapi.json と生成型）
```

## 3. 手動 e2e（実 DB・実アプリ）
```bash
# API(014) + ops(028) + front を起動（deploy/compose もしくは個別 dev）
# 予測の無い実レース（例 2023 のレース）を front で開く → 「予測する」を押す
```
期待:
- ボタン: 受付→生成中→完了 と遷移（数十秒、ポーリング）。
- 完了後、予測セクション（勝率 p / p vs q / 校正 / RunAudit）が再読み込みなしで表示。
- ingestion_jobs に job_type=predict の行（status=succeeded, summary に prediction_run_id/model_version）。
- prediction_runs に新 run（model_version=lgbm-026, computed_at）。

## 4. read-only 境界の確認
```bash
cd api && uv run pytest -q -k "no_write_boundary or readonly"   # 014 は GET のみ（不変）
```
期待: 緑（predict 追加後も api は read-only）。

## 5. エッジ確認
- 未来レースで出走馬未確定 → ボタン「対象なし」(skipped)、prediction_run を作らない。
- 採用モデルが無い/複数 → 「生成失敗」＋理由表示（job failed）。
- 連打 → 二重ジョブにならない（reused / disabled）。
- predict と データ更新(refresh) を同レースで併用 → 競合せず両方完了。
