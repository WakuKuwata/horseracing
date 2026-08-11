# Contract: 体重 regime と mask 変換

**Feature**: 091 | **Date**: 2026-08-10

`apply_weight_mask` の意味論と、どの経路がどの regime で動くかの契約。

## 1. 純関数の契約

```
apply_weight_mask(frame, *, spec) -> frame
```

- **純関数**。DB・時刻・乱数状態・グローバル状態に触れない
- `spec is None` のとき、**入力とバイト同一のフレームを返す**(実装上はコピーを返してよいが、値・dtype・列順・index がすべて一致すること)。これが既存経路のパリティを構造的に保証する(INV-W5)
- `spec` があるとき、選ばれたレースの行についてのみ `weight` / `weight_diff` / `carried_weight_ratio` を NaN にする。他の列・他の行は一切触らない
- 入力に対象列が無い場合は fail-closed(黙って何もしないことを禁じる)。列の集合が将来変わったときに静かに無効化されるのを防ぐ

## 2. mask 対象列(凍結)

| 列 | mask する | 理由 |
|---|---|---|
| `weight` | ✅ | 当日体重 |
| `weight_diff` | ✅ | 当日増減 |
| `carried_weight_ratio` | ✅ | 斤量 ÷ 当日体重 |
| `carried_weight` | ❌ | 斤量そのもの。発走前既知 |
| `carried_weight_rel` | ❌ | 斤量 − レース平均斤量。体重非依存 |
| `carried_weight_change` | ❌ | 斤量の前走差。体重非依存 |
| `prev_weight` | ❌ | **絶対に mask しない**。発走前既知であり、mask したら feature の目的が消える |

この表は実装内の定数として持ち、テストで表そのものを検証する(列名のタイポで静かに mask 漏れが起きるのを防ぐ)。

## 3. 選択規則(決定論)

レース `r` を mask するか否か:

```
mask(r) ⟺ unit_interval(stable_hash((r.race_id, spec.seed))) < spec.rate
```

- 行順・実行順・並列度・pandas のグループ順に依存しない
- `rate = 0.0` → どのレースも mask しない / `rate = 1.0` → すべて mask する
- 同一 `(race_id, seed, rate)` は常に同一結果(INV-W6)
- **レース内で mask 状態が混在してはならない**。1 レースの全馬が同時に mask される、されないのどちらかである

## 4. regime 定義

| regime | mask spec | 意味 |
|---|---|---|
| `serving` | `rate=1.0` | 体重が公表されていない条件。**実際に予測が行われる条件** |
| `full_info` | `None` | 体重が公表済みの条件。**評価上の定義**であり、本番 backfill(混在レースだけ §5a が効く)とは 0.3〜0.4% 行ぶん一致しない |
| `mixed(m)` | `rate=m` | 学習時の混合。本 feature の事前登録値は `m = 0.5` |

## 5. 経路別の適用契約

| 経路 | fit 時 | predict 時 | 備考 |
|---|---|---|---|
| 候補モデルの学習 | `mixed(0.5)`(model-fit 行 + 校正 holdout に同一 spec・同一 seed) | — | seed は gate-config に事前登録 |
| 現行モデル(比較対象) | 変更なし | — | 現行 recipe をそのまま再 fit する |
| PRIMARY 評価 | 上記のとおり | **両アームに `serving`** | 測りたい差 |
| GUARD 評価 | 上記のとおり | **両アームに `full_info`** | 非劣化ガード |
| 本番 serving | — | **レース単位の可用性正規化のみ**(下記 §5a) | 人工的な確率 mask は本番経路に入れてはならない。ただし可用性の二値化は行う |
| backfill | — | **ライブと同一条件で可用性正規化を適用**(§5a) | 確率 mask は使わない。混在は backfill でも起きる(確定済レースにも 0.3〜0.4% の体重欠損が残る)ので、ライブだけに適用すると §5a の主張が成立しない(FR-034b) |

**本番 serving に確率的 mask を適用してはならない**のは重要な契約である。本番では体重が実際に無い(あるいは実際にある)ので、`rate` に従って人工的に消す理由がない。確率 mask は学習と評価にのみ存在する道具である。

## 5a. レース単位の可用性正規化(本番 serving:ライブ・backfill 共通)

本番 serving は確率 mask を使わない代わりに、**可用性をレース単位に正規化する**:

```
そのレースの started 馬のうち 1 頭でも当日体重が未公表 ⟹ そのレースの全馬について
weight / weight_diff / carried_weight_ratio を欠損として扱う
```

**なぜ必要か**: 学習は race-atomic mask(全馬 masked か全馬 full-info の二値)である。本番でレース内に混在が生じると、モデルにとって out-of-distribution な入力になる。目的関数がレース内 softmax なので 1 頭の入力変化が全馬の正規化確率を動かし、影響はその 1 頭に留まらない。可用性を二値に正規化すれば、**本番の入力分布が学習分布と厳密に一致**し、混在は構造的に発生しなくなる(research D12)。

**適用条件**: 予測に使うモデルの入力列に `prev_weight` が含まれる場合のみ発動する(FR-034a)。前走体重を持たないモデル(現行 active を含む)では**完全な no-op** — 代替値を持たないモデルから当日体重を取り上げるのは補償のない劣化であり、SC-004 のバイト不変にも反する。

**適用範囲**: ライブと backfill の**両方に同一条件**(FR-034b)。片方だけだと「混在は構造的に起きない」という主張自体が崩れる。

**性質**:

- これは「測っていない挙動を避ける」ための設計であって、精度最適化ではない。混在レースで一部の馬に当日体重があってもそれを捨てる
- 判定は started 馬に対して行う(取消馬の未計量は判定に含めない)
- 正規化が発動した回数と (計測済み頭数 / 出走頭数) の分布は観測できるようにする(FR-035)。**正しさの担保ではなく運用上の可視性のため**
- 全馬が計測済みのレースは従来どおり full-info として扱われ、当日体重が使われる

## 6. 監査

| 記録先 | 内容 |
|---|---|
| モデル artifact metadata | `weight_mask`: `{rate, seed, unit, columns}`。mask 無しなら `null` |
| 評価レポート | regime 別のスコア、両アームの mask 適用レース数 |
| `logic_version`(serving) | 体重 regime marker(その予測が体重ありで計算されたか) |

`logic_version` の marker は監査のためではなく**フィルタ**のためである。憲法 V の再現性は `feature_snapshots` が per-horse のモデル入力ベクトルを保存していることで既に満たされている。marker があると shadow log や backtest で「体重公表前の予測だけ」を選べる。

## 7. 禁止事項

- **行単位の mask**。欠損はレース(時間帯 regime)単位で発生する。`unit` を列挙型にして構造的に禁じる
- **結果に依存する mask**。mask の選択に `finish_order`・オッズ・勝敗を一切使わない(憲法 II)
- **`prev_weight` の mask**
- **本番 serving 経路での確率 mask**(`rate` に従う人工欠損)。§5a の可用性正規化は確率 mask ではないので禁止対象ではない
- **full-info backfill 予測を live 品質の代理として使うこと**。065 の closing-oracle バイアスと同型。この禁止は契約であり、レポート生成側で regime を混ぜないことで担保する
