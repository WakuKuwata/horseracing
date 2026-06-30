---
description: "Task list — 低履歴×血統適性 交互作用 (032)"
---

# Tasks: 低履歴×血統適性 交互作用 + 種牡馬デビュー戦適性 (Debut/Low-history × Pedigree)

**Input**: [plan.md](plan.md) / [spec.md](spec.md) / [research.md](research.md) / [data-model.md](data-model.md) / [contracts/debut-pedigree-features.md](contracts/debut-pedigree-features.md) / [quickstart.md](quickstart.md)

**Tests**: リーク防止(憲法 II)・パリティ が核のため**テスト中核**。leak-guard / parity / columns / correctness を必須化。

**Organization**: user story 単位。MVP = US1(sire_debut_win_rate 新情報) + US2(ゲーティング交互作用) + US3(リーク)。

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [X] T001 前提確認: main(features-009/lgbm-031)・026 `_other_offspring`/`_normalize_name`/`_runs`・history の is_debut/is_low_history・025 materialization 利用可・horseracing DB head 0006。sire_name カバレッジ確認
- [X] T002 [P] [contracts/debut-pedigree-features.md](contracts/debut-pedigree-features.md) の列契約(5列)・デビュー戦集約契約・ゲーティング積・NaN 規律・採用プロトコル(bundle 事前登録)・不変条件を確定(契約先行、codex 反映)

## Phase 2: Foundational（全 story の前提）

- [X] T003 `features/src/horseracing_features/debut_pedigree_features.py`(新): スケルトン。`DEBUT_PEDIGREE_COLUMNS`(5列) 定義、`build_debut_pedigree_features(frames, *, history=None, pedigree=None, min_starts=10)` が 026 `_runs`/`_other_offspring` を import、history/pedigree は None なら内部計算・materialize からは既算出を渡す土台。生の今走 result/odds は読まない
- [X] T004 `features/src/horseracing_features/registry.py`: 5 列を source=pedigree/timing=PRE_ENTRY/missing=NULL で REGISTRY 登録、FEATURE_GROUPS に group=`debut_pedigree` 付与(STATIC_COLUMNS に入れない)、`FEATURE_VERSION="features-010"`。**版 bump 波及**: `test_materialize_core.py`/`test_feature023_leak_guard.py` の features-009 リテラルを 010 に

**Checkpoint**: モジュール骨格・列メタ・version が揃う。

---

## Phase 3: User Story 1 - 種牡馬デビュー戦適性(新情報) (P1, MVP)

**Goal**: 同種牡馬の他産駒デビュー戦勝率を as-of 自馬除外で算出。

**Independent Test**: 他産駒 2 頭がデビュー戦 1 勝/1 敗 → sire_debut_win_rate=0.5(自馬除外・strictly-before)。

### 実装
- [X] T005 [US1] `debut_pedigree_features.py`: 各 horse の race_date 最小 STARTED 出走=debut run をマーク → debut-runs サブセットに 026 `_other_offspring(targets, debut_runs, "sire_name")` 適用 → `sire_debut_win_rate = where(o_cnt>=min_starts, o_wins/o_cnt, NaN)`。float64

### US1 テスト
- [X] T006 [P] [US1] `features/tests/unit/test_debut_pedigree_features.py`(新): INV-C1(デビュー戦勝率値)・INV-C2(自馬除外)・INV-C5(母数<min_starts→NaN)

**Checkpoint**: 新情報シグナルが成立。

---

## Phase 4: User Story 2 - 低履歴×血統 ゲーティング交互作用 (P1, MVP)

**Goal**: is_debut/is_low_history × sire_* の積。

**Independent Test**: is_debut=1 で debut_x_sire_win_rate=sire_win_rate、is_debut=0 で 0。

### 実装
- [X] T007 [US2] `debut_pedigree_features.py`: history(is_debut/is_low_history)× pedigree(sire_win_rate/sire_dist_band_win_rate)の per-row 積で `debut_x_sire_win_rate`・`debut_x_sire_dist_band_win_rate`・`lowhist_x_sire_win_rate`・`lowhist_x_sire_dist_band_win_rate`。片側 NaN→NaN。最終 `out[DEBUT_PEDIGREE_COLUMNS].astype("float64")`

### US2 テスト
- [X] T008 [P] [US2] `test_debut_pedigree_features.py`(追記): INV-C3(ゲーティング積)・INV-C4(sire NaN→積 NaN)・INV-C6(全列 float64)

**Checkpoint**: ゲーティング交互作用が成立。

---

## Phase 5: User Story 3 - リーク安全保証 (P1, MVP)

**Goal**: 今走結果・同日他産駒・未来 に不変。

**Independent Test**: leak-guard 全通過 + ソース grep。

### テスト
- [X] T009 [P] [US3] `features/tests/unit/test_debut_pedigree_leak.py`(新): INV-L1(自馬今走 finish/result 変更で不変)・INV-L2(同日他産駒結果変更で sire_debut_win_rate 不変)・INV-L3(未来同種牡馬デビュー戦変更で不変)・INV-L4(grep: 今走 result/finish_order/odds を生参照しない)

**Checkpoint**: リーク境界を新設しないことを保証(release gate)。

---

## Phase 6: User Story 4 - materialization パリティ・カバレッジ (P2)

**Goal**: 単一 as-of 源結線・bit パリティ・serving fallback。

### 実装
- [X] T010 [US4] `features/src/horseracing_features/materialize.py`: `build_asof_features` に debut_pedigree ブロック(build_debut_pedigree_features に既算出 history/pedigree を渡す)を単一経路で merge。source_fingerprint 無改修(新ソース列なし=sire_name は 026 で包含)を確認。serving 未来レースは単一レース fallback

### US4 テスト
- [X] T011 [P] [US4] `features/tests/unit/test_materialize_core.py`(拡張): INV-P1(parity, debut_pedigree 5 列含む, assert_frame_equal check_exact/check_dtype)・INV-P2(5 列 materialized・odds/payout/dividend トークン無し)・INV-P3(FEATURE_VERSION=="features-010")

**Checkpoint**: 出力再現可能・serving 一貫を保証。

---

## Phase 7: User Story 5 - 採用判定（事前登録 bundle OOS） (P1)

**Goal**: bundle ゲートで採否、採用なら serving 昇格。

### 実装/評価
- [X] T012 [US5] `training/src/horseracing_training/cli.py`: feature-eval 既定 `--drop-groups` を `debut_pedigree` に(baseline=features-009、candidate=full features-010)
- [X] T013 [US5] 実 DB walk-forward OOS: `feature-eval --drop-groups debut_pedigree` で AdoptionReport 取得。事前登録基準を機械適用、結果を research/quickstart に記録。`feature-ablation`・`feature-diagnostic`(デビュー馬セグメント)は SECONDARY 併記

**Checkpoint**: 採否が客観ゲートで決まる。

---

## Phase 8: Polish & 横断

- [X] T014 [P] `features` lint/test: `uv run ruff check src tests` + `uv run pytest` 緑、eval/training/serving 既存テスト透過で緑
- [X] T015 実 DB 生成スモーク(quickstart): `features materialize`(features-010・debut_pedigree 5 列収録)、`use_materialized` で parity bit 一致、5 列カバレッジ確認
- [X] T016 採否に応じた serving 反映: 採用なら `train-evaluate --model-version lgbm-032 --baseline baseline-uniform-v1 --artifacts-dir ../artifacts`→active 昇格・lgbm-031 retired(feature_hash=features-010)・serving 自動ロード確認。不採用なら main を features-009/lgbm-031 のまま維持しブランチ保全
- [X] T017 [P] `CLAUDE.md` の 032 サマリを OOS 結果で更新(採否・LogLoss/AUC/ECE/fold + デビュー馬セグメント)
- [X] T018 codex 反映確認: 実装が codex(新情報を主役・単純積は副次・採用確率を正直に・dist 系は 033 へ) に沿うことを最終確認

---

## Dependencies & Execution Order
- Phase1→2(T003 骨格・T004 registry/version)が全 story をブロック。
- US1(T005)→US2(T007)は同一ファイル(debut_pedigree_features.py)編集のため逐次。US3(T009 leak)は US1/US2 後。MVP=US1+US2+US3。
- US4(T010 結線・T011 parity)は実装後。US5(T012-T013 評価)は結線後。Polish(T014-T018)は最後。

## Parallel 実行例
- T006/T008 は同ファイル追記のため逐次、T009[P](leak)・T011[P](materialize テスト)は並行可。Polish T014/T017[P]。

## 実装戦略
1. MVP: Phase1→2→US1(sire_debut)→US2(ゲーティング)→US3(リーク)。
2. 横断: US4(パリティ/serving fallback)。
3. 採用: US5 で事前登録 bundle ゲート→採否→serving 反映。
4. 憲法 II(他産駒の過去デビュー戦のみ・自馬除外・同日除外・今走非参照)/III(bundle OOS + セグメント診断 SECONDARY)/IV(009 不変)/V(parity)/VI(スキーマ変更なし)維持。**最優先 release gate = leak-guard + parity bit 一致**。

## analyze 反映（inline 実行・findings 解消）
- **A1 (確認)**: features-009 リテラルは `test_materialize_core.py`/`test_feature023_leak_guard.py` の 2 箇所(031 で 009 に更新済)→ T004 で 010 に。eval/training/serving は版を動的参照=透過。
- **A2 (確認)**: feature-eval の `--drop-groups`(030/031 で確立)実在 → 既定を debut_pedigree にするのみ(T012)。
- **A3 (リーク構造)**: sire_debut_win_rate は 026 `_other_offspring`(sire 累積−自馬累積・同日除外)を debut-runs サブセットに適用 → リーク面が 026 機構に閉じ込められる。ゲーティングは as-of 列の積のみ。leak-guard(T009)で担保。
- **A4 (採用見込み)**: codex は 031 より採用確率を不確実と見積もり。全体ゲインはデビュー馬出走比(~10.5%)に希釈されうる → SECONDARY でデビュー馬セグメント診断を併記(採否は全体 OOS=憲法 III)。
- codex 反映済(新情報 sire_debut_win_rate を主役・単純積は副次・dist 系条件替わりは 033 へ分離)。

## 注意
- 今走 result/finish_order/odds は**生参照しない**。デビュー戦集約は他産駒の過去デビュー戦のみ。
- bundle 採用後に OOS を見て列を削るのは禁止(選択リーク)。market_edge/セグメントは SECONDARY。
- min_starts は 026 と同既定(=10)を流用。テストでは小さく設定して検証。
