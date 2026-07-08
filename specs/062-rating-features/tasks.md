# Tasks: 062 as-of レーティング特徴

**Input**: [spec.md](spec.md) / [plan.md](plan.md) / [research.md](research.md) D1–D9 / [contracts/rating.md](contracts/rating.md)

**Tests**: 憲法の品質ゲート + 逐次状態のため leakage/parity/決定論テストは必須。

**確定設計(codex 全12指摘反映)**: Elo 多者ペアワイズ K/(m−1)(m=除外後有効頭数)・日単位凍結(朝スナップショット→日末一括更新)・派生5列(全て朝スナップショットから)・DNF除外/tie=0.5・K=24/初期1500/スケール400 固定・pl_topk 必須確認。

## Phase 1: Setup

- [X] T001 features-016 canonical hash 計測済み(`300b28a9312a3fb6e171b1dfd38cc88413ccbae2a0cfa9936ed278b5d14b66ac`、128 列、lgbm-061 metadata と一致)を registry pin に使用

## Phase 2: Foundational — Elo コア + materialize 安全性(US1/US2/US3 をブロック)

- [X] T002 `features/src/horseracing_features/rating_features.py` 新規: (a) 全レースを (race_date, race_id, horse_id) stable sort → **日単位ループ**(その日の朝スナップショットを全出走馬の特徴行に記録) (b) その日の各レースで finish_order 付き valid finisher のみで Elo 多者ペアワイズ delta 計算(`ΔR_i=K/(m−1)·Σ(S_ij−E_ij)`、E_ij シグモイド、tie=0.5、m=有効頭数) (c) 日末に全 delta を朝スナップショットへ一括適用 (d) 派生5列(asof_rating/recent_delta/max/starts を状態オブジェクトから朝時点値で、vs_field は後段=下記)。K=24/初期1500/スケール400 定数。float64・stable sort・非並列(codex #11)
- [X] T003 `features/src/horseracing_features/materialize.py`: build_asof_features に rating ブロック結線(単一 as-of 源)。`asof_rating_vs_field`(今走出走馬の asof_rating 平均との LOO 差、059 同型)は組み立て済み `out` を入力に後段で計算(059 relative_ability と同じ位置)
- [X] T004 `features/src/horseracing_features/registry.py`: FEATURE_VERSION="features-017"・rating 群 5 列を REGISTRY/FEATURE_GROUPS に追加(**STATIC_COLUMNS に入れない**)・`COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-017"]={"features-016": 300b28a9..., "features-015": 0a93f210...}`
- [X] T005 [P] レーティング正しさ+更新式テスト `features/tests/unit/test_rating_features.py`(codex 推奨): (i) 手計算小フィクスチャで Elo delta 一致(既知 R・着順→期待 ΔR) (ii) 一貫勝者のレーティングが上がる/敗者下がる(INV-R6) (iii) tie=0.5・DNF(finish_order NULL)除外・m=有効頭数 (iv) 初出走=初期値1500・starts=0.0
- [X] T006 [P] リーク/materialize 安全テスト(codex 推奨、最重要): (i) 今走結果変更で今走 rating 特徴不変・過去結果変更で変化(INV-R1 正の対照) (ii) **pool-end 非依存: end_date=D1 build と D2>D1 build で races≤D1 の rating 列一致**(INV-R2) (iii) 同日凍結: 同日他レースの結果変更で対象行不変(INV-R1) (iv) **同日2走馬が両スタートで同一朝 rating**(codex #3) (v) 決定論: 2 回 build で bit 一致(INV-R3) (vi) **下限窓ロード禁止回帰: load_frames が下限なし(2007から)で呼ばれること**(codex #1 の前提保護) (vii) 派生列も朝スナップショットから(同日結果を見ない、codex #8)
- [X] T007 [P] additive/registry テスト(058/061 同型): 既存共有列バイト不変・FEATURE_VERSION=="features-017"・pin に 016/015 両 hash・rating 群が STATIC でなく model_input・grep 型 leak-guard(odds/payout トークン 0)
- [X] T008 features 全テスト + 実 DB parity: in-memory と materialized 経路の rating 列 bit 一致(要 1 回再 materialize)・共有列 check_exact+check_dtype・**full-history build と(下限ありの)窓 build で重複行 parity**・rating カバレッジ実測記録

## Phase 3: User Story 1 — 事前登録ゲート評価 (P1)

**Goal**: spike go → フル事前登録ゲート → 通過時 lgbm-062 再学習・昇格判断

- [X] T009 [US1] **spike NO-GO**: (1) レーティング正しさ+materialize 安全 = T005/T006/T008 で担保(**pool-end index 整合バグを発見・修正**、69万行 mismatch 0)。(2) binary 直近窓(5 fold)は ADOPTED=True だが弱い(LogLoss −0.00017・AUC −0.00028 僅悪化)。(3) **pl_topk group-marginal(直近 2 fold)で悪化: 2025 +0.00037・2026 +0.00036=rating ありが負**。**059(within-race 相対、pl_topk で 6 倍縮小だが正維持)をさらに越えて負に転じた=相手品質軸(Elo)は既存勝率系能力+race-softmax が既に捕捉済みで冗長**。事前登録ゲート NO-GO で採用せず(FR-011)

## ⛔ 以降は NO-GO により実施せず(027 前例=不採用は FEATURE_VERSION を merge しない)

- [X] REVERT: FEATURE_VERSION を features-017→**016 に戻す**・rating 群を REGISTRY/FEATURE_GROUPS/compat pin から除去・materialize 結線解除・parquet を features-016 に再 materialize。**rating_features.py + test_rating_features.py は負の結果の記録として保全**(build 未結線・単体テストは直接呼び出しで緑)。既存テストの FEATURE_VERSION assert を 016 に復帰
- [ ] ~~T010 フル 19-fold ゲート~~ / ~~T011 lgbm-062 再学習~~ / ~~T012 materialize E2E~~ / ~~T013 serving 互換~~ — NO-GO で不要(features-016=lgbm-061 active のまま不変)
- [ ] T010 [US1] フル walk-forward 事前登録ゲート(`feature-eval --drop-groups rating`、contracts の 3 条件)。結果・カバレッジを記録
- [ ] T011 [US1] 通過時のみ: production 構成で lgbm-062 再学習(`train-evaluate --objective pl_topk --calibration isotonic --target-encode jockey_id,trainer_id --baseline lgbm-061 --model-version lgbm-062`、nohup+監視)。全指標を lgbm-061 と比較 → **active 昇格はユーザーに諮る**。昇格時は **lgbm-061 を手動 retire**(train-evaluate は旧 active を retire しない=061 の教訓・2つ active で serving 壊れる)

## Phase 4: User Story 2/3 — materialize 安全性 + serving 互換 (P1/P2)

- [ ] T012 [US2] materialize 安全性の実 DB 総合確認(T008 の一部を E2E 化): 再 materialize 後の parquet が in-memory と bit 一致・content_hash 決定論・pool-end 非依存を実データ規模で確認
- [ ] T013 [US3] 実 DB serving 互換 E2E(058/061 同型スクリプト流用): (a) lgbm-061(features-016)compat-load・予測が persisted 値とバイト一致 mismatch 0 (b) lgbm-058-acc・lgbm-060-mkt(features-015)compat-load 成功

## Phase 5: Polish

- [ ] T014 [P] 全パッケージテスト(features/training/serving/eval)+ ruff クリーン
- [ ] T015 [P] spec/plan/tasks 結果記録・CLAUDE.md SPECKIT 更新・memory(feature-062 結果)更新

## Dependencies

- T002→T003→T004 同系列。T005/T006/T007 は T002-T004 後に並行可。T008 は Phase 2 の締め
- **T009(spike go)が T010 以降をブロック**。T012/T013 は T004(pin)後なら T010/T011 の長時間実行と並行可
- 長時間: T010 フル feature-eval(binary 19-fold)・T011 pl_topk 再学習(~30分+)は nohup+監視

## Implementation Strategy

MVP = Phase 2 + T009(spike)。逐次状態の materialize 安全性(T006)が本 feature の生命線 — ここが緑にならなければ実装を進めない。no-go なら Phase 2 の実装+「相手品質軸は既存能力/pl_topk と重複」の知見記録で終了。US2(materialize 安全)は P1 として US1 と同格に扱う。
