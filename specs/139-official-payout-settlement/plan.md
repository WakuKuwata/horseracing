# Implementation Plan: 139 確定オッズと公式単勝払戻・注目条件の精算を公式払戻へ

**Spec**: `spec.md` | **Date**: 2026-10-04

## 0. 契約

### 0.1 DB: migration `0020_official_win_payouts`(`down_revision = "0019_attention_picks"`)

| 列 | 型 | 制約 |
|---|---|---|
| race_id | TEXT | PK の 1 列目, FK races.race_id |
| horse_number | INTEGER | PK の 2 列目, CHECK (horse_number >= 1) |
| payout_yen | INTEGER | NOT NULL, CHECK (payout_yen >= 100)(100 円あたり。元返し 100 も入る) |
| source | TEXT | NOT NULL DEFAULT 'netkeiba_result', CHECK IN ('netkeiba_result') |
| observed_at | TIMESTAMPTZ | NOT NULL(結果ページを取得した時刻=アーカイブ修復ならファイル名の時刻) |
| created_at / updated_at | TIMESTAMPTZ | TimestampMixin(updated_at トリガ) |

- 上書き可(公式の訂正に追随)。同着は馬番ごとに 1 行。
- ORM `OfficialWinPayout`(`db/src/horseracing_db/models/market.py` に追加・`models/__init__.py`)。
- 追随: head 固定テスト 10 本(features 9・live 1)を `0020_`、`_TABLES_ADDED_AFTER_0012`(db 2 ファイル)に追加、`test_market_ev_predictions.py` の `_TABLES_ADDED_AFTER_0018` と `test_attention_picks.py` の 0018 への downgrade 断言に `official_win_payouts` を追加。

### 0.2 scrape

- `models.ScrapedResultRow` に `win_odds: float | None = None`・`popularity: int | None = None` を追加(既定値つき=既存の生成箇所は不変)。`parse_results` が列 10・9 を読む(数値でなければ None)。
- `models.ScrapedWinPayout(horse_number: int, payout_yen: int)`。`parse/exotic_odds.py` に `parse_win_payouts(html) -> list[ScrapedWinPayout]` を追加: `table.Payout_Detail_Table` の `tr.Tansho`(無ければラベル「単勝」の行)から、Result 欄の馬番(`div span` 群)と Payout 欄(`<br>` 区切りの「N円」)を 1:1 で対にする。数が合わなければ `ParseError`。行が無ければ `[]`。080 の `parse_exotic_odds` は変更しない。
- `upsert.apply_final_odds(session, race_id, rows) -> int`: `resolve_entity` で horse_id を引き、`race_horses.odds`(win_odds ≥ 1.0 のとき)と `popularity` を上書きする。値が変わった行数を返す。**結果ページは netkeiba 期のレースにしか取りに行かないので、JRA-VAN 期の確定オッズは触られない**(既存の「JRA-VAN の値を保護」の規約は `update_odds` 側のまま)。
- `upsert.upsert_official_win_payouts(session, race_id, payouts, observed_at) -> int`: INSERT … ON CONFLICT (race_id, horse_number) DO UPDATE(payout_yen・observed_at)。
- `pipeline.scrape_results`: `backfill_results` の後、同じ HTML から `apply_final_odds` と `parse_win_payouts`→`upsert_official_win_payouts` を、080 と同じ `session.begin_nested()` で隔離して実行する(`apply_result_page_settlement`・修復 CLI と共用)。summary には `SETTLEMENT_COUNT_KEYS` を常に出す: `final_odds_updated`・`final_odds_unreadable`・`final_odds_unmatched`・`final_odds_missing`(レース単位)・`final_odds_skipped_pre_netkeiba`・`final_odds_race_unknown`・`final_odds_errors`・`win_payouts`・`win_payout_missing`(レース単位)・`win_payout_errors`(D15)。
- CLI `scrape repair-final-odds --from YYYY-MM-DD --to YYYY-MM-DD --archive-dir ABS [--dry-run]`: 期間内の確定済みレースについて、アーカイブ(`{archive_dir}/race.netkeiba.com/{sha16}/url.txt` の URL が `result.html?race_id=…`、結果表のある最新の `*.html.gz`)を読み、上の 2 関数で書く。ネットワークは使わない。出力は `error:` 行(最大 20)・`missing archive:`・`odds missing:`・`detail: skipped_pre_netkeiba=… race_unknown=… payout_missing=… odds_missing=… odds_unreadable=… odds_unmatched=…`・(dry-run のとき)`# dry-run: nothing was written`・最終行 `OK|FAILED: races=N archived=A odds_updated=U payouts=P missing_archive=M errors=E`(E>0 で FAILED・終了コード 1)。

### 0.3 138 の精算(eval / training / api / front)

- **eval `attention_rules`**:
  - v2 = v1 の分類 + 公式払戻での精算。v1(判断時オッズ精算)は同じ集計対象の参考値として並記する。
  - `PickFacts` に `official_payout_yen: float | None = None`(このレースのこの馬の公式単勝払戻・100 円あたり)・`race_payout_known: bool = False`(このレースに公式払戻が 1 件以上ある)・`race_payout_consistent: bool = False`(このレースの FINISHED かつ 1 着の馬の馬番の集合 == 払戻のある馬番の集合。馬番は race_results→race_horses.horse_number・不明は不一致)を追加。既定はすべて fail-closed。集合の比較は `race_payout_consistent(winner_numbers, paid_numbers)` が唯一の定義で、training はこれを呼び、api は同じ集合の等価を SQL(両方向の NOT EXISTS)で書く。
  - `PickClass`/`EXCLUSION_ORDER` に `payout_race_missing`(結果はあるが `race_payout_known` が偽)と `payout_inconsistent`(`race_payout_consistent` が偽=レース単位。保険として馬単位の「勝ち馬なのにこの馬の払戻が無い/勝ち馬でないのに払戻がある」も同じ分類)を `pending_result` の直後にこの順で追加(D11)。`SELECTION_POLICY_VERSION = "v2"`・`PROSPECTIVE_START_DATE = 2026-10-05`(D12)。
  - `official_payout(f) = f.official_payout_yen if f.won else 0.0`。`decide_checkpoint(..., payout_of=official_payout)` を既定にし、記録の bootstrap メタに `settlement: "official_win_payout"` を入れる。`frozen_payout` は v1 の参考として残す。
  - `BUY_TIME_EXPECTATION: dict[rule_id, (point, ci_low, ci_high)]`・`BUY_TIME_EXPECTATION_VERSION = "buy-time-v1"`・`BUY_TIME_EXPECTATION_VERIFIED = False`・`BUY_TIME_EXPECTATION_SOURCE`(`version`・R02 の出典・`status`)。配信は `buy_time_expectation(rule_id)` 経由だけで、検証前は None(D6)。値と版の対応は `evidence/buy_time_expectation.json`(版は追記のみ)に記録し、eval のテストが照合する(D13)。
- **training `attention_checkpoints`**: 材料に `official_win_payouts` と、レース単位の払戻照合に要る勝ち馬の馬番(`race_horses.horse_number` だけ・抽選で固定される値)を加える(race_horses のオッズと出走状態は読まない原則のまま)。記録は `selection_policy_version='v2'`。
- **training の pick 書き込み**は `selection_policy_version=v2` を書く(分類は同じ)。
- **api**: `attention_tally_rows` に公式払戻(馬ごと)・レースの払戻有無・レース単位の払戻照合(`race_payout_consistent`)を足す。前向き現況に `official`(valuation_basis `official_win_payout`・段階の基準)を追加し、`frozen` は参考(v1)、`stored` は据え置き(修正後は確定オッズになる)。`counts` に `payout_race_missing`・`payout_inconsistent`。段階・前向き軸のレベルは公式払戻の点推定で判定。`RuleSummary` に `buy_time_expectation {roi, ci_low, ci_high, source{version, …}} | null`(検証前は null)。判定記録は v2 のもので絞る。メモ化キーに `official_win_payouts` の件数と max(updated_at) を足す。
- **front**: `ROI_BASIS_LABELS` に `official_win_payout: "公式払戻"`(「近似」を付けない)。一覧・展開パネルの前向き検証は公式払戻を主、判断時オッズを「参考(v1)」として並記。期待値「判断時点の見込み(過去データからの換算)」の欄: 検証後は値・区間・出典・版、検証前(API が null)は数値もラベルも出さず検証待ちの文だけ。`counts` の表示語に「公式払戻なし」「払戻の不整合」。期待値の文言は D13。OpenAPI 再生成。
- **138 の文書**: spec/plan に D27(方針 v2=公式払戻精算)と D28(判断時点の期待値の凍結)を追記し、139 を参照する。

## 1. 実装順

1. DB(0.1)+ head 追随 → 2. scrape(0.2)と eval(0.3 の eval)を並列 → 3. training・api(0.3)→ 4. front(OpenAPI 再生成後)→ 5. ローカル DB に migration・修復 CLI(アーカイブのみ)・features materialize の再実行・api/worker/ops-api 再起動・確認(順序と時刻の制約は D14・D16・tasks T014)。

## 2. 決定

| # | 決定 | 根拠 |
|---|---|---|
| D1 | 公式単勝払戻は新テーブル `official_win_payouts` に置き、`exotic_odds` に 'win' を足さない | `exotic_odds` は 080 のゲート・API の組合せ配当表示・`canonical_selection` が組合せ券を前提にしている。'win' を足すと読み手全部に影響が出る。単勝専用の小さな表なら影響範囲が閉じる |
| D2 | 確定オッズは結果取込のときに結果ページの値で上書きする。ops のジョブ順は変えない | 順序を変えても、確定後の API は単勝を返さないので確定値は取れない。結果ページは確定値を持っている。netkeiba 期のレースしか取りに行かないので JRA-VAN 期を壊さない |
| D3 | 138 は集計方針 v2 として公式払戻で精算する。v1(判断時オッズ)は参考に残す | 単勝で払われるのは確定オッズ。判断時オッズの精算は受け取れない額(R05: 勝ち馬の払戻/判断時オッズ 中央値 0.93・合計 1.12)。チェックポイントの記録は 0 件で、集計対象も 17 点しかない今が切り替えの時期。138 の規約「方針を変えるときは v2 を切り、v1 を書き換えずに並記」に従う |
| D4 | 公式払戻の無いレースはレース単位で集計外(`payout_race_missing`。D11 で 2 分類に分けた) | 勝ち馬だけ除くと回収率が下に偏る。レース単位の有無は結果と独立 |
| D5 | 同着は v1 と同じく集計外のまま | 凍結表(過去検証)が同着を除いているので、比べられる形を保つ |
| D6 | 判断時点の期待値は独立検証を通った値だけを画面に出す。R02 の値は版つきで registry に登録するが、`BUY_TIME_EXPECTATION_VERIFIED=False` の間は API が null を返し、画面は検証待ちの文だけを出す。独立検証(T017)を通ったら status を `verified — <検証の出典>` にして配信を開け、値が変われば版を上げる | R02 は診断扱いで反証役の検証をまだ通っていない(報告書の付録 B は R01・R06・R07・R08 のみ)。検証前の値を出すと、前向き成績を未検証の基準と比べることになる(2026-10-04 レビュー R3) |
| D7 | アーカイブの無いレースの再取得は利用者の許可を得てから | netkeiba の取得予算(一括 backfill はしない方針)に関わる。約 239 リクエスト |
| D8 | **JRA-VAN 期の保護をコードで明示**: 確定オッズの上書きは `race_date >= NETKEIBA_FINAL_ODDS_FROM = 2025-10-11`(DB で `nk:` の馬が初めて現れる開催日。JRA-VAN 分は 2025-10-05 まで)のレースだけ。それより前のレースで呼ばれたら何も書かず件数に出す | codex Q1: 「netkeiba 期しか取りに行かない」という運用前提だけでは弱い。更新ボタンは古いレースでも押せる |
| D9 | 上書きは結果ページで**数値が読めた馬だけ**・**値が変わる行だけ**。空欄・取消・除外は既存値を残す(NULL にしない)。同じ HTML を 2 回流しても DB は変わらない | codex Q1(取消/空欄で既存値を消す事故・冪等性) |
| D10 | 公式払戻の行に `html_sha256` を残す(出典の監査)。訂正の履歴表は作らない(公式の訂正は極めてまれ・上書きと件数ログで足りる) | codex Q1 の履歴表案は部分採用 |
| D11 | 払戻の欠落は 2 つに分け、**どちらもレース単位**で集計外にする: `payout_race_missing`(結果はあるがレースに単勝払戻が 1 件も無い)と `payout_inconsistent`(レースの払戻の行が結果と食い違う=1 着の馬番の集合 ≠ 払戻のある馬番の集合=データ不整合・件数を目立たせる)。馬単位の判定(勝ち馬なのに自分の払戻が無い/勝ち馬でないのに払戻がある)は保険として残す | codex Q2: 勝ち馬だけの欠落を pending と同列にすると都合のよい除外になる。2026-10-04 レビュー R1: 不整合を pick 単位で判定すると、勝ち馬と払戻のある負け馬だけが外れ、同じレースの残りの負け馬は払戻 0 で数えられて回収率が下に偏る(D4 と同じ理由)。結果は INSERT-ONLY・払戻は訂正に追随して上書き/削除されるので、id 分裂や訂正で両者がずれる経路は実在する。v2 の集計開始(10-05)前に直す(後からでは v3 が要る) |
| D12 | **方針 v2 は自身の開始日から数える**(`PROSPECTIVE_START_DATE = 2026-10-05`)。v1 期間(2026-10-02〜04)に数えた 17 点は v2 に混ぜない。v2 の前向き現況には、同じ集計対象を判断時オッズで精算した値を v1 参考として並記する。方針の版は「選び方・分類・精算」をまとめて表し、別々の版の列は作らない | codex Q2: 17 点を見た後の基準変更に見えないよう、正式な記録は導入後から。138 の規約(開始日を動かすなら方針の版を上げる)どおり |
| D13 | 判断時点の期待値の表示は「過去データで、判断時のオッズで条件を満たした馬を買ったと仮定した換算回収率(参考値・購入を勧めるものではありません)」とし、算出期間・組数・区間・版を併記する。registry には値と出典(報告・期間・件数・区間・算出日)と版 `BUY_TIME_EXPECTATION_VERSION` を置き、値と版の対応を `evidence/buy_time_expectation.json`(版ごとに追記のみ)に記録して eval のテストで照合する=値を変えて版を上げないとテストが落ちる。版は API の出典(`source.version`)と画面の出典の文に出す | codex Q2: 「見込み」は推奨・保証と読まれやすい。2026-10-04 レビュー R3: 版の識別子が無いと値の差し替えを記録上区別できない |
| D14 | **確定オッズの修復はデータ修復であって特徴の変更ではない**: FEATURE_VERSION は据え置き(067 の前例)。修復は v2 の開始(2026-10-05)より前に流し、v2 の pick がすべて修復後の入力から作られるようにする。順序は「0020 → 修復 CLI → `features materialize` の再実行 → api/worker/ops-api 再起動」。修復の前後で、次開催の数レースについて ens15 の期待回収率と S1〜S5 の該当集合の差を **1 回だけ**、保存しない計算(pick を書かない)で測り、`evidence/repair_input_shift.json` に残す | 2026-10-04 レビュー R2: 修復は 2025-10-11 以降の確定済みレースの `race_horses.odds`/`popularity` を発走前の値から確定値に変える。これらは過去走の入力として ens15(prev_odds・prev_popularity・prev_q・prev_beat_market・騎手/調教師の q 超過=`market_ev.py`)と mix-129 の過去市場特徴(F02 `pm_core_strength`・058)に入るので、これから走るレースの期待回収率・該当・勝率予測が修復の時点で動く。materialize 済み parquet は source_fingerprint に odds を含むので fail-closed になる(再 materialize が要る)。学習データの大半(JRA-VAN 期)の過去走オッズは確定値なので、修復は入力を学習時の分布に揃える向き。凍結表(138)と R02 は修復前のデータで算出したので、v2 の前向き成績を BUY_TIME_EXPECTATION と比べる前提のずれの大きさを測って開示する(凍結表への影響は R05 で誤差程度=S1 C 窓 1.177→1.188/1.191) |
| D15 | 結果ページから確定オッズが 1 頭も読めないレース(`final_odds_missing`)・race_horses に行が無い馬(`final_odds_unmatched`)・数値が読めない馬(`final_odds_unreadable`)・race_date 不明(`final_odds_race_unknown`、`skipped_pre_netkeiba` と分ける)を summary と修復レポートに必ず出し、レース単位の 2 つはジョブの `error_message` にレース ID つきで出す(ジョブは失敗にしない) | 2026-10-04 レビュー R4: ヘッダが変わると安全側で全馬が読めず、ジョブは SUCCEEDED・`final_odds_updated=0` で終わる=R05(発走前オッズが残る)の再発が運用画面から見分けられない。払戻側の `win_payout_missing` と対称にする |
| D16 | **再起動の時刻**: worker・api を新コード(方針 v2)で再起動するのは、10-04(v1 の最終日)の最後のレースの market-ev 計算が済んだ後、かつ 10-05(JST)の最初の計算より前。間に合わなかった場合は、札(`selection_policy_version`・logic_version の `;policy=`)が計算日の方針と食い違う pick の件数を T015 で数えて記録する。また 10-05 00:00 JST より前に将来レースの market-ev を計算しない(pick はレースの最初の計算でだけ作られるので、10-04 に計算された将来レースは v2 で恒久に before_start になる) | 2026-10-04 レビュー R5: 集計上は before_start なので数値への影響は無いが、v1 期間の pick に v2 の札が付くと監査上の記録が混ざる |

## 3. codex レビュー(2026-10-04・1 回・`codex exec`)

採用: JRA-VAN 期の明示的な境界(D8)・数値だけ・差分だけの上書きと冪等(D9)・出典 hash(D10)・払戻欠落の 2 分類(D11)・v2 は導入後から数える(D12)・期待値の文言と出典の凍結(D13)・テスト一式(JRA-VAN 期を触らない/取消で消さない/2 回流して差分なし/1 レースの失敗が他を壊さない/v1 参考値は確定オッズの上書き後も不変/画面に推奨と読める語がない)。
既に満たしている: 判断時のオッズは 138 の pick(`odds_used`・`odds_observed_at`)と 137 の行に書いた時点で凍結されるので、`race_horses` の上書きで変わらない(テストで固定する)。
不採用: 払戻の履歴表(D10)・選び方/分類/精算の版を別々の列にすること(D12・方針の版で一括して表す)。

## 4. 実装後レビュー(2026-10-04・修正役)

R1(払戻の不整合をレース単位に=D11)・R2(修復の波及=D14)・R3(判断時点の見込みの版と検証前の非表示=D6・D13)・R4(確定オッズの読めない件数=D15)・R5(再起動の時刻=D16)・R6(spec・db のコメント・`AttentionRoi.tsx` の説明の更新)をすべて採用して反映した。R3 は独立検証が間に合わないので「検証前は出さない」を選び、検証そのものは T017 に分けた。

## 5. 独立検証の反映(2026-10-04・T017)

R02 の独立検証(`evidence/r02_verification.md`)は confirmed-with-caveats だった。判断時点で選ぶと期待回収率が下がる向きはどの部分集合でも崩れなかった。一方で、(a) 2 つの推定量の差 ±0.06 はどの区間にも入っていない、(b) S2 の区間は分母が 2 頭で無効、(c) 区間が 100% を含むのは S1・S2 だけ、の 3 点が分かった。そこで D6・D13 の版規則に従い `buy-time-v2` を登録した。1 点の値はやめ、2 推定量の範囲(5% 丸め)と、両者の 95% 区間の包絡(外側丸め)で示す。区間が 100% を含むかどうかは条件ごとに文で書く。S2 は単独の値を出さず S1 を参照させる。時間帯別の値は出さない(発走 1 時間以内の対比は事前登録で null)。v1 は evidence に残し、`verified:false`(superseded)とした。前向きの公式払戻成績が溜まったら、この換算値ではなく実測で読む。
