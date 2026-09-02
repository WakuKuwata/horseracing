# Tasks: 騎手の時変切片 — confirmatory 測定と採否

**Input**: Design documents from `/specs/107-jockey-tv-intercept/`
**Prerequisites**: plan.md, research.md (D1-D10), data-model.md (INV-J1..J6), contracts/, gate-config.json(凍結済み・2026-09-02 に analyze C1+H2 で再凍結・hash `c872172a…`)

**組織**: US1(confirmatory 測定)が MVP。US2/US3 は verdict の**排他分岐**(両方は実行されない)。

## Phase 1: Setup

- [ ] T001 実行前提の確認: postgres 稼働・materialized parquet が 2024-12-31 を被覆
  (manifest の data_through ≥ 2024-12-31・feature_version=features-021)・
  `specs/107-jockey-tv-intercept/gate-config.json` の hash が quickstart 記載値
  `c872172a57be8a052a7ecd9e4b6492574fcd852e1f8c2845e4736e3cddac8eaa` と一致することを
  eval の `gate_config_hash` で再計算して確認

## Phase 2: Foundational(US1 の前提・blocking)

- [ ] T002 confirmatory driver を作成: `scripts/jockey_tv_confirmatory.py` —
  contracts/confirmatory.md の全 fail-closed(assert_confirmatory + assert_delta_provenance +
  窓 to<2025-01-01 assert + `load_eval_races(start_date=FEATURE_POOL_START)` + 定数一致検査)
  → preflight 監査(fold 別 λ raw/clamped・騎手数・window 行数・nk: 騎手 ID 件数・
  評価レース騎乗の b カバレッジ)→ 公式 `paired_eval(candidate_factory, CalibSplitFactory,
  gate_config=cfg, first_valid_year=2022, subgroups=True)` → 縮退検査(差分頭数 ≥1 でなければ
  abort・効果数値非出力)→ `final_decision` → `evidence/verdict.json` +
  `evidence/paired-evidence.json`(append-only)+ `evidence/preflight.json`。
  候補 factory は spike と同一定義(W=730/MIN_RIDES=30/λ クランプ[10,500]/露出加重中心化/
  isotonic 全 OOF 行)を driver 内に持つ
- [ ] T003 [P] driver の構造テスト: `eval/tests/unit/test_jockey_tv_confirmatory.py` —
  (a) driver 定数 == gate-config 凍結値 == `scripts/jockey_timevarying_spike.py` の定数
  (INV-J4・三点一致) (b) 窓 to≥2025-01-01 の config で fail-closed (c) gate-config hash
  不一致で fail-closed (d) 縮退(全予測一致の合成 preds)で abort し数値を出力しない
  (INV-J3) (e) 凍結 config のキーが decision/paired の**実際に読むキー**と一致
  (`critical_subgroups(cfg)==[]`・`_min_eval_days(cfg)==300` を生関数で検査 —
  analyze H2 の「縛っているつもりの死にキー」再発防止)。
  scripts/ を import できる形は spike のテスト前例に従う
- [ ] T004 [P] スモーク実行(構造のみ): rounds 30・**first_valid_year 2021・to 2021-03-01**
  の縮小 config(別ファイル・凍結 config は触らない・**凍結採点窓 2022-2024 と互いに素**=
  analyze H3)で driver が preflight→評価→verdict.json まで完走することを確認。
  driver に `--smoke` モードを実装し、smoke では point/CI を verdict.json からも redact
  (構造フィールドのみ出力)— 「効果数値を要約に書かない」を規律でなく機構にする

**Checkpoint**: driver とテストが緑 = US1 実行可能

## Phase 3: User Story 1 — 別窓 confirmatory 測定 (P1・MVP) ★判定★

**Goal**: 凍結 gate-config の下で一度だけ測定し三値 verdict を得る
**Independent Test**: evidence/ 一式が生成され、証拠からの再計算が判定値とビット一致

- [ ] T005 [US1] 本番実行: quickstart のコマンド(凍結 config + hash)で nohup 実行
  (推定 ~60 分)。完走後 `evidence/` に verdict.json / paired-evidence.json /
  preflight.json が揃うこと
- [ ] T006 [US1] 証拠検証(SC-001): 公式 `evidence.recompute` で paired-evidence.json から
  点推定・sample_ci・total_ci を再計算し verdict.json の値とビット一致を確認。
  窓検証(SC-002): 証拠の全レース日 ≤ 2024-12-31
- [ ] T007 [US1] preflight 監査のレビュー(SC-003): fold 別 λ がクランプ域内か・
  b カバレッジ・nk: 件数を確認し異常があれば verdict を NO_DECISION 扱いにする根拠として
  記録(数値の読み替えはしない — 実行妥当性の検査のみ)
- [ ] T008 [US1] 実 DB E2E(SC-004): active モデルの任意 1 レース予測が測定前後で
  バイト一致(測定は persist しない=構造的成立の確認)
- [ ] T009 [US1] verdict を確定し、以降のフェーズを分岐: ADOPT → Phase 4 /
  REJECT・NO_DECISION → Phase 5(もう一方のフェーズは「非該当」とマークして閉じる)

**Checkpoint**: 三値 verdict 確定 — ここが本 feature の中断点

## Phase 4: User Story 2 — ADOPT 時の serving 統合 (P2・verdict=ADOPT のときのみ)

**Goal**: 騎手切片を本番予測経路に opt-in で組み込み、候補モデルとして登録する
**Independent Test**: 候補 artifact の b で serving 予測が confirmatory 候補アームの手続きと
一致・既存 active はバイト不変

- [ ] T010 [US2] 設計確定: contracts/adoption.md を実装可能な詳細(artifact スキーマ・
  recipe フィールド名・serving 結線点)に更新し、codex レビュー(復旧済みなら)または
  セルフレビュー checklist を通す
- [ ] T011 [US2] 推定純関数の正規モジュール化: `training/src/horseracing_training/jockey_intercept.py`
  + 単体テスト(手計算 fixture・クランプ境界・MIN_RIDES 境界・中心化・UNKNOWN 縮約)
- [ ] T012 [US2] ModelRecipe に opt-in フィールド(既定 None)+ `NEW_HASH_DEFAULT_OMISSIONS`
  登録 + 両系 recipe_hash スナップショットテスト(099 機構)in
  `training/src/horseracing_training/recipe.py`
- [ ] T013 [US2] 学習時の b/λ/window メタデータの artifact 凍結 + 読込時整合検証 in
  `training/src/horseracing_training/artifacts.py`(鍵数・有限性・メタデータ突き合わせ)
- [ ] T014 [US2] serving 外部加算: `serving/src/horseracing_serving/predictor.py` 経路に
  artifact の b を適用(b に無い騎手は 0)+ logic_version `;jti=w730` マーカー +
  既定 OFF で既存予測バイト不変のテスト
- [ ] T015 [US2] 候補モデル学習・登録(active にしない)+ 実 DB E2E(候補予測が
  confirmatory 手続きと一致・active 不変)
- [ ] T016 [US2] 昇格判断の材料整備: 標準昇格ゲート+直近窓・nk: 可搬性の確認を実行し
  結果を記録(昇格自体はユーザー承認)

## Phase 5: User Story 3 — REJECT/NO_DECISION 時の閉鎖と保全 (P3・排他分岐)

**Goal**: 負の結果を保全し、騎手軸をこの設計族について閉じる
**Independent Test**: 全スイート緑・保全スクリプトが残る・閉鎖記録が spec で読める

- [ ] T017 [US3] 結線差分ゼロの確認: `git status` で training/serving/eval に測定由来の
  差分が無いこと(driver とテストは保全対象として残す)+ 全スイート回帰
  (eval/training 緑)
- [ ] T018 [US3] contracts/adoption.md に「不発効」注記を追加

## Phase 6: Polish & 記録(両分岐共通)

- [ ] T019 spec.md に実測結果を転記(FR-014/FR-016/SC-006): 点推定・total CI・verdict・
  ラベル二重使用の限界注記(FR-016)・
  preflight 要旨・閉鎖範囲(設計族限定)または採用範囲・screening 履歴との一続きの表
- [ ] T020 [P] memory 更新: `jockey-identity-residual.md` に confirmatory の結末を追記
  (verdict・数値・次に測るなら何が必要か)+ MEMORY.md の行を更新
- [ ] T021 [P] CLAUDE.md の SPECKIT 区間を手動 Edit で完了ステータスに更新
  (agent-context スクリプトは実行禁止)
- [ ] T022 コミット(driver・テスト・specs/107 一式・evidence)

## Dependencies

- Phase 2 → Phase 3(driver なしに測定不可)。T003/T004 は T002 後に並列可
- Phase 3 T005 → T006/T007/T008(並列可)→ T009
- Phase 4 と Phase 5 は **T009 の verdict で排他**。Phase 4 内は T010 → T011/T012[P] →
  T013 → T014 → T015 → T016
- Phase 6 は分岐確定後

## Implementation Strategy

MVP = Phase 1-3(US1)。**T009 が中断点** — verdict を見てから Phase 4 か 5 のみ進める。
効果数値の出力規律: スモーク(T004)と縮退時は数値を出さない。判定後の数値の読み替え・
凍結値の変更は禁止(再測定は新規事前登録)。
