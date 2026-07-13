# Research: 評価契約の是正 + 校正分割の見直し

**Feature**: 068 | **Date**: 2026-07-12

本書は plan.md の NEEDS CLARIFICATION と主要設計判断を解決する。前例は memory と specs を根拠にする。

## D1: winner NLL を PRIMARY にする定義と母集団

**Decision**: PRIMARY = race-level winner NLL = レースごとに `-log(p_winner)` を1標本として平均。母集団は「勝者がちょうど1頭のレース」。同着（複数勝者）・勝者不在（全馬DNF）・結果未確定レースは除外し、除外件数を surface する。started-all LogLoss/Brier（per-horse、DNF・失格=win0）と finished-only（過去互換）を SECONDARY として併記。

**Rationale**: pl_topk は race-softmax で「1レース1勝」を直接最適化するモデル（039/042）であり、per-horse binary LogLoss はレース内で相関した67万標本を独立扱いして有効標本数を過大評価する。race-level winner NLL は fold あたりのレース数を有効標本にし、block bootstrap の resample 単位（開催日）とも整合する。finished-only（現行）は DNF を落とすため、race-softmax が started 全馬に配った確率の一部を無視していた（母集団不一致）。

**Alternatives considered**:
- per-horse started-all を PRIMARY: 相関標本で CI が過小になる。SECONDARY に留める。
- top-k listwise NLL を PRIMARY: pl_topk の学習損失そのものだが、2/3着ラベルの品質は勝者ほど安定せず、製品の主目的（勝ち馬確率）から遠い。top2/top3 は non-inferiority ガードとして使う。

## D2: 開催日単位 block bootstrap の paired 差 CI

**Decision**: paired 差（candidate loss − active loss）を race 単位で算出 → **開催日（race_date）単位に集約** → 開催日ブロックを seeded moving-block bootstrap で B=2000 回 resample → paired 差平均の 95% percentile CI。ブロック長は「1開催日=1ブロック」を既定（同日レースは相関するため同一ブロックに束ねる）。seed は実行前固定・metadata 記録。i.i.d. race シャッフルは禁止（016 ruin-prob の block bootstrap 前例と同じ規律）。

**Rationale**: 同一開催日のレース（同じ馬場・天候・トラックバイアス）は損失が相関する。開催日をブロック単位にすると serial/クラスタ相関を保存し、CI が過小にならない。060 の「expanding-window 初期foldアーティファクトで毛差FAIL」は点推定のみで判定した副作用であり、CI を持てば「差は0と区別つかない」と正しく言える。

**Alternatives considered**:
- 固定長 moving block（例 5開催日）: ブロック長選択が恣意的。開催日1ブロックが最も保守的で説明可能。直近窓で開催日が少ない場合は CI が広くなることを許容（spec Edge Case）。
- 解析的 CI（正規近似）: paired 差の分布が歪む（少数の荒れレースが裾を作る）ため不採用。

## D3: C/D 校正器が作用する空間（raw score vs race-normalized p）— 最重要の非対称性

**Decision**: 校正方式ごとに作用空間を明示的に分ける。
- **isotonic / temperature（platt 系）**: raw score（sigmoid 前の per-horse スコア、既存 `fit_calibrator` の入力）に作用。その後 009 engine の normalize+clip でレース内 Σ=1 に戻す（現行 A/B と同じ経路）。
- **race-normalized power（D）**: 013/048/017 と同じく **race-normalized p ベクトル**（softmax 後・正規化後）に `p'∝p^γ` を適用し、engine が使う正確なベクトルで γ を winner-NLL MLE フィットする。marginal p_i に単体で γ を掛けない。

C/D の「全履歴refit」では、booster を全履歴で学習した後、**校正器フィット用の予測を時系列 OOF で生成**（学習に使った行を自分のスコアで校正しない）。OOF スコア分布と最終 refit モデルの score 分布が乖離する場合、校正パラメータの移植で悪化しうる → train 内 valid で移植可能性を確認し、悪化する構成は B にフォールバック（FR-011）。

**Rationale**: race-softmax モデルでは raw score → softmax → 正規化 p という2段があり、isotonic は前段（per-horse 単調変換）、power は後段（race-normalized 分布のシャープ化）に作用する。この非対称を無視して両方を raw score に当てると、power が Σ=1 を壊す。048 が既に「power は race-normalized ベクトルに」と確立済みなので、その規律を C/D に移植する。probability 側の `model_calibration.py`（048 two_gamma/power）と 017 temperature を再利用し、training 側で新しい校正数学は書かない。

**Alternatives considered**:
- 全部 raw score に作用させる: power が順位保存だが Σ=1 を壊し 009 と不整合（憲法IV違反）。却下。
- OOF を使わず全履歴で校正器もフィット: 校正が学習データに楽観適合（021 R2 の in-sample 楽観と同型）。却下。

## D4: A/B/C/D 比較での TE encoder 母集団と bit-parity

**Decision**: 本feature は **bit-parity を要求しない**。A/B/C/D は calib_frac / 学習配分を変える実験であり、TE encoder（jockey/trainer OOF）は model-fit 行だけで fit されるため（[predictor.py:183](../../training/src/horseracing_training/predictor.py)）、配分を変えれば encoder 母集団も変わり raw score も変わる。これは実験の意図どおりの変化であって回帰ではない。**不変を要求するのは feature 列・feature_hash・FEATURE_VERSION のみ**（特徴定義は触らない）。

paired 評価の公平性は「同一 race 集合・同一 fold 境界・同一 seed・同一特徴 version」で担保する（FR-003）。A〜D は同一スナップショット・同一特徴で、校正と学習配分だけが異なる（FR-010）。

**Rationale**: spec の「特徴量固定」は「特徴の定義・列・version が同一」の意味であり、「同一の数値スコアが出る」ではない。校正分割を動かせば booster も TE encoder も学習データが変わるのは当然で、それこそが Phase 1 が測りたい限界効果。bit-parity（058/061 の serving 互換）は本feature の対象外。

**Alternatives considered**:
- TE encoder を全4条件で同一母集団に固定: booster とTE で学習配分が食い違い、C/D の「全履歴学習」の意味が崩れる。却下。

## D5: paired 評価で baseline 保存値を読まない実装

**Decision**: `training paired-eval --candidate <mv> --active <mv>`（または candidate=artifact / active=DB active）で、**両モデルを現DB・現 materialized manifest・同一 race 集合・同一 fold 境界で同時に walk-forward 再評価**する。`model_versions.metrics_summary` の保存値は表示の参考にしても、ゲート判定の baseline には使わない。race_id 集合の hash を両者で一致検証し、不一致なら fail-closed（typed error）。

**Rationale**: spec FR-003 の核心。現行 `train-evaluate` は候補を現DB再評価するが baseline は保存値読み（backfill/materialize/母集団変更で非paired）。056/061 の長時間学習を経た今、保存値は異なるコード version・異なる母集団で作られている可能性がある。paired 同時評価だけが「同じ物差し」を保証する。

**Alternatives considered**:
- active の保存 OOF を再利用: computed_at が古く materialized manifest が違えば非可比。却下。

## D6: codex second-opinion（品質ゲート）

**Decision**: 本セッションで codex-rescue agent は起動失敗（`gpt-5.6-sol requires newer Codex CLI`）。CLAUDE.md 規約に従い、親から `codex exec --sandbox read-only` を直叩きで再試行（[codex-env-recovery] の方式）。取得できた指摘は本 research に追記し、採否と理由を記録する。取得不能なら `codex unavailable: gpt-5.6-sol CLI 非互換` を記録し、セルフレビュー checklist で代替。

**セルフレビュー checklist（codex 代替）**:
- [ ] winner NLL の除外母集団（同着・勝者不在・未確定）がテストで固定されているか
- [ ] block bootstrap が開催日ブロック・seed 記録・i.i.d. 禁止か
- [ ] power 校正が race-normalized p に作用し Σ=1 を保つか（IV）
- [ ] paired 評価が race_id 集合 hash を一致検証し fail-closed か
- [ ] 評価派生値の leak-guard test（特徴非流入）
- [ ] provenance 5項目が校正分割時に train_through と異なるか
- [ ] スキーマ/API/FEATURE_VERSION/feature_hash 不変

**（codex 実行結果は D7 に追記）**

## D7: codex レビュー結果

親から `codex exec --sandbox read-only` 直叩きで取得成功（[codex-env-recovery] 方式・codex-cli 0.144.1）。指摘と採否:

### 採用（correctness-critical — 設計を変更）

**C1. 保存 artifact は全履歴 fit の serving model であって walk-forward モデルではない**（artifacts.py:8）。保存済み lgbm-062/061 を過去 race に適用すると **in-sample**（リーク）。→ **paired-eval は保存 artifact を評価しない**。candidate/active の完全な **ModelRecipe**（objective/calibration/features/seed/TE/calib_frac）を受け取り、**各 outer fold で両者を再 fit** して outer-valid を一度だけ予測する。これは D5 の「両者を現DB再評価」を「両者を recipe から各fold再fit」へ強化する最重要修正。CLI 引数は model_version でなく recipe（既存 register 済みモデルは recipe を復元して使う）。

**C2. 直近fold screening を最終 CI に含めると憲法III違反**（selection leak）。→ **nested walk-forward**: A/B/C/D の transfer-check・校正方式選択・計算量 screening は各 outer fold の **inner-valid のみ**で行う。「直近fold go/no-go → 勝ち候補のみ full」は、その直近fold を最終判定 CI に**含めない**（独立 confirmation window）か、全 arm を全 fold 評価して多重比較補正する。spec US2 AS4・contract の `--derisk-recent-folds` を inner-valid screening に再定義。

**C3. `market_offset=True` は対象 race 自身の odds を読む**（predictor.py:309）。→ 068 対象 recipe は **`market_offset=false` を fail-closed で要求**（FR 追加）。提案書 §2.2 の「対象レース市場は不使用」を recipe レベルで機械強制。

**C4. `split_train_by_time` は race 数ベースで同一開催日が model/calib に跨りうる**（calibration.py:27）。→ 憲法の日時境界・bootstrap 単位（開催日）と整合させ、**model-fit / calib 分割を日単位に**する。A（現行70/30 再現）との差分が出るため、A は「現行 race 数ベース」を再現しつつ、日単位版を B' 的に別測するか、分割そのものを日単位へ統一するかを実装時に固定（既定=日単位へ統一、A は現行再現テスト専用に race 数ベースを残す）。

### 採用（設計明確化 — 表現/契約を修正）

**C5. bit-parity は不可能・私の hash 表現が不正確**。`feature_hash` は列名のみの hash（artifacts.py:34）で値の同一性を証明しない。→ hash 契約を分離: `feature_schema_hash`（全 arm 同一）/ `raw_matrix_content_hash`（全 arm 同一）/ `model_race_set_hash`・`calib_race_set_hash`・`transformed_matrix_hash`・`model_artifact_hash`（arm 別、within-arm 再実行で一致）。A/B/C/D は「純粋な booster 行数効果」でなく「**校正分割を含む学習 pipeline 全体の効果**」と明記。純 booster 効果が要る場合は no-TE 感度分析を別診断に。D4 の結論（bit-parity 非要求）は維持しつつ表現を厳密化。

**C6. C/D 校正用 OOF は strict-past で生成**。現行 `oof_target_encode` は held fold 以外の全 fold を使い strict-past でない（target_encoding.py:68）。外側 valid への直接リークではないが、C/D の校正フィット用 OOF 予測は**必ず expanding strict-past**（各行 `max(train_date) < prediction_date`）で別生成する。D3 を補強。

**C7. 二重校正の整合**。predictor 内 isotonic とは別に、017/048 の power/two_gamma が betting product 経路で適用される。D（race-normalized power）採用後、後段 two_gamma を重ねるのか/置換するのか/新 p 分布で refit するのかを固定しないと**評価 p と運用 p が一致しない**。→ **方針を「新 p 分布で refit」に確定**（analyze U1）: D 校正は serving 段 win-p に適用、betting 側 046/048 `_fit_product_p_calibrator` は D 校正済み永続化予測に対し strictly-before で refit（046 の動的挙動のまま＝スタックしない・自己補正）。049 harville stage discount は別目的で対象外。評価 p == serving p を担保。betting 側実装は D 採用後の別 spec、本feature は方針確定のみ。spec FR-020。

**C8. paired race 集合は model-blind に先に固定**。予測成功後の intersection を採らない。片側の予測欠落は race 除外でなく **contract failure**（fail-closed）。D5 を強化。

**C9. snapshot 監査の強化**。「同一現DB」だけでは不十分。repeatable-read snapshot・result/entry hash・materialized manifest hash・recipe hash・code SHA を保存（V）。

**C10. equal-mass ECE は tie-safe**。isotonic plateau を bin 境界で分断しない仕様・実 bin 数・edge・count を出す。equal-mass は equal-width より推定上有利な傾向があるため bin 数/tie 処理のバイアスを surface。

### 不採用・保留

- codex の「全 arm を全 fold 評価 + 多重比較補正」案（C2 の代替）は、計算コスト（C/D フル walk-forward ~20分×arm×fold）が大きい。→ **inner-valid screening + 勝ち候補の独立 confirmation window** を既定とし、多重比較補正版は tasks の optional に。

### codex 必須テスト（tasks へ反映）

`softmax(s/T)==normalize(softmax(s)^(1/T))` / calibrator の score-space 誤接続拒否 / Σ=1・clip・no-inversion・isotonic tie 安定 / C/D OOF strict-past / inner-heldout label 変更で TE 不変 / hash 契約(schema 同一・matrix/model 相違) / 母集団 golden(started/DNF/失格/取消/除外/同着/勝者不在/未確定/部分取込) / winner NLL race 等重み vs per-horse micro 区別 / 同一モデル paired 差0・交換で符号反転 / hash 不一致 fail / block contiguity・seed 決定論・少block で `NO_DECISION` / AR(1)・開催日 cluster 合成で CI coverage / gate artifact の OOS 後変更拒否 / 対象 race odds 変更で予測不変・result 変更で score のみ変化 / `eval`→`training` import 禁止 / 二重校正防止 / A が現行70/30再現。

**優先修正順（codex）**: C/D 再定義 → recipe-based paired OOS → nested selection → TE/bit-parity 契約言い換え → bootstrap/gate 数値固定 → downstream 校正整理。
