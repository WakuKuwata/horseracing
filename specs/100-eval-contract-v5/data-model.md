# Phase 1: Data Model — 評価契約 v5

**DB スキーマ変更なし。** ここで定義するのはディスク artifact の形と、それが満たすべき不変条件。

---

## 1. PairedEvidenceRow(US1・新規)

判定 1 回につき 1 レース 1 行。判定を再現するのに十分な最小単位。

| フィールド | 型 | 意味 |
|---|---|---|
| `race_id` | str(12桁) | 憲法 I の識別子契約に従う |
| `race_day` | date | bootstrap のクラスタキー。**開催日**であってレース日時ではない |
| `candidate_winner_nll` | float | 候補アームの winner NLL(クリップ後) |
| `active_winner_nll` | float | 基準アームの winner NLL(クリップ後) |
| `diff` | float | **`candidate − active`**。符号規約は固定(INV-E4) |
| `covariates` | dict | 事前登録された共変量。**判定式には入らない**(記録のみ) |

### 不変条件

- **INV-E1**: 行数 == verdict の `n_races`。不一致は fail-closed(FR-007)
- **INV-E2**: `race_id` はこの artifact 内で一意
- **INV-E3**: `diff == candidate_winner_nll - active_winner_nll` が浮動小数点で厳密一致
- **INV-E4**: **符号規約 `候補 − 基準`** を明示検査する。向きが逆でも CI の幅だけは
  もっともらしく見えるため、再計算の一致だけでは取り違えを検出できない(FR-008a)
- **INV-E5**: `covariates` は**結果(着順)を読まない**量のみ。勝ち馬の特定以外の形で結果を
  持ち込まない(FR-011)。leak-guard テストで機械固定
- **INV-E6**: 行順を入れ替えても、再計算した点推定・CI・verdict が不変
- **INV-E7**: 丸めずに round-trip する(losslessly)。丸めると INV-E3 と再計算一致が壊れる

---

## 2. PairedEvidenceArtifact(US1・新規)

per-race 行に、**行だけでは再現できない判定パラメータ**を添えたもの。

| フィールド | 意味 |
|---|---|
| `rows` | `PairedEvidenceRow` の配列 |
| `bootstrap` | `{b, seed, alpha, block}` — CI を再現するのに必須 |
| `seed_noise` | 判定が使った seed 成分の宣言(v4 互換のため欠落を許す) |
| `evaluation_contract_version` | 版 |
| `gate_config_hash` | どの凍結 config の下で作られたか |
| `race_id_set_hash` | 既存の model-blind fixed set hash と一致すること |
| `candidate_recipe_hash` / `active_recipe_hash` | どの 2 アームの差か |
| `window` | `{from, to}` |
| `artifact_kind` / `eligible_for_verdict` | 既存の verdict 適格判定と同じ語彙 |

### 不変条件

- **INV-A1**: この artifact **だけ**を入力に、契約が定める手順で点推定・sampling CI・total CI を
  再計算して verdict と**ビット一致**する(FR-008)
- **INV-A2**: append-only。再実行では上書きせず新しい artifact を作る(FR-010)
- **INV-A3**: 複数窓を束ねる driver は、束ねる前の各窓の証拠を落とさない(FR-009)
- **INV-A4**: モデル特徴に還流させない(FR-012)

---

## 3. EnsembleManifest(US3・新規・Phase D 以降)

k-seed アンサンブル出荷物の同一性を定める。

| フィールド | 意味 |
|---|---|
| `k` | member 数。**宣言値** |
| `members` | member の**順序つき**リスト。各要素は booster の内容 hash |
| `aggregation` | `"race_prob_mean"` 固定(D3) |
| `score_definition` | `"log_p_bar"` 固定(D4) |
| `preprocessing_hash` | 前処理(TE encoder 等)の同一性 |
| `calibrator_hash` | **アンサンブル OOF で再 fit した**校正器(D5) |
| `dtype` / `runtime` | 浮動小数点の再現条件 |

### 不変条件

- **INV-M1**: `len(members) == k`。**学習時と serving のロード時の両方**で検証し、不一致は
  fail-closed。**部分平均・単一 seed へのフォールバックを禁止**(FR-024)
- **INV-M2**: 同一入力に対する予測が**ビット一致**で再現する(FR-025)
- **INV-M3**: 同一性 hash は **seed 集合だけでは不十分**。member の順序つき hash・前処理・
  校正器・集約演算・dtype・runtime を含める。**浮動小数点の平均は member 順序で bit が変わりうる**
- **INV-M4**: レース内で `Σ p̄ = 1`。校正後も `h(p̄_i)/Σ_j h(p̄_j)` の形で Σ=1(FR-022b・憲法 IV)
- **INV-M5**: k 個すべての hash 照合が終わるまで serving を ready にしない

---

## 4. DeltaDerivation(US4・新規)

δ の導出根拠を機械可読に残す。

| フィールド | 意味 |
|---|---|
| `method` | `"multiple_testing_budget"` 固定 |
| `judgments_per_year` | N。実績から取る(直近 1 年で約 8) |
| `acceptable_net_harm_prob` | 実行前に凍結する許容確率 |
| `assumed_effect_distribution` | 導出に使った効果量の分布仮定 |
| `derived_delta` | 導出された δ |
| `frozen_at` | 凍結日 |

### 不変条件

- **INV-D1**: `derived_delta` は `sd_fold` に依存しない。`sd_fold` を変えても動かない(FR-029)
- **INV-D2**: δ の変更は**新しい gate-config の凍結**として行い、過去 verdict の再読み替えに
  使わない(FR-031)
- **INV-D3**: 過去 verdict の表示・比較には**当時の**契約版・δ・provenance・gate hash を使う。
  解決できなければ **fail-closed**。v5 の δ で補わない(FR-031a)

---

## 5. 状態遷移(この feature の唯一の「状態」)

US3 のスパイク中断点。

```
未測定 ──(Phase C スパイク実行)──> 足切り通過 ──> Phase D/E 本実装
                                 └─> 足切り不通過 ──> 非結線保全 + spec 転記 + 終了
```

「足切り値」は**スパイク実行前**に凍結する(FR-016)。結果を見てから閾値を動かすことは
過去の verdict 規約と同じく禁止。
