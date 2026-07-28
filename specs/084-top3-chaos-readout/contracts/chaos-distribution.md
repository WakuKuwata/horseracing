# Contract: 荒れ分布の導出コア (rev2)

**Module**: `probability/src/horseracing_probability/chaos_distribution.py`

純関数のみ。DB・ファイル・時刻に依存しない。既存 `engine.joint_probabilities` を再利用し、
**新しい確率理論を導入しない**。

---

## 公開 API

```python
def chaos_distribution(
    q: dict[str, float],              # horse_id -> 市場 vote-share (正規化前でよい)
    ranks: dict[str, int],            # horse_id -> popularity (重複・欠損なし。1..n の順列は不要)
    events: Sequence[EventDefinition],# 型付き述語つき。事前登録された集合
    *,
    stage_discount: StageDiscount | None = None,
    eps: float = DEFAULT_EPS,
    invariant_tol: float = 1e-9,
) -> ChaosDistribution: ...
```

```python
def chaos_readout(
    q, ranks, events, *, stage_discount, edges
) -> tuple[ChaosDistribution, ChaosDistribution, str]:
    """(raw=λ1, adjusted=λ2/λ3, band) を返す。生と補正は別の同時分布であり混ぜない。"""
```

```python
def band_of(p_primary: float, edges: Sequence[float]) -> str:
    """band 軸は p_s_ge_20。E[S] ではない(期待値は裾を隠す)。"""
```

---

## 契約

### C1: 入力検証(fail-closed)

- `set(q) != set(ranks)` → `ValueError`。**部分再正規化しない**
- `ranks` に**重複がある / 欠損がある** → `ValueError`。**`1..n` の完全な順列は要求しない**
  (取消馬が人気番号を消費するため。実測で取消レースの 26.1% が max(rank) > n)。
  丸めオッズによる暗黙の再ランク・馬番タイブレークは禁止
- `n < 4` → `ValueError`(**fit 母集団と同一の適格条件**。3 頭立ては境界推定の分布外・FR-029a)
- `q` に非正・NaN → `ValueError`

### C2: 導出 — 事象は順序三つ組の走査中に評価する

`joint_probabilities(q, field_size=n, eps=eps, stage_discount=stage_discount)` を呼び、
戻り値の `trifecta`(順序三つ組 `(a,b,c) -> P`)を **1 回走査**して、同じループの中で

```text
s = ranks[a] + ranks[b] + ranks[c]
pmf[s] += P
for ev in events:
    if ev.predicate(ranks[a], ranks[b], ranks[c], n):
        event_mass[ev.key] += P
```

を集約する。

**S の PMF に落としてから事象を計算してはならない。**
`himo_are`(`ra<=3 かつ (rb>=10 または rc>=10)`)と `total_collapse`(1着が二桁人気)は
**着順に依存**し S からは復元不能 — `(1,9,10)` と `(10,1,9)` はどちらも S=20 だが両事象の値が違う。

`EventDefinition.predicate` は**実行可能な型付き述語**であり、監査用文字列ではない。

**これ以外の経路で三つ組確率を計算してはならない**(二重実装の禁止)。

### C3: 正規化の禁止 / 不変条件

`triple_mass_sum = sum(trifecta.values())` を**記録**し、
`abs(triple_mass_sum - 1.0) > invariant_tol` で `ChaosInvariantError` を送出する。
**大域リスケール(和で割る)を実装してはならない。**

根拠: 正規化 q では各段の条件付き分布が和 1 なので Σ = 1 になるはず。ずれは列挙漏れ・分母誤り・
epsilon floor バグの兆候であり、正規化はそれを隠す。

**主張の範囲を限定する**: 「任意の λ・任意の q で Σ=1」とは主張しない。ただし
**エンジンは想像より頑健**である — `_normalize_clip` の clip+再正規化により、
ランダム 720 フィールド × λ=(0.1,5.0) まで Σ=1 が **6e-11** 精度で保たれる(実装後に測定)。
不変条件が発火するのは**ほぼ退化したフィールド**(1 頭が実質全質量・他がクリップ前 1e-300 級)で、
**引き金は λ ではなく入力のクリップ**である(実測: 退化フィールドで運用 λ なら **INV-C5**
(1着 marginal 乖離 1.6e-10)、**λ=1 の生 provenance でも INV-C1**(Σ 乖離 1.6e-9)が発火。
`_normalize_clip` が 1e-300 を eps に持ち上げ支配馬の q を 1.0 → 0.999999983 にずらすため)。
現実的な極端フィールド(gamma shape 0.25・18 頭)200 件では **0 件**。
契約は「**現実的フィールドで成功・退化フィールドで `ChaosInvariantError`**」であり、
`operational_lambda_envelope` は publish 時の数値ゲート(FR-029b)として別に機能する。
ガードの価値は「今崩れるから」ではなく、**列挙漏れ・分母誤り・将来のエンジン変更を捕まえる**点にある。

### C4: 生 / 補正は別の分布

`chaos_readout` は 2 つの `ChaosDistribution` を返し、**不変条件をそれぞれ独立に検査**する。
「単一の同時分布から導く」は **provenance 内**の話。生の質量と補正後の質量を 1 つの分布から
出したと書いてはならない。

### C5: 構造的ゼロは 0.0

`n <= event.infeasible_when_n_le` の事象は **`event_mass = 0.0` + `structural_zero[key] = reason`**
とする。`None` にしてはならない — 確定陰性が Brier / log score / reliability から消え、
estimand が「実行可能フィールドに限った risk」にすり替わる。UI 側で「該当馬なし」と描画する。

境界: `s_ge_20` → N≤7 / `s_ge_30` → N≤10 / `himo_are`・二桁人気系 → N≤9。

### C6: λ 非感応事象

`total_collapse` は λ1=1 により **1 着 marginal が q に固定**されるため λ に依存しない。
`EventDefinition.lambda_sensitive = False` を持ち、生と補正で同値であることをテストで固定する
(実測 |差| = 5.6e-17)。この事象に「校正済み」を主張してはならない。

### C7: 決定論

同一入力 → 同一出力(バイト一致)。`joint_probabilities` が `sorted(win_probs)` で ID 順を
固定しているため dict の挿入順に依存しない。

---

## 不変条件テスト

| テスト | 内容 |
|---|---|
| `test_triple_mass_is_one_operational_lambda` | envelope 内 λ × n=4..18 × 実 q 分布で \|Σ−1\| ≤ 1e-9 |
| `test_degenerate_field_raises` | **退化フィールド**(1 頭 q=1.0・他 1e-300)で `ChaosInvariantError`。**λ に依らず**発火する(運用 λ なら INV-C5・λ=1 なら INV-C1)。ランダムな「やや極端」な q では発火しない(gamma shape 0.25・18 頭 200 件で 0 件)ので、テストは退化ケースを明示的に構成する |
| `test_realistic_extreme_fields_do_not_raise` | gamma shape 0.25・18 頭のランダムフィールド 200 件で例外が上がらない(偽陽性ガードの回帰) |
| `test_no_global_renormalization` | 和で割る実装が無いことを AST で固定 + 壊した分布で例外 |
| `test_events_need_order` | **`(1,9,10)` と `(10,1,9)`**(同 S・異なる着順)で `himo_are`/`total_collapse` が異なる |
| `test_expected_s_identity` | `E[S] == Σ_i rank_i · P(i ∈ top3)` |
| `test_first_place_marginal_equals_q` | λ 任意で 1 着 marginal == 正規化 q |
| `test_total_collapse_lambda_invariant` | 生と補正で `total_collapse` が一致 |
| `test_topk_marginals_match_049` | 運用 λ で `discounted_topk` と一致 |
| `test_lambda_one_matches_harville` | λ=1 で `harville_topk` と一致 |
| `test_nested_events_monotone` | `P(s_ge_30) <= P(s_ge_20)` |
| `test_non_nested_not_forced` | `s_ge_20` と `himo_are` に不等式を課していない |
| `test_structural_zero_is_zero_not_none` | n=7 で `s_ge_20 == 0.0` + 理由、**n=8 では正の数値** |
| `test_events_match_normative_predicates` | FR-010a..010d の式と実装が一致。特に **`himo_are` が or(and でない)**: `(1,10,5)` と `(1,5,10)` は true、`(1,5,6)` は false、`(4,10,11)` は false |
| `test_ranks_with_scratch_gap_accepted` | max(rank) > n(取消で番号が飛ぶ)でも受理。重複・欠損は `ValueError` |
| `test_uniform_field_uniform_triples` | 一様 q で全順序三つ組が等確率 |
| `test_permutation_equivariance` | horse_id 置換で pmf 不変 |
| `test_rejects_invalid_ranks` | 重複・欠損・部分オッズで `ValueError`(番号の飛びは受理) |
| `test_support_bounds` | pmf のキーが `[6, 3n-3]` に収まる |
| `test_uniform_eight_expected_s` | 一様 8 頭で `E[S] == 13.5`(「少頭数は常に堅い」が構造的でない証拠) |

---

## outcome 回帰テスト(SC-008・これが 066 の失敗を捕まえる唯一のテスト)

上記はすべて**算術の整合**であり、**066 はこれらを全て満たしたまま本命強度計であり続けられた**。
したがって次を必須とする:

| テスト | 内容 |
|---|---|
| `test_band_tracks_outcome_on_frozen_fixture`(**`training/tests/integration/`**) | checksum 固定の凍結 fixture に対し、バンド別の実現 `S≥20` / `himo_are` / `total_collapse` が discovery 記録と整合し、単調性と識別力の下限を満たす。**fixture の生成手順**: `training chaos-bands diagnose --export-fixture` で 2024+ の適格レース(n=8,818)の `(popularity ベクトル, オッズ, 1-3着)` のみを抽出し parquet + SHA-256 を repo にコミット(サイズ ~1MB 目安・オッズ以外の個体情報を含めない) |
| `test_semantic_golden_case`(**`live/tests/integration/`**) | 1着=1番人気・2着=17番人気・3着=18番人気 → `S=36`・`himo_are=true`・`total_collapse=false`。**現在 DB の popularity を書き換えた後**に凍結行から算出して検証(DB 依存なので unit ではなく integration) |

**注**: 本モジュール自体は DB・ファイル・時刻に依存しない純関数だが、上記 2 テストは
**凍結 fixture / DB を要する**ため `probability/tests/unit` には置かない。

---

## リーク境界 (憲法 II)

- 本モジュールは `finish_order` / `race_results` / モデル p を**入力に取らない**
- 出力を feature registry / materialized columns / model recipe に登録しない
  (`features/tests/unit/test_feature084_leak_guard.py`)
- 禁止トークンは**本 feature の表示軸名に限定**(`chaos_band` / `p_s_ge_20` / `himo_are` /
  `total_collapse` / `expected_top3_popularity_sum`)。`popularity` 単体は既存 as-of 特徴で
  正当に使われるため禁止トークンにしない
