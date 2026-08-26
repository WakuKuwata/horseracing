# Contract: 採否ゲート(US1)

**実行前に凍結する。** 結果を見てから閾値・アーム構成・判定式を動かすことは禁止(憲法 III)。

---

## 両アームの構成(**pin する**)

判定の土台は**現行本番と同じ**でなければならない。要求水準(δ=0.00352 帯)は本番 baseline に
対する数値なので、土台が違うと比較が成立しない。

| | 基準アーム | 候補アーム |
|---|---|---|
| objective | `pl_topk` | `pl_topk` |
| arm | `oof_isotonic`(arm E) | `oof_isotonic` |
| n_estimators | 900 | 900 |
| n_oof_blocks | 8 | 8 |
| weight_mask_rate | 0.5 | 0.5 |
| weight_mask_seed | 20260810 | 20260810 |
| **recency 半減期** | **無効** | **凍結値** |

**差は recency 重みのみ。** 実行前後の構造 assert で担保し、**差がゼロでないこと**も確認する
(097 で arm E + drop が「差が厳密に 0」になったまま run 1 を無駄にした前例がある。
**「差が無い」は成功ではなく故障である**)。

---

## verdict の正本(単一式)

```
verdict = gate.adopted AND subgroup_guard
```

`gate.adopted` は harness 組込の採用ゲート一式で、次の AND である:

| sub-gate | 内容 |
|---|---|
| `effect_beats_delta` | 点推定が δ より良い |
| `ci_upper_below_zero` | 開催日クラスタ bootstrap + seed 成分合成後の CI 上限 < 0 |
| `recent_no_evidence_of_harm` | 直近窓で害の証拠が無い |
| `top2_noninferior` / `top3_noninferior` | 導出層の非劣性 |
| `calibration_noninferior` / `calibration_not_emergency` | ECE の非劣化 |

`subgroup_guard` は重要部分集団(直近年 / netkeiba 由来 ID 層 / その積)の非劣化。

**harness 組込の `report.decision` と本式が食い違った場合、本式が正本**とする(088 の前例:
`decision` が underpowered 系で NO_DECISION を返し式と乖離した)。個別数値の事後読み替えは禁止。

**099 は top3 非劣性で REJECT された**(点推定でも CI でもない)。導出層のガードは飾りではない。

---

## 三値

| | 条件 |
|---|---|
| **ADOPT** | 上式が成立 |
| **REJECT** | 評価は完走したが上式が不成立 |
| **NO_DECISION** | 評価が実行不能(データ不足・窓不足)。**ボーダーな数値を理由にしない** |

---

## 評価は無重み

訓練に時間重みを入れても、**評価は無重みで行う**。目的は「将来レース当たりの平均 winner NLL」で
あり、重み付きで評価すると「直近で良く見えるモデル」を直近偏重の物差しで測ることになり
estimand がすり替わる。

---

## 要求水準(隠さない)

feature 100 の実測に基づく:

- CI 上限 < 0 は `point < −1.96 × SE = −0.00309` を要求する(SE = 0.001576)
- δ = **0.00352**(100 US4 で多重検定予算から導出)

**したがって実質の拘束は δ である。** 100 では「δ は現行 CI の下で構造的に非拘束」と結論したが、
δ(0.00352)が CI 要求(0.00309)より厳しいので、**この feature では δ が拘束側に回る**。

これは過去に効いたレバー(容量 −0.0067 / arm E −0.0128 / TE+isotonic −0.014)の帯である。
**小さい効果では通らない。**

---

## 契約版

`evaluation_contract_version: "v4"`。**bump しない** — 本 feature は判定ルールを変えないため
(100 の C1 の教訓: 版を上げると凍結済み config が confirmatory 経路から一斉に締め出される)。

## δ の provenance

`delta_derivation_ref` で 100 の `delta-derivation.json` を指し、
`delta_provenance.assert_delta_provenance` を通す。**`sd_fold` 由来の導出は fail-closed で拒否される。**
