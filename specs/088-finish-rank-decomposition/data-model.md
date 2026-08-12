# Data Model: 着順の頭数正規化+ラグ分解 bundle (088)

**DB スキーマ変更なし・migration なし**。本 feature のデータモデルは特徴列(build 出力の DataFrame 列)のみ。

## 入力(既存ロード済み・新規ソース列ゼロ)

| Frame | 列 | 用途 |
|---|---|---|
| `races` | `race_id`, `race_date` | as-of 境界(strictly-before 日付) |
| `race_results` | `race_id`, `horse_id`, `finish_order`, `result_status` | 着順・完走判定 |
| `race_horses` | `race_id`, `horse_id`, `entry_status` | 走の存在・出走頭数(STARTED 数) |

`source_fingerprint` はこれらを既に含む → 無改修(research D6)。

## 中間表現

### 過去完走走(finished run)+ finish_pct

- 系列母集団: `result_status == FINISHED` の走のみ(research D2。ラグ・rolling・expanding・trend は全てこの系列上)
- `n_started(race)` = そのレースの `entry_status=STARTED` 行数(race-level 確定値・全過去レースで計算)
- `finish_pct = (finish_order − 1) / (n_started − 1)`、`n_started == 1` → NaN(理論ケース)
- **意味**: 「自分より先に完走した出走馬の割合」(0=勝ち・大きいほど後方)。分母が出走頭数なので非完走馬がいるレースでは最大値は 1 に届かない(仕様・codex 論点A 採用)
- **範囲検証**: `finish_order < 1` または `finish_order > n_started` はデータ異常 → その走の finish_pct は NaN+カバレッジ監査で件数開示
- 同着は同 `finish_order` → 同値

## 出力列(10 列・group `finish_decomp`・全 float64・欠損 NaN)

as-of 規約: 全列とも対象レースの `race_date` より strictly-before(同日除外)。lag/rolling は完走系列上で merge_asof(backward, allow_exact_matches=False)/ shift。正規化集約(pct 系の rolling/expanding/trend)は窓内に NaN があれば NaN(伝播・スキップしない)。

| # | 列名 | 定義 | NaN 条件 |
|---|---|---|---|
| 1 | `prev_finish_pct` | 直近完走走の finish_pct | 完走 0 走 / その走が退化・範囲異常 |
| 2 | `prev2_finish` | 2 走前(完走系列)の生着順 | 完走 <2 走 |
| 3 | `prev3_finish` | 3 走前(完走系列)の生着順 | 完走 <3 走 |
| 4 | `prev2_finish_pct` | 2 走前の finish_pct | 完走 <2 走 / 退化・範囲異常 |
| 5 | `prev3_finish_pct` | 3 走前の finish_pct | 完走 <3 走 / 退化・範囲異常 |
| 6 | `avg_last3_finish_pct` | 直近 3 完走の finish_pct 平均(rolling 3, min_periods=3)。**#1/#4/#5 の算術平均と完全従属**(残す理由は spec FR-002a: 木は線形結合を分割で構成できない) | 完走 <3 走 / 窓内 NaN |
| 7 | `avg_last5_finish` | 直近 5 完走の生着順平均(rolling 5, min_periods=5) | 完走 <5 走 |
| 8 | `avg_last5_finish_pct` | 直近 5 完走の finish_pct 平均(rolling 5, min_periods=5) | 完走 <5 走 / 窓内 NaN |
| 9 | `best_finish_pct` | 全過去完走走の finish_pct の expanding min(一度勝てば以後 0 に飽和=「勝った実績がある」の連続版であることを注記) | 完走 0 走 / 有効 finish_pct 0 件 |
| 10 | `finish_trend5` | 直近 5 完走の finish_pct を走順序 x∈{1..5}(古→新)に OLS 単回帰した傾き(負=改善)。**5 走の理由**: 3 点 OLS は端点差分/2 と等価で採録済みラグの線形結合になる(codex 論点A 採用・spec FR-002) | 完走 <5 走 / 窓内 NaN |

注:
- `avg_last5_finish` の min_periods=5 は「5 走平均」の定義を守るため(3〜4 走で部分平均を出すと `avg_last3_finish` との差が窓幅でなく充足率を測ってしまう)
- **非対称の注記**: 既存 `avg_last3_finish` は min_periods=1(1〜2 完走でも部分平均)だが、新 `avg_last3_finish_pct` は min_periods=3。「対」は定義面の対応であってカバレッジは非対称 — SC-006 の比較監査で構造的な非欠損率差が出るのは仕様
- `prev_finish_pct` は既存列から導出不能(既存 `field_size` は**今走**の頭数であり、過去走の出走頭数は列に無い)
- 既存列(`prev_finish`/`avg_last3_finish`/`avg_finish` 等)は一切変更しない(純加算・FR-007)
- registry: `FEATURE_GROUPS` に 10 列 → `finish_decomp`、`ALL_COLUMNS` は自動派生、`FEATURE_VERSION = "features-020"`(019 焼却済み・research D4)

## 評価記録(bundle verdict)

既存機構の出力をそのまま使う(新テーブル・新スキーマなし):

- 診断(非ゲート): binary `feature-eval` の AdoptionReport(fold パターン観察のみ・判定機能なし=spec FR-013)
- **判定**: 本番 pl_topk 構成の `paired-eval --subgroups` レポート(winner NLL 差・CI・subgroup 内訳・top2/top3/ECE)
- verdict(三値・spec FR-013a の決定表)と実行条件(seed・窓・列集合)・カバレッジ監査(FR-018: 列別×年別非欠損率+範囲異常件数)は spec の測定結果欄に転記(FR-017)
