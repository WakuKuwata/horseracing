# 表示する期待回収率の版を mev-ens15-v1 → mev-ens15-jrdb-v5 に替える(2026-11-10)

利用者決定(2026-11-10): 「差し替える」「全部切り替える」「(表示値の更新規則の)例外として替える」。

## 根拠と例外
- v5 = JRDB 正本 DB の行で学習(脚質・系統は現行語彙に写す)。本番の行で cur1108 と勝ち馬 NLL の差 +0.000002 [−0.00038, +0.00035]
  (`docs/plan/jrdb-canonical-ens15-20261109.md` v5 の節・事前登録の本番化条件をすべて満たす)。
- 174 B1 の表示値の更新規則(判断記録 ≥300 点で新版が良いときだけ替える)には当てはまらない。**情報源の切替に伴う同じ精度の置き換え**として、
  利用者の決定で例外にする。規則そのものは変えない(今後の「良くなったから替える」は従来どおり規則に従う)。精度や回収率の改善は主張しない。

## 版と記録の分け方(v1 の行・記録は消さない)
| 対象 | v1 | v5 |
|---|---|---|
| 表示版 `DISPLAYED_MARKET_EV_MODEL_VERSION` | mev-ens15-v1 | mev-ens15-jrdb-v5 |
| `ENSEMBLE_LOGIC_VERSION` の先頭 | mev-ens15-v1; | mev-ens15-jrdb-v5;(後ろは同じ) |
| 注目条件の数え方 `SELECTION_POLICY_VERSION`・開始日 | v2・2026-10-05 | v3・2026-11-11(v2 の pick は before_start で数えない) |
| 注目条件の凍結統計 | 2026-10-02 再凍結 | v5 の walk-forward 予測で同じスクリプト・同じ bootstrap で再凍結 |
| 規則の定義(閾値・帯・RULE_SET_VERSION・definitions_sha256) | 変えない | 変えない |
| 判断時点の見込み(139 buy-time-v2) | 検算済みで表示 | v5 では未検算 → 「検証待ち」(`VERIFIED=False`) |
| 市場混合 `MIX_VERSION`・係数・開始日 | mix-ens15-v1・2026-10-19 | mix-ens15-jrdb-v5・v5 の予測で同じ手順で fit・2026-11-11 |
| 判断記録(174)の `LOGIC_VERSION`・既定の ensemble | ens=mev-ens15-v1 | ens=mev-ens15-jrdb-v5(定数から作る) |
| 判断記録の採点(175) | v1 の quote | 採点は表示版の quote だけを読む(v1 の quote は数えず件数を出す)・候補は v5 用に登録し直す |
| 表示値の更新規則の凍結 JSON | v1 の版名 | 版名を v5 にし、例外の記録を足して sha を更新 |

## 凍結のやり直しの入力(結果は見ない・同じスクリプト)
- v5 の 15 seed walk-forward 予測(`_ens15jrdb5`)と v5 の学習行(`rows_horseracing_jrdb_v5.parquet`)。S5(単 seed `mev-binary-v2`)の予測は
  現行 DB の行のものを (race_id, horse_id) で結ぶ。全馬の予測がそろわないレースは除き、件数を記録する。
- 注目条件: `freeze_rules_S1_S5_20261001.py` に入力を指定する引数を足すだけ(閾値・bootstrap は変えない)。採否条件(ens15 の LogLoss ≤ 単 seed・
  ECE ≤ 単 seed+0.001)が通らなければ止める。
- 市場混合: `fit_mix.py` に入力を指定する引数を足すだけ(fit の期間・境界・初期値は同じ)。表示の目印(混合後 >1.0)は変えない。
  141 の一度きりの held-out 確認は手つかずの期間が無いのでやり直さない(記録する)。

## 本番の切替手順
1. コード・凍結値・テストを変更(上の表)。
2. 2025-01-01〜2026-11-08 の v5 行を `market-ev --ensemble-dir artifacts/market_ev/mev-ens15-jrdb-v5 --ensemble-only` で埋める(v1 行は残す)。
3. api・ops-api・worker を再起動(ops の既定 ensemble dir は表示版の名前から作る)。
4. 確認: レース詳細・日付一覧・注目条件・被覆率が v5 の値を返す/混合列が available/判断記録の dry-run が v5 を読む。

## 残るリスク
- 現行 DB の脚質・系統は netkeiba が書いている(2025〜2026 年は netkeiba の語)。R9 で netkeiba を止める前に JRDB から写した値を書く取込みが要る。
- v5 の 2027 年用 booster は 12 月に作る(v1 と同じ課題)。
