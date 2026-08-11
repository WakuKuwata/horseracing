# Contract: 採用ゲート

**Feature**: 091 | **Date**: 2026-08-10

OOS 前に凍結する。凍結後の変更は `assert_confirmatory` の hash 検証で検知され、評価が失敗する。

## 1. verdict の正本(単一式)

```
ADOPT ⟺ serving_regime.gate.adopted
    AND full_info_guard
    AND serving_regime.subgroups.subgroup_guard
```

**subgroup は serving regime のものを読む。** 既存 `PairedReport.subgroups` はトップレベルのフィールドで、regime 別スコアは純追加として足される。修飾しないと既定(full-info)の subgroup を読み、PRIMARY と regime が食い違ったまま式が「成立」してしまう。レポートは上式を評価した `verdict.adopt` を**単一の真偽値フィールド**として出力する(FR-026)。

- **個別数値を事後に読み替えて採否を主張してはならない。**
- 評価が実行不能なら `NO_DECISION`。**ボーダーライン上の数値を理由に NO_DECISION にしてはならない**(070 の前例: CI がゼロを跨いだら REJECT であって NO_DECISION ではない)
- harness 組込みの `report.decision` は 073 の参考値。**正本は上式**(088 で確立した規律)

## 2. PRIMARY: serving regime

| 項目 | 値 |
|---|---|
| 評価条件 | 両アームに `weight_mask(rate=1.0)` を predict 直前に適用 |
| 指標 | レース単位 winner NLL の paired 差(candidate − active、負が改善) |
| CI | 開催日クラスタ bootstrap 95%(`race_day_cluster_bootstrap_ci_v1`) |
| **最小効果 δ** | **0.002**(点推定 < −δ が必要) |
| 統計ガード | CI 上限 < 0 |
| 既存 068 ゲート | winner NLL 勝ち + 直近 3/5 年ガード + top2/top3 non-inferiority + ECE 非劣化 |

`serving_regime.gate.adopted` = 上記すべての AND。

**δ の根拠**: 固定モデルの反実仮想 replay(既存列への流し込み)が −0.0123 を示している。機構が意図どおり働いていれば同程度が出るはずで、−0.002 に届かない場合は mask 学習が効いていない疑いが強く、列追加と再学習のコストに見合わない。既存採用例(059/061 の −0.001 級)より高い水準を要求するのは、本 feature が「新情報の追加」ではなく**既知の大きな劣化の是正**であり期待値が一桁違うためである。

## 3. GUARD: full-info regime

| 項目 | 値 |
|---|---|
| 評価条件 | 両アームに mask を適用しない |
| 指標 | 同上 |
| 非劣化幅 | **+0.003**(paired 差がこれ以下なら PASS) |

`full_info_guard = (full_info_regime.diff <= 0.003)`

**注**: full-info regime は `spec=None`(無変換)で測る**評価上の定義**であり、本番 backfill の挙動と厳密には一致しない — 本番 backfill は FR-034b により混在レースだけ可用性正規化が効く。差が出るのは体重欠損由来の少数レース(確定済の 0.3〜0.4%)に限られる。

**非劣化幅の根拠**: 本 feature は full-info の精度を意図的に一部犠牲にして serving の精度を取る trade-off である。replay で測った当日体重の価値の総量が 0.0187 なので、その 1/6 程度までの劣化は「serving で 0.012 取るための対価」として許容する。これを超える劣化は、mask 率が高すぎて当日体重の経路が壊れた合図と解釈する。

## 4. GUARD: subgroup

069 の三値 intersection-union をそのまま使う。critical subgroup は評価前に固定する。

| 項目 | 値 |
|---|---|
| critical_subgroups | `2026_only`, `nk`, `2026_nk` |
| non_inferior_margin_winner_nll | 0.005 |
| non_inferior_margin_horse_logloss | 0.001 |
| decision | `three_way` |

**注**: 069 の coverage 帯 subgroup は F02 の市場観測数の定義に依存しており本 feature と無関係なので critical には入れない(088 の analyze で同型の誤りを検出した前例)。

## 5. 必須診断(verdict には使わない)

| 診断 | 目的 |
|---|---|
| 校正前(race-softmax 直後)winner NLL | 校正器由来の反転(raw 改善・calibrated 悪化)を識別する。codex 指摘 |
| 診断アーム m=0.0 | mask が本当に必要かの対照。`prev_weight` が当日体重に食われるかを確認 |
| 診断アーム m=1.0 | 当日体重を捨てる設計の先行測定。live と backfill が一致する設計の値を知る |
| カバレッジ監査 | 年別 / レース内カバレッジ / 鮮度帯 / 真デビュー vs 履歴断絶 / ID 名前空間 |
| bootstrap block 幅感度 | 2/3/4 日・週の連続開催日依存(073 の診断枠) |

**診断アームの結果で採用値(m=0.5)を差し替えない。** 差し替えたい場合は新たな事前登録が必要になる(憲法 III・068 C2 の selection leak 回避)。

### 受入・診断の隔離(機械的担保)

すべての評価 artifact は 2 つのフィールドを持つ:

| `artifact_kind` | `eligible_for_verdict` | 用途 |
|---|---|---|
| `full_walk_forward` | `true` | 本評価。**verdict はこれだけから読む** |
| `acceptance` | `false` | outcome-blind 受入(配線確認のみ・効果を見ない) |
| `diagnostic` | `false` | 診断アーム(m=0 / m=1)・block 幅感度 |

verdict loader は `artifact_kind == "full_walk_forward"` のみを受理し、`eligible_for_verdict=false` を読んだら fail-closed とする。**受入で効果を見てはならない** — 受入に使う直近 fold は最終評価窓の内側にあるため、そこでの勝敗で継続判断すると選択リークになる(codex 指摘)。受入項目は mask 件数 / 両アーム一致 / カバレッジ / 指標が有限 / provenance 一致に限る。

## 6. 事前登録する数値

すべて [gate-config.json](../gate-config.json) に**実キー**で書く。`_` 始まりのキーは canonical hash から除外されるため、凍結したい値を `_comment` に書いてはならない(069 が警告し 088 で再確認された罠)。

| キー | 値 |
|---|---|
| `evaluation_contract_version` | `"v2"`(`assert_confirmatory` が MUST として要求。070 の gate-config はこのキーを持たないのでコピーすると起動時に落ちる) |
| `weight_mask.rate` | 0.5 |
| `weight_mask.seed` | 20260810 |
| `weight_mask.unit` | `"race"` |
| `weight_mask.columns` | `weight`, `weight_diff`, `carried_weight_ratio` |
| `min_effect_delta`(トップレベル) | 0.002 |
| `full_info_guard.noninferior_width` | 0.003 |
| `bootstrap.seed` / `b` / `alpha` | 20260810 / 2000 / 0.05 |
| `eval_window.from` / `to` | 2021-01-01 / 実行時に確定した終端 |

`bootstrap.seed` と `weight_mask.seed` は**実キーで pin する**。CLI での上書きを無害化するためで、十数時間走ったあとに「seed が違った」と気づく事故を防ぐ(088 の前例)。

## 7. verdict の JSON パス

| 判定 | パス |
|---|---|
| PRIMARY | `serving_regime.gate.adopted` |
| GUARD (subgroup) | `serving_regime.subgroups.subgroup_guard` |
| 合成結果 | `verdict.adopt`(単一真偽値) |
| GUARD (full-info) | `full_info_guard` |

## 8. verdict 別の後始末

| verdict | 対応 |
|---|---|
| **ADOPT** | 運用者の明示承認を経て昇格。measurement を spec に転記(FR-030・T059) |
| **REJECT** | FEATURE_VERSION bump・build 結線・serving の可用性正規化を revert。`weight_history_features.py` と `weight_mask.py` は**単体テストごと非結線で保全**(062/070 の前例。純関数を残すコストは小さく、次の事前登録の土台になる)。負の結果を memory に記録 |
| **NO_DECISION** | 実行不能の原因を記録し、解消してから再実行。数値を理由にした NO_DECISION は禁止 |

いずれの verdict でも、**registry の availability_timing 是正(US3)は残す**。値不変の独立した契約修正であり、測定結果と論理的に無関係である。
