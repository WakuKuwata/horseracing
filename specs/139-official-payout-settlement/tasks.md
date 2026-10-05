# Tasks: 139 確定オッズと公式単勝払戻・注目条件の精算を公式払戻へ

**Input**: `spec.md`・`plan.md`

## Phase 1: DB
- [X] T001 `db/migrations/versions/0020_official_win_payouts.py` + ORM `OfficialWinPayout`(`models/market.py`・`__init__`)
- [X] T002 [P] `db/tests/integration/test_official_win_payouts.py`(主キー・CHECK・上書き・updated_at・downgrade/upgrade)
- [X] T003 [P] head 固定 10 本を 0020 に・`_TABLES_ADDED_AFTER_0012` 2 ファイル・0018/0019 への downgrade 断言に新表

## Phase 2: scrape と eval(並列)
- [X] T004 `parse_results` で単勝オッズ・人気・`ScrapedResultRow` の新フィールド + 単体テスト(実 fixture 3 本)
- [X] T005 `parse_win_payouts` + 単体テスト(通常・同着・単勝行なし・数の不一致)
- [X] T006 `apply_final_odds`・`upsert_official_win_payouts`・`scrape_results` への結線(隔離)+ 統合テスト(発走前オッズが確定に上書き・払戻保存・払戻の失敗で結果が壊れない)
- [X] T007 CLI `repair-final-odds`(アーカイブのみ・dry-run)+ テスト(合成アーカイブ)
- [X] T008 eval `attention_rules`: 方針 v2(開始日 2026-10-05)・`PickFacts` の 2 フィールド・`payout_race_missing`/`payout_inconsistent`・`official_payout`・`decide_checkpoint` の既定・`BUY_TIME_EXPECTATION` + テスト更新

## Phase 3: training・api
- [X] T009 training `attention_checkpoints` が公式払戻で判定(v2)・pick は v2 を書く + テスト
- [X] T010 api: tally に公式払戻・`official` 基準・`payout_race_missing`/`payout_inconsistent`・`buy_time_expectation`・メモ化キー + テスト
- [X] T011 features の leak guard に `official_win_payouts`/`OfficialWinPayout` を追加

## Phase 4: front
- [X] T012 OpenAPI 再生成(front/admin)
- [X] T013 `ROI_BASIS_LABELS` に公式払戻・一覧/展開パネルで公式払戻を主・判断時オッズを参考・期待値の表示・「公式払戻なし」+ テスト

## Phase 5: 適用と確認
- [X] T014 ローカル DB への適用(plan D14・D16)。**時刻**: 10-04 の最後のレースの market-ev 計算が済んだ後、10-05 00:00 JST の最初の計算より前に再起動まで終える。10-05 00:00 JST より前に将来レースの market-ev を計算しない(そのレースの pick が v2 で恒久に before_start になる)。**順序**:
  1. 修復前の入力で、次開催の entries があるレース数本について ens15 の期待回収率と S1〜S5 の該当集合を**保存しない計算**(pick・market_ev_predictions を書かない)で求めて控える
  2. `alembic upgrade head`(0020)
  3. `scrape repair-final-odds --from 2026-06-27 --to 2026-10-04 --archive-dir <ABS>/artifacts/scrape_archive --dry-run` → 件数確認 → 本実行(`odds missing:`・`detail:` 行を記録)
  4. `features materialize` を再実行(source_fingerprint が odds を含むので、修復後は旧 parquet が fail-closed になる)
  5. 1 と同じレースを修復後の入力で計算し、レースごと・馬ごとの期待回収率の差と該当集合の差を `evidence/repair_input_shift.json` に記録(1 回だけ)
  6. api / worker / ops-api を再起動
- [X] T015 確認: affected レースの保存オッズ=結果ページの確定オッズ・公式払戻の件数・`payout_inconsistent` がレース単位で出ること(同じレースの pick がすべて同じ分類)・`/attention-rules` の公式払戻精算と Σ 照合・判断時点の見込みが検証待ちの文で出ること(数値が出ないこと)・画面。再起動が D16 の時刻に間に合わなかった場合は、札(`selection_policy_version`・logic_version の `;policy=`)が計算日(JST)の方針と食い違う pick の件数を数えて記録する
- [X] T016 138 の spec/plan に D27・D28 を追記・CLAUDE.md・memory
- [X] T017 R02(判断時点の見込み)の独立検証: 反証役が `pairs.parquet` 等の入力から ρ と凍結 ALL × ρ を別実装で再計算し、限界(17 日・時間帯と出所の重なり・g の水準)を確かめる。通れば `BUY_TIME_EXPECTATION_SOURCE["status"]` を `verified — <検証の出典>`・`BUY_TIME_EXPECTATION_VERIFIED=True`・`evidence/buy_time_expectation.json` の `verified`/`verification` を更新して配信を開ける。値が変わるなら `buy-time-v2` として evidence に追記し版を上げる(plan D6・D13)
- [ ] T018 `docs/roi-missed-patterns-20261004/report.md`(R02・R05 を含む・現在未追跡)を 139 と同じコミットに含める(凍結値の出典がリポジトリに残るように)

## 実装後レビューの反映(2026-10-04)

- R1: 払戻の不整合をレース単位に(eval `race_payout_consistent`・training/api の材料・テスト書き換え)
- R2: 修復の波及を plan D14 と T014 の手順に
- R3: 判断時点の見込みに版 `buy-time-v1`・evidence 照合テスト・検証前は API null/画面は検証待ちの文(T017・T018 を追加)
- R4: `final_odds_unreadable`/`unmatched`/`missing`/`race_unknown` を summary・修復レポート・CLI の detail 行に(plan D15)
- R5: 再起動の時刻を plan D16 と T014/T015 に
- R6: spec の分類名・FR-004/005、db の `roi_frozen` のコメント、`AttentionRoi.tsx` の説明

## 適用記録(2026-10-04 22:4x JST)

- T014-1: 修復前の入力で 10-03/04 の 46 レース・684 頭を保存しない計算(次開催の出走表はまだ DB に無いので直近開催で代用)
- T014-2: `alembic upgrade head` → `0020_official_win_payouts`
- T014-3: `repair-final-odds --from 2026-06-27 --to 2026-10-04`(dry-run → 本実行): `OK: races=1003 archived=499 odds_updated=3146 payouts=500 missing_archive=504 errors=0`・detail はすべて 0。再実行の dry-run は `odds_updated=0 payouts=0`(冪等)
- T014-4: `features materialize` → 967,840 行・features-021・fingerprint e70a493d…
- T014-5: 修復後の入力で同じ計算 → `evidence/repair_input_shift.json`(過去走経由の差: 期待回収率の中央値 0.0017・p90 0.010・最大 0.083、S1〜S5 の該当集合は S4 +1 頭のみ)
- T014-6: worker・ops-api・api を再起動(10-04 の最終計算 15:49 JST の後・10-05 00:00 JST の前)。再起動後の `attention_picks` は全 2,717 行が v1 札(v2 札の混入 0)
- 確認の一部(T015): 公式払戻 500 行・499 レース(同着 1 レース 2 行)・08-15〜10-04。勝ち馬の保存オッズ × 100 = 払戻 が同着レース以外の全レースで一致。`/attention-rules` は方針 v2・全 pick が before_start・`official` 基準を返す
- アーカイブの無い 06-27〜08-09 の 504 レースは払戻なし(v2 期間外なので集計には影響しない)。再取得は利用者の許可待ち
- T017(2026-10-04 23:xx): 独立検証(`evidence/r02_verification.md`・confirmed-with-caveats)を受けて `buy-time-v2` に置き換え。1 点の凍結値をやめ、2 推定量(凍結 ALL × ρ と、判断時選定の確定状態に g を当てた値)の範囲を 5% 丸め+両者の 95% 区間の包絡を外側丸め(S1 約 85〜90%・区間 73〜109%=100% を含む/S3 約 85%・76〜98%/S4 約 80〜85%・72〜95%/S5 約 80〜85%・68〜92%/S2 は単独の値なし=S1 に含まれる)。v1 は evidence に `verified:false`(superseded)で残置。api 再起動後、`/attention-rules` と `/attention` 画面で全 5 条件の文・版 `buy-time-v2`・「独立検証済み」を確認(コンソールエラー 0)
- T015 の残り: `/attention` 画面は方針 v2・公式払戻が主・判断時オッズ(v1)と保存オッズが参考・集計外 11 分類で Σ 照合が成立(S1: 0 + 208 = 208)。`/races/{id}/attention` は 200
- 気づき(未修正・低): `/races/{id}/attention` の `selection_policy_version` は保存された札ではなく現在の方針定数を返す(138 からの仕様。front は表示していない)。10-03 判断の pick を持つレースでも "v2" と出る
