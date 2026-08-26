# Phase 1: Data Model — recency weighting

**DB スキーマ変更なし。** ここで定義するのは重みの値としての性質と、artifact に残す記録。

---

## 1. RecencyWeightSpec(凍結される定義)

| フィールド | 意味 |
|---|---|
| `scheme` | `"recency-v1"` 固定 |
| `half_life_days` | 半減期(日)。**日付だけを使った基準で事前登録**される |
| `floor` | 下限 ε(既定 0.05)。0 にしない |
| `normalize` | `"row_sum_equals_n"` 固定。**非交渉**(基底は行重み総和であってレース平均ではない・analyze A1)|
| `ess_floor` | 実効標本数の下限(全体・主要カテゴリ別)。literal で凍結する |
| `major_categories` | 「主要カテゴリ」の集合。literal で凍結する |
| `selection_basis` | 半減期を決めた日付基準(例「新レジーム質量 20〜35%」)と、その実測値 |

### 不変条件

- **INV-W1**: 重みは `(race_date, cutoff)` の**純関数**。結果・オッズ・未来のレースを参照しない。
- **INV-W2**: 重みは**レース内で定数**。`assert_race_constant` を通す。違反は fail-closed。
- **INV-W3**: **行重みの総和が行数に等しい** `Σ_rows w == N`。LightGBM が消費するのは行重みなので、
  レース平均を 1 にしても `Σ_rows w = N + R·Cov(n_r, α_r)` となり総量がずれる(実測 0.6〜1.5%・
  `corr(年, 平均頭数) = −0.809`)。正規化を外す変異でテストが落ちること。
- **INV-W4**: `floor > 0`。どの行も完全には消えない。
- **INV-W5**: 単調非増加(古いほど軽い)。同じ日のレースは同じ重み。
- **INV-W6**: 無効時は重みが `None` として渡り、現行と**ビット一致**。

---

## 2. WeightScope(US2・重みをどこに適用するか)

| フィールド | 意味 |
|---|---|
| `booster` | 常に true(重みの本体) |
| `target_encoder` | 件数・平均・prior に重みを使うか |
| `calibrator` | OOF isotonic に重みを使うか |
| `granularity` | 各消費者について `"per_race"` / `"per_horse_row"` を明示 |

### 不変条件

- **INV-S1**: 宣言と実際の適用が一致する。片方にだけ適用する変異でテストが落ちる。
- **INV-S2**: **暗黙の既定を持たない。** 「booster にだけ渡して他は素通り」を黙って選ばない。
- **INV-S3**: target encoding の**as-of 時間境界は動かさない**。重みは「どれだけ数えるか」で
  あって「何を見てよいか」ではない。
- **INV-S4**: TE に重みを適用する場合、**件数・平均・prior をすべて同じ重みで**計算する。
  一部だけ重み付きは不整合。
- **INV-S5**: **レース当たりか馬行当たりかを各消費者で明示**する。PL 損失では `α_r·L_r` が
  正しいが、TE や校正で各馬行に α を付けると多頭数レースが別途強くなる。

---

## 3. WeightAudit(毎 fit で記録する)

| フィールド | 意味 |
|---|---|
| `cutoff` | その fit で「今」とした日 = **利用可能な最終ラベル日** |
| `half_life_days` / `floor` | 使った定義 |
| `ess_total` | `(Σw)² / Σw²` |
| `ess_by` | 粒度別 ESS: カテゴリ別・供給元別・頭数帯別・特徴の有効値/欠損別・校正スコア帯別 |
| `weight_min` / `weight_max` / `weight_mean` | 分布。`weight_mean` は 1 のはず(INV-W3) |
| `regime_mass` | 新レジーム(2025 年半ば以降)の重み質量比 |
| `vanished_categories` | 学習にほぼ寄与しなくなったカテゴリ値の数と例 |
| `scope` | 実際に適用した `WeightScope` |

### 不変条件

- **INV-A1**: `cutoff` は必ず記録する。**再学習日が動けば全ての重みが動く**ので、これが無いと
  再現できない。
- **INV-A2**: `weight_mean == 1`(正規化の実証)。
- **INV-A3**: `ess_total` と主要カテゴリ別 ESS が凍結した `ess_floor` 以上。**割ったら fail-closed**。
- **INV-A4**: 監査値はモデル特徴に還流させない(憲法 II)。

---

## 4. 日付衛生(fail-closed)

次はすべて例外にする(codex R9):

- `cutoff` が未来日
- 経過日数が負(レース日が cutoff より後)
- `race_date` の欠損
- 半減期の範囲外(`30 <= half_life_days <= 7300`)。年/月/日の取り違えはコードでは原理的に
  判別できないので、実装できるのは範囲検査だけである
- **タイムゾーンは扱わない**。`race_date` は date 型で時刻を持たないので naive date として扱う

---

## 5. 状態遷移

```
Phase A(ラベルを見ない)
  重み関数 + 日付基準で半減期を決めて凍結
        ↓
Phase B ★判定★  ← ここで初めてラベルに触れる
        ├─ ADOPT/有望 ──→ Phase C(US2)→ Phase D(US3)→ Phase E(結線)
        └─ REJECT ──────→ 非結線保全 + spec 転記 + 終了
```

**Phase A が終わった時点で「選択リークが無いこと」が構造的に確定する** — 半減期をラベルを
一度も見ずに決めているため。
