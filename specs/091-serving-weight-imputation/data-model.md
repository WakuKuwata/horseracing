# Data Model: 馬体重欠損時の serving 入力是正

**Feature**: 091 | **Date**: 2026-08-10

DB スキーマの変更は**ない**。本 feature が定義するのは (1) 特徴量列 1 本、(2) 特徴量行列に対する regime 変換、(3) 評価レポートの構造、の 3 つである。

---

## 1. 新規特徴列(1 本)

| 列名 | 型 | source | availability_timing | missing_policy | group |
|---|---|---|---|---|---|
| `prev_weight` | float64 | `race_horses` | `pre_entry` | `null` | `weight_history` |

**意味**: 対象レースより厳密に前に、その馬が**出走(started)し、かつ体重が記録された**直近の走における馬体重(kg)。

**availability_timing が `pre_entry` である根拠**: 前走の体重は過去の確定情報であり、出馬表が出る前から既知である。当日体重(`post_weight`)とは別のタイミング階層に属する。これが本 feature の存在理由そのものである。

**解決規約**:

```
source = race_horses rows where
           entry_status == started
       AND weight IS NOT NULL
       AND 200 <= weight <= 800
match  = merge_asof(targets, source,
                    on="race_date", by="horse_id",
                    direction="backward", allow_exact_matches=False)
```

`allow_exact_matches=False` が同日除外を担う。既存 [`_prev_started`](../../features/src/horseracing_features/lowcost_features.py) と一字一句同じ規約である。

**追加しなかった列と理由**(research D1 の実測):

| 検討した列 | 既存の等価列 | 一致率 | 判断 |
|---|---|---|---|
| `weight_age_days` | `days_since_last` | 100.0000%(262,733 行で差 0) | 追加しない |
| `has_prev_weight` | `has_past_race` / `is_debut` | 100.000000% | 追加しない |
| `starts_since_weight` | (ほぼ恒等的に 1) | — | 追加しない |

---

## 2. 体重 regime 変換(`apply_weight_mask`)

特徴量行列に対する**純関数**。DB に触れず、行の部分集合を受けて同じ形の新しいフレームを返す。

### 変換対象列

| 列 | 変換後 | 理由 |
|---|---|---|
| `weight` | NaN | 当日体重。serving 時に未公表 |
| `weight_diff` | NaN | 当日増減。当日体重なしには存在しない |
| `carried_weight_ratio` | NaN | 斤量 ÷ 当日体重。当日体重に依存(US3 で宣言も是正する) |

**変換しない列**: `carried_weight`(斤量そのもの)、`carried_weight_rel`(斤量 − レース平均斤量)、`carried_weight_change`(斤量の前走差)、`prev_weight`。いずれも当日体重に依存しない。

### MaskSpec

| フィールド | 型 | 意味 |
|---|---|---|
| `rate` | float [0, 1] | mask するレースの割合 |
| `seed` | int | 選択の決定論 seed |
| `unit` | `"race"` 固定 | 選択単位。行単位は禁止(将来の誤設定を防ぐため列挙で固定) |

**選択規則**: レースを mask するか否かは `stable_hash((race_id, seed))` を [0, 1) に写した値が `rate` 未満か、で決まる。行順・実行順・並列度に依存しない。同一 `(race_id, seed, rate)` なら常に同じ結果になる。

`rate = 1.0` は全レース mask、`rate = 0.0` は無変換。`spec = None` は**関数を呼ばないのと完全に等価**(現行とバイト同一)。

### 適用箇所

| 箇所 | spec | 目的 |
|---|---|---|
| 学習の fit 直前(model-fit 行) | `rate=0.5` | 両 regime を学習させる |
| 学習の校正 holdout | 同一 spec・同一 seed | 校正器の定義域を runtime に合わせる |
| 評価の predict 直前(serving regime) | `rate=1.0` | 実際に予測が行われる条件を再現 |
| 評価の predict 直前(full-info regime) | `None` | 非劣化ガード |
| 本番 serving(ライブ・backfill とも) | **確率 mask は使わない。代わりにレース単位の可用性正規化**(§2a) | 出走馬に一頭でも未計量がいればそのレースは全馬を体重なし扱いにする。前走体重を持つモデルにのみ適用 |

---

## 2a. レース単位の可用性正規化(本番 serving)

学習が race-atomic mask(全馬 masked か全馬 full-info の二値)である以上、本番の入力も同じ二値でなければモデルは out-of-distribution な入力を受ける。そこで予測経路で可用性を二値化する。

```
適用条件: 予測に使うモデルの入力列に prev_weight が含まれる          (FR-034a)
発動条件: そのレースの started 馬に 1 頭でも当日体重が未公表の馬がいる  (FR-034)
効果:     そのレースの全馬について weight / weight_diff /
          carried_weight_ratio を欠損として扱う
適用範囲: ライブ・backfill の両方に同一条件                          (FR-034b)
```

- 判定は **started 馬のみ**。取消馬の未計量は判定に含めない
- 前走体重を持たないモデル(現行 active を含む)では**何も起きない**。代替値を持たないモデルから当日体重を取り上げるのは補償のない劣化であり、SC-004 にも反する
- 確率 mask(`MaskSpec`)とは別物である。こちらは `rate` を持たず、可用性から決定論的に決まる

## 3. 評価レポートの構造

既存 `PairedReport` に **regime 別のスコア**とガードを追加する(純追加。既存フィールドは不変)。

| フィールド | 意味 |
|---|---|
| `serving_regime` | mask 率 1.0 で評価した candidate / active のスコアと paired 差・CI |
| `full_info_regime` | mask 無しで評価した同上 |
| `uncalibrated` | 校正前(race-softmax 直後)winner NLL(**診断**・codex 指摘 #2) |
| `full_info_guard` | full-info の paired 差が非劣化幅以内か(真偽) |
| `weight_regime_audit` | 両アームの mask 適用レース数・`prev_weight` カバレッジ |
| `artifact_kind` | `"full_walk_forward"`(本評価)/ `"acceptance"`(受入)/ `"diagnostic"`(診断アーム) |
| `eligible_for_verdict` | 真偽値。`full_walk_forward` のみ `true` |
| `verdict.adopt` | **正本を評価した結果の単一真偽値**(FR-026) |

**verdict の正本**(単一式):

```
ADOPT ⟺ serving_regime.gate.adopted
    AND full_info_guard
    AND serving_regime.subgroups.subgroup_guard
```

**subgroup は serving regime のものを読む。** 既存 `PairedReport.subgroups` はトップレベルのフィールドであり、regime 別スコアは純追加なので、修飾しないと**既定(full-info)の subgroup を読んでしまい PRIMARY と regime が食い違う**。式が一意に評価できるよう、パスを regime で修飾する。

レポートは上式を評価した `verdict.adopt` を**単一フィールドとして出力**する。読み手が 3 つのパスを自分で AND する余地を残さない。

`serving_regime.gate.adopted` は既存 068 のゲート(winner NLL 勝ち + CI 上限 < 0 + 直近ガード + top2/top3 non-inferiority + ECE 非劣化)に、本 feature で追加する**最小効果 δ**(点推定 < −δ)を AND したもの。

---

## 4. 不変条件

| ID | 不変条件 | 検証方法 |
|---|---|---|
| **INV-W1** | `prev_weight` は対象レースより厳密に前の走のみを参照し、同日の走を参照しない | 同日に 2 レース登録した合成 fixture で後発レースの `prev_weight` が同日分を拾わないことを確認 |
| **INV-W2** | `prev_weight` の供給元は `started` かつ体重非 NULL かつ範囲内の行のみ | 取消行・NULL 行・範囲外行を混ぜた fixture で、それらが選ばれないことを確認 |
| **INV-W3** | 対象レースの結果・当日オッズ・同日他レースを変更しても `prev_weight` は変化しない | leak-guard テスト(既存 023/026 と同型) |
| **INV-W4** | 過去に出走がある行で `prev_weight` が欠損する割合が事前登録した境界(過去出走行の 0.01%)以下であり、かつ**全例外が「過去に計量済み出走が一度も無い」で説明できる** | 実 DB integration。**境界超過で fail-closed** = D1 の列縮小の前提が崩れた合図なので、鮮度・有無の列追加を再検討する。実測は 862,274 行中 1 行(0.000116%)で、原因は唯一の過去出走が計不だった馬 1 頭 |
| **INV-W5** | `apply_weight_mask(spec=None)` は入力フレームとバイト同一のものを返す | `assert_frame_equal(check_exact=True, check_dtype=True)` |
| **INV-W6** | mask は決定論的かつレース単位。同一レースの全馬が同時に mask される / されない | 単体テスト。同一 spec で 2 回実行して同一結果、レース内で mask 状態が混在しないことを確認 |
| **INV-W7** | `features-020` の共有列(既存 137 列)は `features-018` とバイト一致する | 実 DB で一度きり実測(058/061/069 と同型) |
| **INV-W8** | `source_fingerprint` が `features-018` から変化しない | 実 DB で実測。新ソース列を読まないことの機械的確認 |
| **INV-W9** | active `lgbm-064-f02acc` の予測が `features-020` の下でバイト不変 | 実 DB E2E(compat 経路)。1 頭 1 ビットも変わらないこと |
| **INV-W10** | registry の timing 是正で特徴量の値・列名・列順・`feature_hash` が変化しない | 是正前後で行列を比較 |
| **INV-W11** | 前走体重を持つモデルに渡る予測入力は、レース内で体重の可用性が**必ず全馬一致**している(混在しない)。判定は started 馬のみ | 混在レースの fixture で全馬が欠損側に倒れることを確認。ライブ・backfill の両経路で検証 |
| **INV-W12** | 前走体重を持たないモデルでは可用性正規化が発動しない | 混在レースで現行モデルの入力が変化しないことを確認(SC-004 の全入力条件版) |

---

## 5. 母集団と除外

評価母集団は既存の 068 population contract をそのまま使う([`dataset.py`](../../eval/src/horseracing_eval/dataset.py) の `population_masks`):

- 勝者がちょうど 1 頭のレースのみ(同着・勝者不在は除外し件数を surface)
- started 数より result 行が少ない部分取込レースは除外(codex 指摘)
- fold は既存の expanding walk-forward

**新たな除外は設けない。** 特にデビュー馬・前走体重なしの馬を除外しない(codex 指摘 #6)。回収できない層も実運用効果の一部であり、除外すると効果を過大評価する。

---

## 6. カバレッジ監査の軸(FR-028)

| 軸 | 区分 |
|---|---|
| 年 | 2021 / 2022 / 2023 / 2024 / 2025 / 2026 |
| レース内 `prev_weight` カバレッジ | 全馬あり / 一部 / 0 頭 |
| 鮮度(`days_since_last`) | ≤45 / 46-120 / 121-365 / >365 日 |
| 出走歴なしの内訳 | 真のデビュー / 非デビューだが供給元なし(`nk:` 個体分裂由来を含む) |
| ID 名前空間 | canonical / `nk:` |

最後の 2 軸は codex 指摘。`nk:` による履歴断絶をデビュー馬と混同して報告しないための分離である。
