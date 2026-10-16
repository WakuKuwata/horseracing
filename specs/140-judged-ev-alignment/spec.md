# Feature Specification: 単勝 quote の来歴(A3)と判断時 EV の整合モデルの並走評価(①-a/①-c)

**Feature Branch**: `140-judged-ev-alignment`
**Created**: 2026-10-11
**Status**: 実装完了・ローカル適用済(2026-10-12)・前向き評価中
**Input**: ユーザー「A3 の修正と ①-c・①-a の並走評価を進めて」(`docs/roi-model-improvement-20261010/report.md` §3・§7 の順位 ①-a/①-c と前提 A3)

## 背景(実測)

- 期待回収率モデル(mev-ens15-v1)は確定オッズで学習し、判断時(発走の中央値 6 時間前)のオッズで適用される。判断時 EV_J>1.0 の馬の Σwon/Σp̂_J は 0.57 [0.35, 0.88](同じ馬の確定時 p̂ では 0.80、確定市場 q では 1.08)。判断時→確定の EV 低下の内訳はオッズ側 +0.50 に対し p̂ 側 −0.82 で、p̂ 側が支配する(train/serve skew)。
- 判断時オッズは確定より圧縮されている(確定 1〜5 倍で log(O_J/O_F) +0.08〜+0.44、100 倍超で −0.48〜−1.07)。注入生成器は `u = a(h) + b(h)·log q_F + s(h)·(√ρ·ε_race + √(1−ρ)·ε_i)`(ρ=0.16)の形が要る。
- spike(V2・3 seed・採用判定ではない): 未摂動行+注入行・`log(分)` 特徴で再学習すると、確定オッズの 2026 OOS NLL は不変、判断時 NLL −0.0023〜−0.0028、EV_J>1.0 の較正 0.63→0.79〜0.88。該当馬は現行と別物(重なり 3/143)なので「保持率」は受入指標にできない。確定側(h=0)で使うと EV>1.2 件数が 6 割減る。
- **A3**: 本番の `odds_observed_at` は `race_horses.updated_at` の代理(`training/market_ev.py` ROWS_SQL)。トリガは WHEN なし・`upsert_entries` は毎回 ON CONFLICT DO UPDATE なので、出走表だけ再取込しても時刻が進む。実害は現状ほぼゼロ(前向き pick 21 件は odds 書込の 24 秒後)だが、判断時専用モデルは h = post_time − odds_observed_at を入力に取るため、h の誤供給は縮小の誤りに直結する。netkeiba の odds API(type=1・単勝+複勝同一レスポンス)の `official_datetime` は既に `official_at` として読まれ、複勝側は `races.place_odds_official_at` / `place_odds_observed_at` に保存済み。
- codex レビュー(10/10): 受入は「新規開催日での現行版との paired 改善+CI」、生成器は評価に使わない日で固定、判断時専用版は表示・確定側に接続しない運用分離、を採用済み。

## スコープ

1. **A3 — 単勝 quote の来歴**(本番コード): `races.win_odds_official_at` / `races.win_odds_observed_at` を追加し(migration 0021)、`update_odds` が単勝を 1 行以上書いたときにその payload の `official_at` と観測時刻を書く。`apply_final_odds`(139)が確定値を書いたときは `win_odds_observed_at` に結果ページの取得時刻を書き、`win_odds_official_at` は NULL にする(確定値の「公式時刻」は発走後であり quote の時刻ではない)。`market_ev` は `odds_observed_at` を `races.win_odds_observed_at` から取り、無いときだけ `race_horses.updated_at` に**フォールバックして件数を summary に出す**。`attention_picks`・API・front は無改修(読む値が正しくなるだけ)。
2. **①-a 判断時専用モデル `mev-ens15-judged`(研究 artifact・本番非結線)**: 注入生成器を R02 の 17 開催日(2026-08-02〜10-04)の組で推定して凍結し、2007〜2025 学習の 2026 年 booster を 15 seed(1 スレッド・決定論)で学習する。学習行は「未摂動(h=1 分)+注入(h は R02 の horizon 分布から抽出)」の 2 倍、特徴は現行 77 列+`log_min`。
3. **①-c 判断時の 2 段補正(軽量版)**: 現行 ens15 の p̂_J に対するロジット補正 `logit p' = β0 + β1·logit p̂_J + β2·log h + β3·log q_J`(事前登録・4 係数)。(c1) R02 の実組(443 レース・5,967 頭)で fit、(c2) 2025 年 OOF 行に生成器を当てた模擬判断時行で fit。両方を評価腕にする。
4. **並走評価ハーネス**: 評価母集団=結果確定済みで、結果確定前に ens15 が計算した run が `market_ev_predictions` に残るレース(`result_pending_at_compute` の行=「発走前の最後の判断」の全馬ベクトル)。**評価対象日は 2026-10-10 以降のみ**(生成器・c1 の fit に使った 10-04 以前は使わない)。判断時特徴は現 DB の as-of 65 列+`odds_used` から再計算した市場 12 列(天候・馬場は現在値=R02 と同じ限界)。腕=現行 ens15 / ①-a / ①-c1 / ①-c2。指標=判断時 NLL_J(馬単位・レース内正規化 winner NLL)・EV_J 帯別 Σwon/Σp̂_J・EV_J>1.2 件数・O_F/O_J・公式払戻での精算 ROI(記録)。現行版との差は開催日ブロック bootstrap の 95% 区間。結果は append-only(`results/<date>.json`)で、週ごとに追記できる。
5. **初回実行**: 10-10・10-11(・結果確定後に 10-12)で採点し、`results/` に残す。採否は材料が揃うまで(FR-010)出さない。

## 非対象

- 判断時専用モデルの本番結線(API/front・version 登録)。評価が通るまで研究 artifact のまま。
- ②(表示 EV の市場混合較正と閾値の再定義)・③④⑤・T−3 分の直前捕捉。
- `attention_picks` の既存行の `odds_observed_at` の書き換え(append-only)。
- 2026-07-04〜08-09 の 504 レースの再取得。

## Requirements

- **FR-001**: migration 0021 は `races` に `win_odds_official_at`・`win_odds_observed_at`(timestamptz・NULL 可)だけを追加する。既存行は NULL のまま(埋め戻さない=代理値を正しい来歴に見せない)。
- **FR-002**: `update_odds` は result-pending のレースで payload に有効な単勝が 1 頭以上あり `official_at` が保存済み以上(同一可・保存済み NULL は無条件)のとき、**値が変わらなくても** `win_odds_official_at = scraped.official_at`、`win_odds_observed_at = now()` を書く(新しい quote は新しい来歴)。ただし **`race_horses` を 1 行以上一致させた(`written>0`)ときだけ**: payload の馬番が 1 頭も一致しないときは保存値が古い quote のままなので来歴も動かさない(レビュー a3-scrape-db-1)。複勝 quote の単調性ガード(`official_at` の逆行拒否)と同じガードを単勝にも置く(逆行する payload は単勝も書かない・`skipped` に数える)。
- **FR-003**: `apply_final_odds` が 1 頭以上の確定値を書いたとき、`win_odds_observed_at = observed_at(結果ページの取得時刻)`・`win_odds_official_at = NULL` を書く。0 頭のときは触らない。来歴はレース単位なので、unmatched/unreadable の馬が残るレースでは来歴は書けた馬の確定値を指す(該当馬は 139 の件数で監視・plan D3)。
- **FR-004a**: summary に `final_odds_provenance_missing`(**2026-10-13 以降**・結果確定済み・単勝あり・`win_odds_observed_at` NULL のレース数)を出し、139 の確定値書込が全対象で走ったかを監視する。139 期の既存レースは埋め戻さず数えない(plan D11)。**この監視は本番で見える形で出す**: CLI は専用行 `market-ev-provenance: fallback_rows=N fallback_races=M final_odds_provenance_missing=K proxy_rows_written=P` を OK/SKIPPED の両経路で印字し、ops の expected_return ジョブは `summary.provenance` に保存し、K>0 なら `error_count`/`error_message`(`provenance: final_odds_provenance_missing=K`)を立てる(ジョブ状態は変えない。レビュー training-token-1)。
- **FR-004**: `market_ev` の ROWS_SQL は `coalesce(races.win_odds_observed_at, race_horses.updated_at)` を `odds_observed_at` にし、フォールバックした行数・レース数を `compute_and_persist` の summary(`odds_observed_fallback_rows` / `odds_observed_fallback_races`=**対象範囲の入力行・書込対象に限らない**)に常に出す。実際に proxy で書いた行数は版ごとに `versions[*].odds_observed_proxy_rows` に出す(レビュー training-token-4/-5)。`LOGIC_VERSION` / `ENSEMBLE_LOGIC_VERSION` に `oddsobs=quote` を追記する(来歴の仕様変更の監査)。**フォールバックした行の `logic_version` は `oddsobs=proxy`** に置き換えて保存する(行ごとの来歴の印・plan D12。確定後に `races.win_odds_*` が上書きされても計算時の出所が行に残る)。
- **FR-005**: `upsert_entries` だけを再実行しても `win_odds_observed_at` は変わらない(統合テスト)。単勝を書いた `update_odds` の後は進む。
- **FR-006**: 注入生成器は `scripts/roi_explore/judged_20261011/generator.json` に凍結(帯別 a/b/s・ρ(ICC)・推定に使った組の sha256・期間 2026-08-02〜10-04)。評価母集団と fit 期間は日付で分離し、ハーネスは fit 期間内の日付を**拒否**する。
- **FR-007**: ①-a は `direct_return_model.py` の学習経路(params・1 スレッド・deterministic・seed 1〜15・2007〜2025)を流用し、`artifacts/market_ev/judged/mev-ens15-judged-v0/seed_NN/model_2026.txt` と spec(features 78 列=現行 77+`log_min`・`drop_groups`・`generator_sha256`)を保存する。本番の `EnsembleMarketEvModel` には読み込ませない(ガードは 3 重: `EnsembleMarketEvModel.load` が spec の `judged: true` を `EnsembleIntegrityError` で拒否・version 不一致の拒否・`compute_and_persist` の attention version 厳密一致。テストで固定。レビュー harness-leak-protocol-1)。
- **FR-008**: ①-c の係数は `coefficients.json` に凍結(c1: R02 実組・c2: 模擬行)。評価時に再 fit しない。
- **FR-009a**: `odds_observed_at` の出所が `updated_at` 代理(A3 適用前の計算=`oddsobs` 無印、または `oddsobs=proxy`)のレースを含む日は `dry_run` として記録のみ・判定の母数に数えない。出所の判定は行の `logic_version` で行い、`races.win_odds_observed_at`(確定後に上書きされる)では行わない。h 帯別の paired 差・件数・h 分布を必ず出力する。
- **FR-009**: 評価ハーネス `score.py --from --to` は、対象日の `market_ev_predictions`(`model_version='mev-ens15-v1'`・`result_pending_at_compute`・結果あり)から全馬の `odds_used`・`odds_observed_at` を取り、h = post_time − odds_observed_at(分・下限 1)で各腕を採点する。出力は `results/<from>_<to>.json`(腕別の指標・現行版との差の開催日ブロック bootstrap 95% 区間・レース数・開催日数・的中数・公式払戻の有無)。同じ区間の再実行は既存ファイルを上書きしない。
- **FR-010**: 採否の判定は、評価対象が **8 開催日以上かつ 200 レース以上**に達するまで出さない(それ未満は「記録のみ」)。判定規則(事前登録): (i) ①-a は確定オッズの 2026 OOS(≤06-26)で NLL ≤ 現行+0.0002 かつ正規化 winner NLL 非悪化、(ii) 各腕の判断時 NLL_J の現行版との paired 差の 95% 区間上限 < 0、(iii) EV_J>1.0 帯の Σwon/Σp̂_J が現行版より 1 に近い(paired 差の区間が 0 を含まないことが望ましいが、的中数が少ない間は方向のみ記録)。保持率・EV_F/EV_J・ROI は受入に使わない。`--judged-seeds` の partial 平均は診断腕で、記録はするが規則 (ii)(iii) の対象にしない(protocol v2)。レース内で `odds_observed_at` が複数値のレースは母集団から除外して数える(protocol v2・fail-closed)。c1/c2 は `log h` 主効果が構造的に不活性で h 依存を表現できない軽量版と読む(plan §4.3 注記)。
- **FR-011**: どの出力にも「ROI の改善を示すものではない」「判断時専用版は表示・確定側に接続しない」を明記する。

## Success Criteria

- **SC-001**: migration 0021 適用後、odds ジョブ→entries 再取込の順で `win_odds_observed_at` が odds ジョブの時刻のまま(統合テスト+ローカル DB で次開催日に確認)。
- **SC-002**: `market_ev` の summary にフォールバック件数が出て、A3 適用後の新規計算ではフォールバックが netkeiba 期のレースで 0。これを **expected_return ジョブの `summary.provenance`**(`fallback_races=0`・`final_odds_provenance_missing=0`・`error_message` なし)で確認する(stdout の `market-ev-provenance:` 行と同値)。
- **SC-003**: ①-a 15 seed が決定論で学習でき、確定オッズ 2026 OOS NLL が現行と ±0.0002 以内。
- **SC-004**: ハーネスが 10-10/10-11 を採点し、4 腕の指標と paired 区間を `results/` に残す(判定は出さない)。
- **SC-005**: 既存テスト(db・scrape・training・api・ops・live・front)が緑。

## 実測結果(2026-10-12・詳細は plan §4)

- A3: migration 0021 適用・worker/ops-api/api 再起動済(08:21 JST)。`update_odds` は値不変でも来歴を書く(馬番が 1 頭も一致しない payload は書かない)・`official_at` 逆行ガード(`FOR UPDATE`)・`apply_final_odds(observed_at=)` は確定値の来歴(`official_at=NULL`)。market_ev は行ごとに `oddsobs=quote|proxy` を刻み、summary と CLI 行 `market-ev-provenance:`・ops の `summary.provenance` に fallback 件数と `final_odds_provenance_missing`(起点 2026-10-13)を出す。
- **FR-010 (i) 不合格**: 判断時専用版 v0 は確定オッズ 2026 OOS(≤06-26・1,591 レース)で NLL +0.00042 [+0.00010, +0.00075]・winner NLL +0.0039(許容 +0.0002・非悪化)。spike の「不変」は 06-27 以降の発走前オッズが混ざった窓の産物(同窓で +0.00008 と再現)。ρ の推定量差は無関係。
- 初回採点(10-10/10-11・35 レース・dry-run): judged は現行より NLL_J +0.0023 [+0.0013, +0.0038] と悪化(h 中央値 24 分=直前の計算では現行の較正 0.92〜1.01 が既に良い)。c1/c2 は NLL_J −0.0012(区間 0 跨ぎ)・係数 b1≈0 で補正後の EV_J>1.0 は 0 頭。判定は `recorded_only`。
- 母集団の制約: 「発走前の最後の計算」が残るレースだけが評価対象(10-10 22/24・10-11 13/24)。利用者の更新タイミングに依存する。
