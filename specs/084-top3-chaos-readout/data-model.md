# Phase 1 Data Model: 上位3着ベースの荒れ度読み出し (rev2)

**Feature**: 084-top3-chaos-readout | **Revised**: 2026-07-26

**migration 0012 を追加する**(rev1 の「スキーマ変更ゼロ」は撤回 — 理由は spec.md 憲法チェック VI)。
追加のみ・既存テーブル/契約は不変。

---

## 1. DB: `chaos_snapshots` (migration 0012・append-only)

表示・検証の根拠となる**不変**の観測。1 行 1 レース 1 捕捉。

| 列 | 型 | 規約 |
|---|---|---|
| `chaos_snapshot_id` | uuid PK | |
| `race_id` | str(12) FK races | |
| `captured_at` | timestamptz | **捕捉処理の実時刻**。`race_horses.updated_at` の転記を禁止 |
| `source` | text | 取得アダプタ由来の実際の出所。呼び出し側が自称してはならない |
| `seconds_to_post` | int \| null | `post_time - captured_at` の**数値**。null は post_time 不明 |
| `capture_strength` | text | `confirmatory` / `weak` / `unknown`(下記) |
| `field` | JSONB | **canonical field = `entry_status='started'` のみ**。`[{horse_id, horse_number, popularity, odds}]` を `horse_id` 昇順。`rank_basis` = 凍結 `popularity` 値そのまま(再基準化しない) |
| `n` | int | `len(field)` |
| `content_digest` | text | `field` の正準化 JSON の SHA-256。**内容同一性の判定用**(キャッシュキー) |
| `status` | text | `active` / `void` |
| `void_reason` | text \| null | `late_scratch` 等 |
| `created_at` | timestamptz | |

**index**: `(race_id, captured_at DESC)`、`(race_id, status)`

### capture_strength の判定(事前登録・変更禁止)

| 値 | 条件 |
|---|---|
| `confirmatory` | 新規取得(キャッシュ不使用)・取得**前後**とも result-pending・`post_time` 既知で `captured_at < post_time` |
| `weak` | 新規取得だが `post_time` 不明、または前後どちらかの pending 確認のみ |
| `unknown` | 上記を満たさない(DB 既存値の読み取り等) |

**`confirmatory` のみが前向き確認コホートに入る。** 他は表示には使えるが確認には使わない。

### 規約

| ID | 内容 |
|---|---|
| SNAP-1 | **append-only**。既存行の UPDATE は `status`/`void_reason` のみ許可 |
| SNAP-2 | canonical field の `popularity` に**重複または欠損**があれば**書かない**(typed skip + 理由計上)。**1..n の完全な順列は要求しない** — 取消馬が番号を消費するため実測で取消レースの **26.1%** が max(popularity) > n になる |
| SNAP-3 | オッズが 1 頭でも欠ければ**書かない**(部分再正規化の禁止) |
| SNAP-4 | **1 レース 1 有効行**(`status='active'` は常に 1 行以下)。再捕捉は旧行を `void` にして新行を追記。void 行は差し替えの監査痕跡であって**オッズ時系列ではない**(憲法 V)。**within-race の意図的多点捕捉は禁止**(v2・憲法改定が前提) |
| SNAP-5 | 出走取消は**旧行を `void` にし新行を追記**(規約を 1 つに固定) |
| SNAP-6 | 表示時の既定は `status='active'` の最新 `captured_at` |
| SNAP-7 | 分析単位は **1 レース 1 行**(active 行)。horizon 別の層別は**レース間**で行う(within-race の多点比較は SNAP-4 によりスコープ外) |

---

## 2. DB: `chaos_readouts` (migration 0012・append-only)

**結果確定前に**書かれ、以後不変。「その時何を表示したか」の証跡。

| 列 | 型 | 規約 |
|---|---|---|
| `chaos_readout_id` | uuid PK | |
| `chaos_snapshot_id` | uuid FK | どの凍結行に基づくか |
| `artifact_version` | text | `chaosbands-v1` |
| `artifact_digest` | text | payload 全体の canonical digest |
| `band` | text | **`t3_calm` / `t3_mild` / `t3_mid` / `t3_rough` / `t3_wild`**(低 `p_s_ge_20` → 高。066 の `firm..open` とは**別語彙**)。境界の包含は `p <= edge` で**下側バンド**に入れる |
| `band_axis` | text | `p_s_ge_20` |
| `p_s_ge_20` / `p_himo_are` / `p_total_collapse` | numeric | **補正後**(総崩れは補正が効かない=生と同値) |
| `raw_p_s_ge_20` / `raw_p_himo_are` / `raw_p_total_collapse` | numeric | λ=1 の生質量 |
| `expected_s` | numeric | 副次 |
| `structural_zeros` | JSONB | `{event_key: reason}` |
| `computed_at` | timestamptz | **捕捉と同時に算出した時刻**。「実際にユーザーに表示された時刻」ではない(表示有無は記録しない) |

**index**: `(chaos_snapshot_id)`、`(computed_at DESC)`

**規約**: 結果確定後に書いてはならない(書き込み時に result-pending を再確認)。
UPDATE 禁止。表示のたびに追記するのではなく、**捕捉と同一トランザクションで 1 回**書く。

---

## 3. 事後ラベル `ChaosOutcome`(算出のみ・テーブルなし)

**報告時に導出**する。テーブルを作らない理由: 結果は `race_results` に既にあり、
**S を凍結行の順位から算出する限り**(現在の `race_horses.popularity` を使わない限り)
束縛は保たれるため、別テーブルは冗長。

| 項目 | 規約 |
|---|---|
| `s` | `chaos_snapshots.field` の `popularity` を用いて 1-3 着馬の合計。**現在の DB からは算出しない** |
| `worst_rank_top3` | 同上 |
| `void_reason` | `dead_heat` / `fewer_than_three_finishers` / `partial_ingest` |
| キー | `(chaos_readout_id)` に紐付ける。1 レースに複数 readout があるときの分析単位は SNAP-7 に従う |

除外は**理由別件数を必ず記録**する。

---

## 4. `ChaosBandsArtifact`(content-addressed JSON)

`artifacts/chaos_bands/{artifact_digest}.json` + コミット済み manifest に承認 digest を pin。

| フィールド | 内容 |
|---|---|
| `version` | `chaosbands-v1` |
| `label_definition` | `top3_popularity_composition_proxy_v1` |
| `lambda2` / `lambda3` | 市場 q で fit(0.8304 / 0.7111) |
| `lambda_fit_objective` | `conditional_nll_stage2` / `stage3` |
| `band_axis` | **`p_s_ge_20`** |
| `quintile_edges` | 4 個・狭義単調増加(実測 [0.01957, 0.06593, 0.11181, 0.17031])。包含規則 `p <= edge` → 下側 |
| `edges_basis` | `closing_history`(「live snapshot の分位点」ではない) |
| `s_threshold_basis` | `fit_window_p90`(閾値 20 の出所。JRA-VAN の G1 観察は根拠に採らない) |
| `fit_from` / `fit_to` / `as_of` | 2020-01-01 / 2023-12-31 |
| `fit_through` | fit 観測の終端 |
| `valid_from` | **`> fit_through` を必須**。確認観測はここ以降 |
| `n_races_fit` | 13788 |
| `race_set_hash` / `fit_input_hash` | レース集合と**入力値**両方のハッシュ |
| `preregistration` | 事象定義・タイブレーク・除外規約・**昇格規則**・最小陽性数・最終判定日 |
| `numeric_stability_report` | 発行時に代表/敵対フィールドで Σ=1 を検査した結果 |
| `operational_lambda_envelope` | 運用 λ の数値範囲。publish 時に envelope 外を拒否し、読み取り時の不変条件テストと一致させる(FR-029b) |
| `eligibility_predicate` | fit / discovery / 表示で**共通**の適格条件(popularity 重複・欠損なし / 全頭オッズ / N≥4)。FR-029a |
| `field_size_reference_quantiles` | 頭数別の `p_s_ge_20` 参照分位。`within_field_size_percentile` の算出元(FR-018a) |
| `code_sha` | |
| `artifact_digest` | 上記すべての canonical JSON の SHA-256(**自己参照を除く**) |
| `calibration_status` | `provisional` / `confirmed`(昇格は**新 version 発行**でのみ) |

### 時間ゲート(全文書で統一・rev1 の逆転を修正)

```text
表示に使える  ⇔  target_date > fit_through  かつ  target_date >= valid_from
```

`fit_through` までは fit 観測、`valid_from` 以降が確認観測、その間は discovery のみ。
`target_date > fit_through` を**拒否**条件にしてはならない(2024 年以降の全レースが消える)。

### EventDefinition

| フィールド | 例 |
|---|---|
| `key` | `s_ge_20` / `himo_are` / `total_collapse` / `s_ge_30` |
| `label_ja` | 「人気順合計が20以上」「**1〜3番人気が勝ち、2着か3着に二桁人気**」「二桁人気が勝つ」。**述語と論理的に等価**でなければならない(「2・3着」の省略は and と誤読される→禁止) |
| `predicate` | **実行可能な型付き述語**(監査用文字列ではない)。順序三つ組 `(a,b,c)` の順位を受け取る。v1 の式は FR-010a..010d を正とする: `s_ge_20`=`ra+rb+rc>=20` / `s_ge_30`=`>=30` / **`himo_are`=`ra<=3 and (rb>=10 or rc>=10)`**(or・and 版は 0.44% で別事象)/ `total_collapse`=`ra>=10` |
| `infeasible_when_n_le` | 7 / 9 / 9 / 10 |
| `nested_under` | `s_ge_30` → `s_ge_20`。他は null(**入れ子でない事象に不等式を課さない**) |
| `lambda_sensitive` | `total_collapse` は **false**(λ1=1 で 1 着 marginal が q に固定・実測 5.6e-17) |
| `promotion_role` | `controls`(s_ge_20)/ `secondary`(himo_are)/ `not_eligible`(total_collapse)/ `diagnostic_only`(s_ge_30) |
| `min_positives_for_decision` | 100(s_ge_20 / himo_are)、S≥30 は昇格対象外 |

---

## 5. `ChaosDistribution`(メモリ上・純関数の戻り値)

**provenance ごとに 1 個**。生(λ=1)と補正(λ2/λ3)で**2 個**返す。

| フィールド | 内容 |
|---|---|
| `provenance` | `raw` / `stage_discount_adjusted` |
| `n` / `support` | 頭数 / `(6, 3n-3)` |
| `pmf` | S の確率質量 |
| `expected_s` | |
| `event_mass` | dict[str, float] — **常に数値**(構造的ゼロも `0.0`) |
| `structural_zero` | dict[str, str] — 事象キー → 理由 |
| `triple_mass_sum` | Σ(順序三つ組)。**検査用に保持し、割るためではない** |

### 不変条件 (INV)

| ID | 内容 |
|---|---|
| INV-C1 | `abs(triple_mass_sum - 1.0) <= 1e-9`。超えたら `ChaosInvariantError`(fail-closed) |
| INV-C2 | `sum(pmf.values()) == 1`(同許容誤差) |
| INV-C3 | `support[0] <= expected_s <= support[1]` |
| INV-C4 | `expected_s == Σ_i rank_i · P(i ∈ top3)`(1e-12) |
| INV-C5 | 1 着 marginal == 正規化後の `q`(1e-12)。**λ に依存しない** |
| INV-C6 | top2/top3 marginal == `discounted_topk`(**運用 λ で**・1e-12) |
| INV-C7 | λ=1 で `harville_topk` と整合 |
| INV-C8 | 入れ子事象のみ `P(s_ge_30) <= P(s_ge_20)`(構成上自動) |
| INV-C9 | `total_collapse` の値が **λ に依存しない**(生と補正で一致・実測 5.6e-17) |
| INV-C10 | 構造的ゼロ事象は `event_mass == 0.0` かつ `structural_zero` に理由 |
| INV-C11 | 一様 q で順序三つ組が一様。horse_id の置換に対して同変 |
| INV-C12 | **`operational_lambda_envelope` 外の λ/q** では `ChaosInvariantError` が上がる(黙って壊れない)。envelope は artifact が定義する(FR-029b) |

**大域正規化は行わない。** `triple_mass_sum` は検査するために保持する。

---

## 6. API 応答モデル(純追加)

`PredictionsResponse` に `race_chaos: RaceChaos | None = None` を純追加。
066 の `race_dispersion` / `race_divergence` は**一切変更しない**。

### RaceChaos(available / unavailable のタグ付き 2 形状)

| フィールド | 型 | 備考 |
|---|---|---|
| `status` | `"available"` \| `"unavailable"` | タグ |
| `unavailable_reason` | str \| null | `status="unavailable"` のときのみ非 null |
| `band` | str | available では**必須・非 null**。値域は `t3_calm`/`t3_mild`/`t3_mid`/`t3_rough`/`t3_wild` |
| `band_axis` | str | 常に `p_s_ge_20` |
| `field_size` | int | 必須・非 null |
| `feasible_support` | [int, int] | 必須・非 null |
| `feasible_support_ja` | str | 「人気合計は 6〜21 の範囲」 |
| `events` | list[ChaosEvent] | |
| `expected_top3_popularity_sum` | float | 副次 |
| `within_field_size_percentile` | float \| null | 「同頭数の中では低め/高め」。artifact の `field_size_reference_quantiles` から算出(FR-018a) |
| `calibration_status` | `"provisional"` \| `"confirmed"` | |
| `calibration_basis` | str | `closing_history_2020_2023` |
| `is_market_derived` / `is_pseudo` | bool | 常に true |
| `snapshot` | {`captured_at`, `source`, `seconds_to_post`, `capture_strength`, `content_digest`, `snapshot_id`} | `content_digest`=field 内容(数値はこれだけで決まる)/ `snapshot_id`=捕捉イベント識別子。**両方返す**(片方だけだと監査で捕捉時刻が追えない・キャッシュキーが定まらない) |
| `artifact_version` / `artifact_digest` | str | |

### ChaosEvent

| フィールド | 型 |
|---|---|
| `key` / `label_ja` | str |
| `adjusted_mass` | float(**必須・非 null**。名前は `stage_discount_adjusted_market_mass` の短縮) |
| `raw_mass` | float(方法詳細用) |
| `is_structural_zero` | bool |
| `structural_zero_reason` | str \| null |
| `lambda_sensitive` | bool(false = 補正が効かない生の質量である旨を UI が表示) |

**規約**: 「全 number は nullable」ではない。**available 形状では数値フィールドは必須・非 null**。
pydantic は `extra="forbid"`、応答生成は明示 keyword マップ(`Model(**dict)` を禁止 — 075 の
splat-null 事故の再発防止)。

### unavailable_reason の値域(事前登録・変更禁止)

`no_snapshot` / `partial_market_odds` / `invalid_popularity_ranks` / `field_too_small` /
`artifact_unavailable` / `out_of_validity_window` / `invariant_violation`

---

## 7. 状態遷移

```text
[未捕捉] --live capture(065 規律)--> [chaos_snapshots 行 + chaos_readouts 行]
                                              |
                          artifact 不在 / digest 不一致 / target_date <= fit_through
                          / target_date < valid_from
                                              |--> [unavailable]
                                              v
                                    [provisional 表示]
                                              |
                    US5 の昇格規則(p_s_ge_20 が支配・最小陽性 100・最終判定日)
                                              v
                                     [confirmed 表示]   or   [NO_DECISION → 主枠から撤去]
```

昇格は `calibration_status` の書き換えではなく**新 artifact version の発行**で行う。
