
---

## T015 実 DB 実行結果(2026-07-21・horseracing DB / lgbm-063)

**既存の OOF bundle を発見**(`artifacts/oof/5130197c…`=folds 2025/2026・5319 レース / `4fef0955…`=fold 2026)
→ 数時間の `generate_oof_bundle` 再生成なしで実データ検証を実施。

**実 lgbm-063 OOF 校正 verdict**(bundle 5130197c 経由):
| stage | verdict | 詳細 |
|---|---|---|
| two_gamma_win | **NO_DECISION**(within_margin) | ECE raw 0.003995 → cal 0.003438(−0.000557 改善だが non_inferior_margin 内)・n_days 167 |
| stage_discount_topk | **NO_DECISION**(no_held_out_stage_evidence) | folds=2025/2026 の 2 つ = held-out 1 fold で stage prequential に prior 不足 |

**deployment final-fit**: 両 stage とも正しく **identity を出荷**(NO_DECISION → 非採用)。

**生成 manifest**(bundle 自身の attestation で provenance 充足): schema_v3・**activation_eligible=False**・
両 stage identity・fit_through=1970-01-01(floor)・digest-keyed path に write。→ **076 loader は正しく拒否**
(not production-eligible)。

**結論**:
1. **パイプライン全体が実データで実証**(read bundle → 校正 verdict → deployment fit → v3 manifest → write)。
2. 既存 bundle は**部分的(2025-2026)**で fold 履歴不足 → NO_DECISION。**決定的な verdict には full-history
   (2008-2026)OOF bundle 生成(数時間)が必要**=真の T015 の残作業。
3. 2025-2026 データでは校正が決定的に効かない(ECE 0.004 は既に良好)= lgbm-063 は既に良く校正されている
   という [[accuracy-levers-exhausted-2026-07]] の傾向と整合。
4. **build_oof_manifest に bundle-attestation binding チェックが欠けていた実バグを T015 が炙り出し修正**
   (bundle 生成世代 ≠ 供給 attestation を fail-closed・codex D7)。

**残: full-history bundle 生成(operator・数時間)**: `training oof-generate --active-dir <lgbm-063 dir>
--from 2007-01-01 --to 2026-12-31 --out <root>` → `training generate-manifest`。full 履歴なら決定的 verdict が出る。
