# Tasks: 140 単勝 quote の来歴(A3)+判断時 EV 整合モデルの並走評価

## Phase 1: A3(本番)
- [x] T001 migration 0021 `win_odds_official_at`/`win_odds_observed_at` + ORM `Race` + db 統合テスト(列・NULL 可・downgrade/upgrade)
- [x] T002 head 固定テスト(features 9 本・live 1 本・db の downgrade 断言)を 0021 に
- [x] T003 scrape `update_odds`: 書込>0 かつ result-pending で `win_odds_*` を書く・`official_at` 逆行ガード・テスト(書く/書かない/逆行/entries 再取込で不変)
- [x] T004 scrape `apply_final_odds(observed_at=)`: `updated>0` で `win_odds_observed_at=observed_at`・`official_at=NULL`。pipeline/repair の呼び出しを更新・テスト
- [x] T005 training `market_ev`: ROWS_SQL の coalesce + fallback 列・summary 2 キー・LOGIC_VERSION 追記・テスト(フォールバック件数・来歴あり行は quote 時刻・logic_version の期待値更新)

## Phase 2: 研究ハーネス
- [x] T006 `fit_generator.py` → `generator.json`(帯別 a/b/s・ρ・sha・期間)+ 単体テスト(合成データで既知係数を回収)
- [x] T007 `train_judged.py --seed` → `artifacts/market_ev/judged/mev-ens15-judged-v0/seed_NN/`(決定論・1 スレッド)+ 本番 loader が拒否するテスト
- [x] T008 `fit_correction.py` → `coefficients.json`(c1/c2)+ テスト
- [x] T009 `score.py` + `protocol.json/sha256`(FR-009/010)+ テスト(fit 期間拒否・append-only・paired bootstrap の形)

## Phase 3: レビュー・適用・初回採点
- [x] T010 独立レビュー(リーク・来歴・契約・fail-closed)と修正(採否は plan §3.1。high 1・medium 1・low 14 を反映、注記のみ 4 件)
- [x] T011 ローカル適用(**10-12 の最初の refresh より前**・`quickstart.md`): `alembic upgrade head`(0021)→ worker/ops-api/api 再起動 → 次開催日に SC-001/002(expected_return ジョブの `summary.provenance.final_odds_provenance_missing=0`)を確認。適用が 10-13 のレース確定より後なら `FINAL_ODDS_PROVENANCE_FROM` を実適用日の翌日に
- [x] T012 ①-a 15 seed 学習(1 スレッド・決定論)・確定オッズ 2026 OOS NLL の非劣化確認(SC-003)
- [x] T013 `score.py --from 2026-10-10 --to 2026-10-11`(10-12 は結果確定後に追記)→ `results/`
- [x] T014 記録: spec に結果・plan §3 に codex 採否・CLAUDE.md・memory
