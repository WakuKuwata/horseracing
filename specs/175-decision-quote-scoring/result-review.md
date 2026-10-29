# 175 実装の記録(2026-10-29)

**出荷したもの**(未コミット・DB/スキーマ/API/front 不変):

| 層 | 内容 |
|---|---|
| live(174 の小変更) | `near_post.py` に `save_day_features`/`load_day_features`/`features_parquet_path`(`artifacts/near_post/<day>/features-<sha12>.parquet`・存在時は書かない・失敗は警告で捕捉継続・読込後 sha 照合)。`near-post-capture --features-dir`(dry-run でも保存)。判定順・fetch・保存は不変。単体 4 件 |
| eval | `decision_quote_scoring.py`(純関数: `QuoteRecord`・funnel 10 段・列別の目印と 100 円精算・`ratio_ci`・`paired_tally`=[現行, 候補] を 1 回の同期再標本化で渡し replicate 行差の percentile・`verdict`=`display_update_rule.decide`・診断 `calibration_cells`/`price_drift`/`retention`・`normalize_148`・`build_report`・`forbidden_vocabulary`)。単体 31 件 |
| live | `near_post_score.py`(bulk 4 クエリのローダ・確定単勝の来歴条件・`candidates.json` の読込と sha 照合・再現ゲート `replay_race`=ビット一致・`candidate_races`・report 組立)+ CLI `near-post-score --from --to [--candidate] [--candidates] [--features-dir] [--out]`(凍結 JSON の sha 照合・`--out` 既存拒否・終了 2)。単体 7 件・統合 2 件(testcontainers) |
| api | `test_prospective_funnel_parity_175.py`: B8 `classify_race` と 175 `classify` が同じ合成事実で共有 7 段とも一致(10 シナリオ)・閾値/MIN_BETS/BOOTSTRAP が同値 |
| features | leak-guard に `decision_quote_scoring`/`near_post_score`/`artifacts/near_post` を追加 |
| 運用 | `specs/175/candidates.json` に `ens15-norm148-v1`(postprocess・sha=eval モジュール・登録日 2026-10-29)。`scripts/near_post_ops.sh score <from> [<to>] [cand...]`・`capture`/`morning` に `--features-dir`。`specs/174/manual.md` §4 |

## T0(実 DB・2026-10-25・455 行×120 列)

parquet 往復で `frame_sha256` 一致(pickle も一致)。dtype の表示差は `race_date`(datetime64[s]↔[us])・`grade`/`dist_band`(string↔object)の 3 列で sha は不変。→ parquet 採用。14.4 秒/日。

## 再現ゲートの前提(実 artifact・2026-10-25 の 1 レース 13 頭)

`ProductionPredictor` を同じ行で 2 回呼ぶと p_ens は**ビット一致**(`np.array_equal`)・2 回で 0.02 秒・mix `available`。148 の正規化はその出力で動く(Σp_ens 0.919・このレースでは目印 0 頭=現行も 0 頭)。

## 実 DB での CLI(行 0 の完走)

`near-post-score --from 2026-10-25 --to 2026-10-31 --candidate ens15-norm148-v1` は decision_quotes が 0 行のため funnel 全 0・両列 `remaining_to_min=300`・候補 `verdict=insufficient`・`verdict_overall=insufficient` で終了 0・JSON 1 件。拒否経路は単体テストで固定(`--out` 既存・from>to・候補 sha 不一致)。

## 実装中に直した欠陥

- `paired_tally` の出力で候補名のキー `candidate` が腕の集計 `candidate` に上書きされ、CLI が `StopIteration` で落ちた(実 DB 実行で発覚)。名前は `name` に改名(contracts/report.md も更新)。
- 統合テストの fixture が同じ馬を 2 レースに `seed_race` して PK 衝突 → 2 レース目は既存の馬を再登録する helper。

## 設計上の性質(意図したもの・運用で意識する)

- **eval モジュールを変更すると postprocess 候補の sha 照合が落ちる**(候補=コードの定義なので設計どおり)。変更後は `candidates.json` の sha を更新して再登録する=登録日が動く。本実装中にも 1 回起きた(名前キーの修正後に再登録・登録日は同日)。
- 再現ゲートは「現行がビット一致で再現できたレース」だけを対にする。後日の人気・馬場の更新で `features_sha256` は動くが、parquet があれば入力は同一。parquet が無い日(再構築)でビット一致しない件数は 10/31 以降の実測で分かる(`not_reproducible`・`max_abs_diff_p_ens` を出力)。

## テスト

eval 709(+31)/ live 186(+13)/ api unit 231(+11)/ features leak-guard 2。ruff: live・api・features・eval の新規ファイルは緑(eval 全体の既存 11 件は `stage_discount_eval.py`/`test_buy_pattern_gate.py` 由来で本 spec の前から)。

## セルフレビュー(codex unavailable・400)

research D13 のとおり。追加で確認: 読み取り専用は統合テストで DB 行数不変を固定・同期再標本化は `race_block_ratio_bootstrap_ci_v1` の複数行入力で実現(別々に呼ばない)・`n_points`=候補の目印本数(登録日以降・対のみ)・語彙は `forbidden_vocabulary` を統合テストと単体テストで固定。

## 残り

- 10/31 の試運転後に `score 2026-10-31` を回し、funnel・`not_reproducible` の実測を本ファイルに追記。
- 読み取り API/ページ(後段 spec)は eval の `build_report` をそのまま呼べる。
