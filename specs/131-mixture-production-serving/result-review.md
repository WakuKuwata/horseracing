# 131 結果レビュー(2026-09-09)

## できたこと

- `serving/mixture_serving.py`: mixture model_version の loader 分岐・予測(serving 条件 → 6 member 補正平均 → win)・表示 top2/top3 は本番規約(平均 win から Harville + stage 割引)・履歴/日付の as-of 入力・係数年の猶予(1 年・超過は例外)。
- `model_loader.load_serving_model`: metadata の `artifact_kind: mixture_model_version` で分岐。既存 booster 経路は無変更。
- `pipeline`: `run_serving` / `run_serving_backfill` が mixture のとき日ごとに履歴を 1 クエリで取り `_predict_persist(history=)` へ。logic_version に `;mix=6:<sha12>;coef=<年>`(猶予適用時 `;coefstale=N`)。
- `mixture_model.predict_mixture(coefficient_grace_years=)`: shadow は 0(2026 のみ)、本番は 1。
- `scripts/register_mixture_model_version.py`: bundle を `artifacts/model_versions/mix-129-nj6/` に SHA 照合しながら自己完結コピーし、本番 loader で読めることを確認してから candidate 行を作成(19MB)。
- unit テスト 15 件追加(serving unit 192 件緑)。既存 serving 経路のテストは無改修で緑。

## E2E(実 DB)

| 確認 | 結果 |
|---|---|
| `serving predict --race-id 202607030311 --model-version mix-129-nj6` | run 作成・13 頭・logic_version `…;mix=6:28f39715a9c1;coef=2026;sdisc=harville;…;wregime=full_info` |
| WIN の parity(shadow `--regime serving` 記録との比較) | **最大差 0.0** |
| Σwin / Σtop2 / Σtop3 | 1.0 / 2.0 / 3.0 |
| explanation | 全馬 null(API は「未提供」) |
| `promote-model` 昇格前確認(dry-run) | OK: artifact 実在・feature schema が serving に乗る |

## 昇格(実行済み・2026-09-09)

ユーザー指示で `promote-model --apply` を実行。ACTIVE = mix-129-nj6、lgbm-094-cap900 は candidate へ降格(rollback コマンドは metrics_summary に記録)。続けて 2026-01-01〜09-06 の backfill を実行(結果は下記)。

## セルフレビュー checklist(codex unavailable・使用上限 9/15 まで)

1. 既存経路の不変: 分岐は `isinstance` 1 点(pipeline 4 箇所+loader 1 箇所)。booster 経路のコードは 1 行も変えていない。既存 unit 177 件緑。
2. リーク: 入力は `build_feature_matrix(end_date=対象日)` の as-of 行と、race_horses の started 行を対象日より厳密前に絞った履歴のみ。結果・オッズは読まない。
3. パリティ: win は shadow 記録と厳密一致。top2/top3 は本番規約(stage 割引)で研究の平均 head とは異なることを spec FR-002 に明記。
4. 冪等: `_has_run_for_model` は model_version + logic_version 要素で判定するので、既存モデルの run と衝突しない。
5. fail-closed: bundle SHA 不一致・member 欠落/改変・列プロファイル不一致・係数年超過・履歴未供給はすべて例外。
6. 運用: 係数は 2026 用。2027 は `coefstale=1` で動き、2028 は止まる。2026-12 に係数を更新した bundle v2 を作ること。

## 残余リスク

- 将来レースでの実測はゼロ(129 の prospective capture を継続し 112 の事後確認に使う)。
- 4 日 rehearsal(+0.0015)は 130 と矛盾しないが支持もしない。
- integration テスト(testcontainers)は 106 の append-only トリガが試験用 TRUNCATE を拒むため、本変更と無関係に赤(既存問題・別タスク)。

## backfill(2026-01-01〜09-06)

generated=2,441・skip_exists=1(E2E で先に作った run)・error_days=0。2026 年の全 2,442 レースに mix-129-nj6 の run があり、API の既定 run と `/models` の active も新モデル。`coefstale` 付き run は 0(すべて 2026 年=係数年と一致)。
