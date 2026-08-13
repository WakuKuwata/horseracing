# 091 evidence

再現スクリプトと、実際に走らせた測定の JSON レポート。

## artifact_kind — verdict に使えるのはどれか

| ファイル | kind | verdict 適格 |
|---|---|---|
| `confirmatory.json` | `full_walk_forward` | **これだけ** |
| `acceptance.json` | `acceptance` | 不可(配線確認・効果非依存) |
| `diagnostic_m0.json` | `diagnostic` | 不可(対照アーム m=0.0) |
| `diagnostic_m1.json` | `diagnostic` | 不可(対照アーム m=1.0) |

acceptance と診断アームの fold は confirmatory の窓の内側にある。そこで効果を見て判断すれば選択リーク
(068 C2)なので、`eval.decision.assert_verdict_eligible` が kind で構造的に弾く。CLI 側も、候補の
mask 率が凍結値と違えば自動で `diagnostic` を刻む(フラグの付け忘れで verdict 適格な artifact が
生まれないようにするため)。

## JSON 内の `feature_version: "features-020"` について

これらは**実行時点の記録なので書き換えていない**。091 は当初 `features-020` を使っていたが、088 が
bump 後 REJECT で revert して焼却済みの番号だったため、main 取り込み時に `features-021` へ改名した。
`feature_hash` は列名の集合から計算されるので値は不変(`663fe86c…`)で、**再学習は不要だった** —
動いたのはラベルだけである。gate-config の凍結 hash も同じ理由で `c3594766…` → `5524f474…` に
変わっているが、判定に使われるキーが 1 つも動いていないことを機械的に検証してから再凍結した
(`gate_config_hash.txt` に両方の hash と理由を記録)。verdict は不変。
