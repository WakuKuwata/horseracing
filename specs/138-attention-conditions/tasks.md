# Tasks: 138 注目条件の表示と前向き検証

**Input**: `spec.md`(rev3)・`plan.md`(rev3・§0 契約・§1 実装順・§3 決定 D1〜D26)
**rev3(2026-10-02)**: `/speckit-analyze` の 25 件を反映(新規 T004a・T013a・T016a・T020a・T020b、T046 の leak-guard 拡張を T013a へ移動、依存関係の追記。対応表は plan §5 末尾)。続く検証ワークフロー(codex+3 体+反証)の 25 論点も反映(新規 T043a・Phase 6 の順序変更。plan §5「rev3 の検証」)。
**Tests**: このリポジトリの規律(リーク境界・契約・append-only・表示規律・再現性)はテストで固定するので、各フェーズにテストタスクを含める。
**Format**: `[ID] [P?] [Story] 説明(ファイルパス)`。`[P]` は並列可(別ファイル・依存なし)。

## Phase 1: 学習と凍結(plan 0.1・0.3 の定義と bootstrap)

- [X] T001 `scripts/roi_explore/direct_return_model.py`: `--save-last-model` の親 dir を `mkdir(parents=True, exist_ok=True)`、`model.spec.json` に seed/rounds/num_threads/deterministic/train_from/drop_groups/feature_hash/input_rows_sha256/training_cutoff_by_year を追加(既存キー不変)+ `scripts/tests/test_direct_return_model_spec.py`
- [X] T002 seed 1〜15 を plan 0.1 のコマンド(`--threads 1 --deterministic --tag _ens15 --save-last-model /ABS/…/seed_NN/model`)で学習(背景・約 2 時間・`nohup`・結果 dir は seed 1 のみ `_seed1` 無し)。所要時間を plan §6 に記録
- [X] T003 [P] `scripts/roi_explore/assemble_ens15_20261001.py`(新規): 15 本の spec.json の features/cats/cat_maps/feature_hash/input_rows_sha256 完全一致と `result.json` の seed/threads/deterministic を検証 → `ensemble.spec.json`・`ensemble_2019..2026.json`(相対パス+sha256)。不一致は fail-closed。`scripts/tests/test_assemble_ens15.py`
- [X] T004 [P] `eval/src/horseracing_eval/bootstrap.py`: `centered_one_sided_p_from_replicates(replicates, point)` を追加(同じ draw から p)、`_COUNTS_CACHE` を LRU(maxsize 8)に + `eval/tests/unit/test_bootstrap_centered_p.py`(帰無で一様・ROI=1 ちょうどで p≈0.5・seed 固定で再現・CI と p が同一 replicates・LRU 上限)
- [X] T004a [P] `eval/src/horseracing_eval/attention_rules.py`(新規・**定義部分だけ**・plan 0.3 の「定義」): `RULE_SET_VERSION`・`DISPLAYED_MARKET_EV_MODEL_VERSION`・`SINGLE_SEED_MODEL_VERSION`・`RuleDefinition`・`RULE_DEFINITIONS`(`definition_ja` 含む)・`DEFINITION_SEMANTICS`・`definitions_sha256()`・`match_mask`(唯一の実装)・`matches`(match_mask の 1 要素版)・`applicable_rules`・`field_digest`(training と API が共有)+ `eval/tests/unit/test_attention_rules.py` の定義部分(境界格子で `matches`≡`match_mask`・包含関係・`field_digest` が順序と重複に対して決定的・import 許可リスト(AST・`collections.abc` を含む)・禁止トークン不在)。**禁止トークンを書かない**(G2・D20)
- [X] T005 (実装時に変更: `scripts/roi_explore/evaluate.py` は過去の研究数値の再現のため据え置き、凍結スクリプトが eval を直接呼ぶ=plan 0.3)~~`evaluate.py` の `day_bootstrap`/`centered_pvalue` を eval 実装を呼ぶ wrapper に置換~~(block universe=賭けのある開催日・返り値 dict 互換)(**day_keys は race_date の ISO 文字列を渡す。factorize の整数コードを日付キーにしない**=来歴の `day_key_order: ascending` を守る・CF-4。wrapper と eval 直呼びの CI と p がビット一致するテストを付ける)+ `scripts/roi_explore/freeze_rules_S1_S5_20261001.py` を `ENS_RUNS`(`_ens15` 15 dir)と `SINGLE_RUN`(`serving_v2_2007`)に分け、**選定を `attention_rules.match_mask` に置換(スクリプトに閾値・帯を書かない)**、bootstrap/p を eval 経由に、plan 0.1 の来歴(入力 parquet の sha256/行数/日付範囲・15+1 予測の sha256/行数・script sha・git commit・numpy 版・bootstrap/pvalue メタ・`definitions_sha256`・`price_noise` と `price_noise_provenance`・**`calibration`・`calibration_spec`・`adoption_gate`・rule ごとの `selected_calibration`**・`all_horses`・丸め 6 桁・sort_keys)を JSON に記録(較正・来歴・採否条件・単 seed のキー名 `single`・価格ずれ試験の乱数の来歴の訂正と評価母集団の行数/行順 hash は研究版で実装済み=2026-10-02。残りは入力の分割と eval 化)
- [X] T006 再凍結の実行(T002・T003・T004・T004a・T005 後)→ **`adoption_gate.passed` を確認(false なら停止して利用者に判断を仰ぐ・D15)** → `specs/138-attention-conditions/evidence/rules_S1_S5_freeze.json` にコピーしてコミット → **spec の全数値**(凍結表・価格ずれ表・較正表・選ばれた馬の対比・注記の 106/111/72%・パネル例・レベル当てはめの注記「(S1・S2)」等)と出典行(seed/B/実装)を更新 → 研究値との差を `specs/138-attention-conditions/refreeze-diff.md` に記録 → plan §6 に採否を記録

**出口**: `ensemble_2026.json` の sha 検証が通る・evidence JSON がコミット済み・`adoption_gate.passed=true`・spec の数値が再凍結値・差が記録済み。

## Phase 2: eval レジストリの残り(plan 0.3)

- [X] T007 `eval/src/horseracing_eval/attention_rules.py` に追記: 前向き検証の規約(`SELECTION_POLICY_VERSION`・`PROSPECTIVE_START_DATE: date | None = None`・`LOCAL_TZ`・`OBSERVING_MIN`・`CHECKPOINTS`・`CHECKPOINT_ORDER`(pick_id でタイブレーク)・`CHECKPOINT_SETTLEMENT_LAG`・`day_key`・`Stage`・`Decision`・`PickClass`・`PickFacts`(`dead_heat`=レースの勝ち馬数 != 1)・`classify_pick`(排他的・優先順つき・日本時間)・`decide_checkpoint`(**関数の中で並べ替えて先頭 N を取り、sha・最後の pick・開催日リスト hash も返す**)・`stage_from`(記録を正・`checkpoint_pending`))、凍結統計(`FrozenStats`/`PriceNoise`/`SelectedCalibration`/`AttentionRule`・`RULES`=evidence JSON の値)、レベル(`backtest_level`/`price_noise_level`/`prospective_level`)、`chip_rule`(不通過・保留でない最上位 → 無ければ最上位の不通過/保留・主チップ S2 では副チップ false)、`chip_now`(現在値で `matches`)
- [X] T008 [P] `eval/tests/unit/test_attention_rules.py` に追記: `definitions_sha256()` が evidence JSON と一致・凍結統計との一致(小数 3 桁)・`classify_pick` の優先順と排他性(格子で Σ=件数)・開始日 None で全部 `before_start`・日本時間の日付境界・`decide_checkpoint` の通過/不通過/継続/保留・**同じ開催日の途中に N 点目の境界がある入力をシャッフルしても判定・sha・開催日リストが不変**・`day_key` の日本時間境界・同着(他馬同着で勝ち・勝ち馬 0 頭)の分類・`stage_from` が記録を正とする・`checkpoint_pending`・全該当 rule が failed でも `chip_rule` が None にならない・主チップ S2 で副チップ false・`chip_now` 3 値・レベル関数が spec の表どおり・pandas/training/features/db 非 import
- [X] T009 [P] `api/pyproject.toml` に `horseracing-eval` を dependencies と `[tool.uv.sources]` の両方に追加 + `uv lock`/`uv sync`

**出口**: eval テスト緑・features leak-guard(現行)緑。

## Phase 3: DB(plan 0.2・head 追随)+ leak-guard の拡張

- [X] T010 `db/src/horseracing_db/models/attention.py`(`AttentionPick`(`horse_number`・`rule_set_version`・部分 UNIQUE 2 本(pick 側は rule_set_version を含む)・CHECK 群・`ensemble_model_version`/`single_model_version`・`field_digest`)・`AttentionRaceScan`(主キー (race_id, rule_set_version)・`n_picks`)・`AttentionCheckpoint`(UNIQUE・decision×checkpoint の CHECK・bootstrap JSONB・`prospective_start_date`・`skipped_pending_before_last`))+ `models/__init__.py`
- [X] T011 `db/migrations/versions/0019_attention_picks.py`(`down_revision="0018_market_ev_predictions"`・3 表の create_table・部分 UNIQUE 2 本・index・共有関数 `reject_attention_mutation` + 各表に行/TRUNCATE トリガ・REVOKE TRUNCATE・downgrade 逆順)
- [X] T012 [P] `db/tests/integration/test_attention_picks.py`: pick 追記 OK・同 key の 2 件目 IntegrityError・rule_set_version が違えば同じ (race, horse, rule) を書ける・同 pick への void 2 件目 IntegrityError・scan の主キー重複・checkpoint の UNIQUE と CHECK・3 表とも UPDATE/DELETE/TRUNCATE が「append-only」で拒否・pick の CHECK(kind/void_reason/voids_pick_id・必須列・horse_number)・inspector で部分 index の unique と predicate・downgrade/upgrade 往復
- [X] T013 [P] head 固定テスト 10 本を 0019 に(features 9・live 1)+ `_TABLES_ADDED_AFTER_0012`(db/tests 2 ファイル)に 3 表 + `db/tests/integration/test_market_ev_predictions.py` の 0017 への downgrade 断言から `_TABLES_ADDED_AFTER_0018` を引く(CF-1)
- [X] T013a [P] `features/tests/unit/test_market_ev_leak_guard.py` の拡張(rev2 の T046 から移動・O1): `_FORBIDDEN` に 3 表の表名・クラス名と `horseracing_training.attention_picks`/`attention_checkpoints`、`_FORBIDDEN_COLUMN_TOKENS` に `attention`・`ens15`・`ens_expected_return`、3 モデルの sanity、特徴経路の閉包 assert(**ここ 1 か所だけ**)、eval `attention_rules` の版名の例外と db/training 非 import の明示 assert(A6)(SC-008)
- [X] T014 ローカル DB に `cd db && uv run alembic upgrade head` → `scripts/stack.sh restart api`(health の head キャッシュ)

**出口**: db・features(拡張した leak-guard を含む)・live テスト緑。

## Phase 4a: training(plan 0.4)

- [X] T015 `training/src/horseracing_training/market_ev.py`: `ENSEMBLE_LOGIC_VERSION`・`_row_values(..., logic_version=LOGIC_VERSION)`・`EnsembleMarketEvModel.load`/`booster_manifest_for_year`/`predict_ensemble`(manifest sha 照合・平均 p・同じ `target`/`invalid` フィルタ・race 集合一致 assert)・`compute_and_persist(..., ensemble_dir=None, ensemble_version=None)`(同一 run_id/computed_at・同一トランザクション・lock は日付昇順で単版→ens 版・summary `versions`/`picks`/`checkpoints`・両版空のときだけ skipped)・**`ensemble_only=True`(ens15 行・scan・pick だけ書き単 seed 行に触れない・lock も ens 版だけ・D24)**。単版経路はバイト不変
- [X] T016 `training/src/horseracing_training/attention_picks.py`(新規): 入力は同一実行のメモリ上の 2 版予測のみ。(1) void pass を `in_range_ids` 全レースで行い、**pick 馬の race_horses 行が存在し取消・除外のときだけ** void(scratched)(行が無ければ書かない・冪等・対象が同 key の pick であることを検証・`rule_set_version` で絞る・D26)(2) **`attention_race_scans` に `INSERT … ON CONFLICT DO NOTHING RETURNING` で scan 行を書き、行が返ったレースだけ**で `applicable_rules` の該当を pick として INSERT(`horse_number`・`run_id` は 2 版と同一・`n_picks` は INSERT 前に数える・`logic_version=ENSEMBLE_LOGIC_VERSION+";policy=v1"`・`rule_set_version`)(3) `--ensemble-dir` 無しでは触らず `picks=None`。`horseracing_eval.attention_rules` を import。betting leak-guard の禁止 10 語を書かない(H1・D16)
- [X] T016a `training/src/horseracing_training/attention_checkpoints.py`(新規): `evaluate_checkpoints(session, *, now, dry_run=False)`= 固定キーの advisory lock → rule ごとに pick(void 解決・現在の rule set の版)× race_results **だけ**を読み(race_horses は読まない)→ `classify_pick` で counted かつ `post_time <= now − 3 日` → 300(未記録・件数 ≥300)/600(300 が continue・300 の記録の集計開始日が現在と同じ・未記録・件数 ≥600)を `decide_checkpoint` で判定して `INSERT … ON CONFLICT DO NOTHING`(判断時オッズ精算・`counted_pick_ids_sha256`・`last_pick_id`・`settlement_cutoff`・`prospective_start_date`・`skipped_pending_before_last`・bootstrap メタ)。`compute_and_persist` は commit 後に**別トランザクション**で呼び、例外は捕まえて `summary.checkpoints.error` に(H3・D18)
- [X] T017 `training/src/horseracing_training/cli.py`: `market-ev` に `--ensemble-dir`(任意・絶対パス・`ensemble.spec.json` 必須・worktree 拒否)と `--ensemble-version`。`--ensemble-only`(`--ensemble-dir` 必須)。stdout 最終行は指定時のみ ` versions=2 picks=P checkpoints=ok|pending|error` を付加(`--ensemble-only` は `versions=ens`・error 時は直前に `attention-checkpoints: error=<型: 要約>` 行・単版の最終行は不変)。新 CLI `attention-checkpoints [--dry-run]`
- [X] T018 [P] `training/tests/unit/test_market_ev.py` 拡張: `ENSEMBLE_LOGIC_VERSION` の pin・manifest sha 不一致で fail-closed・平均 p・CLI の `--ensemble-dir` 検証・2 版 marker(`checkpoints=ok|pending|error` の 3 ケース・`--ensemble-only` の marker)・既存 5 本の最終行 pin は無変更で緑
- [X] T019 [P] `training/tests/integration/test_market_ev_persist.py` 拡張: 2 版が同一 run_id/computed_at・片方の失敗で両方 rollback(scan 行も残らない)・lock キーの順序・race 集合一致・`pending_only` との組合せ・**`--ensemble-only` では単 seed 行が 1 バイトも変わらない**
- [X] T020 [P] `training/tests/integration/test_attention_picks.py`(新規): scan 行を挿入できた実行だけが pick を書く(2 回目で追記されない・**初回 0 頭のレースで 2 回目に帯に入った馬も書かれない**・**初回 odds_unavailable → 次にオッズがそろった実行が初回**)・取消→void(scratched)・出走馬変更では void されず再 pick もない・**race_horses の horse_id を書き換えた(067 の re-key 相当)後の計算で void が書かれない**・同じ pick への void は 1 件・race_ok を外れたレースでも void pass が走る・確定済みレースは `result_pending_at_compute=false`・`days_since_last` と `horse_number` が特徴と一致・S1 該当時に S3/S4 も pick・pick と scan の `run_id` が 2 版と一致・`--ensemble-dir` 無しで scan/pick/void/checkpoint を触らない(閉包 assert は T013a に置き、ここには置かない)
- [X] T020a `scripts/roi_explore/parity_ens15_20261001.py`(新規・T002・T003・T015 後): 2025-01-01〜2026-09-30 で本番の特徴量ビルド + `predict_ensemble`(書き込みなし)と研究 15 run の平均 p̂ を (race_id, horse_id) で照合(合格=共通行の 99.99% 以上で |Δp| < 1e-9・不一致は理由つき列挙・研究のみ/本番のみの行を両方向に数え既知の理由以外 0 件・研究側で該当する (馬, rule) が本番でも 100% 該当)→ `specs/138-attention-conditions/evidence/parity_ens15.json` にコミット・plan §6 に記録(G1・D19)
- [X] T020b [P] `training/tests/integration/test_attention_checkpoints.py`(新規): 発走 3 日以内の pick を材料にしない・300 で continue のときだけ 600 を書く・2 回目は ON CONFLICT で書かない・判定の記録後に早い発走の結果を遅れて入れても記録は変わらない・判定の失敗が計算結果を巻き戻さない・`--dry-run` は書かない・`counted_pick_ids_sha256` の再現

**出口(4a)**: 各テスト緑・parity 合格(T020a)。

## Phase 4b: API(plan 0.5)

- [X] T021 `api/src/horseracing_api/market_ev.py`+`queries.py`+`routers/market_ev.py`: 表示版定数を eval から import(再宣言しない)・`market_ev_rows(session, race_id, *, model_version)`・「最新 computed_at」選択を削除。テスト追随: `_synth.seed_market_ev` の既定版を定数に・`== "mev-binary-v2"` 2 箇所・`test_newest_market_model_version_is_shown` → 「単 seed が新しくても ens15」
- [X] T022 `api/src/horseracing_api/queries.py`: `attention_scan_for_race`/`attention_picks_for_race`/`attention_picks_for_date`/`attention_tally_rows()`(rule の全 pick × 結果 × 現在の race_horses・void 解決・同着判定。日付では絞らない・**すべて `rule_set_version = RULE_SET_VERSION`**)/`attention_checkpoint_rows()`/`attention_memo_key()`(pick 行数・max computed_at・判定記録数・pick のあるレースの max(race_results.updated_at)・max(race_horses.updated_at))。SELECT のみ・`.add/.delete/.merge` 不使用・文字列定数に DML 語なし
- [X] T023 `api/src/horseracing_api/attention.py`(純関数): `build_attention`(scan 行で available・`judged`=pick 凍結値・**`current`=ens15 の最新行(単 seed は同じ run_id のときだけ・market-ev が available でなければ null)**・`chip_now`・`field_changed_after_pick` は `attention_rules.field_digest` で比較・`StageDetail`・全 void は applicable=[]・`field_changed_after_pick`・`chip_stage`・`levels`(`price_noise`)null 可)・`build_rule_summaries`(`classify_pick` で分類・精算 2 基準=凍結で段階判定・`stage_from(判定記録)`・`checkpoint_pending`(記録の集計開始日が現在と違う場合も)・区間の開催日は `day_key`・`decisions`・`counts`(排他的)と `flags` の分離・Σ reconciliation(分母は日付絞り込み前の pick 総数)・判断時鮮度帯・`odds_drift`・選ばれた馬の対比・eval bootstrap を seed/b 固定で・rule 単位メモ化)・`build_day_items`(chip_rule 非 null のみ・`has_results`・`chip_now`・発走順)
- [X] T024 `api/src/horseracing_api/schemas.py`: plan 0.5 のモデル(`EvSnapshot`(run_id 含む)・`StageDetail`・`AttentionHorse`・`AttentionResponse`・`CheckpointDecision`・`RuleSummary`・`AttentionRulesResponse`・`AttentionDayResponse`・`extra="forbid"`・既定値なし・勝率と p̂ なし・ASCII `Stage`)
- [X] T025 `api/src/horseracing_api/routers/attention.py`(3 route・422/404・`date` 不正は 422・typed empty)+ `app.py` に登録
- [X] T026 [P] `api/tests/unit/test_attention.py`: 分岐(not_computed/odds_unavailable/available・scan 行ありで 0 頭)・chip の段階(不通過のみの馬・主チップ S2)・表示源(`judged`/`current`)・**単 seed だけ後から再計算しても `current` のオッズ/時刻・`chip_now` が変わらず単 seed の現在値は null**・field_changed/odds_unavailable で `current=null`・`chip_now`・レベル・Σ reconciliation・`DISPLAYED_MARKET_EV_MODEL_VERSION` の非再宣言(AST)・no-write 境界が新モジュールでも緑
- [X] T027 [P] `api/tests/integration/test_attention_api.py`: 422/404・2 版共存で `/market-ev` が ens15 かつ単 seed の後追い再計算で変わらない・合成 pick と判定記録で段階遷移(研究中/観察中/判定待ち/通過/不通過/保留)・**判定記録の後に早い発走の結果を遅れて入れても段階が変わらない**・void/同着/未結果馬/発走後観測/集計開始前/集計外の件数・`flags` が Σ に入らない・**training が書いた pick の digest と API が同じ出走馬から再計算した digest が一致**・記録の集計開始日が現在と違えば `checkpoint_pending`・`/attention/day` の発走順・空日・不正 date・GET のみ・書き込みなし・メモ化キーの更新(新 pick・新判定・結果の再取込・オッズの再取込で再計算)
- [X] T028 api 起動 → `front/scripts/gen-types.sh`・`admin/scripts/gen-types.sh` → `openapi.json`(front/admin バイト一致)+ `schema.d.ts`。`front/src/api/openapi.test.ts` の path 一覧と `api/tests/integration/test_openapi_contract.py` の `_EXPECTED_PATHS` に 3 path

## Phase 4c: ops(plan 0.7)

- [X] T029 `ops/src/horseracing_ops/config.py` に `market_ev_ensemble_dir`(env `OPS_MARKET_EV_ENSEMBLE_DIR`・既定絶対パス)+ `runner._training_market_ev` の argv に `--ensemble-dir`(`--model-dir X` の直後)・audit summary に `ensemble_dir`
- [X] T030 [P] `ops/tests/integration/test_expected_return_flow.py`: argv 期待に 2 トークン・`test_config_defaults` に ensemble 既定・audit summary の `ensemble_dir`・最終行の `checkpoints=` が summary の result に載る。lanes/orphan/conftest ガードは不変

**出口(4a/4b/4c)**: 各パッケージのテスト緑。実 DB で `market-ev --date 2026-09-27 --model-dir … --ensemble-dir …` が 2 版+scan+pick を書き、`GET /market-ev` が ens15 行を返す。

## Phase 5: front(plan 0.6・T028 後)

- [X] T031 `front/src/lib/forbiddenPhrases.ts` に `ATTENTION_SCOPE`
- [X] T032 [P] `front/src/lib/attention.ts`(過去検証の表示語=基準と同じ内容(FR-006)・`ROI_BASIS_LABELS`(3 つ・FR-008)・`freshnessLevel`=現在値の最新行のオッズ取得時刻から・`emphasisLevel`=4 軸の最小値で `chipNow` が no_longer/unknown なら 1・表示語表 ASCII→日本語・`{checkpoint} 点通過/不通過`・「判定待ち」「判断時点のみ該当」「現在値なし」「最新の計算なし」)+ `attention.test.ts`(境界 10/60 分・発走後・時刻不明・最新の計算なし・最小値・chip_now・表示語)
- [X] T033 [P] `front/src/api/queries.ts` に `useAttention`/`useAttentionRules`/`useAttentionDay`、`types.ts`、`tests/fixtures.ts` の `happyHandlers`(unavailable/not_computed・5 rule fixture(判定記録・選ばれた馬の対比を含む)・空 day)。`RefreshButton` 2 箇所と `DayRefreshButton` の invalidate + spy テスト
- [X] T034 `front/src/components/HorseEntriesTable.tsx`: prop `attention`・チップ(主ラベル=ID・段階名(`StageDetail` の checkpoint で「300 点不通過」、pending で「観察中・判定待ち」)、failed/undecided は段階名が主ラベル・level 1 固定・S2 副チップは `chip_s2` のときだけ・閾値数値なし・S5 のみは出さない・「判断時点のみ該当」「現在値なし」を添える)・`entry--ev-over` は level 3 のみ・「120%超」チップと `evOverLabel` 削除・展開行に `AttentionPanel`
- [X] T035 [P] `front/src/components/AttentionPanel.tsx`(`useAttentionRules` 結合・4 軸・凍結値と bootstrap メタ・選ばれた馬の対比・前向き現況 2 基準と判定記録・`judged`/`current` を `PseudoValue` で「判断時点」「現在値」・バッジ「探索後固定」常時/「前向き未確認」通過まで・`field_changed_after_pick` の注記)+ `AttentionNote.tsx`(FR-011・判断時点の注記を含む)
- [X] T036 [P] `front/src/components/AttentionRulesPanel.tsx`(`<details>`・順位固定・不通過/保留も表示・判定記録・判定待ち・判断時鮮度帯別・`odds_drift`・`counts` と `flags`)+ `pages/AttentionPage.tsx`(`/attention`)+ `router.tsx`/ナビ
- [X] T037 [P] `front/src/components/AttentionDayList.tsx`(発走順・null 末尾「発走時刻不明」・該当なし・`StageDetail` の段階名・`chip_now` の印)+ `RaceListPage.tsx` に組込み
- [X] T038 `front/src/components/ExpectedReturnNote.tsx`: 検証要約を再凍結値(S3 と `all_horses`)に・系列変更の注記・「120%超の目印」文の削除(`ExpectedReturnNote.test.tsx` の期待文字列も同時更新)
- [X] T039 `front/src/pages/RaceDetailPage.tsx`: `useAttention` → `HorseEntriesTable`・`AttentionNote` を注記の直後・`AttentionRulesPanel` を table-hint の後
- [X] T040 `front/src/styles.css`: `.attn-chip--1/2/3/sub`・`.attn-level`・`.attn-badge`(損益色なし・`--ev-mark` 流用)
- [X] T041 [P] テスト: `HorseEntriesTable.test.tsx`(137 ブロックを新仕様に=チップ 1 つ・S2 副チップ・主チップ S2 で副チップなし・不通過のみの馬・判断時点のみ該当/現在値なしで最弱・level 3 のみ白枠(凍結値の API は level 3 を返さないので合成状態での意匠の回帰テストとして残す)・S5 のみはチップなし・包含関係・列数不変・「300 点不通過」「観察中・判定待ち」がチップと日付一覧に出る)・`AttentionPanel/AttentionNote/AttentionRulesPanel/AttentionDayList.test.tsx`・`RaceDetailPage/RaceListPage.test.tsx` のハンドラ。**禁止語テストの範囲は注目条件のコンポーネントが描く DOM だけ**(`.attn-chip`・各 Attention* のコンテナ。表・ページ全体には当てない=I5)で `ATTENTION_SCOPE`・`UNMEASURED_ODDS_DRIFT`・損益色セレクタ・範囲内 `[aria-label]` の `/印|推奨|おすすめ/` 無し。**回収率ラベルの不変テスト**(範囲内の `data-kind="roi"` ノードすべてに `ROI_BASIS_LABELS` のどれか 1 つ・折りたたみは展開してから・G3)
- [X] T042 `pnpm -C front test && typecheck && lint && check:openapi`、admin も `test` と `check:openapi`

**出口**: front/admin 緑。

## Phase 6: E2E・運用・記録(plan §1-6)

- [X] T043 埋め戻しを**先に**回す: `market-ev --from 2025-01-01 --to <投入日の前日> --model-dir … --ensemble-dir … --ensemble-only`(月ごと可・単 seed 行は 137 の値のまま・確定済み=pick は集計外・D24。稼働中の旧 API は ens15 行が混ざっても落ちない)→ 所要時間を plan §6 に記録
- [X] T043a `scripts/stack.sh restart worker && restart ops-api`(以後の新レースは ops が 2 版+scan+pick+チェックポイント判定で計算)→ `scripts/stack.sh restart api`(D8 の表示版切替。T043 より前に再起動しない=列が消える)→ 1 開催日の実行時間と `/attention-rules` 初回コストを **plan §6「運用実測」**に記録(SC-011・O3)
- [X] T044 ブラウザで 2026-09 と 2026-10 のレース詳細(チップ・展開パネル・注記)・`/attention`・日付ページを確認(SC-002/003/009/010)
- [X] T045 `PROSPECTIVE_START_DATE` を投入日に確定(T007 の None を置換・過去の日付にしない)→ **`scripts/stack.sh restart api`**(常駐プロセスは import 時の定数を持つ)→ `/attention-rules` の `prospective.start_date` が投入日であることを確認 → 翌開催日に scan と pick が `result_pending_at_compute=true` で書かれ、件数 Σ が合うことを確認
- [X] T046 全パッケージのテスト・ruff/tsc/eslint(leak-guard の拡張は T013a に移動済み)
- [X] T047 spec/plan/CLAUDE.md の状態更新(再凍結値・実測時間・レビュー採否)・memory 更新

## 依存関係

- Phase 1 → 2(レジストリの統計は evidence JSON を読む)→ 3 → 4a/4b/4c(並列)→ 5(T028 後)→ 6
- T005 は T004・T004a に、T006 は T002・T003・T004・T004a・T005 に依存
- T007 は T004a・T006 に依存(定義の上に規約と統計を足す)
- T013a は T010 に依存(sanity が ORM を参照する)
- T015/T016/T016a は T007 と T011 に、T020a は T002・T003・T015 に、T020b は T016a に依存
- T043a は T043 に(api の再起動は埋め戻しの後)、T045 は T043a・T044 に依存
- **T021〜T023 は T007 と T009 に依存**(api が eval を import する・O2)、T022 は T011 にも依存
- T034〜T041 は T028 に依存
- 並列可: T003/T004/T004a、T008/T009、T012/T013/T013a、T018/T019/T020/T020b、T026/T027、T030、T032/T033/T035/T036/T037/T041

## 実装メモ(落とし穴)

- api src の文字列定数(docstring 含む)に `insert `/`update `/`delete `/`create `/`drop `/`alter `/`truncate` を書かない。属性呼び出し `.add(`/`.delete(`/`.merge(` を使わない(no-write 境界テスト)
- eval/probability/features/serving/betting の src に `market_ev_predictions`/`MarketEvPrediction`/`horseracing_training.market_ev`/`attention_picks`/`AttentionPick`/`attention_race_scans`/`AttentionRaceScan`/`attention_checkpoints`/`AttentionCheckpoint` を書かない(leak-guard は文字列走査)
- training/src に betting の leak-guard 10 語を書かない。`attention_picks.py`・`attention_checkpoints.py` を特徴経路から import しない
- ops は training を import しない(subprocess 境界)。stdout の最終行だけが契約。単版の最終行は不変
- `agent-context` の更新スクリプトは実行しない(CLAUDE.md は Edit で手動更新)
- artifact は絶対パス・worktree 配下禁止。凍結 JSON・parity JSON は evidence にコミット
- 「最初の計算」は scan 行の挿入で決める。pick 行の有無で決めない(D16)
- 段階は判定記録が正。読み取り時の点数から通過/不通過を導き直さない(D18)
