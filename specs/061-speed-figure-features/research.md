# Research: 061 本格スピード指数特徴

**Date**: 2026-07-08 | **Spec**: [spec.md](spec.md)

実査で確認した既存機構:

- `features/pace_features.py`: runs 構築(`_to_seconds`・race_id groupby の finisher 平均)と `_rolling_asof`(horse_id groupby → expanding/rolling → `merge_asof(backward, allow_exact_matches=False)`)— 本 feature が再利用する土台。
- `features/registry.py`: `COMPATIBLE_PRIOR_FEATURE_VERSIONS`(058)は current_fv キーの辞書 — features-016 エントリに **features-014(lgbm-057 用、既存 pin 値の継続)と features-015(lgbm-058-acc/lgbm-060-mkt 用、新規計測)の両方**を載せる必要がある。
- loader: races から venue_code/distance(正確値)/track_type/going、race_results から finish_time(timedelta)ロード済み — **新ソース列なし=source_fingerprint 不変**。

## D1: 基準タイムのセル定義(codex 反映・実測で確定)

**Decision**: `venue_code × track_type × distance(正確値) × going` のフルセル。**標本は race-level 1 レース 1 標本(そのレースの finisher タイム平均)**とし(codex 指摘採用: finisher 行プールだと多頭数レースが基準に過重)、`min_races=50` 未満のセルは NaN。階層フォールバックは初版なし。

**実測(2026-07-08、実 DB 全期間)**: フルセル 585 個。min_races=50 を満たすセルは 217 個で**全レースの 93.2%**をカバー(≥30 なら 96.0%)→ フォールバック無しでも NaN は主に希少 going×距離の組に限られ許容範囲。

**Rationale**: 距離正確値は JRA の離散距離体系に自然。going はタイム影響が大きくセルに含める。race-level 標本により codex 指摘の「多頭数過重」「runner count だけの閾値管理」問題を同時に解消(count=レース数)。

**Alternatives**: 階層フォールバック(going→無し)は codex 推奨だったが、実測カバレッジ 93% で NaN 正直路線の方が監査性が高く初版は不採用(deferred)。

## D2: 標準化方式(codex 反映)

**Decision**: as-of z-score。`z = (cell_mean_before − race_mean_time) / cell_std_before` を**過去走の属するレース**に対して算出(正=速い)し、当該過去走の個馬タイムは `z_horse = (cell_mean_before − time_s) / cell_std_before`。std は daily-cumsum(x・x²・count、race-level 標本)から導出、min_races 未満/退化で NaN。z は [−5, +5] clip。**std 非依存の秒/100m 正規化(`(cell_mean − time_s) / distance × 100`)を登録済みフォールバックレバー**とする(codex 提案: 初期年・希少セルの std 不安定への保険。spike で z が不発なら 1 回だけ試す)。

**Rationale**: 距離でタイム分散が変わるため秒差はスケール不整合。race-level 標本で std の過信(少数レース×多頭数)も解消。

## D3: 集約列セット(FEATURE_GROUPS: speed_figure、codex 反映)

**Decision**: **5 列** — `asof_spdfig_avg` / `asof_spdfig_best`(cummax)/ `asof_spdfig_recent3` / `asof_spdfig_last` + **`asof_spdfig_count`(有効 z を持つ過去走数=信頼度、codex 提案採用)**。全 float64・NaN 伝播(count は履歴ゼロで NaN でなく 0.0? → **0.0 とする: 「有効指数の数」は事実として 0 が正しい**(Unknown ではない)。他 4 列は NaN)。

**Rationale**: 023/041 の列構成 + 信頼度列(031 の coverage 列前例)。avg×recent3 の相関は高いが GBM は冗長に頑健で、bundle ゲートが判定する。

## D4: クラス(race_class)の扱い(codex 反映)

**Decision**: セルに入れない(基準はクラス混合)。**その帰結として本指数は「純粋な条件差補正済み時計」ではなく「クラス混合基準との差=能力寄りの特徴」である**ことを明記(codex 指摘採用)。クラス起因の水準差は既存の race_class・クラス遷移・賞金系特徴と木の分割に任せる。

**Rationale**: クラスをセルに足すとスパース性が跳ね上がる。codex も v1 見送りを妥当と評価(追加するならセル分割でなく class 別 expected-speed の別レイヤー補正 — deferred)。

## D5: as-of 実装(リーク・materialize 安全)

**Decision**: 020 `_cumulative_before` と同じ **daily cumsum − 当日** 機構をセル単位に適用: セル×日で finisher の (count, Σt, Σt²) を日次集計 → セル内で日付順 cumsum → 当日分を引く=「その日より厳密前」の統計。過去走行に (走行日, セル) で結合して z を算出 → 馬単位 `_rolling_asof`。全て per-row as-of(pool-end 非依存)= materialize-safe(031/059 同型)。挙動型テスト: 今走/同日/未来のタイム変更で不変・**過去走タイム変更で変化(正の対照)**・基準タイム側も未来レース追加で不変。

## D6: serving 互換(058 T013 第2回)

**Decision**: `COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-016"] = {"features-014": <既存 pin 値>, "features-015": <bump 前に計測した canonical hash>}`。hash は**新列追加前の** `feature_hash(model_input_features())` を計測して固定。実 DB E2E で lgbm-057(014)・lgbm-058-acc/lgbm-060-mkt(015)の compat-load + lgbm-057 予測バイト一致を検証。

## D7: spike 設計(FR-009、codex 反映で強化)

**Decision**: 実装を features 層まで通した段階で、(1) 共有列バイト不変+parity 確認 → (2) 直近 3-4 fold の binary feature-eval(baseline=新群 drop)→ (3) **pl_topk 少数 fold 確認をゲイン幅に関わらず必須**(codex: 絶対軸にも 059 同型の縮小リスクあり得る — 「微小なら」の条件を外して常時実施)。**Go**: binary で win LogLoss 改善かつ pl_topk で非悪化。No-go: D2 の秒/100m フォールバックを 1 回だけ試行 → なお不発なら中断・記録。

## D8: 既存 rel_time との重複リスク

**認識**: rel_time(レース内相対)と本指数(絶対)は数学的に独立だが、強い馬は両方高く相関は高い。増分情報は「メンバーレベルの違いを跨いだ絶対比較」— 特に昇級馬・少履歴馬・格上挑戦の評価。ゲートが素直に判定する(だからこそ spike de-risk を先に置く)。

## 未決(codex second opinion 待ち → plan.md に記録)

- D1 のセル定義(going 含否・階層フォールバック)と min_samples の目安
- D2 の z-score vs 秒/ハロン、as-of std の初期年不安定性
- D4 クラス混合基準の妥当性
- 集約列の過不足・見落としテスト
