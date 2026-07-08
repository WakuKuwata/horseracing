# Research: 060 市場残差型・精度最優先モデル

**Date**: 2026-07-08 | **Spec**: [spec.md](spec.md)

実査で確認した既存機構(plan の前提):

- `training/win_model.py` `WinModel._fit_softmax`: pl_topk/cond_logit は `lgb.train` + custom fobj(閉包が group_sizes/ranks を保持、preds から `race_softmax` を計算)。predict は `booster.predict(raw_score=True)` → `race_softmax`。
- `training/predictor.py` `LightGBMPredictor`: fit 内で calib held-out 行にも `win_model_.predict` を通して isotonic をフィット(=predict と同一経路)。`assemble_predictions` が clip→Σ=1 正規化→Harville。
- `training/dataset.py` `TrainingMatrix.frame`: `race_id, horse_id, <features...>, race_date, win, finish_rank` — ラベル側補助列(`finish_rank`)を特徴列外に持つ前例あり。
- `eval/baselines.py`: **市場オッズ baseline predictor が既存**(`result_market.odds` → q → Prediction、harville_topk 共有)。q 単体 baseline は同一ハーネスで算出可能。
- `eval/market_edge.py` `_market_q`: q=(1/odds)/Σ(1/odds) の定義が eval 側に既存。
- `serving/model_loader.py`: metadata 駆動の fail-closed ロード(exact/compat feature_hash ゲート、058)。`ServingModel.raw_predict`。
- `serving/pipeline.py`: `_predict_persist` → `predict_race(model, race_id, feature_rows)` → persistence。logic_version 付帯の前例(`sdisc=`、`reg=`)。

## D1: offset の注入方式 — objective 閉包内加算(init_score 不使用)

**Decision**: `lgb.Dataset(init_score=...)` は使わず、**objective 閉包に offsets を渡し、fobj 内で `race_softmax(preds + offsets)`** を計算する。predict 側は `raw_score=True` の出力に offsets を加算してから `race_softmax`(fit の calib 行・eval・serving すべて同一経路)。

**Rationale**:
- 数学的に init_score 方式と完全等価(softmax のスコアに定数列を加えるだけ。勾配 ∂L/∂s は加算後の p で同一)。
- init_score 方式は「custom fobj に渡る preds に init_score が含まれるか」「predict 出力に含まれないか」という LightGBM の版依存セマンティクスに依存する。閉包方式は**全ての offset 演算が自前コード内**にあり、単体テストで完全に検証可能・決定論。
- 加算漏れ(predict 側で offset を足し忘れる)の検知: 「特徴を全 drop した offset モデル ≒ q baseline と一致」という等価性テストで機械検出できる。

**Alternatives considered**: (a) `init_score` — LightGBM セマンティクス依存で却下(等価なら依存の少ない方)。(b) q を特徴列に追加 — registry/FEATURE_VERSION/feature_hash/serving ゲートに波及し、「offset は特徴列ではない」という spec の設計(FR-003)に反するため却下。木が市場信号を「曲げる」自由度は落ちるが、まず残差学習の純形で評価する。

## D2: q・offset の定義

**Decision**: `q_i = (1/odds_i) / Σ_j (1/odds_j)`(started かつ有効オッズの馬で正規化、010 定義)。`offset_i = log(clip(q_i, 1e-6, 1))`。レース内の定数シフトは softmax 不変なのでそのまま。

**Rationale**: 既存定義の再利用(`market_edge._market_q` と同式)。clip は log の発散防止のみ。FL バイアス(本命過小・穴過大)は q に残るが、初版では**補正しない**: isotonic 校正が最終 p の単調再マップとして marginal のバイアスを吸収し、047/048 で確立済みの two_gamma 系 γ 事前補正は spike が失敗した場合の登録済みフォールバックレバーとする(結果を見てゲートを変えるのではなく、構成の代替をあらかじめ登録)。

## D3: 学習時のオッズ供給経路

**Decision**: `TrainingMatrix.frame` にラベル側補助列 `mkt_odds`(race_horses.odds、(race_id, horse_id) キー)を追加。`finish_rank`(042)と同型の「特徴列ではない教師/補助情報」で、`feature_cols` に入らないため feature_hash・FEATURE_VERSION・serving 互換に不干渉。

**Rationale**: fit(offset 構築)と eval 経路の predict_race(frame 行の reindex)の両方が同じ列から offset を再構成でき、経路分岐がない。

## D4: オッズ欠損の fail-closed 方針

**Decision**: **レース単位の全か無か**。学習・評価=started 行に 1 頭でも欠損/不正(null, ≤0, 非数)オッズがあるレースは丸ごと除外(件数を集計・報告)。serving=同条件で型付きスキップ(offset なし縮退・欠損馬への field 平均 offset 代入は禁止)。

**Rationale**: 部分補完は「市場情報の捏造」で、欠損馬の q を作ると残りの devig も歪む。JRA-VAN 単勝オッズの started 行カバレッジはほぼ全量の見込み(spike で実測し件数を plan 通りに記録)。除外がまとまって発生する期間があれば gate 母集団の代表性の問題として報告する。

## D5: ゲート評価の母集団と baseline

**Decision**: 事前登録ゲート(FR-004)の 3 比較対象 — (candidate) 市場残差モデル、(A) q 単体 baseline、(B) lgbm-058-acc 構成 — を**同一 fold・同一「オッズ完全カバー」レース集合**で評価する。(A) は**専用実装**: 既存 `eval/baselines.py` の MarketBaseline は欠損オッズを floor 補完しており fail-closed(D4)と非互換(codex 発見)のため流用せず、制限母集団上で q を clip→正規化→Harville の同一 assemble 経路に通す baseline を新設する(母集団制限により欠損は構造的に存在しない)。(B) は既存 058-acc 構成(pl_topk+TE+isotonic、features-015)を制限母集団で**再評価**する(公表値 0.21579 は全レース母集団のため直接比較不可)。ハーネスは predictor 間の共通レース集合フィルタを持たない(codex 確認)ため、**オッズ完全カバー race_id 集合を先に確定して 3 者の evaluate に同一集合を渡す比較ドライバ**を実装する。

**Rationale**: 母集団が揃わない比較は無意味(011 の canonical field 規律と同じ思想)。

## D6: 校正

**Decision**: 既存どおり isotonic(calib held-out 行、offset 込み予測に対してフィット)。校正 A/B(isotonic vs none)を 039/042 同様に spike で確認。

**Rationale**: offset が支配的な場合、isotonic は実質「市場 q の単調再校正」となり 013 の FL 補正と同じ働きを最終 p 上で行う。二重補正の懸念(γ 事前補正 + isotonic)は初版で γ を入れないことで回避。

## D7: serving 結線

**Decision**: model metadata に `market_offset`(例 `{"kind": "log_q_devig", "source": "win_odds"}`)を追加。`load_serving_model` はキー透過(無し=offset なし=既存モデル後方互換)。`pipeline._predict_persist` の前段で、market_offset モデルの場合のみ対象レースの started 馬オッズを DB から読み q→offset を構成して `predict_race` に渡す。欠損は typed skip(report に集計)。logic_version に `mkt=logq` を付与(`sdisc=`/`reg=` と同型の監査マーカー)。

**Rationale**: 057/058 で確立した「metadata 駆動・fail-closed・default 経路バイト不変」パターンの踏襲。default モデルは market_offset キーを持たないため経路が一切変わらない。

## D8: リーク境界(挙動型 guard)

**Decision**: grep 型 leak-guard は本モデル専用経路に非適用(058 前例)。挙動型テスト: (i) 他レース・未来レースのオッズ変更で対象レース予測不変、(ii) レース結果変更で予測不変、(iii) 対象レース自身のオッズ変更で予測変化(市場情報が実際に効いている正の対照)。eval predictor 契約の `is_leaky_reference` フラグは本 predictor 構成で**真実を申告**する(意図的に result-time オッズ由来情報を使うため)— ハーネス側でこのフラグに依存する箇所の有無を実装時に確認し、必要ならフラグ名の意味論を保ったまま結線する。

## D9: spike (T0) — go/no-go

**Decision**: フル実装前に 2 段の spike。
1. **合成データ単体検証**: 特徴を情報ゼロにした offset モデルが q baseline と(校正差を除き)一致すること、offset のレース内定数シフト不変性、predict 側加算漏れの検出。
2. **実 DB 少数 fold**(直近 3-4 fold): pl_topk+offset vs q baseline vs 058-acc 構成(全て D5 の制限母集団)。**Go 条件(事前登録)**: offset モデルの win LogLoss が q baseline を平均で下回る。No-go なら γ 事前補正(D2 フォールバック)を 1 回だけ試し、それでも負ければ中断して結果を記録(FR-009)。

## D10: 登録・命名

**Decision**: ゲート全通過時のみ `lgbm-060-mkt` を**非 active(candidate)登録**。既存 `train-evaluate` は「ゲート通過→ACTIVE 保存」の汎用ロジック(codex 確認)のため、**通過しても candidate 固定で保存する分岐**(例 `--register-as candidate`)を追加する(058-acc は手動手順だったものの機械化)。057 の `set-model-label` で display_name/purpose に「市場情報利用・精度最優先・意思決定支援には非使用・retrospective(closing-leaning オッズ)」を明示。自動昇格なし(default 切替は将来の明示的ユーザー判断)。

## D11: q 実装の single source of truth(codex 指摘)

**Decision**: `probability.market_odds.market_implied_win_probs()` を正とする。training からの import が既存の import-graph 境界テストで許容されるなら直接 import、不可なら `training/market_offset.py` に純関数を実装し **probability 実装との定義同一性テスト**(同一入力→同一出力)で固定する(実装時に境界テストを確認して決定、どちらでも INV-M1 は満たされる)。

## 未決(codex second opinion 待ち → plan.md に記録)

- init_score vs 閉包方式の見落とし
- FL バイアスの事前補正要否(D2/D6 の初版判断の妥当性)
- 部分オッズ欠損の全か無か方針の副作用
- ゲート母集団制限の穴、TE と offset の相互作用、不足テスト
