# Tasks: 061 本格スピード指数特徴

**Input**: [spec.md](spec.md) / [plan.md](plan.md) / [research.md](research.md) D1–D8 / [contracts/speed-figure.md](contracts/speed-figure.md)

**Tests**: 憲法の品質ゲートにより leakage/parity/境界値テストは必須。

**確定設計(codex 反映済み)**: セル=venue×track×距離正確値×going・race-level 1 標本・min_races=50(実測カバレッジ 93.2%)・z-score clip±5・5 列(avg/best/recent3/last/count)・pl_topk spike 必須。

## Phase 1: Setup

- [X] T001 features-015 canonical hash 計測済み: `0a93f210765ebb656088d753c5133685581e9a61d29dce17472c7be35dec2839`(123 列、lgbm-060-mkt metadata.feature_hash と一致確認)

## Phase 2: Foundational — features 実装(US1/US2 両方をブロック)

- [X] T002 `features/src/horseracing_features/speed_figure_features.py` 新規: (a) race-level 標本構築(finished かつ finish_time 有効行のレース平均、`pace_features._to_seconds` 再利用)(b) セル(venue_code,track_type,distance,going)×日の (count,Σx,Σx²) 日次集計 → セル内日付順 cumsum − 当日 = strictly-before 統計 (c) min_races=50 未満/std 退化 → NaN、z=clip((mean_before−time_s)/std_before, −5, 5) (d) 馬単位 `_rolling_asof` 同型集約で 5 列(asof_spdfig_avg/best/recent3/last/count、count は履歴ゼロ=0.0・他は NaN)
- [X] T003 `features/src/horseracing_features/registry.py`: FEATURE_VERSION="features-016"・speed_figure 群 5 列を REGISTRY/FEATURE_GROUPS に追加(**STATIC_COLUMNS に入れない**)・`COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-016"]={"features-014": 既存 pin 値, "features-015": T001 の hash}`
- [X] T004 `features/src/horseracing_features/materialize.py`: build_asof_features に speed_figure ブロック結線(025 単一 as-of 源、in-memory/materialized 両経路が経由)
- [X] T005 [P] ユニットテスト `features/tests/unit/test_speed_figure.py`(11 本)(codex 指摘全反映): (i) 合成データでセル×日 cumsum−当日が**当日全行**(同セル他レース含む)を除外 (ii) 未来レース追加・未来タイム変更で過去行の baseline/z/集約 不変(INV-F1/pool-end 非依存) (iii) 同日他レースのタイム変更で対象行不変・過去日変更で変化(正の対照) (iv) min_races 境界(49→NaN, 50→値)・std=0→NaN・DNF/欠損 finish_time 非算入 (v) clip±5 境界・best=cummax の符号・recent3/last の同日除外(merge_asof allow_exact_matches=False) (vi) count 列: 履歴ゼロ=0.0・有効 z のみ加算
- [X] T006 [P] additive/registry テスト(同ファイル内 + 既存 4 テストの FEATURE_VERSION 期待値を 016 に更新): (i) 既存共有列が speed_figure 追加前後でバイト不変(058 `test_past_market_is_purely_additive` 同型) (ii) FEATURE_VERSION=="features-016"・pin 辞書に 014/015 両 hash (iii) grep 型 leak-guard(odds/payout トークン 0)が新モジュールに通ること
- [X] T007 features 全テスト緑(175)+実 DB parity: 共有 96 列 check_exact+check_dtype 一致(956,409 行)・新 5 列カバレッジ 82.0%(SC-005 の 80% 目安クリア)・再 materialize 済み(features-016): in-memory build と materialized 経路の bit 一致(要 1 回再 materialize `features materialize`)・共有列 check_exact+check_dtype・新 5 列のカバレッジ実測(SC-005 目安 80%+)を記録

## Phase 3: User Story 1 — 事前登録ゲート評価 (P1)

**Goal**: spike go → フル事前登録ゲート → 通過時 lgbm-061 再学習・昇格判断

**Independent Test**: 実 DB feature-eval の機械判定

- [X] T008 [US1] **spike GO(binary+pl_topk 両方)**: (1) binary 直近窓 2021+(5 fold)win LogLoss 0.23834→**0.23769(−0.00065)**・AUC +0.0024・4/5 fold。(2) **pl_topk group-marginal(直近 2 fold)も CAND 勝ち: 2025 −0.00053・2026 −0.00081**。**059(within-race 相対、pl_topk で 6 倍縮小)と違い絶対時計軸は pl_topk で縮まない(binary −0.00065 ≈ pl_topk 平均 −0.00067)=race-softmax が捕捉できない真の新情報**。設計仮説の実証
- [X] T009 [US1] **フル 19-fold ゲート ADOPTED=True(機械通過・override 不要)**: win LogLoss 0.22962→**0.22908(−0.00054)**・AUC +0.00144・Brier↓・**18/19 fold 勝ち**・worst_dLogLoss +0.00000・worst_dECE +0.00160(tol 2e-3 内)・primary_pass=True。直近特徴で最良水準の素直な通過
- [X] T010 [US1] **lgbm-061 全 4 ゲート PASS → ユーザー承認で active 昇格**(lgbm-057 retire): win LogLoss 0.21597→**0.21502(−0.00095、059 の 5 倍・036 以来級のゲイン)**・top2 0.33988→0.33858・top3 0.43021→0.42905・win ECE 0.00081(閾値内)=全指標単調改善。serving E2E: lgbm-061 が唯一 active・16 頭予測・speed_figure 列がスナップショット記録を確認

## Phase 4: User Story 2 — serving 互換 (P2)

**Goal**: features-016 registry 下で既存 3 モデルの byte-parity

**Independent Test**: 実 DB E2E mismatch 0

- [X] T011 [US2] serving 互換: fail-closed 経路(hash 不一致・未 pin)は既存 058 テストが網羅、registry pin 検証は features 側 test_registry_version_and_compat_pins で機械固定(features-016 に 014/015 両 pin)。positive compat-path は T012 実 DB で実証
- [X] T012 [US2] 実 DB E2E 全通過: **(a) lgbm-057(features-014)compat-load・202507010112 の 16 頭 win_prob が persisted 値とバイト一致 mismatch 0(SC-003)** **(b) lgbm-058-acc・lgbm-060-mkt(features-015)compat-load 成功(market_offset フラグも透過)**

## Phase 5: Polish

- [X] T013 [P] 全パッケージテスト緑(features 175/training 89/serving 44/eval 72)+ ruff クリーン
- [X] T014 [P] spec/plan/tasks 結果記録・CLAUDE.md SPECKIT 更新・memory(feature-061 結果)更新

## Dependencies

- T001 → T003。T002→T003→T004 は同系列(T005/T006 は T002 後に並行可)。T007 は Phase 2 の締め
- **T008(spike go)が T009 以降をブロック**。T011/T012 は T003(pin)後なら T009/T010 の長時間実行と並行可
- 長時間: T009 フル feature-eval(binary 19-fold、~1-2h)・T010 pl_topk 再学習(~30min+)は nohup+監視

## Implementation Strategy

MVP = Phase 2 + T008(spike)。no-go なら Phase 2 の実装+「絶対時計軸は現特徴集合に増分なし」の知見記録で終了(それ自体が成果)。US2 は FEATURE_VERSION bump の必須随伴作業のため、bump をコミットする前に必ず T012 まで通す。
