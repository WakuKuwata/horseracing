# Data Model: margin-aware 教師信号(099)

スキーマ変更ゼロ・migration なし。ここで定義するのは**学習時のみ存在する派生値**と、
その監査痕跡の形。

## margin(着差)

- **定義**: ステージ j(j∈{2,3})の的中馬(着順 j)と次の完走馬(着順 j+1)の確定走破
  時計差(秒)。`race_results.finish_time`(interval)の隣接差分
- **算出規約(run 1 バグの構造回避)**: LEAD は**全完走馬**(`result_status='finished'`・
  着順昇順。**時計 NULL の finished 行も window に含める** — 除外すると j+1 着の時計欠損時に
  次の別馬とペアリングされ「定義済みだが誤った margin」になり INV-MT8 と矛盾する =
  analyze 2 周目 M1)に対して計算し、着順 1..3 への制限は**その後**に適用する。差分が
  NULL(次馬なし/次馬の時計欠損)→ margin 未定義 → 中立 1.0
- **性質**: 結果由来。ラベル側のみ(finish_rank と同一契約)。負値は理論上ないが
  `max(gap, 0)` で防御

## ステージスケール

- **定義**: `s_j = clip(margin_j / M0, GMIN, 1.0)`、M0=0.2s・GMIN=0.25(spike 凍結値)。
  margin 未定義 → `s_j = 1.0`(中立)。レースが margin 表に無い → 全ステージ 1.0
- **対象**: ステージ 2,3 のみ(V1)。ステージ 1(勝者)は常に 1.0
- **値域**: [0.25, 1.0]。増幅なし(≤1.0)— 教師信号の総量は現行以下

## TrainingMatrix の aux 列(新規 2 本)

| 列名 | 型 | 意味 | 契約 |
|---|---|---|---|
| `margin_scale_s2` | float64 | そのレースのステージ 2 スケール(レース内定数) | ラベル側のみ |
| `margin_scale_s3` | float64 | 同ステージ 3 | ラベル側のみ |

- `MKT_ODDS` / `finish_rank` と同じ**ラベル側 aux**: `feature_cols` に含めない・
  `feature_hash` に影響しない・`feature_snapshots` に書かない
- **レース内定数**であることが group 先頭行取り出し(win_model)の前提 — fit 時に
  `groupby(race_id).nunique() == 1` 相当の assert で固定

## ModelRecipe の新フィールド

| フィールド | 型 | 既定 | recipe_hash |
|---|---|---|---|
| `margin_teacher` | `str \| None` | `None` | None は**省略**(既存 hash 不変)・"v1" は含む |

- 受理値は `None` / `"v1"` のみ。他は `__post_init__` で ValueError(fail-closed)
- `CalibSplitFactory._RECIPE_FIELD_DISPOSITION`: `"forward"`(booster の学習に効く)

## fit_info / artifact metadata の監査ブロック

```json
"margin_teacher": {
  "variant": "v1", "m0": 0.2, "gmin": 0.25,
  "s2": {"source_available": 60000, "scale_lt1": 31000, "fire_and_lt1": 30500, "fireable_mean": 0.69},
  "s3": {"source_available": 59000, "scale_lt1": 33000, "fire_and_lt1": 32400, "fireable_mean": 0.66},
  "neutral_missing_time": 120, "neutral_absent_race": 30
}
```

(数値は形の例。実際の booster fit 行に対して数える — 全レース平均は fit に使われなかった
行を混ぜて診断力を失う。`scale==1.0` は「大差で cap」と「時計欠損で中立」を分計する)

- OFF(None)のとき **key 自体を出さない**(既存モデルの metadata はバイト不変 — 060/085
  前例)
- これが「実際に変調されたか」の唯一の事後検証点(spike run 1 の教訓)

## 不変条件(contracts/teacher-signal.md の INV-MT 群に対応)

- INV-MT1: `stage_scales=None` の勾配・ヘシアンは現行 `pl_topk_objective` とビット一致
- INV-MT2: aux 列 2 本は feature_cols / feature_hash / feature_snapshots に現れない
- INV-MT3: aux 列はレース内定数・値域 [0.25,1]・有限・s1==1(fit 時 ValueError)
- INV-MT4: ステージ発火・中立化・break 規則(039/042)は変調の有無で不変
- INV-MT5: canonical payload を ModelRecipe に一本化し両 Factory が共有 — holdout 系・
  arm E 系とも既存 hash 完全一致(Factory 直 `meta()` hash のままでは arm E 系が全滅)
- INV-MT6: 予測経路は margin / スケールを読まない(serving 予測バイト不変)
- INV-MT7: 実データ形状でステージ 2・3 の**両方**のスケール平均が 1.0 を実質下回る
  (run 1 バグ形の回帰検出)
- INV-MT8: margin 未定義・不在レースは中立(1.0)であり学習から除外されない
- INV-MT9: fit_info の監査ブロックは ON のときのみ存在し、OFF の metadata はバイト不変
