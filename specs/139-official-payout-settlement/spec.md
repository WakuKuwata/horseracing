# Feature Specification: 確定オッズと公式単勝払戻の保存・注目条件の精算を公式払戻へ

**Feature Branch**: `139-official-payout-settlement`
**Created**: 2026-10-04
**Status**: Draft
**Input**: ユーザー「1と2を進めて」(2026-10-04 の ROI 見落とし検定 `docs/roi-missed-patterns-20261004/report.md` の次の一手 1=精算不具合の修正、2=138 の前向き精算を公式払戻へ切替+判断時点で買った場合の期待値の事前登録)

## 背景(実測)

- **精算不具合(R05)**: ops の `refresh_race` は「出走表 → 結果 → オッズ」の順に取る。結果が 1 行でも入ると `update_odds` は空欄しか埋めず(`scrape/src/horseracing_scrape/upsert.py:212-222`)、確定後の netkeiba のオッズ API は単勝を返さない(`pipeline.py:203-247`)。このため**発走前に最後に取ったオッズが `race_horses.odds` に残る**。2026-07-04〜09-22 の 474 レースと 10-03/04 の 14 レースで発生し、今も続いている。
- 結果ページには馬ごとの単勝オッズ・人気(`parse/results.py` の列 10・9)と単勝の払戻(`Payout_Detail_Table` の `tr.Tansho`)があるが、どちらのパーサも捨てている(`parse_results` は読まず、`parse_exotic_odds` は 単勝・枠連 の行を `continue`)。**単勝の公式払戻はどこにも保存されていない**。
- 138 の前向き検証は段階の判定を「判断時のオッズ `odds_used` × 100 円」で精算している(138 D13)。しかし単勝はパリミュチュエルで、**払われるのは確定オッズ**。勝ち馬の払戻/判断時オッズは中央値 0.93・合計比 1.12 で、帯によって 0.95〜1.35 に偏る(R05)。判断時オッズでの精算は実際には受け取れない額で、段階の判定がずれる。
- 判断時点のオッズで選ぶと、締切時にも同じ条件に入るのは S1 11%・S3 20% で、判断時点で買った場合の期待回収率は S1 約 0.90・S3 約 0.86(R02)。138 の凍結表(S1 121%・S3 106%)は締切オッズで選んだ値なので、前向き成績の期待値として読めない。

## スコープ

1. **確定オッズの保存**: 結果を取り込むとき、結果ページの馬ごとの単勝オッズ・人気で `race_horses.odds` / `popularity` を上書きする(netkeiba の結果ページを取り込むのは netkeiba 期のレースだけなので、JRA-VAN 期の値には触れない)。
2. **公式単勝払戻の保存**: 結果ページの単勝の払戻(同着は複数行)を新テーブル `official_win_payouts` に保存する(migration 0020)。組合せ配当の `exotic_odds` には混ぜない(080・API の読み手が組合せ券と仮定しているため)。
3. **過去分の修復**: 保存済みの結果ページ(`artifacts/scrape_archive/race.netkeiba.com/…/result.html` 504 件・ネットワーク不要)から、確定オッズと公式払戻を埋め直す CLI。アーカイブの無いレース(2026-07-04〜08-09 の約 239 件)の再取得はネットワークを使うので、利用者の許可を得てから別に行う(このスコープでは実行しない)。
4. **138 の精算を公式払戻へ(集計方針 v2)**: 段階の判定(チェックポイント)と前向き現況の主指標を公式単勝払戻で精算する。判断時オッズでの精算(v1)は参考として並記し、書き換えない。払戻の欠落・不整合は**レース単位**で集計外にする(勝ち馬だけ除くと回収率が下に偏るため): 公式払戻がまだ 1 件も無いレースは `payout_race_missing`、払戻の行が結果と食い違うレース(1 着の馬番の集合 ≠ 払戻のある馬番の集合)は `payout_inconsistent`(plan D4・D11)。
5. **判断時点で買った場合の期待値の事前登録**: R02 の換算値を版(`buy-time-v1`)つきで registry に登録し、**独立検証を通った後に**一覧と展開パネルに「判断時点の見込み(過去データからの換算)」として出す。前向き成績はこの値と比べて読む。R02 はまだ独立検証を通っていない(報告書の付録 B は R01・R06・R07・R08 のみ)ので、検証が済むまで API は `buy_time_expectation: null` を返し、画面は数値を出さずに「検証待ち」の文だけを出す(plan D6)。

## 非対象

- 枠連の払戻・組合せ券の精算の変更(080 の範囲)
- ops のジョブ順序の変更(1 で結果側が確定オッズを書くので不要)
- 137 の期待回収率の再計算(確定済みレースは再計算しない契約のまま)
- アーカイブの無いレースの再取得(利用者の許可待ち)
- R02 の独立検証そのもの(別タスク T017。通るまで判断時点の見込みは画面に出さない)

## 波及(plan D14)

確定オッズの上書き(スコープ 1・3)は、2025-10-11 以降の確定済みレースの `race_horses.odds` / `popularity` を発走前の値から確定値に変える。これらは**過去走の入力**として、137/138 の ens15 モデル(prev_odds・prev_popularity・prev_q・prev_beat_market・騎手/調教師の q 超過)と本番 mix-129 の過去市場特徴(F02・058)に入るので、修復の時点でこれから走るレースの期待回収率・注目条件の該当・勝率予測が動く。materialize 済みの parquet は source_fingerprint に odds を含むので fail-closed になる。これは**データ修復であって特徴の変更ではない**(学習データの大半=JRA-VAN 期の過去走オッズは確定値で、修復はそれに揃える)ので FEATURE_VERSION は据え置き(067 の前例)。修復は v2 の開始(2026-10-05)より前に流し、v2 の pick がすべて修復後の入力から作られるようにする。修復前後の入力のずれは T014 で 1 回だけ測って evidence に残す。

## Requirements

- **FR-001**: `parse_results` は馬ごとの単勝オッズ(列 10)と人気(列 9)を読む。数値でない(`---` 等)ときは None。
- **FR-002**: 結果ページの単勝払戻を読むパーサ `parse_win_payouts` を追加する。馬番と払戻(円)の対を返し、同着は複数。馬番の数と払戻の数が合わなければ `ParseError`。結果ページに単勝の行が無いときは空。
- **FR-003**: `scrape_results` は結果を書いた後、同じページから確定オッズ・人気で `race_horses` を上書きし(`race_date >= 2025-10-11` のレースだけ・数値が読めた馬だけ・値が変わる行だけ)、公式払戻を `official_win_payouts` に upsert する。どちらも 080 の組合せ配当と同じく `begin_nested` で隔離し、失敗しても結果の保存を壊さない。summary には常に次の件数を出す: `final_odds_updated`・`final_odds_unreadable`(数値が読めない馬)・`final_odds_unmatched`(race_horses に行が無い馬)・`final_odds_missing`(結果行があるのに読めたオッズが 0 のレース=発走前オッズが残る R05 の再発)・`final_odds_skipped_pre_netkeiba`・`final_odds_race_unknown`(race_date 不明)・`final_odds_errors`・`win_payouts`・`win_payout_missing`(単勝の行が無いレース)・`win_payout_errors`。`final_odds_missing`・`final_odds_unmatched`・`win_payout_missing` はジョブの `error_message` にもレース ID つきで出す(ジョブは失敗にしない)。
- **FR-004**: migration 0020 で `official_win_payouts`(race_id, horse_number>=1, payout_yen>=100(100 円あたり・元返し 100 を含む), source='netkeiba_result', observed_at, html_sha256(出典の監査・plan D10), created_at/updated_at・主キー (race_id, horse_number))を作る。訂正に備えて上書き可(append-only にはしない)。ページが勝ち馬として挙げなくなった馬番の行は削除する。
- **FR-005**: 修復 CLI `scrape repair-final-odds --from --to --archive-dir ABS [--dry-run]` はアーカイブの結果ページだけを読み(ネットワークなし)、FR-003 と同じ関数で確定オッズと払戻を書く。出力は次の形(行の順):
  - `error: <race_id>: <理由>`(最大 20 行)
  - `missing archive: <race_id …>`(最大 20 件 + `(+N more)`)
  - `odds missing: <race_id …>`(読めたオッズが 0 のレース・最大 20 件)
  - `detail: skipped_pre_netkeiba=… race_unknown=… payout_missing=… odds_missing=… odds_unreadable=… odds_unmatched=…`
  - `# dry-run: nothing was written`(`--dry-run` のときだけ)
  - 最終行 `OK: races=N archived=A odds_updated=U payouts=P missing_archive=M errors=E`(E=0 のとき・終了コード 0)、E>0 なら先頭が `FAILED:` で終了コード 1。1 レースの失敗は他のレースを止めない。
- **FR-006**: 138 の集計方針を v2 にする。v2 = v1 と同じ分類 + 公式払戻での精算・開始日 2026-10-05(v1 期間の集計対象は混ぜない)。分類に `payout_race_missing`(結果はあるがそのレースの公式払戻が 1 件も無い)と `payout_inconsistent`(そのレースの払戻の行が結果と食い違う=FINISHED かつ 1 着の馬の馬番の集合 ≠ 払戻のある馬番の集合。馬番の分からない勝ち馬は一致しない扱い)を `pending_result` の直後に追加する。**どちらもレース単位**で、該当レースの pick はすべて集計外になる(馬単位の判定=勝ち馬なのに自分の払戻が無い・勝ち馬でないのに払戻がある、は保険として残す)。チェックポイント判定・段階・前向き現況の主指標は公式払戻。判断時オッズ精算(v1)は参考として並記する。
- **FR-007**: registry に判断時点で買った場合の期待値 `BUY_TIME_EXPECTATION`(rule ごとの点推定と区間・出典)を版 `BUY_TIME_EXPECTATION_VERSION`(`buy-time-v1`)つきで登録し、値と版の対応を `evidence/buy_time_expectation.json` に記録してテストで照合する(値を変えたら版を上げる)。API(`/attention-rules`)と front(一覧・展開パネル)には、独立検証を通った(`BUY_TIME_EXPECTATION_VERIFIED`)後にだけ出す(出典に `version` を含める)。通る前は API が null を返し、画面は数値もラベルも出さずに「独立検証を通った値だけを表示します(現在は検証待ちのため、換算値は表示していません)」とだけ出す。回収率ラベルは `公式払戻` を追加し、公式払戻の数値には「近似」を付けない。
- **FR-008**: 表示規律(138 FR-008)をそのまま守る。期待値の表示は「判断時点で買った場合の見込み(過去データからの換算)」とし、利益や購入を示唆しない。

## Success Criteria

- **SC-001**: 実 fixture(通常・同着)で確定オッズ・人気・公式払戻が取れ、同着は 2 行になる。
- **SC-002**: 結果取込の統合テストで、発走前オッズの入った `race_horses.odds` が確定オッズに上書きされ、公式払戻が保存される。結果の保存は払戻の失敗で壊れない。
- **SC-003**: 修復 CLI をローカル DB で流し、アーカイブのある affected レースの保存オッズが結果ページの確定オッズと一致する(R05 の照合で 239/239 不一致だったものが一致に変わる)。
- **SC-004**: 138 の前向き現況とチェックポイント判定が公式払戻で精算され、公式払戻の無いレースは `payout_race_missing`、払戻が結果と食い違うレースは `payout_inconsistent` として**レース単位で**集計外になり(同じレースの全 pick が同じ分類)、Σ 照合が成り立つ。v1(判断時オッズ)は参考として並記される。
- **SC-005**: 一覧と展開パネルに判断時点の見込みの欄が出る。独立検証の前は数値を出さず検証待ちの文だけ、検証後は値・区間・出典・版が出る(どちらの状態もテストで固定)。禁止語・損益色・回収率ラベルの不変テストが緑。
- **SC-006**: 既存のテスト(scrape・db・training・api・ops・front)が緑。
