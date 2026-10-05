# Implementation Plan: 138 注目条件の表示と前向き検証

**Branch**: `138-attention-conditions` | **Date**: 2026-10-01(rev2: codex plan レビュー + 解析 2 体を反映)/ 2026-10-02 rev3(`/speckit-analyze` 25 件と検証ワークフローの 25 論点を反映・§5 末尾と D15〜D26) | **Spec**: `spec.md`(rev3)

**前提の実測(2026-10-01)**: LightGBM の学習は seed とスレッド数を固定すれば 2 回の実行でビット一致した(1 スレッド決定論モードも 8 スレッドも、2025〜26 年 80,386 行で差 0)。スレッド数を変えた場合の一致は確認していないので、本番 15 本は **`--threads 1 --deterministic`** で固定する(1 seed の 17 年 walk-forward が約 7〜8 分、15 本で約 2 時間)。

## 0. 契約(全パッケージ共通・名前はこのとおりに実装する)

### 0.1 ens15 の学習と artifact

- `direct_return_model.py` の**追加のみ**の拡張: (a) `--save-last-model` の親ディレクトリを `mkdir(parents=True, exist_ok=True)`(現状は作らず 2019 年の保存で落ちる) (b) `model.spec.json` に `seed / rounds / num_threads / deterministic / train_from / drop_groups / feature_hash(sha256 of features+cats JSON) / input_rows_sha256 / training_cutoff_by_year` を追加(既存キー不変・137 の loader は未知キーを無視)。
- 学習コマンド(s=1..15):
  ```
  cd training && uv run python ../scripts/roi_explore/direct_return_model.py \
    --rows /ABS/artifacts/market_ev/rows_2007.parquet --objective binary --train-from 2007 --start-year 2010 \
    --drop-groups sameday,weightlive --threads 1 --deterministic --seed s --tag _ens15 \
    --save-last-model /ABS/artifacts/market_ev/mev-ens15-v1/seed_{s:02d}/model
  ```
  結果 dir(tag 規則: seed 1 は `_seed1` が付かない): seed 1 → `artifacts/roi_explore/results/armC_binary_drop-sameday+weightlive_from2007_ens15/`、seed s≥2 → `…/armC_binary_seed{s}_drop-sameday+weightlive_from2007_ens15/`。seed 1 も同条件で学習し直す(現行 `mev-binary-v2` は 8 スレッド fit で別物。`mev-binary-v2` は 137 の契約どおり不変で、S5 と単 seed 列の系列として残す)。
- レイアウト(gitignore・絶対パス):
  ```
  artifacts/market_ev/mev-ens15-v1/
    ensemble.spec.json      # version, objective, features, cats, cat_maps, seeds[1..15], rounds, num_threads, deterministic, train_from,
                            # feature_hash, input_rows_sha256, training_cutoff_by_year, members[seed_01..seed_15]
    ensemble_2019.json … ensemble_2026.json   # {"year":2026,"members":[{"seed":1,"path":"seed_01/model_2026.txt","sha256":"…"}, …×15]}
    seed_01/model.spec.json, seed_01/model_2019.txt … model_2026.txt   …   seed_15/…
  ```
  `scripts/roi_explore/assemble_ens15_20261001.py`(新規)が 15 本の spec.json の features/cats/cat_maps/feature_hash/input_rows_sha256 の**完全一致**と `result.json` の seed/threads/deterministic を検証してから書く(不一致は fail-closed)。
- **再凍結(SC-001)**: `freeze_rules_S1_S5_20261001.py` の入力を `ENS_RUNS`(上の 15 dir)と `SINGLE_RUN`(S5 用・parity 済み `armC_binary_drop-sameday+weightlive_serving_v2_2007` = `mev-binary-v2`)に分け、**選定は eval の `attention_rules.match_mask`(0.3・定義だけの部分を Phase 1 で先に作る)で行い**(スクリプト内に閾値・帯・境界を書かない=G2)、bootstrap と p 値は eval の実装(0.3)で計算して JSON を上書き。JSON には `input_rows{path, sha256, row_count, min_date, max_date}`・`ens_prediction_inputs[{seed, path, sha256, row_count}]`・`single_prediction_input{…}`・`freeze_script_sha256`・`git_commit`・`numpy_version`・`definitions_sha256`(= `attention_rules.definitions_sha256()`・レジストリとの一致をテスト)・`definition_semantics`・`bootstrap{impl: "horseracing_eval.bootstrap.race_block_ratio_bootstrap_ci_v1", b: 20000, seed: 20260905, block: "race_day", block_universe: "race-days with >=1 counted bet of the rule", rng: "numpy.default_rng(PCG64)", day_key_order: "ascending"}`・`pvalue{method: "centered_one_sided_ratio_bootstrap_v1", center: "pooled_roi_1", alternative: "greater", plus_one_correction: true, same_draws_as_ci: true}`・**`price_noise{sigmas: [0.1,0.2,0.3], reps: 10, seed0: 100}` と `price_noise_provenance{rng, perturbation, selection, settlement, years, overlap_with_unperturbed, aggregation}`**(G4・研究版で記録済み。2026-10-02 の検証で rng の記述が実装と違っていた=実際は 2010 年以降に絞った後の行順で引く → 訂正し、評価母集団の行数と行順 hash `evaluated_population{row_count, row_order_sha256}` も記録)・**`calibration{ens15|single: {ALL|C: {n, logloss, ece_equal_width, ece_equal_mass, mean_p, win_rate, reliability[{p_lo,p_hi,n,mean_p,win_rate}]}}}`・`calibration_spec{ece_bins: 10, reliability_edges, p_hat: "EV / odds", population}`・`adoption_gate{window: "ALL", logloss: "ens15 <= single", ece_equal_mass: "ens15 <= single + 0.001", ens15, single, passed}`・rule ごとの `selected_calibration{ALL|C: {n, mean_p_hat, win_rate, mean_ev, realized_roi}}`**(C1・D15・研究版で記録済み)・丸め(ROI/CI/p は小数 6 桁)・`all_horses{ens15_ev_gt_1.2: roi, single_ev_gt_1.2: roi, all: roi}`(注記用)・`json.dumps(sort_keys=True)`。**凍結 JSON は `specs/138-attention-conditions/evidence/rules_S1_S5_freeze.json` にコミット**し、eval のテストはそれを読む(artifacts/ は gitignore でクリーン checkout に無い)。研究 fit の暫定版は `evidence/rules_S1_S5_freeze_research_provisional.json`(2026-10-02・較正と来歴を追加して再実行・既存の凍結値は 0 件の差)。再凍結後に spec の全数値(表・較正表・選ばれた馬の対比・注記の 106/111/72%・パネル例・レベルの当てはめ注記)と出典行(seed/B/実装)を書き換え、研究値との差を `refreeze-diff.md` に記録。`adoption_gate.passed=false` なら Phase 2 以降に進まず利用者に判断を仰ぐ(D15)。
- **本番経路の parity(G1・Phase 4a の出口)**: `scripts/roi_explore/parity_ens15_20261001.py`(新規・137 の `parity_market_ev.py` と同型)が 2025-01-01〜2026-09-30 の各開催日について `market_ev` の特徴量ビルド + `EnsembleMarketEvModel.predict_ensemble` を書き込みなしで回し、研究 15 run(`_ens15`)の予測から作った平均 p̂(= EV ÷ オッズ)と (race_id, horse_id) で照合する。合格 = (a) 共通行の 99.99% 以上で |Δp| < 1e-9・不一致行を理由つきで列挙 (b) 研究のみ・本番のみの行を (race_id, horse_id) で両方向に数え、各行に理由(二重登録=137 と同型・研究スナップショット後の取込や ID 統合)を付け、既知の理由以外が 0 件 (c) 研究側で `match_mask` が真の (race_id, horse_id, rule) すべてについて本番経路にも行があり、該当判定が 100% 一致。共通行数・各差集合の件数・rule ごとの該当一致率を記録。結果を `specs/138-attention-conditions/evidence/parity_ens15.json` にコミット。単 seed(`mev-binary-v2`)は 137 の parity のまま。

### 0.2 DB: migration `0019_attention_picks`(`down_revision = "0018_market_ev_predictions"`・フル ID)

3 表とも append-only(0017 型トリガ)。

**`attention_race_scans`**(レースの最初の計算の記録・D16):

| 列 | 型 | 制約 |
|---|---|---|
| race_id | TEXT | PK の 1 列目, FK races.race_id |
| rule_set_version | TEXT | PK の 2 列目(= `attention_rules.RULE_SET_VERSION` = `attention-S1-S5-v1`) |
| run_id | UUID | NOT NULL(同じ実行の期待回収率 2 版・pick と同一) |
| computed_at | TIMESTAMPTZ | NOT NULL(同上) |
| post_time | TIMESTAMPTZ | NULL(計算時点の races.post_time) |
| result_pending_at_compute | BOOLEAN | NOT NULL(pick と同じ定義) |
| field_digest | TEXT | NOT NULL(`attention_rules.field_digest(started horse_id)`・0.3。training と API が同じ関数を呼ぶ) |
| n_picks | INTEGER | NOT NULL, CHECK (n_picks >= 0)(この実行で書いた `kind='pick'` 行の数=馬 × rule・0 も書く) |

**`attention_checkpoints`**(チェックポイント判定の記録・D18):

| 列 | 型 | 制約 |
|---|---|---|
| checkpoint_id | UUID | PK |
| rule_id | TEXT | NOT NULL, CHECK IN ('S1','S2','S3','S4','S5') |
| checkpoint | INTEGER | NOT NULL, CHECK IN (300, 600) |
| selection_policy_version / rule_set_version | TEXT | NOT NULL |
| decision | TEXT | NOT NULL, CHECK IN ('passed','failed','continue','undecided'), CHECK ((checkpoint = 300 AND decision <> 'undecided') OR (checkpoint = 600 AND decision <> 'continue')) |
| n_counted / n_hits | INTEGER | NOT NULL(n_counted = checkpoint) |
| roi_frozen / ci_low / ci_high | NUMERIC | roi_frozen は NOT NULL、ci_low/ci_high は NULL 可(開催日が 1 日しかなく区間を出せないとき=`ratio_ci` が None を返す。判定はそのとき continue/undecided)。判断時オッズ精算。p 値は列を持たず `bootstrap` JSONB には入れない(判定には区間だけを使う) |
| bootstrap | JSONB | NOT NULL({impl, b, seed, block, block_universe, rng, numpy_version}) |
| counted_pick_ids_sha256 | TEXT | NOT NULL(材料にした pick_id を CHECKPOINT_ORDER で並べて連結した sha256) |
| last_pick_id | UUID | NOT NULL, FK attention_picks.pick_id |
| settlement_cutoff | TIMESTAMPTZ | NOT NULL(= 判定時刻 − 3 日。post_time がこれ以前の pick だけを材料にする) |
| prospective_start_date | DATE | NOT NULL(判定に使った集計開始日。600 の判定は 300 の記録と同じ値でなければ fail-closed。API も現在の定数と照合し、違えばその記録を段階に使わず `checkpoint_pending` にする=CX-05) |
| skipped_pending_before_last | INTEGER | NOT NULL(材料の最後の pick より前に発走したのに結果未確定で材料にできなかった pick の数。材料は「判定時に結果がそろっていた先頭 N 点」であることの監査) |
| decided_at | TIMESTAMPTZ | NOT NULL DEFAULT now() |
| run_id | UUID | NULL(計算ジョブの run_id。CLI 起動なら新しい uuid) |

- UNIQUE `uq_attention_checkpoints_decision` (rule_id, checkpoint, selection_policy_version, rule_set_version)。selection_policy_version は pick の UNIQUE には入れない(集計方針は読み取り時の解釈で、v1 と v2 は同じ pick を共有する)。600 の判定は同じ key の 300 が `continue` のときだけ書く(行跨ぎなのでアプリで検証・テスト)。

**`attention_picks`**(1 レース 1 馬 1 rule 生涯 1 件):

| 列 | 型 | 制約 |
|---|---|---|
| pick_id | UUID | PK(アプリ生成 uuid4) |
| race_id | TEXT | NOT NULL, FK races.race_id |
| horse_id | TEXT | NOT NULL |
| horse_number | INTEGER | NULL, CHECK (kind<>'pick' OR horse_number IS NOT NULL)(計算時点の馬番・CHECKPOINT_ORDER に使う=A5。void 行は NULL) |
| rule_id | TEXT | NOT NULL, CHECK IN ('S1','S2','S3','S4','S5') |
| rule_set_version | TEXT | NOT NULL |
| kind | TEXT | NOT NULL, CHECK IN ('pick','void') |
| void_reason | TEXT | NULL, CHECK ((kind='void') = (void_reason IS NOT NULL)), CHECK (void_reason IS NULL OR void_reason IN ('scratched')) |
| voids_pick_id | UUID | NULL, FK attention_picks.pick_id, CHECK ((kind='void') = (voids_pick_id IS NOT NULL)) |
| ens_expected_return / single_expected_return / odds_used / odds_observed_at / days_since_last | NUMERIC×3 / TIMESTAMPTZ / NUMERIC | NULL 可、CHECK (kind<>'pick' OR (ens_expected_return IS NOT NULL AND odds_used >= 1.0 AND odds_observed_at IS NOT NULL)) |
| post_time | TIMESTAMPTZ | NULL(計算時点の races.post_time・集計はこの保存値を使う) |
| seconds_to_post | INTEGER | NULL(= post_time − computed_at・診断列) |
| result_pending_at_compute | BOOLEAN | NOT NULL(= 計算時点で race_results 無し かつ(post_time IS NULL または computed_at < post_time)) |
| field_digest | TEXT | NOT NULL(`attention_rules.field_digest(started horse_id)`・0.3。training と API が同じ関数を呼ぶ) |
| ensemble_model_version | TEXT | NOT NULL(`mev-ens15-v1`) |
| single_model_version | TEXT | NOT NULL(S5 判定に使った単 seed 版 `mev-binary-v2`) |
| logic_version | TEXT | NOT NULL(= `ENSEMBLE_LOGIC_VERSION + ";policy=v1"`) |
| run_id | UUID | NOT NULL(同じ実行で書いた期待回収率 2 版と同一) |
| selection_policy_version | TEXT | NOT NULL(= `v1`) |
| computed_at | TIMESTAMPTZ | NOT NULL DEFAULT now() |

- 部分 UNIQUE `uq_attention_picks_pick` ON (race_id, horse_id, rule_id, rule_set_version) WHERE kind='pick'(scan の主キー・checkpoint の UNIQUE と同じく rule set の版を含める。版を上げたとき旧版の pick と衝突して 2 版の書き込みごと rollback しないため=CX-02。読み取りはすべて `rule_set_version = RULE_SET_VERSION` で絞る)、`uq_attention_picks_void` ON (voids_pick_id) WHERE kind='void'(ORM `Index(..., unique=True, postgresql_where=text(...))` と migration `op.create_index(..., postgresql_where=sa.text(...))` の両方・0012 の形。db テストは `inspect(engine).get_indexes` で unique と predicate を検証)。index `ix_attention_picks_race` (race_id)、`ix_attention_picks_rule_computed` (rule_id, computed_at)。void の対象が同 key の `pick` 行であることは行跨ぎ CHECK で書けないのでアプリ(0.4)で検証。
- 0017 と同形の関数を 3 表で共有: `reject_attention_mutation()`(RAISE に `append-only` と `TG_TABLE_NAME`)・各表に `trg_<table>_reject_mutation`(BEFORE UPDATE OR DELETE FOR EACH ROW)と `trg_<table>_reject_truncate`(BEFORE TRUNCATE FOR EACH STATEMENT)・`REVOKE TRUNCATE … FROM PUBLIC`。downgrade は逆順(トリガ → 表は checkpoints → picks → scans の順で drop → 関数)。index `ix_attention_checkpoints_rule` (rule_id)。
- ORM `AttentionPick`・`AttentionRaceScan`・`AttentionCheckpoint`(`db/src/horseracing_db/models/attention.py`・`models/__init__.py` に import と `__all__`)。
- 追随: head 固定テスト 10 本(features 9: `test_feature020/021/023/040/066/084/086_leak_guard`・`test_purchase_leak_guard`・`test_materialize_fallback_columns`、live 1: `test_no_schema_change`)を `0019_` / `0019_attention_picks` に。`_TABLES_ADDED_AFTER_0012`(`db/tests/integration/test_chaos_tables.py`・`test_capture_provenance.py`)に 3 表。`db/tests/integration/test_market_ev_predictions.py::test_downgrade_to_0017_removes_the_table_and_upgrade_restores_it` は 0017 まで戻すと 3 表も消えるので `_TABLES_ADDED_AFTER_0018` を引く形に直す(CF-1)。`api` の `/health` は head 変更で 503 になるので migration 適用後に api 再起動(lru_cache)。

### 0.3 レジストリと統計(eval)

- `eval/src/horseracing_eval/attention_rules.py`(新規・純定数+dataclass+純関数。**import は `__future__`・`dataclasses`・`datetime`・`hashlib`・`json`・`math`・`typing`・`collections.abc`・`zoneinfo`・`numpy`・`horseracing_eval.bootstrap` だけ**(AST テストで固定=A6。`Sequence`/`Mapping`/`Iterable` は eval の ruff(UP035)に従い `collections.abc` から import する=CF-6。db・training・pandas・sqlalchemy を import しない)。**禁止トークン `market_ev_predictions` / `MarketEvPrediction` / `horseracing_training.market_ev` / `attention_picks` / `AttentionPick` / `attention_race_scans` / `AttentionRaceScan` / `attention_checkpoints` / `AttentionCheckpoint` を docstring 含め書かない**=features の leak-guard が eval/src を文字列走査する。`AttentionRule`/`attention_rules` は別語なので可。版の文字列 `mev-ens15-v1` を eval に置くのは表示版の単一定義のため(leak-guard のトークンは表名とモジュール名で、版名は対象外=D1・ガード側にこの例外を明示の assert として書く)。**Phase 1 で「定義」部分を先に作り(再凍結がそれを使う=G2)、Phase 2 で凍結統計を足す**:
  ```python
  # --- 定義(Phase 1・T004a) ---
  RULE_SET_VERSION = "attention-S1-S5-v1"
  DISPLAYED_MARKET_EV_MODEL_VERSION = "mev-ens15-v1"; SINGLE_SEED_MODEL_VERSION = "mev-binary-v2"
  @dataclass(frozen=True) class RuleDefinition: id, rank, definition_ja, uses_ensemble: bool, ev_gt: float, odds_band: tuple[float,float]|None, gap_days: tuple[int,int]|None, posthoc: bool
  RULE_DEFINITIONS: tuple[RuleDefinition, ...]          # S1..S5(rank 順)
  DEFINITION_SEMANTICS = "EV > t (strict); band: 20 <= odds < 40 and 14 <= days_since_last <= 112 (NaN gap -> not matched)"
  def definitions_sha256() -> str                        # RULE_DEFINITIONS と DEFINITION_SEMANTICS の正準 JSON の sha256(凍結 JSON に記録・一致をテスト)
  def match_mask(defn, *, ens_ev, single_ev, odds, days_since_last) -> np.ndarray   # ベクトル版=唯一の実装(再凍結が使う)
  def matches(defn, *, ens_ev, single_ev, odds, days_since_last) -> bool           # match_mask の 1 要素版(別実装しない)
  def applicable_rules(*, ens_ev, single_ev, odds, days_since_last) -> tuple[str, ...]   # 包含関係込み(S1 なら S3・S4 も)
  def field_digest(horse_ids: Iterable[str]) -> str   # sha256("\n".join(sorted(ids)).encode("utf-8")).hexdigest()。training(書き込み)と API(現在の出走馬との比較)が同じ関数を呼ぶ=CF-5

  # --- 前向き検証の規約(Phase 2・T007) ---
  SELECTION_POLICY_VERSION = "v1"
  PROSPECTIVE_START_DATE: datetime.date | None = None    # 本番投入日に確定(T045)。None の間は全 pick が before_start(I4)
  LOCAL_TZ = ZoneInfo("Asia/Tokyo")                      # computed_at の日付はこの TZ で判定
  def day_key(post_time: datetime.datetime) -> str      # post_time.astimezone(LOCAL_TZ).date().isoformat()=開催日クラスタの鍵(凍結の race_date と同じ単位)。チェックポイント判定と API の現況が共有=CF-7
  OBSERVING_MIN = 100; CHECKPOINTS = (300, 600); CHECKPOINT_ORDER = ("post_time", "race_id", "horse_number", "pick_id")
  CHECKPOINT_SETTLEMENT_LAG = datetime.timedelta(days=3)   # 判定の材料は post_time <= 判定時刻 − 3 日の pick だけ(D18)
  Stage = Literal["researching", "observing", "passed", "failed", "undecided"]        # API 値は ASCII。日本語は front の表示語表だけ
  Decision = Literal["passed", "failed", "continue", "undecided"]
  PickClass = Literal["voided_scratched", "before_start", "post_time_unknown", "computed_after_post", "result_known_at_compute",
                      "observed_after_post", "pending_result", "unsettled_horse", "dead_heat", "counted"]   # 左から順に最初に当たったもの(排他的)・どれにも当たらなければ counted
  @dataclass(frozen=True) class PickFacts: pick_id, kind, voided: bool, computed_at, post_time, odds_observed_at, result_pending_at_compute,
      has_race_result: bool, horse_has_result: bool, won: bool, dead_heat: bool, odds_used, stored_odds|None, horse_number, race_id
      # dead_heat = そのレースの勝ち馬数 != 1(レース単位・勝ち馬 0 頭を含む=凍結の `n_winners != 1` と同じ定義・CS-11)。stored_odds は API の参考基準だけで使い、分類と判定には使わない
  def classify_pick(f: PickFacts, *, start_date=PROSPECTIVE_START_DATE) -> PickClass   # API の現況とチェックポイント判定の唯一の分類
  def decide_checkpoint(counted: Iterable[PickFacts], checkpoint: int) -> tuple[Decision, dict]
      # 関数の中で CHECKPOINT_ORDER に並べ先頭 checkpoint 点を取り(並べ替えを呼び出し側に置かない=CS-07)、判断時オッズ精算(odds_used×100)の tally・
      # bootstrap(day_key(post_time)・block universe = 先頭 N 点の開催日・seed/b 固定)・counted_pick_ids_sha256・last_pick_id・開催日リストの hash を返す
  def stage_from(decisions: Mapping[int, Decision], n_counted: int) -> tuple[Stage, int|None, bool]   # (段階, 判定済みチェックポイント, checkpoint_pending)

  # --- 凍結統計とレベル(Phase 2・T007。値は evidence/rules_S1_S5_freeze.json と一致=テスト) ---
  @dataclass(frozen=True) class FrozenStats: roi, ci_low, ci_high, p_one_sided, n, hits(窓ごと ALL/C)
  @dataclass(frozen=True) class PriceNoise: sigma, roi, n, overlap
  @dataclass(frozen=True) class SelectedCalibration: n, mean_ev, realized_roi(窓ごと ALL/C。p̂ と勝率は evidence JSON にだけ置き、ここに持たない=憲法 IV)
  @dataclass(frozen=True) class AttentionRule: definition: RuleDefinition, backtest_all, backtest_c, bets_2024_25_26,
      price_noise: tuple[PriceNoise, ...], selected_all, selected_c
  RULES: tuple[AttentionRule, ...]
  def backtest_level(rule) -> int; def price_noise_level(rule) -> int; def prospective_level(stage, point_roi_frozen) -> int
  def chip_rule(applicable, stages) -> tuple[str|None, bool, Stage|None]
      # (不通過・保留でない最上位 → 無ければ最上位の不通過/保留, S2 副チップ = chip が S1 かつ S2 該当かつ S2 が不通過/保留でない, chip の段階)。S5 は除外
  def chip_now(defn, *, current_ens_ev, current_single_ev, current_odds, days_since_last) -> Literal["matches", "no_longer", "unknown"]
      # 現在値(最新行)で matches を呼ぶだけ。最新行が無ければ unknown(H2・D17)
  ```
  `backtest_level`/`price_noise_level` は spec の表の規則をそのまま関数に(価格ずれ軸の 3 は「σ=0.2 で >100% かつ優位の半分保持」=現状該当なし)。`classify_pick` の優先順は上の列挙順(void → 集計開始前 → 発走時刻不明 → 発走後の計算 → 計算時に結果確定済み(`result_pending_at_compute=false` の残り)→ 発走後のオッズ → 結果未確定 → 自馬の結果行なし → 同着 → 集計対象)で、`before_start` は `computed_at.astimezone(LOCAL_TZ).date() < start_date` または `start_date is None`。
- `eval/src/horseracing_eval/bootstrap.py` に追加(numpy のみ): `centered_one_sided_p_from_replicates(replicates, point) -> float`(払戻を pooled ROI=1 に再スケールしたときの `(1+#{ROI*_b ≥ roi_hat})/(B+1)` を、**同じ draw の replicates から**閉形で計算=CI と p が構造的に 1 回の抽選)。`block_bootstrap_counts` の `_COUNTS_CACHE` を **`maxsize=8` の LRU** に変える(現状は無制限で API 常駐プロセスのメモリが増え続ける。109 の再計算パリティは決定性だけが要件でキャッシュ常駐は不要)。区間は既存 `race_block_ratio_bootstrap_ci_v1(num, den, day_keys, block="race_day", b=20000, seed=20260905)`。**block universe = その rule の集計対象 pick が 1 点以上ある開催日(研究の `day_bootstrap` と同じ・ゼロ列を入れない)**。これは `bootstrap.py` の docstring(賭けの無い日もゼロ列として母集団に含む=109 の買い目パターン用)と逆の選択なので、呼び出し側(`attention_rules.decide_checkpoint` と freeze)で day_keys を集計対象のある日だけにして渡し、その旨を `block_universe` に記録する(D2・A7)。凍結スクリプトは eval の実装を直接呼ぶ(日付キーは race_date の ISO 文字列の昇順)。**実装時の変更(2026-10-02)**: 研究スクリプト `scripts/roi_explore/evaluate.py` の `day_bootstrap`/`centered_pvalue` は wrapper に置換せず据え置く。置換すると過去の研究スクリプト全部の乱数列が変わり、コミット済みの研究報告の数値が再現できなくなるため。画面に出す凍結値が製品の単一実装(eval)から出るという D2 の要件は、凍結スクリプトが eval を直接呼ぶことで満たす。
- `api/pyproject.toml` に `"horseracing-eval"` を dependencies と `[tool.uv.sources]`(`{ path = "../eval", editable = true }`)の両方に追加し `uv lock`。

### 0.4 training: 2 版の計算と pick の保存

- `market_ev.py`(追加のみ・単版経路はバイト不変・stdout の `OK:` 行も `--ensemble-dir` 無しでは不変):
  - `ENSEMBLE_LOGIC_VERSION = "mev-ens15-v1;seeds=1-15;threads=1;deterministic;features=roi-explore-2026-09;drop=sameday,weightlive;data>=2007"`。`_row_values(..., logic_version=LOGIC_VERSION)`。
  - `EnsembleMarketEvModel.load(ensemble_dir, version)`: `ensemble.spec.json` → `booster_manifest_for_year(year)`(`ensemble_{year}.json`、無ければ直前の年)→ 15 本を `_read_booster` し sha が manifest と一致することを検証(不一致は fail-closed)。`predict_ensemble(model, feats)`: `p = mean_s(bst_s.predict(X))`、`expected_return = p × odds`、`booster = ensemble_{year}.json`、`booster_sha256 = sha256(manifest bytes)`。**同じ `target` と同じ `race_ok`/`odds<1.0` フィルタ**を使い、書く前に `set(pred_single.race_id) == set(pred_ens.race_id)` を assert(fail-closed)。
  - `compute_and_persist(..., ensemble_dir=None, ensemble_version=None)`: 両方指定のとき同一 feats で 2 版を予測し、**同一 run_id/computed_at・同一トランザクション**で書く。advisory lock は `for day in sorted(days): lock(single, day); lock(ensemble, day)`(昇順固定=デッドロック回避)。summary に `versions: {version: {races, horses, boosters}}`、`picks: {races_first_computed, races_already_scanned, written, voided_scratched} | None`、`checkpoints: {written, pending, error} | None`。`status=skipped` は両版とも空のときだけ。
  - pick の保存(`attention_picks.py`・新規、`horseracing_eval.attention_rules` を import・**lock 取得後・commit 前の同一トランザクション内**): 入力は同じ実行のメモリ上の `pred_single`/`pred_ens`(同一 run_id/computed_at)だけで DB の予測行を読み直さない。
    1. **void pass は `in_range_ids` 全レース**(race_ok/invalid フィルタより前の集合・`raw` の出走状態を使う)で行う: 既存の `pick` 行(void なし・`rule_set_version = RULE_SET_VERSION`)のうち、**pick 馬の race_horses 行が同じ (race_id, horse_id) で存在し `entry_status` が `EntryStatus.NON_STARTERS`(出走取消・競走除外)**のもの → `void(scratched)` を追記(行が見つからない=067 の re-key などで ID が変わった場合は void を書かない。その pick は結果と結合できず `unsettled_horse` で集計外になる=AR-NEW-6・部分 UNIQUE で 1 件・既に void があれば書かない・対象が同 key の pick 行であることを検証)。**出走馬集合の変化は void にしない**(単勝 pick の精算は自馬の結果だけで決まる。読み取り時に `field_digest` との差を監査フラグとして出す=D11)。
    2. **最初の計算の判定(D16・H1)**: `pred_ens` に含まれる各レースについて `INSERT INTO attention_race_scans … ON CONFLICT (race_id, rule_set_version) DO NOTHING RETURNING race_id` を実行し、**行が返ったレースだけが「最初の計算」**。返らなかったレース(既に scan 行がある=2 回目以降)には pick を 1 件も書かない。「そのレースに pick 行が無い」は判定に使わない(最初の計算で該当 0 頭のレースに、後から帯に入った馬の pick が書かれてしまうため)。scan 行は同じトランザクションなので、計算が rollback されれば消え、次の実行が改めて「最初の計算」になる。
    3. 最初の計算のレースで、`applicable_rules(ens_ev, single_ev, odds, days_since_last)` に該当する (horse, rule) を `kind='pick'` で INSERT(`horse_number`・`result_pending_at_compute` は列の定義どおり・`post_time`・`seconds_to_post`・`field_digest`・`run_id` は 2 版・scan 行と同一・`ensemble_model_version`/`single_model_version`・`logic_version`・`selection_policy_version`・`rule_set_version`)。scan 行の `n_picks` は INSERT 前に数えて同じ文で書く(scan 行は更新できないため)。
    4. `--ensemble-dir` 無しの実行は scan/pick/void/checkpoint を一切触らず `summary.picks=None`・`summary.checkpoints=None`(ops の既定は常に両方渡す・テストで固定)。
  - **チェックポイント判定(`attention_checkpoints.py`・新規・D18・H3)**: `compute_and_persist` が 2 版+pick を commit した**後、別トランザクション**で `evaluate_checkpoints(session, now=computed_at)` を呼ぶ(失敗しても計算結果は残す=例外を捕まえ `summary.checkpoints.error` に記録し、CLI の最終行の `checkpoints=error` と直前の `attention-checkpoints: error=<型: 要約>` 行で ops の job summary に残す=CS-14/CF-11。API 側は「判定待ち」で可視化)。処理: `pg_advisory_xact_lock(<attention_checkpoints 固定キー>)` → rule ごとに **pick 行(void 解決済み・`rule_set_version = RULE_SET_VERSION`)× race_results(勝ち・同着・結果有無)だけ**を読み(race_horses は読まない=可変の表を判定の材料にしない・CX-07)、`attention_rules.classify_pick` で `counted` かつ `post_time <= now − CHECKPOINT_SETTLEMENT_LAG` の pick を集める → 300 の判定が無く件数 ≥ 300 なら `decide_checkpoint(集めた pick, 300)`(並べ替えと先頭 300 の切り出しは関数の中)を `INSERT … ON CONFLICT DO NOTHING`、300 が `continue` で 600 の判定が無く件数 ≥ 600 なら同様に 600(300 の記録の `prospective_start_date` が現在の定数と違えば fail-closed で書かない)。精算は判断時オッズ(`odds_used` × 100 円・D13)。`counted_pick_ids_sha256`・`last_pick_id`・`settlement_cutoff`・`prospective_start_date`・`skipped_pending_before_last`(材料の最後の pick より前に発走し `pending_result` に分類された pick の数)・bootstrap メタを記録。summary に `checkpoints: {written: [(rule, checkpoint, decision)], pending: [(rule, checkpoint)], error: str|None}`。CLI `attention-checkpoints [--dry-run]`(training・同じ関数・運用の再実行用)。training は既に eval に依存しているので新しい依存はない。
  - CLI `market-ev` に `--ensemble-dir ABS`(任意・`validate_model_dir` と同じ検査+`ensemble.spec.json` 必須)と `--ensemble-version`(既定 basename)。stdout 最終行は `--ensemble-dir` 指定時のみ ` versions=2 picks=P checkpoints=ok|pending|error` を付加(`OK: races=N horses=M from=D1 to=D2 versions=2 picks=P checkpoints=ok`。`--ensemble-only` では `versions=ens`)。単版の既存 5 本の最終行 pin は不変。
  - **`--ensemble-only`(埋め戻し専用・D24・CF-2/AR-NEW-1)**: `--ensemble-dir` と併用必須。同じ特徴量から 2 版を予測し、S5 の判定と pick の `single_expected_return` にはメモリ上の単 seed 値を使うが、`market_ev_predictions` には **ens15 行だけ**を置き換える(advisory lock も ens 版だけ・単 seed 行には触れない)。scan/pick の `run_id` は「この実行で書いた版の run_id」。137 が発走前のオッズで計算して `--pending-only` で守ってきた単 seed 行を、確定後の値で上書きしないための唯一の例外(D4 の「2 版を 1 回で書く」に対する例外として明記)。ops は使わない。
  - training/src に betting の leak-guard 10 語を書かない。`horseracing_training.attention_picks`・`horseracing_training.attention_checkpoints` は特徴経路(dataset/predictor/target_encoding/win_model)から import しない(leak-guard の閉包 assert を features の 1 か所に置く=O1)。

### 0.5 API(読み取り専用・純追加)

- `market_ev.py`: `from horseracing_eval.attention_rules import DISPLAYED_MARKET_EV_MODEL_VERSION`(再宣言しない・AST テスト)。`queries.market_ev_rows(session, race_id, *, model_version)` に引数化し `routers/market_ev.py` は表示版を渡す(「最新 computed_at の版」選択を廃止)。既存テストの追随: `api/tests/_synth.py::seed_market_ev` の既定版を表示版定数に、`== "mev-binary-v2"` の 2 断言を定数に、`test_newest_market_model_version_is_shown` を「単 seed が新しくても ens15 が出る」に置換。
- 新 `attention.py`(純関数・DB なし): `build_attention(race_id, scan, picks, ens_rows, single_rows, current_started, post_time, has_results, stages)`、`build_rule_summaries(registry, tally_rows, decisions, now)`、`build_day_items(...)`。表示源の規則(D17): `applicable`/`chip_rule`/パネルの**判断時点**の EV・`odds_used`・オッズ取得時刻は **void されていない pick 行の凍結値**、**現在値**(`current`)は**表示版(`mev-ens15-v1`)の最新行**の EV・`odds_used`・`odds_observed_at`・`computed_at`・`run_id`。単 seed の現在値 `single_expected_return` は、単 seed の最新行の run_id が ens15 の最新行と同じときだけ返し、違えば null(`--ensemble-dir` 無しの手動再計算で単 seed だけ新しくなった場合に、違う時点の値を 1 つの「現在値」に混ぜない=CX-03)。列(`/market-ev`)も表示版の最新行。137 の market-ev の状態が available でない(not_computed・field_changed・odds_unavailable)ときは `current=null`・`chip_now=unknown`・日付一覧の `current_odds_observed_at=null`(front の鮮度は「最新の計算なし」・CS-15)。`chip_now = attention_rules.chip_now(chip の定義, current の EV とオッズ, pick.days_since_last)`(S5 の判定には current の単 seed 値を使い、null なら unknown)。全 pick が void の馬は `applicable=[]`・`pick_status=void:scratched`。`field_changed_after_pick = (pick.field_digest != attention_rules.field_digest(現在の started horse_id))` を馬ごとに返す(training と同じ関数・監査フラグ・集計には影響しない)。pick の読み取りはすべて `rule_set_version = RULE_SET_VERSION` で絞る。`status="available"` は **scan 行があるとき**(`judged_at` = scan の computed_at。scan 行があって該当馬 0 頭なら `horses` は全馬 `applicable=[]`)。scan 行が無ければ 137 の market-ev の状態から `not_computed`/`odds_unavailable`。
- `queries.py` 追加: `attention_scan_for_race`、`attention_picks_for_race`、`attention_picks_for_date`、`attention_tally_rows()`(= rule の**全** pick 行(`rule_set_version = RULE_SET_VERSION`・日付で絞らない・分類は `classify_pick` が行う)× race_results(勝ち・同着・結果有無)× 現在の `race_horses`(odds・started)。void 解決済み)、`attention_checkpoint_rows()`、`attention_memo_key()`(下記)、`market_ev_rows(..., model_version=SINGLE_SEED_MODEL_VERSION)` を現在値に再利用。SELECT のみ・属性呼び出し `.add/.delete/.merge` を使わない・文字列定数に DML 語を書かない。
- `routers/attention.py`: `GET /races/{race_id}/attention`(422 `invalid_race_id` / 404 `race_not_found`)、`GET /attention-rules`、`GET /attention/day?date=`(必須・不正は 422 `validation_error`・該当なしは 200 `items=[]`)。`app.py` に登録。
- 応答(`schemas.py`・`extra="forbid"`・既定値なし・勝率なし):
  ```
  EvSnapshot { ens_expected_return: float|None, single_expected_return: float|None, odds: float|None, odds_observed_at: datetime|None, computed_at: datetime|None, run_id: UUID|None, is_pseudo: Literal[True] }
      # judged = pick の凍結値(2 版とも同じ実行)。current = ens15 の最新行(odds・時刻・run_id は ens15 行の値。single は同じ run_id のときだけ)
  StageDetail { stage: Stage, checkpoint: 300|600|None, checkpoint_pending: bool }      # 「300 点不通過」「観察中・判定待ち」を描くのに必要(CS-01)
  AttentionHorse { horse_id, horse_number, applicable: list[RuleId], chip_rule: RuleId|None, chip_stage: StageDetail|None, chip_s2: bool,
                   chip_now: "matches"|"no_longer"|"unknown"|None, judged: EvSnapshot|None, current: EvSnapshot|None, field_changed_after_pick: bool,
                   levels: {backtest:int, prospective:int, price_noise:int}|None, stages: {RuleId: StageDetail}, pick_status: {RuleId: "pick"|"void:scratched"|"none"} }
  AttentionResponse = { status:"available", race_id, post_time, has_results, judged_at, selection_policy_version, rule_set_version, horses }
                    | { status:"unavailable", race_id, reason: "not_computed"|"odds_unavailable" }
  CheckpointDecision { checkpoint: 300|600, decision: "passed"|"failed"|"continue"|"undecided", n_counted, n_hits, roi_frozen, ci: [lo, hi], decided_at, settlement_cutoff,
                       prospective_start_date, skipped_pending_before_last, counted_pick_ids_sha256, bootstrap: {impl, b, seed, block_universe} }
  RuleSummary { id, rank, definition_ja, uses_ensemble, ev_gt, odds_band, gap_days, posthoc,
                backtest: {all: FrozenStats, c: FrozenStats, bets_2024_25_26, selected: {all: {n, mean_ev, realized_roi}, c: {…}}, valuation_basis: "closing_odds_approx", bootstrap: {impl, b, seed, block, block_universe}},
                price_noise: [PriceNoise…], levels: {backtest, price_noise},
                prospective: { start_date: date|None, policy_version, stage, checkpoint: int|None, checkpoint_pending: bool, decisions: [CheckpointDecision…],
                               next_checkpoint: int|None, remaining_to_next: int|None, n_counted, n_hits, n_picks_total,
                               frozen: {valuation_basis:"frozen_pick_odds", roi, ci, p_one_sided}, stored: {valuation_basis:"stored_odds_mutable", roi, ci},
                               bootstrap: {impl, b, seed, block, block_universe, rng, numpy_version},
                               counts: {voided_scratched, before_start, post_time_unknown, computed_after_post, result_known_at_compute, observed_after_post, pending_result, unsettled_horse, dead_heat},   # 排他的
                               flags: {field_changed_after_pick},                                    # 排他的でない監査フラグ(集計対象にも数える)
                               by_judged_freshness: {"<=10m","<=60m",">60m": {n, hits, roi_frozen}},   # 判断時鮮度帯 = post_time − odds_observed_at(pick)
                               odds_drift: {n, median_log_ratio, p10, p90} } }                   # log(現在の保存オッズ / odds_used)・診断
  AttentionRulesResponse { items: list[RuleSummary](rank 順固定), disclaimer }
  AttentionDayResponse { date, items: [{race_id, post_time, has_results, venue_code, race_number, horse_id, horse_number, horse_name, chip_rule, chip_stage: StageDetail, chip_s2, chip_now, stages: {RuleId: StageDetail}, levels, current_odds_observed_at}] }
  ```
  `items` は chip_rule 非 null(S1〜S4・void なし)の馬のみ、`post_time asc nulls last → race_id → horse_number`。勝率・p̂ は返さない(選ばれた馬の較正も期待回収率の単位だけ・憲法 IV)。
- 現況の計算(読み取り時): 各 pick を `attention_rules.classify_pick`(集計開始日は日本時間の日付・未設定なら全部 `before_start`)で 1 つに分類し、`counted` を集計対象とする。**Σ reconciliation**: `n_counted + Σcounts == n_picks_total`(rule の `kind='pick'` 行の総数・日付で絞らない・void された pick を含む。API 統合テスト・047 と同型)。`flags` は Σ に入れない。精算 2 基準(凍結 `odds_used`=段階判定 / 保存オッズ=参考)。**段階 = `attention_rules.stage_from(判定記録, n_counted)`**(判定記録が正。記録の無い範囲だけ点数で researching/observing。件数がチェックポイントに達して記録が無ければ `checkpoint_pending=true`。記録の `prospective_start_date` が現在の定数と違えばその記録を使わず `checkpoint_pending=true`)。現況の区間は `day_key(post_time)` を開催日の鍵にする(判定と同じ関数)。**API プロセス内で rule ごとの tally をメモ化**し、レース詳細/日付の GET で bootstrap を再実行しない。メモのキー(I6)= `attention_memo_key()` が 1 回の集約クエリで返す (rule_id, pick 行数, max(attention_picks.computed_at), checkpoint 行数, pick のあるレースの max(race_results.updated_at), pick のあるレースの max(race_horses.updated_at), `PROSPECTIVE_START_DATE`)。`race_horses.updated_at` は TimestampMixin の DB トリガで更新されるので、オッズの再取込で参考基準の回収率と `odds_drift` も再計算される。初回コストを SC-011 と併せて実測(§6)。
- OpenAPI: api 起動 → `front/scripts/gen-types.sh` と `admin/scripts/gen-types.sh` → `openapi.json`(front/admin バイト一致)+ `schema.d.ts` 再生成。`front/src/api/openapi.test.ts` の path 一覧と `api/tests/integration/test_openapi_contract.py` の `_EXPECTED_PATHS` に 3 path を追加。

### 0.6 front

- `api/queries.ts`: `useAttention(raceId)`(key `["attention", raceId]`・retry false)、`useAttentionRules()`(`["attention-rules"]`)、`useAttentionDay(date)`(`["attention-day", date]`)。`RefreshButton` の 2 箇所(完了時・followup 完了時)で `["attention", raceId]` を、`DayRefreshButton` 完了時に `["attention-day", date]` を invalidate(spy テスト)。`api/types.ts` に型 alias。
- `lib/attention.ts`(純関数・単体テスト): `freshnessLevel(currentOddsObservedAt, postTime, hasResults, now) -> 1|2|3`(**現在値=表示版の最新行**のオッズ取得時刻で決める・null は 1=「最新の計算なし」・D17)、`emphasisLevel(levels, freshness, chipNow) -> 1|2|3`(4 軸の最小値。`chipNow` が `no_longer`/`unknown` なら 1)、表示語表(FR-006・過去検証 3=「通算の区間下限 100% 超・確認窓 110% 以上」2=「通算・確認窓とも 100% 超」1=「通算か確認窓が 100% 以下」(基準と同じ内容の語=CS-02)・ASCII stage(`StageDetail`)→ 日本語: researching=研究中 / observing=観察中(`checkpoint_pending` なら「観察中・判定待ち」)/ passed=「{checkpoint} 点通過」/ failed=「{checkpoint} 点不通過」/ undecided=判定保留、鮮度 3=「10 分以内」2=「60 分以内」1=「60 分超」「発走後」「発走時刻不明」「最新の計算なし」、chip_now: no_longer=「判断時点のみ該当」unknown=「現在値なし」)。鮮度は描画時刻で評価し自動更新しない(再取得で更新)。
- `HorseEntriesTable`: prop `attention?`。`ev-cell` 内にチップ `<span className="attn-chip attn-chip--{level}" role="note" aria-label="注目条件 {id}・{stage}">注目条件 S1・研究中</span>` + S2 副チップ `attn-chip--sub`「S2」(閾値の数値は出さない・S5 のみ該当は出さない・chip_stage が failed/undecided なら主ラベルを段階名にし level 1 固定・`chip_s2` は API が主チップ S2 のとき false で返す・`chip_now` が no_longer/unknown なら「判断時点のみ該当」「現在値なし」を添える)。行クラス `entry--ev-over` は **level===3 のときだけ**。137 の「120%超」チップと `evOverLabel` を削除(テスト `HorseEntriesTable.test.tsx` の 137 ブロックを新仕様に)。展開行に `AttentionPanel`(`useAttentionRules()` を結合して凍結値と現況を描く・4 軸・`judged`(「判断時点」ラベル・判断時刻=`judged_at`)と `current`(「現在値」ラベル)の ens/単 seed/オッズを `PseudoValue kind="expected_return"` で並べる・過去検証に「選ばれた馬の期待回収率の平均 X%(実際の回収率 Y%)」・前向きに判定記録(チェックポイント・判定日時)と「判定待ち」・S1/S2 のバッジ「探索後固定」(常時)と「前向き未確認」(stage が passed になるまで)・`field_changed_after_pick` なら「出走馬がその後変わっています」)。
- `AttentionNote`(新規・`ExpectedReturnNote` の直後・FR-011 文言)。`ExpectedReturnNote`: 検証要約を再凍結値(S3 の通算/確認窓・`all_horses.all`)に、「注目条件の投入日(YYYY-MM-DD)より前の単 seed 表示とは系列が異なり、以前の値と比較できません」を追加、「120%超の目印」の文を削除(テストの期待文字列も同時更新)。
- `AttentionRulesPanel`(`<details>`・RaceDetailPage の table-hint の後)+ ページ `/attention`(`router.tsx`・ナビ「注目条件」)。`AttentionDayList`(RaceListPage の QueryStateView の後・`post_time` 昇順・null は末尾「発走時刻不明」・該当なし表示)。
- `forbiddenPhrases.ts`: `ATTENTION_SCOPE = /妙味|危険|儲|edge|買うべき|買え|買い目|勝てる|おすすめ|お得|利益が出|狙い目|勝負|推奨/`。テスト(**範囲は注目条件のコンポーネントが描く DOM だけ**=`.attn-chip` 要素・`AttentionPanel`・`AttentionNote`・`AttentionRulesPanel`・`AttentionDayList` のコンテナ。`HorseEntriesTable` 全体やページ全体には当てない=既存の「…推奨ではありません」「買い目推奨」に当たる・I5): `ATTENTION_SCOPE`・`UNMEASURED_ODDS_DRIFT`・`${PROFIT_COLOUR_SELECTOR}, .up, .down`・範囲内の `[aria-label]` に `/印|推奨|おすすめ/` 無し。**回収率ラベルの不変テスト(G3)**: 計算基準のラベルを front の定数 `ROI_BASIS_LABELS` に凍結する(過去検証・選ばれた馬の実際の回収率・価格ずれ試験=「確定オッズ近似」/前向き(段階判定)=「判断時オッズ・近似」/前向き(参考)=「保存オッズ(参考)・近似」・AR-G3)。範囲内で `%` を含む回収率の数値ノード(`data-kind="roi"` を付ける)はすべて、同じ要素内にこの定数のどれか 1 つを持つ(fixture の全 rule・全基準を列挙して検証。折りたたみは展開してから検証=087 の教訓)。
- `styles.css`: `.attn-chip--3`(= 現 `.ev-chip` の塗りつぶし)・`--2`(枠線)・`--1`(文字のみ)・`--sub`、`.attn-level`(●○○ はテキスト「検証の進み具合」併記)、`.attn-badge`。
- MSW: `happyHandlers` に `/attention`(unavailable/not_computed)・`/attention-rules`(5 rule 分の fixture)・`/attention/day`(空)の既定ハンドラ。

### 0.7 ops

- `config.py`: `market_ev_ensemble_dir`(env `OPS_MARKET_EV_ENSEMBLE_DIR`、既定 `<repo>/artifacts/market_ev/mev-ens15-v1` 絶対パス)。`runner._training_market_ev`: argv の `--model-dir X` の直後に `--ensemble-dir CONFIG.market_ev_ensemble_dir`。audit summary に `ensemble_dir`。timeout 600 秒は据え置き(SC-011 で実測・超えるなら `_MARKET_EV_TIMEOUT_S` と `_DETACHED_CHILD_GRACE_S[JOB_TYPE_EXPECTED_RETURN]` を同時に 900 未満で調整)。job_type・enqueue・admin は不変。
- テスト: `test_launcher_argv_cwd_env_and_timeout` の argv 期待に 2 トークン追加、`test_config_defaults` に `_DEFAULT_MARKET_EV_ENSEMBLE_DIR.parts[-3:] == ("artifacts","market_ev","mev-ens15-v1")`、audit summary に `ensemble_dir` が載るテスト。

### 0.8 リーク境界・契約テスト

- `features/tests/unit/test_market_ev_leak_guard.py`(**Phase 3 で DB モデルと同時に拡張**=pick を書く Phase 4a より前・O1): `_FORBIDDEN` に `attention_picks`・`AttentionPick`・`attention_race_scans`・`AttentionRaceScan`・`attention_checkpoints`・`AttentionCheckpoint`・`horseracing_training.attention_picks`・`horseracing_training.attention_checkpoints`、`_FORBIDDEN_COLUMN_TOKENS` に `attention`・`ens15`・`ens_expected_return`。sanity に 3 モデルの `__tablename__`/`__name__`。特徴経路の閉包に `horseracing_training.attention_picks`/`attention_checkpoints` が含まれない assert(**閉包 assert はここ 1 か所だけ**・T020 には置かない)。eval の `attention_rules` について「版名 `mev-ens15-v1` を含むのは表示版の単一定義のため許容し、db/training/sqlalchemy を import しない」を明示の assert で書く(A6)。
- `eval/tests/unit/test_attention_rules.py`: 定義(`definitions_sha256()` が evidence JSON の `definitions_sha256` と一致=表示している過去成績が今の定義のものであることの保証・G2)・`matches` と `match_mask` が境界格子(オッズ 19.99/20/39.99/40・gap 13/14/112/113/NaN・EV 1.2 ちょうど/1.2+ε)で一致・凍結統計が evidence JSON と一致(小数 3 桁)・包含関係・`classify_pick` の優先順と排他性(全組み合わせの格子で Σ=件数)・集計開始日 None で全部 `before_start`・日本時間の日付境界(UTC 15:00 前後)・`decide_checkpoint` の通過/不通過/継続/保留・`stage_from` が記録を正とする(記録 failed なら点数が戻っても failed)と `checkpoint_pending`・順序を入れ替えた入力で結果不変(ソート内蔵)・全該当 rule が failed でも `chip_rule` が None にならない・主チップ S2 で副チップ false・`chip_now` 3 値・レベル関数・禁止トークン不在・import が許可リストだけ(AST・A6)。`test_bootstrap_centered_p.py`: 帰無で一様・同じ replicates から CI と p・LRU の上限。
- `api/tests`: 単体(`build_attention` の分岐・scan 行ありで 0 頭・chip の段階・`judged`/`current` の値源・`chip_now`・定数の単一定義・no-write 境界)・統合(422/404/typed empty・2 版共存で列が ens15 かつ単 seed の後追い再計算で変わらない・合成 pick と判定記録で段階遷移・**判定記録の後に早い発走の結果を遅れて入れても段階が変わらない(H3)**・void/同着/未結果馬/集計開始前の除外・Σ reconciliation(`flags` は Σ に入れない)・`/attention/day` の発走順と空日と不正 date・GET のみ・書き込みなし・メモ化キーの更新(新 pick・新判定・結果の再取込・**オッズの再取込**で再計算・I6))。
- `front`: 各コンポーネントのテスト・`lib/attention.test.ts`(鮮度は現在値から・`chip_now` で最弱)・`openapi.test.ts`・invalidate spy・禁止語と回収率ラベルの不変テスト(範囲は注目条件のコンポーネント・I5/G3)。
- `training/tests`: 2 版同一 run/計算時刻・片方の失敗で両方 rollback(scan 行も消える)・lock 昇順両版・race 集合一致 assert・**scan 行を挿入できた実行だけが pick を書く(初回 0 頭 → 2 回目に帯に入った馬も書かない・初回 odds_unavailable → 次にオッズがそろった実行が初回)**・pick 生涯 1 件・scratched void(field 変化は void しない)・`--ensemble-dir` 無しで scan/pick/checkpoint を触らない・pick と scan の run_id = 2 版の run_id・`horse_number` の保存・CLI marker(単版は不変・2 版で `versions=2 picks=P`)・`ENSEMBLE_LOGIC_VERSION` の pin・**チェックポイント判定**(発走 3 日以内の pick を材料にしない・300 で continue のときだけ 600 を書く・2 回目は ON CONFLICT で書かない・判定の失敗が計算結果を巻き戻さない・`attention-checkpoints --dry-run` は書かない)。
- `db/tests/integration/test_attention_picks.py`: 3 表のトリガ(UPDATE/DELETE/TRUNCATE を「append-only」で拒否)・部分 UNIQUE 2 本(inspector で predicate 検証)・scan の主キー・checkpoint の UNIQUE と decision×checkpoint の CHECK・pick の CHECK(horse_number 必須)・downgrade/upgrade 往復。

## 1. 実装順(各段の出口条件つき)

1. **学習と凍結(0.1・0.3 の定義と bootstrap)** — `direct_return_model.py` 拡張 → 15 seed 学習(約 2 時間・背景・`nohup`)→ assemble → eval bootstrap 追加と**レジストリの定義部分**(0.3 の「定義」・`match_mask`・`definitions_sha256`)→ `freeze_rules_S1_S5_20261001.py` 改修(選定は `match_mask`・較正と来歴を記録)と再凍結 → evidence にコミット → spec の全数値・出典行・`refreeze-diff.md` を更新。出口: `ensemble_2026.json` の sha 検証が通る・evidence JSON が存在・`adoption_gate.passed=true`(false なら停止して利用者に判断を仰ぐ=D15)・差が記録済み。
2. **eval レジストリの残り(0.3 の規約・統計・レベル)** — 出口: eval テスト緑・features leak-guard(現行)緑。
3. **DB(0.2・head 追随)+ leak-guard の拡張(0.8 の 1 項目目)** — 出口: db/features/live テスト緑(拡張した leak-guard を含む)・ローカル DB に `alembic upgrade head`・api 再起動。
4. **training(0.4)/ API(0.5)/ ops(0.7)を並列** — 出口: 各テスト緑。実 DB で `market-ev --date <確定済みの開催日> --model-dir … --ensemble-dir … --ensemble-only` が ens15 行+scan+pick を書き、単 seed 行は変わらない(確定済みの日に通常モードを `--pending-only` 無しで回すと 137 の発走前の値を上書きするので使わない=検証 F6)。**本番経路の parity(0.1・`parity_ens15_20261001.py`)が合格し evidence にコミット**(G1)。
5. **front(0.6・OpenAPI 再生成後)** — 出口: front/admin テスト・tsc・lint・check:openapi 緑。
6. **E2E と運用** — ens15 の埋め戻しは **2025-01-01〜投入日の前日**(137 が `mev-binary-v2` で埋め戻した 5,809 レースと、2026-10-01 以降に 137 の単版ジョブだけで確定したレースに ens15 行が無いと、表示版固定で列が消えるため)。**`--ensemble-only` で回す**(単 seed 行は 137 の値のまま・確定済みなので pick は集計外・D24)。**順序(実装時に確定・2026-10-02)**: ① 埋め戻しを先に回す(稼働中の旧 API は「最新 computed_at → 版名の降順」で版を選ぶので、ens15 行が混ざっても落ちない=旧コードを確認済み)→ ② worker と ops-api を再起動(以後の新レースは ops が 2 版+scan+pick で計算)→ ③ api を再起動(D8: 表示版の定数に切替。①より前に再起動すると列が全部「未計算」になる=検証 R4)→ ブラウザ確認 → `PROSPECTIVE_START_DATE` を投入日に確定 → **api 再起動**(常駐プロセスは import 時の定数を持つ=CX-05)→ `/attention-rules` の `prospective.start_date` を確認。

## 2. 運用手順(quickstart)

1. 学習: 0.1 のコマンドを s=1..15 で順に → `assemble_ens15_20261001.py` → `freeze_rules_S1_S5_20261001.py`(`adoption_gate.passed` を確認)→ evidence にコピー。
2. `cd db && uv run alembic upgrade head` → `scripts/stack.sh restart api`。
3. `scripts/stack.sh restart worker && scripts/stack.sh restart ops-api` → 以後はレース更新で自動計算(2 版+scan+pick+チェックポイント判定)。
4. 埋め戻し: `cd training && uv run python -m horseracing_training market-ev --from 2025-01-01 --to <投入日の前日> --model-dir /ABS/artifacts/market_ev/mev-binary-v2 --ensemble-dir /ABS/artifacts/market_ev/mev-ens15-v1 --ensemble-only`(月ごとに分けてよい・単 seed 行は変えない)。`PROSPECTIVE_START_DATE` を確定したら `scripts/stack.sh restart api`。
5. 前向き現況は `/attention` ページで確認。チェックポイント判定は計算ジョブの最後に自動で走る(失敗・判定待ちが続くときは `cd training && uv run python -m horseracing_training attention-checkpoints --dry-run` で確認してから `--dry-run` 無しで再実行)。閾値・定義・方針は触らない(変えるなら新 ID / 新版)。

## 3. 決定記録

| # | 決定 | 根拠 |
|---|---|---|
| D1 | レジストリは eval に置く(`attention_rules.py`) | training と api の共通依存は {db, eval, probability}。eval には 066 の `dispersion_bands` という「凍結表示定数を api が読む」前例があり、bootstrap も eval にある。api は eval を既に直接 import している(chaos/dispersion) |
| D2 | 区間と p 値は eval の `race_block_ratio_bootstrap_ci_v1` + 同じ replicates から出す `centered_one_sided_p_from_replicates` に一本化し、研究スクリプトもそれを呼ぶ。block universe は「集計対象が 1 点以上ある開催日」 | 研究の `day_bootstrap` は multinomial の別乱数列で同 seed でもビット一致しない。製品が再現できない数値を凍結しない。CI と p を同じ抽選から出せば構造的に 1 回の draw。**block universe は `bootstrap.py` の docstring(賭けの無い日もゼロ列で含む=109 の買い目パターン用)と逆**で、研究の `day_bootstrap` と同じ選択を保つため。呼び出し側で day_keys を絞って渡し `block_universe` に記録する(A7) |
| D3 | ens15 は 1 スレッド決定論で学習し、seed 1 も作り直す | 2 回実行のビット一致を実測。`mev-binary-v2` は 8 スレッド fit で ens15 の seed 1 と別物 → 137 の契約は不変のまま、S5 と単 seed 列の系列として残す(再凍結の S5 入力も `mev-binary-v2` 系列の run) |
| D4 | 2 版は 1 回の CLI 実行・同一 feats・同一 run_id/トランザクション・lock 昇順両版 | 別 job にすると特徴量ビルド 40 秒 × 2 と job の二重化、`followup_job_id` が 1 つしか追えない。ops の job_type を増やさない |
| D5 | pick は生涯 1 件・void 後に撮り直さない | 086 の analyze 結論(多点捕捉=オッズ履歴=憲法 V) |
| D6 | `field_digest` を pick に保存する | 読み取り時の監査フラグ `field_changed_after_pick` の材料 |
| D7 | 前向き現況は読み取り時計算・API 側で eval を呼ぶ・rule 単位でメモ化・eval のキャッシュは LRU | FR-005。常駐プロセスで無制限キャッシュが増え続ける(解析 HIGH)ため上限と API 側メモ化 |
| D8 | `GET /market-ev` の版選択は定数で固定 | 現行の「最新 computed_at」選択は 2 版共存で列が黙って入れ替わる |
| D9 | 「120%超」チップは廃止し注目条件チップに統合・閾値の数値はチップに出さない | 列が ens15 になると `exceeds_threshold` は S3 の意味になり二重表示。「130% 超」は期待値シグナル(codex)。**帰結(2026-10-02 の検証で判明)**: 価格ずれ試験の軸は凍結値で全条件 2 以下(S2 でも優位 29.2 点が σ=0.2 で 7.6 点)なので強調レベル 3 には到達せず、137 の白枠・太い左帯・塗りつぶしチップは出荷後に表示されない=137 のユーザー決定の実質的な変更。発火条件を変えるかは利用者の判断(AR-NEW-3) |
| D10 | 価格鮮度は front で描画時に計算、他 3 軸は API | 鮮度だけが時刻依存。閾値・段階の正本を API(eval レジストリ)に置き三重定義を避ける |
| D11 | 出走馬集合の変化は void にせず監査フラグ(`field_changed_after_pick`)にする。void は `scratched` のみ | 単勝 pick の精算は自馬の結果だけで決まる。086 の荒れ度(全馬依存)とは違う。spec rev2 の「field_changed void」と「pick 馬が出走していれば精算」の矛盾を解消(spec も修正) |
| D12 | pick はレースの**最初の計算**でのみ作る(以後の計算で新たに該当した馬も書かない) | 「発走前の最初の判断」を馬単位でなくレース単位に固定。後から帯に入る馬ほど鮮度が高いという系統的偏りを避ける。**「最初の計算」の判定方法は D16 で置き換え**(rev2 の「pick 行が 1 件も無いとき」は初回 0 頭のレースで破れていた) |
| D13 | 前向き精算は凍結(`odds_used`)で段階判定・保存オッズは参考 | 保存オッズは再取込で変わりうる(2025 年以降は非確定値も混在)。append-only の値だけで段階が決まるようにする(codex) |
| D14 | ens15 の埋め戻し範囲は 2025-01-01〜**投入日の前日**(rev3 で終端を変更) | 表示版固定で ens15 行の無いレースは列が消える(解析 MEDIUM)。2026-10-01 から投入までの開催日は 137 の単版ジョブだけで確定し ops は再計算しない(CF-2) |
| D15 | 表示モデルの切替(単 seed → ens15)を採用とみなし、再凍結の値で「全馬 LogLoss ens15 ≤ 単 seed」かつ「ECE(等質量 10 区分)ens15 ≤ 単 seed + 0.001」(2010〜26)を事前登録の採否条件にする。満たさなければ停止して利用者に判断を仰ぐ。選ばれた馬の過大評価は期待回収率の単位で開示 | 憲法 III(採用時のベースライン比較+ECE)。137 は ECE 0.0012 と選ばれた馬の過大評価を記録していた。15 本の平均は確率の分布を変えるので較正は自明に引き継げない(analyze C1)。研究 fit の暫定値は ens15 0.201204/0.00066 vs 単 seed 0.201584/0.00111 で満たす。許容 0.001 は 020 以来の平均 ECE の許容と同じ。勝率の単位で出すと憲法 IV の「勝率を表示しない」と衝突する |
| D16 | 「最初の計算」は append-only の `attention_race_scans`((race_id, rule_set_version) 主キー)への `INSERT … ON CONFLICT DO NOTHING RETURNING` が行を返したかで決める。該当 0 頭でも scan 行を書く | rev2 の「そのレースに pick 行が無いとき」は、最初の計算で該当 0 頭だったレース(S5 で年 ~600 点に対し ~3,400 レース=大半)で 2 回目以降の計算が pick を書き、D12 が避けた鮮度の偏りが戻る(analyze H1)。`mev-ens15-v1` 行の有無で判定する案は、その表が「最新のみ・レース単位置換」で寿命が別契約なので不採用。同一トランザクションなので rollback と整合し、advisory lock と UNIQUE の二重で競合にも安全 |
| D17 | チップは「判断時点」の表示(pick から決め、付け外ししない)。価格鮮度は**表示版の最新行**の `odds_observed_at` で決め、チップの条件を現在値で確かめて(`chip_now`)満たさない/現在値なしなら強調を最弱にする。後から条件に入った馬にはチップを付けない(注記で説明) | pick の時刻で鮮度を決めると、最初の計算は発走のかなり前なので強調がほぼ常に最弱になる。最新行の時刻だけで決めると、凍結した該当と現在の鮮度が食い違う(analyze H2)。現在値の確認は eval の同じ `matches` を呼ぶだけなので定義の二重化は起きない。後から入った馬にチップを付けると前向き集計の対象と画面の印が食い違う |
| D18 | チェックポイント判定を append-only の `attention_checkpoints` に記録し、記録を正とする。判定は training(計算ジョブの最後・別トランザクション・失敗は計算を巻き戻さない/CLI `attention-checkpoints`)が書き、材料は発走から 3 日以上たった集計対象 pick だけ | 読み取り時に毎回導くと、結果の遅れた取込・同着訂正・再取込(`race_results` は TimestampMixin で後から更新されうる)で 300 点目が入れ替わり、通過/不通過が反転しうる=「ラチェット」が保存なしでは成立しない(analyze H3)。「結果が入った順」に並べる案は再取込で created_at が変わるので不採用。API は read-only のままにするため書き手は training。3 日は当夜取込+翌日の通過順取込(memory: corner-orders-late-publication)より長く、判定の遅れは最大でも数日 |
| D19 | 本番の計算経路(`predict_ensemble`)と研究 15 run の平均 p̂ を 2025〜26 の同じ行で照合し、合格を Phase 4a の出口にする | 凍結表は研究の予測から、画面の判定は本番の計算から来る。両者の一致が無いと表示している過去成績が本番の選定のものだと言えない(analyze G1)。137 の parity(80,384/80,386 行)と同型 |
| D20 | 条件の定義をレジストリの「定義」部分に先に置き、再凍結はその `match_mask` で選定する。凍結 JSON に `definitions_sha256` を残し、レジストリと一致をテストする | 定義が freeze スクリプトとレジストリの 2 か所にあると、境界(`<`/`≤`・NaN)のずれが検出できない(analyze G2)。再凍結がレジストリを使い hash で結べば、二重実装が構造的に消える(spec の「bootstrap の二重実装禁止」と同じ扱い) |
| D21 | 集計対象の分類を eval の `classify_pick` 1 関数にし(排他的・優先順つき)、API の現況とチェックポイント判定の両方が使う。集計開始日は None で始め、日付は日本時間で判定。「遡及しない」は集計対象に対する約束 | 埋め戻しで確定済みレースの pick は書かれるので「pick が無い」は成り立たない。分類が 2 か所にあると判定記録と現況の数字が食い違う。`computed_at::date` は TZ 依存(analyze I4) |
| D22 | API のメモ化キーに pick のあるレースの max(`race_horses.updated_at`)・max(`race_results.updated_at`)・判定記録の行数を含める | オッズの再取込で参考基準の回収率と `odds_drift` が古いまま返る(analyze I6)。`updated_at` は TimestampMixin の DB トリガで更新される |
| D23 | 集計の内訳は「判断時鮮度帯」(発走時刻 − pick のオッズ取得時刻)と呼び、描画時の「価格鮮度」と分ける。応答のレベルは `price_noise`(価格ずれ試験)と呼ぶ | 同じ語が 2 つの量を指していた(analyze A2・A3) |
| D24 | 埋め戻しは `--ensemble-only`(ens15 行・scan・pick だけを書き、単 seed 行には触れない)で行う。D4 の「2 版を 1 回で書く」の唯一の例外 | 2 版を同時に書く通常モードを `--pending-only` 無しで確定済みレースに回すと、137 が発走前のオッズで計算して守ってきた単 seed 行が確定後の値で上書きされる(137 の契約違反)。範囲を 9/30 で止めると 10/1〜投入日の列が恒久的に消える。両方を避ける唯一の形(CF-2・AR-NEW-1) |
| D25 | rule set の版を pick の部分 UNIQUE にも含め、読み取りはすべて現在の版で絞る。集計方針の版は UNIQUE に入れない | scan の主キーと checkpoint の UNIQUE だけが版を含むと、版を上げた最初の計算で旧版の pick と衝突し、2 版の書き込みごと rollback して以後の計算ジョブが毎回失敗する(CX-02・CS-03・CF-10)。集計方針は読み取り時の解釈なので v1/v2 が同じ pick を共有する |
| D26 | void は pick 馬の race_horses 行が存在し出走取消・競走除外のときだけ書く。行が無い(067 の re-key 等)ときは書かない | 「started 集合に無い」で void にすると、ID の付け替えで実際に走った馬に取り消せない void が付く(AR-NEW-6)。行が無い pick は結果と結合できず `unsettled_horse` で集計外になるので、集計の正しさは保たれる。限界として spec に明記 |
| D27 | **集計方針 v2(139・2026-10-04)**: 段階判定と前向き現況の主指標を公式単勝払戻(`official_win_payouts`・migration 0020)で精算する。開始日 2026-10-05(`PROSPECTIVE_START_DATE`。v1 の開始日 2026-10-02 は `V1_START_DATE` に残す)。v1(判断時オッズ精算)は参考として並記し書き換えない。公式払戻が 1 件も無いレース(`payout_race_missing`)と払戻が結果と食い違うレース(`payout_inconsistent`)は**レース単位**で集計外にする。詳細は `specs/139-official-payout-settlement/plan.md` D4・D11・D12 | 単勝はパリミュチュエルで払われるのは確定オッズ。判断時オッズの精算は受け取れない額で、帯により払戻/判断時オッズが 0.95〜1.35 に偏る(R05)。勝ち馬だけを除外すると回収率が下に偏るので除外はレース単位 |
| D28 | **前向き成績の比較基準は凍結表ではなく「判断時点の見込み」(139 `buy-time-v2`)**: 凍結表(S1 121%・S3 106%)は締切オッズで選んで締切オッズで精算した値で、判断時点のオッズで選ぶと条件に入る馬が入れ替わる(締切時にも入るのは S1 11%・S3 20%)。独立検証(`specs/139-official-payout-settlement/evidence/r02_verification.md`)を通った換算値を 2 推定量の範囲(5% 丸め)と区間で登録し、S2 は単独の値を出さない(S1 に含まれる) | R02(`docs/roi-missed-patterns-20261004/report.md`)。前向きの公式払戻成績が溜まればこの換算値ではなく実測で読む |

## 4. Constitution Check

- **I**: ens15 は 2007 年以降のみで学習(`--train-from 2007`)。凍結統計は 2010 年以降。PASS。
- **II**: 市場連動モデルは本番勝率モデル系列の外。ens15 出力と pick はどの特徴量にも入らない(leak-guard 拡張)。eval レジストリは禁止トークンを含まない。PASS。
- **III**: 過去検証は年次 walk-forward の OOS。**表示モデルの切替はベースライン(単 seed)比較+ECE の事前登録条件で判定**(D15・研究 fit の暫定値で LogLoss 0.201204 vs 0.201584・ECE 0.00066 vs 0.00111=満たす。最終判定は再凍結値)。選ばれた馬の過大評価(S1 期待回収率の平均 140.6% → 実際 121.1%・S3 138.2% → 106.4%・S5 140.2% → 100.8%)を開示。S1/S2 の事後性を画面と文書に明記。前向きの段階・方針を実装前に凍結(本 plan)。チェックポイント判定は記録して覆らない(D18)。PASS(確認は前向き)。
- **IV**: 勝率を返さない・表示しない。PASS。
- **V**: pick は決定時点の凍結値を append-only・生涯 1 件・最初の計算は scan 行で記録・void は scratched のみ。2 版・scan・pick は同一 run_id。モデル版・logic_version・booster manifest sha を行に保存。段階判定は append-only の値(判断時オッズ)から決まり、判定自体も append-only に記録。回収率は常に「近似」と計算基準のラベル付き(不変テスト)。PASS。
- **VI**: migration 0019 → API → front。OpenAPI 純追加。PASS。
- **品質ゲート**: codex(spec 2 回・plan 1 回・rev3 の解消案 1 回)+ 解析(spec 2 体・plan 2 体)+ `/speckit-analyze` 1 回。採否は §5。

## 5. codex / 解析レビュー(plan)

### codex(2026-10-01・plan 1 回目)— 採否

| # | 指摘 | 採否 | 反映 |
|---|---|---|---|
| 1 | pick に S5 判定の単 seed 版が無く行単位で監査できない | 採用 | `ensemble_model_version` / `single_model_version`(0.2) |
| 2 | pick がどの予測行から作られたか契約が曖昧 | 採用 | 同じ実行のメモリ上の 2 版予測からのみ・`run_id` 同一をテストで固定(0.4) |
| 3 | 現在の `race_horses.odds` で精算すると前向き成績が後から動く | 採用 | 精算 2 基準(D13)・公式払戻の取込は次 feature |
| 4 | void 判定の順序 | 採用(D11 で上位解決) | field 変化は void にしないので順序問題は消滅。scratched のみ |
| 5 | `voids_pick_id` の一意性 | 採用 | 部分 UNIQUE・冪等 INSERT(0.2・0.4) |
| 6 | `result_pending_at_compute` の定義と `computed_at < post_time` | 採用 | 列定義と集計条件に明記(0.2・0.5) |
| 7 | チップの「130% 超」は期待値シグナル | 採用 | 副チップ「S2」・閾値の数値を出さない(D9) |
| 8 | 再凍結の来歴(hash・行数・日付範囲・script/git・numpy/rng・丸め・p 値メタ) | 採用 | 0.1 の JSON 契約 |
| 9 | `entry--ev-over` は level 3 でも強調しすぎ | 不採用 | level 3 は凍結値では出現しない(価格ずれ試験の軸が最大 2・D9 の帰結)。意匠はレベル 3 用に残すが表示されない。情報密度は展開パネルで担保 |

### 解析 2 体(2026-10-01・plan)— 採否(全件採用、1 件は判断で一部)

| 重大度 | 指摘 | 反映 |
|---|---|---|
| HIGH | field_changed の void と「pick 馬が出走していれば精算」が矛盾 | D11: void は scratched のみ・出走馬変化は監査フラグ。spec の規約・Edge Cases を修正 |
| HIGH | pick 追記の単位(レース vs key)が spec と不一致・再計算で新規 pick が書かれうる | D12: レースの最初の計算でのみ・テストに両ケース |
| HIGH | 全該当 rule が不通過の馬でチップが消える | `chip_rule` は不通過/保留でも最上位を返し `chip_stage` を応答に(0.3・0.5・0.6) |
| HIGH | void 後/再計算後の表示源(pick か最新行か)が未定義 | 0.5 に表示源の規則(チップ・パネル=pick の凍結値「判断時点」、列=最新行)。spec の Edge Case を修正 |
| HIGH(実現性) | eval の `_COUNTS_CACHE` が無制限で API 常駐プロセスのメモリが増え続ける | LRU(maxsize 8)+ API 側で rule 単位メモ化(D7)・初回コストを実測 |
| HIGH(実現性) | void pass が `race_ok` を通ったレースにしか届かず、取消馬の pick が 100 円の損として精算される | void pass は `in_range_ids` 全レースで `raw` の started 集合から・tally は結果行の無い馬を `unsettled_horse` で除外 |
| MEDIUM | 再凍結後の seed/B/実装が spec の出典行と食い違う | Phase 1 の出口に spec 出典行の更新・`backtest.bootstrap` を応答に |
| MEDIUM | S5 の再凍結入力 | `SINGLE_RUN`(`mev-binary-v2` 系列)を別キーで |
| MEDIUM | 凍結 JSON が gitignore 下でテスト不能 | `specs/138-attention-conditions/evidence/` にコミット |
| MEDIUM | 表示版固定で 2025-01〜2026-08 の列が消える | D14: 埋め戻し範囲を 2025-01-01〜 |
| MEDIUM | チェックポイントの並び順が未定義 | `CHECKPOINT_ORDER=(post_time, race_id, horse_number)`・順序不変テスト |
| MEDIUM | 段階名の表記揺れ・600 点の名前・日本語 enum | ASCII `Stage` + `checkpoint` 値、日本語は front の表だけ |
| MEDIUM | 鮮度帯の定義(集計) | 帯 = `post_time − odds_observed_at`・`seconds_to_post` は診断列 |
| MEDIUM | counts の抜け(集計外・発走後観測)と Σ 照合 | counts を排他的に定義し `n_counted + Σcounts == pick 総数` をテスト |
| MEDIUM | RefreshButton/DayRefreshButton の invalidate | 0.6 に明記+spy テスト |
| MEDIUM | tasks が plan/spec から逸脱 | tasks.md を rev2 に合わせて書き直し |
| MEDIUM | 選定時オッズ vs 保存オッズの差の診断が応答に無い | `odds_drift` を `prospective` に追加 |
| MEDIUM(実現性) | seed 1 の結果 dir 名 | 0.1 に正しい tag を明記 |
| MEDIUM(実現性) | `--save-last-model` が親 dir を作らない | 0.1 (a) で mkdir |
| MEDIUM(実現性) | `OK:` 行の無条件拡張が 5 本の pin と CLI fake を壊す | `--ensemble-dir` 指定時のみ付加 |
| MEDIUM(実現性) | api 統合テストが `_synth` の既定版で 11 本壊れる | 0.5 に追随手順(既定版定数化・断言 2 箇所・置換テスト) |
| MEDIUM(実現性) | block universe(ゼロ列の有無)が未指定で再凍結値が変わる・p 値の seed | universe=集計対象のある開催日・p は同じ replicates から(D2) |
| LOW | `definition_ja` の置き場所・`next_checkpoint` の意味・日付一覧の S5/void/has_results/不正 date・logic_version 値と start_date 列・パネルの rules 結合と MSW fixture・「前向き未確認」の消える条件・全馬 72% の ens15 値・gap 欠損・levels の null・`--ensemble-dir` 省略時の挙動・lock 昇順・uv sources・void 対象の検証・閉包 assert・ops テストの具体 | すべて該当節に反映 |
| LOW(判断で一部) | 137 の `entry--ev-over` の扱いは codex #9 と同じ | 不採用(上記) |

### `/speckit-analyze`(2026-10-01・spec/plan/tasks 横断)— 解消(rev3・2026-10-02)

| ID | 重大度 | 指摘 | 解消 |
|---|---|---|---|
| C1 | CRITICAL | 表示モデルを ens15 に切り替えるのに較正(ECE)の確認が無い(憲法 III) | freeze に較正(LogLoss・ECE 等幅/等質量・信頼度区分)と選ばれた馬の対比を追加し研究 fit で実行(既存の凍結値は差 0)。採否条件を事前登録(D15・FR-015)。暫定値で満たす。spec の III 行・較正表、§4 III |
| H1 | HIGH | 「最初の計算」の実装条件(pick 行が無いとき)が初回 0 頭のレースで破れる | `attention_race_scans` + `ON CONFLICT DO NOTHING RETURNING`(D16)。テスト「初回 0 頭 → 2 回目も書かない」「初回 odds_unavailable → 次が初回」 |
| H2 | HIGH | 価格鮮度の計算元が未定義・チップが判断時点の表示であることの受入シナリオが無い | 鮮度は表示版の最新行・`chip_now` で現在値を確認し不一致/欠落は最弱(D17・FR-016)。US1 受入 8〜11 |
| H3 | HIGH | 「ラチェット」なのに段階を保存しておらず、遅れた結果で反転しうる | `attention_checkpoints` に判定を記録・材料は発走 3 日経過分(D18・FR-005)。US2 受入 6・7、API 統合テスト |
| G1 | HIGH | 本番の `predict_ensemble` と研究の予測の parity タスクが無い | `parity_ens15_20261001.py`・Phase 4a の出口(D19・T020a) |
| G2 | HIGH | S1〜S5 の判定が freeze とレジストリの 2 か所 | 定義を先に作り freeze は `match_mask` を使う・`definitions_sha256` で結ぶ・境界格子テスト(D20・T004a/T008) |
| I1 | MEDIUM | Tally の「void(取消/出走馬変更)」が rev1 の残り | 「void(取消)件数・出走馬変更フラグ件数」・応答で `counts`(排他的)と `flags` を分離 |
| I2 | MEDIUM | チップ規則(不通過のみ除外 vs 保留も除外)と主チップ S2 の副チップ重複 | 不通過・保留の両方を除外・主チップ S2 では副チップを出さない(spec・FR-012・`chip_rule`) |
| I3 | MEDIUM | 600 点の扱いと「観察中」のレベルが表に無い | 600 点通過/不通過を表に追加・「観察中」は点推定で 2 か 1(FR-006) |
| I4 | MEDIUM | 遡及しない対象・開始日の暫定値・TZ・Σ の分母が未定義 | 遡及しないのは集計対象・`PROSPECTIVE_START_DATE=None` で開始・日本時間・分母は日付絞り込み前の pick 総数(D21) |
| I5 | MEDIUM | 禁止語テストを表・ページ全体に当てると既存文言で赤 | 範囲を注目条件のコンポーネントの DOM に限定(FR-008・0.6) |
| I6 | MEDIUM | メモ化キーにオッズ更新が入っていない | max(race_horses.updated_at)・max(race_results.updated_at)・判定記録数をキーに(D22) |
| O1 | MEDIUM | leak-guard 拡張が Phase 6・閉包 assert が重複 | Phase 3 に移動・閉包 assert は features の 1 か所(T013a) |
| G3 | MEDIUM | 回収率の「近似」と計算基準のラベルを検証するテストが無い | 範囲内の回収率ノード全部にラベルがある不変テスト(FR-008・SC-007・T041) |
| G4 | MEDIUM | 価格ずれ試験の来歴が JSON 契約に無い | `price_noise` + `price_noise_provenance` を 0.1 の契約に(研究版で記録済み) |
| A1 | LOW | 表以外の研究値(注記・パネル例・レベル注記)が T006 の対象外 | T006 に spec 全体の数値更新を明記・spec に暫定値の注記 |
| A2 | LOW | 「価格鮮度」が 2 つの量を指す | 集計側を「判断時鮮度帯」(D23) |
| A3 | LOW | `levels.price` が鮮度と紛らわしい | `price_noise` に改名(D23) |
| A4 | LOW | FR-002「該当判定は training 側の 1 箇所」と plan(定義は eval)の食い違い | FR-002 を「定義は eval・pick を作る判定を呼ぶのは training だけ」に |
| O2 | LOW | T021〜T023 の T009 依存が未記載 | tasks の依存関係に追記 |
| O3 | LOW | SC-011 の記録先が §5(レビュー記録) | 新設 §6「運用実測」に |
| A5 | LOW | `CHECKPOINT_ORDER` の `horse_number` が pick に無い | pick に `horse_number` を保存 |
| A6 | LOW | eval に版名を置いても leak-guard を通るのは大文字小文字の偶然 | `attention_rules` の import 許可リスト(AST)と leak-guard 側の明示の例外 assert |
| A7 | LOW | block universe が bootstrap の docstring と逆 | D2 に逸脱を明記・呼び出し側で day_keys を絞り `block_universe` に記録 |
| A8 | LOW | FR-014 が FR-013 より前 | 並べ替え |
| — | — | `.specify/feature.json` が 135 のまま | 138 に更新 |

### rev3 の検証(2026-10-02・ワークフロー: codex 1 回 + 整合・コード照合・analyze 再確認の 3 体 → 各指摘を別の検証役が反証を試みる)

codex は 2 回起動に失敗(macOS に `timeout` が無い/scratchpad が git 管理外)した後、3 回目(`perl -e 'alarm 900; exec @ARGV'`・`--skip-git-repo-check`・`-m gpt-5.5`)で完走。指摘 45 件(codex 8・整合 15・コード照合 12・analyze 再確認 10。codex の残り 7 件は rev3 で対応済みと判定)のうち、反証に耐えた 34 件(重複を除き 25 論点)をすべて反映した(棄却 11 件)。

| 論点 | 出所 | 反映 |
|---|---|---|
| pick の UNIQUE に rule set の版が無く、版を上げると 2 版の書き込みごと rollback | CX-02・CS-03・AR-NEW-4・CF-10 | D25・0.2・0.4・0.5 |
| 「現在値」に ens15 と単 seed の別時点の値が混ざる/出走馬変更・オッズ欠落時の current が未定義 | CX-03・CS-15 | 0.5 の表示源(current = ens15 の最新行・単 seed は同じ run のときだけ・状態が available でなければ null) |
| parity が共通行の一致だけで行集合の差を見ない | CX-04 | 0.1 の合格条件 (b)(c) |
| 集計開始日が判定記録に残らず、常駐 API が古い定数のまま | CX-05・CS-08 | `prospective_start_date` 列・照合・投入手順に api 再起動・FR-003 の例外 |
| チップと日付一覧が「300 点」「判定待ち」を描けない | CS-01 | `StageDetail` |
| 過去検証の表示語が基準とずれる(レベル 2 の基準がレベル 3 の語) | CS-02 | FR-006・0.6 |
| 埋め戻しの終端 9/30 で 10/1〜投入日の列が恒久的に消える/延ばすと単 seed 行を上書き | CS-04・AR-NEW-1・CF-2 | `--ensemble-only`(D24)・D14・§1・§2 |
| spec の件数の列挙が `classify_pick` の 9 分類と合わない・SC-009 の分類 | CS-05 | spec の一覧・Tally・SC-009 |
| SC-002 の副チップが無条件 | CS-06 | SC-002 |
| `decide_checkpoint` の並べ替えの置き場所とテストの食い違い | CS-07(CX-01 は棄却されたが同じ論点) | 関数の中で並べる・開催日の鍵も関数で |
| 凍結 JSON の単 seed のキー名(`seed1` vs `single`) | CS-10 | freeze スクリプトを `single` に揃えて再実行(値の差 0) |
| 同着の定義(レース単位か) | CS-11 | `dead_heat` = 勝ち馬数 != 1(凍結と同じ) |
| 強調レベル 3 は構造的に到達不能=137 の白枠・左帯が表示されなくなる | CS-12・AR-NEW-3 | D9 の帰結・spec の前提・codex #9 の理由を訂正。**利用者に判断を仰ぐ** |
| Key Entity の「発走 3 日前」の向き | CS-13 | spec を「判定時刻 − 3 日」に |
| 判定の失敗が ops から見えない | CS-14・CF-11 | CLI 最終行に `checkpoints=ok\|pending\|error` |
| 0017 への downgrade テストが 3 表で赤になる | AR-NEW-2・CF-1 | 0.2 の追随・T013 |
| 回収率ラベルの語彙が文書ごとに揺れる | AR-G3 | `ROI_BASIS_LABELS` に凍結・spec の画面例を修正 |
| 価格ずれ試験の乱数の来歴が実装と違う(2010 年以降に絞った後の行順で引いている) | AR-G4・CF-3 | 来歴の文を訂正し行数と行順 hash を記録(再実行・値の差 0) |
| 研究の bootstrap wrapper が factorize の整数コードを日付キーにすると来歴が再現しない | CF-4 | T005 で race_date の ISO 文字列を渡す |
| `field_digest` の二重実装 | CF-5 | eval の 1 関数 |
| import 許可リストに `collections.abc` が無い(ruff UP035 と衝突) | CF-6 | 許可リストに追加 |
| 開催日の鍵が未定義 | CF-7 | `day_key(post_time)` |
| re-key で実際に走った馬に void が付く | AR-NEW-6(CF-12 は同じ論点を棄却) | D26: void は出走取消・競走除外の行があるときだけ。棄却側の「実害は小さい」も正しいが、修正の費用が小さいので採用 |
| 判定の材料が「判定時に結果がそろっていた先頭 N 点」になる | codex 担当の観察(未検証) | `skipped_pending_before_last` を判定記録に残して監査可能に(判定の条件にはしない=永久に判定できなくなるのを避ける) |
| 判定は race_horses を読まない | CX-07(棄却されたが表現を明確化) | 0.4 に明記 |

棄却したもの: CX-06(採否条件に「選ばれた馬の過大評価が悪化しない」副条件を足す=暫定値を見てから条件を足すのは事前登録に反する)・CX-08(n_picks の整合テスト=監査列で仕様の欠陥ではない)・CS-09・AR-H3・AR-I4・AR-NEW-5・CF-8・CF-9。

## 6. 運用実測(実装後に記入)

| 項目 | 値 | 出典 |
|---|---|---|
| ens15 学習 15 本の所要時間 | 17 分(5 本並列・1 スレッド決定論・09:42〜09:59) | `scratchpad` の学習ログ |
| 再凍結の採否条件(`adoption_gate`) | **満たす**(LogLoss 0.201204 ≤ 0.201584・ECE 0.00066 ≤ 0.00111+0.001)。予測は研究 fit とビット一致し、変わったのは区間と p だけ | `evidence/rules_S1_S5_freeze.json`・`refreeze-diff.md` |
| 学習の再現性 | seed 3 を学習し直して booster バイト一致・予測 786,049 行完全一致 | `refreeze-diff.md` |
| 本番経路の parity(一致行/共通行) | **合格**: 共通 80,386 行中 80,384 行が 1e-9 未満で一致(残る 2 行は 137 と同じ二重登録の馬 `2022106098`/`nk:2022106098`・race 202504040407)・本番のみ 104 行=勝ち馬が 1 頭でないレース(研究側が除外)・研究のみ 0 行・S1〜S5 の該当判定は両方向 100% 一致(S1 207・S2 78・S3 714・S4 464・S5 1,232 頭)・日ごと再構築の標本も一致。55 秒 | `evidence/parity_ens15.json` |
| 計算ジョブ 1 回(1 開催日・2 版+scan+pick+チェックポイント判定)の所要時間(SC-011) | 1 開催日(2026-09-22・11 レース)の `--ensemble-only` 実行が 15.5 秒(特徴量ビルド込み)。通常モードは単 seed 予測が増えるだけで同水準=ops の timeout 600 秒に十分収まる | CLI 実測(2026-10-02) |
| `/attention-rules` の初回(メモ化前)応答時間 | 0.01 秒(集計対象 0 点の時点)。集計対象が増えると bootstrap(B=20,000)の分だけ伸びる=メモ化キーが変わった最初の 1 回だけ | curl 実測(2026-10-02) |
| 埋め戻し 2025-01〜投入日の前日(`--ensemble-only`)の所要時間 | 50 秒(2025-01-01〜2026-09-21 の 5,798 レース・80,343 頭・pick 2,680 件。9/22 の 11 レース・pick 16 件は単日の出口確認で先に実施)。ローカル DB の最終開催日は 2026-09-22 で、それ以降のレースは未取込 | CLI 実測(2026-10-02) |
| 投入(go-live) | 2026-10-02。埋め戻し → worker/ops-api 再起動 → api 再起動(D8)→ ブラウザ確認(チップ・展開パネル・注記・`/attention`・日付一覧・コンソールエラーなし)→ `PROSPECTIVE_START_DATE = 2026-10-02` → api 再起動。埋め戻しの pick は投入日に計算したので「集計開始前」ではなく「発走後の計算」「発走時刻不明」で集計外(どれも集計 0 点) | 2026-10-02 |
