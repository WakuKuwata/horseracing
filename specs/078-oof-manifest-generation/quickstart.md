
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

---

## T015/T016 DECISIVE full-history 結果(2026-07-21・実 lgbm-063)

**full-history OOF bundle 生成**: `oof-generate --from 2007-01-01 --to 2026-12-31`(19 fold・2008-2026・
64,073 レース)= **~17 分**(1 fold 実測 93s からの ETA 見積り 3-8h は大幅に過大だった=後半 fold も想定より速い)。
bundle_digest=`52d0831b…`。

**DECISIVE VERDICT**(18 held-out fold):
| stage | verdict | 数値 |
|---|---|---|
| **two_gamma_win** | **REJECT**(calibrated_worse) | OOF ECE raw **0.000322**→cal 0.001667(**+0.001345 悪化**)・transfer_ks 0.051・n_days 2003 |
| **stage_discount_topk** | **ADOPT** | top2 ECE 0.00781→**0.00198**(4x)・top3 ECE 0.02021→**0.00354**(6x)・16/18 fold・worst +0.00111 |

**deployment final-fit**: two_gamma=identity(REJECT)/ stage λ2=**0.81812** λ3=**0.69047**・n_fit 63991・
fit_through 2026-07-18。

**生成 manifest**(`generate-manifest` CLI・clean tree→scope=production・attestation binding 通過):
schema_v3・**activation_eligible=True**・manifest_digest=`d9f45bb0…`。

**activation 検証(T016)**: 076 loader で activate 成功(two_gamma identity・stage λ=0.818/0.690)・replay
parity PASSED・fit_through 以前のレースは temporal guard で拒否。

### 科学的結論(074 の存在意義を実証)
1. **048 の two-gamma 採用は leak-optimistic だった** — 非OOF評価では良く見えたが、honest OOF では
   **REJECT**(win は既に OOF ECE 0.0003 で完璧に校正済み → two-gamma は悪化させる)。**これこそ 074 が
   捕らえるべきリークで、leak補正後に verdict が反転した**。
2. **049 の stage discount は本物** — OOF-faithful でも ADOPT、λ(0.818/0.690)は歴史的 fit(0.82/0.70)と
   一致=頑健。
3. → **eligible な実 manifest が存在**: activate すれば betting/dispersion の two-gamma を identity 化
   (leaky校正を除去)+ serving 表示の stage-λ を OOF-faithful 値に。**activation は operator 判断**
   (do-not-default-ON waiver の明示解除)であり、betting 推薦を変える(two-gamma 除去)ため自動では行わない。

**注**: bundle/manifest は `artifacts/artifacts/oof/…`(--out artifacts が artifacts/oof を付加=二重)。
次回は `--out .` 推奨。manifest は絶対パスで機能するため実害なし。
