# Implementation Plan: 140 単勝 quote の来歴(A3)+判断時 EV 整合モデルの並走評価

**Branch**: `140-judged-ev-alignment` | **Spec**: `spec.md`

## 0. 契約

### 0.1 DB(migration 0021 `0021_win_odds_provenance`・down_revision `0020_official_win_payouts`)
- `races.win_odds_official_at TIMESTAMPTZ NULL`・`races.win_odds_observed_at TIMESTAMPTZ NULL`。既存行は NULL。
- ORM `Race` に 2 列(`place_odds_*` と同じ注釈: 単一最新値の来歴であって履歴ではない=憲法 V)。
- head 固定テストを 0021 に更新(features 9 本・live 1 本・db の downgrade 断言)。

### 0.2 scrape
- `upsert.update_odds(session, race_id, scraped)`: result-pending のレースで payload に有効な単勝(odds>0)が 1 頭以上あり、かつ `scraped.official_at` が保存済み `win_odds_official_at` 以上(保存済みが NULL なら無条件・同一時刻は可・payload の `official_at` が NULL なら observed_at だけ更新)のとき、**値が変わらなくても** `Race.win_odds_official_at=scraped.official_at`・`win_odds_observed_at=now(UTC)` を書く(codex: 値が同じでも新しい quote は新しい来歴)。保存済みより古い `official_at` の payload は単勝も来歴も書かず `skipped` に数える(複勝の単調性ガードと同型)。結果確定後の fill-if-null 経路では `win_odds_*` を触らない(確定値の来歴は FR-003)。比較は tz-aware(payload の JST → UTC)で、テストに NULL 既存値・同一時刻・未来時刻・tz の 4 例を置く。**来歴を書くのは `race_horses` を 1 行以上一致させたとき(`written>0`)だけ**(レビュー a3-scrape-db-1: payload の馬番が 1 頭も一致しない=entries 未取込・馬番ずれでは保存値が古い quote のままなので来歴を進めない。同価格の再 quote は PG の UPDATE rowcount が一致行を数えるので `written>0` のまま=「値が同じでも新しい quote は新しい来歴」は保たれる)。逆行ガードの SELECT は `FOR UPDATE`(a3-scrape-db-2: live CLI と ops worker が同一レースに同時に書いても read-compare-write が原子的)。
- `upsert.apply_final_odds(session, race_id, rows, *, observed_at)`: 署名に `observed_at` を追加(pipeline は結果ページの取得時刻を持っている=139 の `apply_result_page_settlement(observed_at=)`)。`updated>0` のとき `win_odds_observed_at=observed_at`・`win_odds_official_at=NULL`。
- `final_odds_repair.py`(アーカイブ修復)も同じ関数を通るので、アーカイブのファイル時刻が `observed_at` になる(139 と同じ規約)。

### 0.3 training `market_ev`
- ROWS_SQL: `coalesce(r.win_odds_observed_at, rh.updated_at) as horse_row_updated_at`、`(r.win_odds_observed_at is null) as odds_observed_fallback`。
- `compute_and_persist` summary に `odds_observed_fallback_rows`・`odds_observed_fallback_races`(対象範囲の行・レース数)と、**netkeiba 期(race_date ≥ 2025-10-11)で結果確定済み・単勝あり・`win_odds_observed_at` NULL のレース数 `final_odds_provenance_missing`**(139 の `apply_final_odds` が全対象で走ったかの fail-closed 監視。codex 採用)。
- `LOGIC_VERSION`・`ENSEMBLE_LOGIC_VERSION` の末尾に `;oddsobs=quote`。`EnsembleMarketEvModel.load` の logic_version ガードは定数を読むので追随(テストで固定)。`attention_picks` の `PICK_LOGIC_VERSION` は **`oddsobs` トークンを外した** ENSEMBLE_LOGIC_VERSION に `;policy=` を足す(レビュー training-token-3: pick 行は `odds_observed_at` を market_ev 行から複写するので proxy 時刻を持ちうるのに、定数が `oddsobs=quote` だと行内で印と値が食い違う。pick の logic_version は選定方針の版に純化し、来歴は同 run_id・同レース・同馬の market_ev 行のトークンで読む。既存行は append-only で書き換えない)。
- **summary の 3 キーは stdout と ops ジョブに出す**(レビュー training-token-1): CLI は最初の `market-ev:` 行の直後に専用行 `market-ev-provenance: fallback_rows=N fallback_races=M final_odds_provenance_missing=K proxy_rows_written=P` を OK/SKIPPED 両経路で印字(最終行マーカー契約は不変)。ops `run_expected_return` は `_parse_marker` 同型でこの行を読み `job.summary['provenance']` に保存し、**`final_odds_provenance_missing>0` なら checkpoints=error と同型に `error_count`/`error_message`(`provenance: final_odds_provenance_missing=K`)を立てる**(ジョブは SUCCEEDED/SKIPPED のまま。計算値は正しく、欠けているのは来歴なので「ジョブ一覧に見える」ことが目的)。両者に対応テスト。
- `odds_observed_fallback_rows/races` は**対象範囲の入力行**(書込対象に限らない=race_ok=False・pending-only で untouched の確定済み・odds<1.0 で落としたレースも含む・training-token-4)。実際に proxy で**書いた**行数は版ごとに `versions[*]['odds_observed_proxy_rows']`(CLI の版別 `market-ev:` 行に `proxy_rows=`・提案行に `proxy_rows_written=`)で併記し、run 単位の `logic_version`(契約値=`oddsobs=quote` のまま)と行の実値を照合できるようにする(training-token-5)。
- `EnsembleMarketEvModel.load` は `judged: true` の spec を `EnsembleIntegrityError` で拒否する(harness-leak-protocol-1。従来は version 不一致と `_design_matrix` の KeyError(log_min)で止まっていたが、version を本番値に書き換えた spec は予測時まで通ってしまった)。
- `_row_values` は `rec["odds_observed_at"]` をそのまま使う。**行ごとの来歴の印**: `odds_observed_fallback`(ROWS_SQL)を `predict`/`predict_ensemble` が透過し、`_row_values` が fallback 行の `logic_version` を `oddsobs=quote` → `oddsobs=proxy` に置き換える(`row_logic_version`・None/NaN は proxy=fail-closed)。定数 `LOGIC_VERSION`/`ENSEMBLE_LOGIC_VERSION` は `oddsobs=quote` のまま。理由は D12(確定後に `apply_final_odds` が `races.win_odds_*` を上書きするので、計算時の出所は行にしか残らない)。行ごとのトークンは `recommendations.logic_version` の `prospective=1`(065)と同型。
- `FINAL_ODDS_PROVENANCE_FROM = 2026-10-13`(A3 稼働日の翌日・D11)。

### 0.4 研究ハーネス `scripts/roi_explore/judged_20261011/`
- `fit_generator.py` → `generator.json`(帯 (0,10]/(10,30]/(30,60]/(60,180]/(180,600]/(600,3000] 分の a/b/s・ρ・入力 pairs.parquet の sha256・期間・出所の内訳)。R02 の `pairs.parquet` から `m2_drift_reverse.py`(evidence)と同じ帯別 OLS。**ρ は ICC**(1 − プールしたレース内残差分散 / 全残差分散)=**0.080**。m2 の「レース内平均残差の分散比」0.16 は (1−ρ)/n̄ だけ上に偏る推定量で(合成テストで 0.16 の真値に 0.228 を返した)、比較のため `rho_racemean_estimator` として併記するだけ(D13)。
- `train_judged.py --seed s` → `artifacts/market_ev/judged/mev-ens15-judged-v0/seed_NN/{model_2026.txt, model.spec.json}`。学習データ: `rows_2007.parquet` の race_ok ∧ ¬dead_heat ∧ year≤2025 を「未摂動(log_min=0)+注入(h を R02 の組の horizon 分布から抽出・`u` で odds を摂動・レース内 Σ1/O を保存・市場 12 列を `market_block` で再計算)」の 2 倍。params は現行(lr 0.05・leaves 63・min_leaf 1000・ff 0.8・bagging 0.8/1・λ2 10・300 rounds)+`num_threads=1, deterministic=True, force_row_wise=True`。spec に `objective='binary'`・`features`(78)・`generator_sha256`・`judged=True` を書き、本番 `EnsembleMarketEvModel.load` が `features` 不一致で拒否することをテストで固定。
- `fit_correction.py` → `coefficients.json`: c1 = R02 `scored_pairs.parquet`(判断時 p̂_J(seed 平均)・h・q_J・won)で 4 係数のロジスティック回帰、c2 = 2025 年 OOF 行に generator を当てた模擬判断時行(p̂ は現行 2025 booster の予測)で同じ 4 係数。
- `score.py --from --to [--results-dir]`: 各レースの `odds_observed_at` の出所を **行の `logic_version` の `oddsobs=` トークン**(全行 `oddsobs=quote` → quote・`oddsobs=proxy` か無印=A3 前の計算 → proxy)で分け(`races.win_odds_observed_at` は確定後に上書きされるので使わない=D12)、**proxy(A3 適用前=`updated_at` 代理)の日は `dry_run: true` として記録のみ・判定の母数に数えない**(codex 採用)。h 帯別((0,10]/(10,30]/(30,60]/(60,180]/(180,600]/>600 分)の paired 差・EV_J>1.0 件数・h 分布を必須出力にし、特定帯だけの改善を全体採用と誤読しない。評価母集団(FR-009)を DB から読み、`market_ev.load_rows/build_features` で as-of 特徴を作り、市場 12 列を `odds_used` から再計算(`r02_drift.market_block` を関数として取り込む)、h=max(1, (post_time−odds_observed_at)/分)。腕: `ens15`(現行 15 seed 平均)/`judged`(①-a 15 seed 平均・log_min=log h)/`c1`/`c2`。指標: 馬単位 NLL_J・レース内正規化 winner NLL_J・EV_J 帯別(≤1.0/1.0–1.2/>1.2)の n・won・Σp̂・Σwon/Σp̂・EV_J>1.2 の件数と公式払戻精算 ROI(official_win_payouts・無ければ除外して件数を出す)・O_F/O_J(`race_horses.odds` 現在値=確定)。現行版との paired 差は開催日ブロック bootstrap(B=4,000・seed 固定)。出力は append-only。fit 期間(≤2026-10-04)を含む `--from` は拒否。
- `protocol.json` + `protocol.sha256`(判定規則 FR-010・腕・指標・bootstrap seed)を採点前に凍結。**v1(2026-10-12)**: 来歴の判定を行トークンに改訂・ρ を ICC に。v0 の下で有効(非 dry-run)な採点は 1 件も無く、v0 の dry-run 出力 1 件は破棄(`revisions` に記録)。
- `closing_oos.py` → `results/closing_oos_2026.json`: FR-010 (i)/SC-003 の確定オッズ 2026 OOS(≤06-26・1,591 レース・52 開催日)で ens15 と judged(log_min=0)の NLL・winner NLL の paired 差(開催日ブロック bootstrap)。

### 0.5 ops
- `_maybe_enqueue_expected_return` は不変(odds.written>0 で発火。0.2 の `written>0` 条件で来歴とも厳密に整合)。
- `run_expected_return` が CLI の `market-ev-provenance:` 行を `job.summary['provenance']` に保存し、`final_odds_provenance_missing>0` を `error_count`/`error_message` に載せる(0.3・training-token-1)。

## 1. 実装順

1. DB 0021 + ORM + head 固定 → 2. scrape(update_odds/apply_final_odds/pipeline/repair・テスト)→ 3. training market_ev(ROWS_SQL・summary・logic_version・テスト)→ 4. 研究ハーネス(generator・train_judged・fit_correction・score・protocol)→ 5. レビュー → 6. ローカル適用: migration → worker/ops-api/api 再起動(**10-12 の最終レース後**)→ ①-a 15 seed 学習 → 10-10/10-11(/12)を採点 → 7. 記録(spec/plan に結果・CLAUDE.md・memory)。

### 1.1 T011 ローカル適用の手順(順序が正しさに関わる・**今日 10-12 の最初の refresh より前**)

**前提の訂正(レビュー training-token-2・high)**: 当初「worker/ops-api/api は editable install で再起動まで旧コード」と書いたが、**ops worker はジョブごとに `uv run --project <training|serving|betting|live>` を作業ツリー cwd で起動し、各 venv は `_editable_impl_horseracing_db.pth` でこのツリーを import する**ので、subprocess 経路は再起動なしに**今の 140 コード**で動く。本番 DB が 0020 のまま(`races.win_odds_*` 列なし)でこのツリーに 140 の変更がある限り、開催日の refresh 1 回で次が起きる: `serving/pipeline.py`・`live/orchestrate.py` の `session.get(Race)` が UndefinedColumn(predict 失敗)/ `scrape/upsert.py` の `select(Race.win_odds_official_at)`(pending レースの odds ジョブ失敗)/ market_ev ROWS_SQL(expected_return 全件 FAILED)/ **`apply_final_odds` は `begin_nested` 内で黙って失敗し、結果は保存されるが単勝は発走前値のまま確定扱い**(139 R05 の再発・来歴も付かない)。= **0021 を当てるまでスタックは潜在故障状態**。最後のジョブは 10-11 23:12 JST・コード変更は 10-11 23:21/10-12 07:39 なので、まだどのジョブも新コードに当たっていない。

**手順(migration が先・再起動は後・コード反映だけ先行させない)**:

```bash
cd db && DATABASE_URL=postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing uv run alembic upgrade head   # → 0021_win_odds_provenance
```
```bash
scripts/stack.sh restart worker && scripts/stack.sh restart ops-api && scripts/stack.sh restart api
```

確認(詳細は `quickstart.md`): `alembic current` が 0021 / `information_schema.columns` に 2 列 / 次の開催日の odds ジョブ後に `races.win_odds_official_at`・`win_odds_observed_at` が入り、entries 再取込で動かない(SC-001)/ 新規 `market_ev_predictions` 行の `logic_version` が `oddsobs=quote` で終わり、expected_return ジョブの `summary.provenance` が `fallback_races=0`・**`final_odds_provenance_missing=0`**・`error_message` なし(SC-002)。

**適用が 10-13 のレース確定より後にずれた場合**: 10-13 以降に確定したレースは `apply_final_odds` の来歴を持たず `final_odds_provenance_missing` が恒久的に >0 で張り付く(全 expected_return ジョブに `error_message` が立つ)。`FINAL_ODDS_PROVENANCE_FROM` を実適用日の翌日に合わせるか、該当レースの `win_odds_observed_at` を `official_win_payouts.observed_at` から埋める(D11 の別作業)。コードのコメント「A3 went live 2026-10-12」は適用して初めて事実になる。

## 2. 決定

| # | 決定 | 理由 |
|---|---|---|
| D1 | 来歴は `race_horses` でなく `races` に置く(レース単位) | quote は 1 payload=1 レース分で全馬同時刻。複勝の前例(`place_odds_*`)と同じ |
| D2 | 既存行は埋め戻さない。読み側は `updated_at` にフォールバックし件数を出す | 代理値を来歴に見せない。前向き pick は append-only で書換不可。netkeiba 期の新規計算でフォールバック 0 が SC-002 |
| D3 | 確定値(結果ページ)の `win_odds_official_at` は NULL | 確定オッズの「公式時刻」は締切後で quote の時刻ではない。`observed_at` だけで「発走後に取った値」と分かる。来歴はレース単位(憲法 V・複勝と同形)なので、`apply_final_odds` が unmatched/unreadable の馬を残して 1 頭でも書いたレースでは、来歴は書けた馬の確定値を指し該当馬の odds は発走前 quote のまま(構造的に表現できない混在)。該当馬は 139 の `final_odds_unmatched`/`final_odds_unreadable` 件数で監視する(a3-scrape-db-3・修正不要と判断) |
| D4 | 単勝にも `official_at` 逆行ガード | 再送・古いレスポンスで最新値が巻き戻るのを防ぐ(複勝と同じ) |
| D5 | ①-a は 2026 年 booster だけを学習(17 年 walk-forward はしない) | 評価対象が 2026-10-10 以降の判断時データと 2026 OOS(確定)だけ。2010〜2025 の判断時データは存在しない |
| D6 | 生成器は R02 17 日で固定、評価は 10-10 以降 | codex 指摘「生成器選択の楽観」。fit 日と評価日を日付で分離しハーネスが拒否 |
| D7 | ①-c は 4 係数のロジット補正に固定(帯別係数は作らない) | 的中 20 件規模で帯別は推定不能。c1(実組)と c2(模擬)の両方を記録 |
| D8 | 受入は paired 差+CI、保持率・ROI は使わない。200 レース/8 開催日未満は判定しない | codex 指摘。1 点あたり払戻 SD 6〜7 で ROI は判定不能・NLL_J は全馬で測れる |
| D9 | 判断時特徴の天候・馬場は現在値 | R02・10/05 価格診断と同じ限界。前向きの全馬凍結(B1)が無い間は避けられない |
| D10 | 本番非結線 | 評価前。`EnsembleMarketEvModel.load` が features 不一致で拒否(fail-closed) |
| D11 | `final_odds_provenance_missing` の起点は **2026-10-13**(A3 稼働日の翌日)。139 期(2025-10-11〜2026-10-12)の確定値来歴は埋め戻さない | 139 期の確定済み約 3,500 レースは来歴が無く、うち 06-27〜08-09 の 504 レースは確定値そのものが無い。2025-10-11 起点だと監視値が恒久的に数百〜数千で張り付き、本来捕まえたい「apply_final_odds が走らなかった」1 件が見えない。`official_win_payouts.observed_at` からの正確な埋め戻しは別作業として可能 |
| D12 | 計算時の来歴の出所は **行の `logic_version`**(`oddsobs=quote|proxy`)に残す。採点側はこれで quote/proxy を決める | `races.win_odds_*` は単一最新値で、結果確定時に `apply_final_odds` が上書きする(憲法 V)。採点時にそこを見ると、A3 前に proxy で計算したレースも確定後は quote に見える=codex の「代理値の日を判定に混ぜない」が守れない。行トークンは `prospective=1` と同型で、消費側は等値比較していない(api は表示のみ・attention は別定数) |
| D13 | 生成器の ρ は ICC(1 − within/total)=0.080 | レース内平均残差の分散比は (1−ρ)/n̄ だけ上に偏る(合成 0.16 → 0.228)。spike の 0.16 はこの偏った値。m2 参照値との比較は併記で残す |

## 3. codex レビュー(2026-10-11・`codex exec --sandbox read-only`・gpt-5.5)

プロンプト `codex-prompt.md`・出力 `codex-review.md`。採否:

採用(契約を修正した): (1) `update_odds` の来歴更新を「書込行数>0」でなく「有効 payload かつ official_at が進んだ(同一可)」に変更=値が同じでも新しい quote は新しい来歴(0.2)。(2) `apply_final_odds` が全対象で走ったかを fail-closed に監視する `final_odds_provenance_missing` を summary に追加(0.3)。(3) 逆行ガードのテストに NULL 既存値・同一時刻・未来時刻・tz の 4 例(0.2)。(4) 10-10/10-11 の `updated_at` 代理値は dry-run 扱いで判定母数に入れない(0.4)。(5) h 帯別の paired 差・件数・h 分布を必須出力(0.4)。(6) FR-010 は NLL 主・較正は方向確認に限ると明文化(spec の記述どおり)。(7) 本番 loader の拒否テストは必須(0.4)。

同意(変更なし): 確定値の `official_at=NULL`、`;oddsobs=quote`、c1 の fit/評価の日付分離、c2 の時系列逆転はラベル非接触で致命でない(研究腕として凍結・表示)、天候・馬場の現在値は全腕共通で一次比較を無効にしない。

保留(測って決める): 「最後の計算」の偏りは h 帯別の結果で運用帯限定の採用を判断/①-a の R02 horizon 分布の楽観性は評価日の実 h 分布との距離と帯別性能で判断/`apply_final_odds` のカバーは `final_odds_provenance_missing` が 0 に近いことを確認してから/c1・c2 の優劣は h 帯別で順位が反転しないかを見る。

不採用: なし。

### 3.1 独立レビュー(2026-10-12・4 レンズ+反証)の採否

| id | severity(反証後) | 採否 | 対応 |
|---|---|---|---|
| training-token-2 | **high**(medium から昇格) | 採用 | 本番 DB が 0020 のまま+subprocess 経路は再起動なしに新コードを読む=潜在故障。§1.1 を全面訂正・`quickstart.md` 新設・`FINAL_ODDS_PROVENANCE_FROM` のコメントを「計画であって事実ではない」に。**適用そのものは利用者の操作**(本番 DB は読み取り専用) |
| training-token-1 | medium | 採用 | CLI 専用行 `market-ev-provenance:` + ops `summary.provenance` + `final_odds_provenance_missing>0` を error_message に(0.3/0.5)。テスト: training unit 2・persist e2e・ops 5 |
| a3-scrape-db-1 | low(medium から降格) | 採用 | `update_odds` の来歴書込を `written>0` 条件に。回帰テスト「馬番が 1 頭も一致しないと来歴は動かない」 |
| statistics-1 | low(medium から降格) | 注記のみ | c1/c2 の `log h` 主効果は 1 レース 1 勝+レース内正規化入力の下で構造的に不活性(cluster SE で b2 0.035→0.002・落としても LL 変化 0.000/0.009)。「b2≈0=h は効かない」と読まない。**v2 候補(帯別 b3・log h×log q)は事前登録しない**: 反証で帯別 b3 は c2 でも平坦(1.28〜1.29)・交互作用の符号は生成器の機構と逆(>600 帯のノイズ減衰が駆動)で、「1/(1−b_band) に対応」の根拠が無い。§4.3 に注記 |
| a3-scrape-db-2 | low(未検証) | 採用 | prior の SELECT に `with_for_update()`。複勝ガードは別タスク |
| a3-scrape-db-3 | low(未検証) | 注記のみ | D3 に 1 行(race 単位来歴の設計上の限界・139 件数で監視) |
| training-token-3 | low(未検証) | 採用(案 A) | `PICK_LOGIC_VERSION` から `oddsobs` トークンを外す(0.3)。test_attention_picks の期待を更新 |
| training-token-4 | low(未検証) | 採用 | docstring/spec に「入力行」と明記+版ごとの `odds_observed_proxy_rows`(書込行) |
| training-token-5 | low(未検証) | 採用 | 提案行の `proxy_rows_written=` と版別 `proxy_rows=` で照合可能に。run 単位 `logic_version` は契約値のまま |
| harness-leak-protocol-1 | low(未検証) | 採用 | loader に `judged` 拒否 1 分岐+training unit テスト+harness テストの順序更新。spec FR-007 の記述を実ガードに是正 |
| harness-leak-protocol-2 | low(未検証) | 採用 | `verdict_block(diagnostic_arms=)`: partial judged は記録のみ・規則 (ii)(iii) を適用しない(`verdict.diagnostic_arms_not_judged`)。protocol v2 の arms 記述に反映 |
| harness-leak-protocol-3 | low(未検証) | 採用 | score/fit_generator/fit_correction の main 末尾で免責 2 文を stdout に |
| harness-leak-protocol-4 | low(未検証) | 変更なし(事前登録値) | A3 稼働後の初回 results で `h_distribution` と generator.json の pool を並べて記録。乖離が続けば新 version の generator を再事前登録(既存 generator.json は編集しない)。§4.4 |
| statistics-2 | low(未検証) | docstring+テスト | `perturb_odds` の docstring に「a と ε_race は再正規化で打ち消される・実効は b と s√(1−ρ)」、合成テストでレース内分散 s²(1−ρ) を検査。**generator.json の method 文は変更しない**(sha が judged spec に束縛されている・親に報告) |
| statistics-3 | low(未検証) | 注記のみ | b1/b3 は共線(corr 0.98・VIF 27.7)で個別には弱識別・実質量は b1+b3(c1: 1.312±0.07)。coefficients.json は変更しない(§4.3 注記) |
| statistics-4 | low(未検証) | 採用(**protocol v2**) | レース内で `odds_observed_at` が複数値のレースを除外して数える(`odds_observed_at_mixed_within_race`)。10-05〜10-12 の pending run 41 レースは全て 1 値=影響ゼロ。v1 の唯一の出力(10-10/10-11 dry-run)は v1 の sha のまま保持 |

## 4. 実装記録(2026-10-12)

### 4.1 実装とテスト
- A3(db 0021・scrape・training)と研究ハーネスを実装。テスト: db 132 / scrape 403 / training unit 687(+7 skip)/ training integration(market_ev persist・attention)60 / features leak-guard 49 / live 2 / harness 18。ruff: 本番変更ファイルは全てクリーン(ハーネスは E501 のみ残り=既存研究スクリプトと同じ扱い)。
- 独立レビュー(4 レンズ+反証): 採否は §3.1。反映後のテスト: training unit test_market_ev 68 / training integration(persist・attention picks・checkpoints)43 / scrape test_win_odds_provenance 19(全体は §4.1 末尾の再実行値)/ ops test_expected_return_flow 49 / harness 22。ruff: scrape・training・ops の src クリーン。
- ①-a 15 seed は 1 スレッド決定論で学習(seed 1 の再学習がバイト一致・57〜65 秒/seed)。生成器 ρ は ICC 0.080。

### 4.2 規則 (i)=確定オッズ 2026 OOS(≤06-26・1,591 レース・52 開催日・`results/closing_oos_2026.json`)
| 腕 | NLL | winner NLL | EV>1.2 件数 |
|---|---|---|---|
| ens15(現行) | 0.19939 | 1.9477 | 186 |
| judged v0(h=0) | 0.19981 | 1.9515 | 74 |
| paired 差 judged−ens15 | **+0.00042 [+0.00010, +0.00075]** | **+0.0039 [+0.0001, +0.0079]** | −60% |

→ **規則 (i) 不合格**(許容 +0.0002・winner NLL 非悪化の両方)。spike の「確定 OOS 不変」は 2026 全体(06-27 以降の保存オッズが発走前値のまま=判断時寄り)で測った値で、同じ窓で 15 seed を測り直すと +0.00008 [−0.00023, +0.00037] と再現する(`results/diagnostics/closing_window_full2026_15seeds.json`)。ρ の推定量差は原因でない(3 seed: 偏った ρ 0.148 で +0.00059・ICC 0.080 で +0.00048)。判断時専用版は確定側の精度を僅かに削り、確定側の EV>1.2 を 6 割消す=設計どおり「確定側に接続しない」前提だが、(i) の事前登録値は満たしていない。

### 4.3 初回採点(10-10・10-11・dry-run=`updated_at` 代理・`results/2026-10-10_2026-10-11.json`)
母集団 35 レース/483 頭(10-10 は 22/24・10-11 は 13/24=発走前の最後の計算が残るレースだけ。残りは利用者の更新タイミングで計算されずに確定した)。h の中央値 24 分(10-30 分帯に 276 頭/483)=「結果確定前の最後の計算」はほぼ直前なので、R02 の判断時分布(中央値 278 分)とは別物。ens15 の再計算値は保存 win_prob と全 483 行で一致(parity 0.0)。

| 腕 | NLL_J | winner NLL_J | EV_J>1.0 n/won/Σwon÷Σp̂ | EV_J>1.2 n | ΔNLL_J vs ens15 [95%] |
|---|---|---|---|---|---|
| ens15 | 0.21115 | 2.0114 | 40 / 2 / 0.92 | 9 | — |
| judged | 0.21347 | 2.0430 | 19 / 5 / 1.18 | 0 | **+0.00231 [+0.00132, +0.00375]** |
| c1 | 0.20996 | 2.0031 | 0 | 0 | −0.00119 [−0.00352, +0.00216] |
| c2 | 0.20988 | 2.0017 | 0 | 0 | −0.00127 [−0.00317, +0.00145] |

読み: (a) 直前(h≈24 分)では現行 ens15 の較正は既に 0.92〜1.01 で、判断時専用版はここでは**悪化**(帯別でも (10,30] +0.0004・(30,60] +0.0029)。(b) c1/c2 は b1≈0・b3≈1.3〜1.4=判断時の p̂_J は q_J を超える情報を持たず、補正後は EV_J>1.0 の馬が 0 頭になる(NLL は −0.0012 だが区間は 0 を含む)。(c) 2 開催日・dry-run なので判定は `recorded_only`。採否は 8 開催日・200 レース(現状の母集団密度で 5〜7 週末)後。

c1/c2 の読み方の注記(レビュー statistics-1/-3): (1) **c1/c2 は h 依存を表現できない軽量版**。`log h` は主効果のみでレース内定数なので、1 レース 1 勝+レース内正規化入力(logit p̂_J・log q_J)の下で Σ_race p'≈1 の制約が b2 を構造的にほぼ 0 に固定する(race-cluster SE で c1 0.035→0.002・c2 0.013→0.0005、落としても LL は c1 0.000/c2 0.009 しか動かない)。b2≈0 は「h は効かない」ではなく「この式では h を使えない」。生成器が b(h)=0.14→0.36 の h 依存を**構成上もつ** c2 でも b2=0.0017。h 依存は GBM の judged 腕(log_min を木に入れる)と h 帯別の paired 差で経験的に読む。v2 候補として帯別 b3 や log h×log q を事前登録するのは見送り(反証: 帯別 b3 は c2 でも 1.28〜1.29 と平坦・交互作用の符号は生成器の機構と逆)。(2) **b1 と b3 は共線**(logit p̂_J と log q_J の corr 0.98・VIF 27.7・corr(b1,b3)=−0.974)で個別値は弱識別。識別できているのは b1+b3(c1: 1.312±0.07)。報告 SE は逆ヘッセ(iid)で pair クラスタを無視するが、cluster SE でも b0/b3 は概ね同水準(b2 のみ上記の理由で潰れる)。coefficients.json は変更しない(凍結)。

### 4.4 T011 の実施(2026-10-12 08:19〜08:21 JST・最初の refresh の前)
レビュー training-token-2(high)により「10-12 最終レース後」の予定を前倒し: subprocess 経路が再起動なしに新コードを読むため、0021 未適用のままでは当日最初の refresh で predict/odds/expected_return が UndefinedColumn で落ち、`apply_final_odds` が黙って失敗して 139 R05 が再発する状態だった。`alembic upgrade head` → 0021(08:19)→ worker/ops-api/api 再起動(08:21)→ `/api/v1/health` が `alembic_current=alembic_head=0021_win_odds_provenance, schema_in_sync=true`・新コードの `load_rows` が本番 DB を読める・`final_odds_provenance_missing=0`。10-12 時点の `races.win_odds_*` は全行 NULL(埋め戻さない=D2)。SC-001/SC-002 は 10-12 の最初の odds ジョブ以降に quickstart の SQL で確認する(当日の計算行は `oddsobs=quote` になり、10-12 は dry-run でなく適格日になる見込み)。

### 4.5 残る作業と判断
- 以後、週ごとに `score.py --from --to` を追記(8 開催日・200 レースで判定)。**10-12 は最初の適格日**(`results/2026-10-12_2026-10-12.json`・23 レース/307 頭・全行 `oddsobs=quote`・dry_run=false): ens15 NLL_J 0.18336 / judged 0.18015(−0.0032)/ c1 +0.0006 / c2 −0.0005。1 開催日なので区間は出ない(記録のみ)。10-12 の運用: expected_return 39 ジョブ成功・fallback 0・`final_odds_provenance_missing` 0・確定 24 レース全てに確定値来歴=SC-001/002 成立。h は {'(0,10]': 0, '(10,30]': 307, '(30,60]': 0, '(60,180]': 0, '(180,600]': 0, '>600': 0}(中央値 21 分)。
- **①-a v0 は規則 (i) 不合格**(§4.2)。v0 のままでは採用に進めない。再設計(例: 判断時専用版を確定側と分離したまま h 条件付きの混合、または h 帯別の 2 段補正)は新規事前登録が要る。
- ①-c は b1≈0(判断時 p̂_J は q_J を超える情報を持たない)で、補正後は EV_J>1.0 の馬が出ない=「判断時の高 EV 表示に実体が無い」ことの定量。表示側の対応(②)は別 feature。
- results の v1 出力(10-10/10-11)は protocol v2 で再生成し数値は同一(v1 の出力は作業ログに保管)。protocol.json の revisions にある「v1 出力は v1 の sha のまま保持」は、再生成後は本記述が正。
