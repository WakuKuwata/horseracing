あなたは独立レビュアーとして呼ばれた Codex です(この依頼の中でさらに別のレビューを起動しない。リポジトリ全体を読み込まず、以下に埋め込んだ情報だけで答える。実装は不要。レビューと代替案だけを返す)。

# 目的
競馬(JRA)アプリ。期待回収率モデル(市場連動 LightGBM・15 seed・確定オッズで学習)が判断時オッズ(発走の中央値 6 時間前)で適用されるため、判断時 EV>1.0 の馬の Σwon/Σp̂_J が 0.57(確定時 p̂ では 0.80)と約 2 倍過大。これを直す 2 案(①-a 判断時相当のオッズずれを注入した再学習版、①-c 現行 p̂_J への 4 係数ロジット補正)を**並走評価**するハーネスと、その前提 A3(単勝 quote の取得時刻の来歴)を実装する feature 140 の plan を書いた。前回の codex レビュー(10/10)で「受入は新規開催日での現行版との paired 改善+CI」「生成器は評価に使わない日で固定」「判断時専用版は表示・確定側に接続しない運用分離」を採用済み。

# 既存の制約
憲法 I(2007 年以降のみ学習)・II(本番勝率モデルに市場を入れない)・III(事前登録・OOS・選択窓と確認窓の分離)・V(オッズ履歴を DB に保存しない。「単一最新値の来歴(観測時刻・公式時刻)」は複勝 quote で前例あり= races.place_odds_official_at / place_odds_observed_at)・VI(API 純追加・契約先行)。注目条件 S1〜S5 の前向き記録(attention_picks)は append-only で odds_observed_at を凍結保存している。前向き pick の鮮度帯(10 分/60 分)はこの値で決まる。

# 現状のコード(事実)
- scrape: netkeiba の odds API(type=1)は単勝+複勝を同一レスポンスで返し、`official_datetime` を `scraped.official_at` として読む。`update_odds` は result-pending のレースでは単勝を上書き、結果確定後は NULL の行だけ埋める。複勝は `update_place_quote` が全馬揃ったときだけ書き、`official_at` の逆行 payload を拒否し、`races.place_odds_official_at/observed_at` を書く。139 で `apply_final_odds` が結果ページの確定単勝で `race_horses.odds` を上書き(netkeiba 期 2025-10-11 以降・値が変わる行のみ)。
- training `market_ev.py`: ROWS_SQL が `rh.updated_at as horse_row_updated_at` を読み、予測行の `odds_observed_at` に改名して保存。`race_horses` の BEFORE UPDATE トリガは WHEN なしで、`upsert_entries` は毎回 ON CONFLICT DO UPDATE → 出走表だけ再取込しても時刻が進む。実害は現状ほぼゼロ(前向き pick 21 件はすべて odds ジョブ書込の 24 秒後)。
- `market_ev_predictions` はレース単位置換で、結果確定後は `--pending-only` で触らないため「結果確定前の最後の計算」の全馬ベクトル(odds_used・win_prob・odds_observed_at・result_pending_at_compute)が残る。10-10 285 行・10-11 198 行・10-12 51 行(結果待ち)。
- 判断時の実データ: R02 pairs.parquet(564 組・444 レース・2026-08-02〜10-04 の 17 開催日・判断時/確定時の同一馬ベクトル)。10 分以内は 8 レース。

# plan(要点)
0.1 migration 0021: `races.win_odds_official_at`・`races.win_odds_observed_at`(timestamptz NULL)。既存行は NULL(埋め戻さない)。
0.2 scrape: `update_odds` は単勝の書込行数>0 かつ result-pending のとき両列を書く(official_at=payload の official_datetime、observed_at=now)。保存済み `win_odds_official_at` より古い payload は単勝を書かず skipped(複勝と同型)。結果確定後の fill-if-null 経路では触らない。`apply_final_odds(observed_at=)` は updated>0 のとき `win_odds_observed_at=結果ページ取得時刻`・`win_odds_official_at=NULL`(アーカイブ修復も同関数=ファイル時刻)。
0.3 training: ROWS_SQL を `coalesce(r.win_odds_observed_at, rh.updated_at)` にし、フォールバック行数・レース数を compute_and_persist の summary に常に出す。LOGIC_VERSION / ENSEMBLE_LOGIC_VERSION の末尾に `;oddsobs=quote` を追記(attention_picks の PICK_LOGIC_VERSION は ENSEMBLE_LOGIC_VERSION に `;policy=` を足すので自動追随)。
0.4 研究ハーネス(本番非結線): (a) 注入生成器 `u=log(O_J/O_F)=a(h)+b(h)·log q_F+s(h)·(√0.16·ε_race+√0.84·ε_i)` を R02 17 日の組から帯別(0–10/10–30/30–60/60–180/180–600/600–3000 分)に推定して凍結(sha・期間)。(b) ①-a: 2007〜2025 の学習行を「未摂動(log_min=0)+注入(h は R02 の horizon 分布から抽出・レース内 Σ1/O 保存・市場 12 列を再計算)」の 2 倍にし、特徴 77+`log_min` で 2026 年 booster を 15 seed(1 スレッド・決定論・現行 params・300 rounds)学習。本番 loader は features 不一致で拒否することをテストで固定。(c) ①-c: `logit p' = β0+β1·logit p̂_J+β2·log h+β3·log q_J` を c1=R02 実組(5,967 頭)・c2=2025 年 OOF 行に生成器を当てた模擬判断時行で fit し凍結。(d) `score.py --from --to`: 評価母集団=結果確定済みかつ結果確定前の ens15 計算が残るレース(10-10 以降のみ。fit 期間 ≤10-04 を含む範囲は拒否)。判断時特徴=現 DB の as-of 65 列+`odds_used` から再計算した市場 12 列(天候・馬場は現在値=既知の限界)。h=max(1,(post_time−odds_observed_at)/分)。腕=ens15 現行/judged/c1/c2。指標=馬単位 NLL_J・レース内正規化 winner NLL_J・EV_J 帯別(≤1.0/1.0–1.2/>1.2)の Σwon/Σp̂_J・EV_J>1.2 件数・公式払戻での精算 ROI(記録)・O_F/O_J。現行版との差は開催日ブロック bootstrap(B=4,000・seed 固定)。出力 append-only。protocol.json+sha を採点前に凍結。
判定規則(FR-010・事前登録): 8 開催日以上かつ 200 レース以上に達するまで判定しない。(i) ①-a は確定オッズ 2026 OOS(≤06-26)で NLL ≤ 現行+0.0002 かつ正規化 winner NLL 非悪化 (ii) 各腕の NLL_J の現行版との paired 差の 95% 区間上限<0 (iii) EV_J>1.0 帯の Σwon/Σp̂_J が現行より 1 に近い(的中少数の間は方向のみ記録)。保持率・EV_F/EV_J・ROI は受入に使わない。
適用順: 0021 → worker/ops-api/api 再起動(10-12 の最終レース後)→ ①-a 学習 → 10-10/10-11 を採点。

# 確認してほしいこと(3 問・各指摘に採用/不採用の判断材料となる理由を付ける)
Q1. A3 の設計: (a) 来歴を races(レース単位)に置く判断、既存行を埋め戻さず読み側 coalesce+件数開示にする判断、確定値の official_at=NULL、単勝の逆行ガードに穴はあるか。特に「結果確定後の fill-if-null 経路で来歴を書かない」ことで、netkeiba-only の確定済みレースに観測時刻が付かないケースはあるか(139 の apply_final_odds が結果取込時に走るので実質カバーされる、という理解は正しいか)。(b) LOGIC_VERSION への `oddsobs=quote` 追記は、既存の ens15 行・attention_picks の冪等/UNIQUE・API メモ化と衝突するか。
Q2. ハーネスの設計: (a) 評価母集団「結果確定前の最後の計算」は、判断時刻が運用(利用者の操作・自動追随)で決まり、発走直前の再計算が無い=h が大きい側に偏る。この偏りで ①-a/①-c の比較が歪む経路はあるか(両腕同じ母集団なので paired 差は守られる、という理解で足りるか)。(b) 判断時特徴の天候・馬場が現在値である限界が、腕の優劣を一方に偏らせる経路はあるか。(c) 10-10/10-11 の判断時 odds_observed_at は A3 前の代理値(updated_at)。採点に使ってよいか、それとも A3 後の日付からにすべきか。(d) 判定規則 (ii)(iii) と「200 レース/8 開催日」の最小標本は妥当か。NLL_J の paired 差は全馬で測れるが、較正(Σwon/Σp̂_J)は的中数依存。
Q3. リーク・時系列: (a) c1 を R02 実組(≤10-04)で fit し 10-10 以降で評価=分離は十分か。(b) c2 の模擬行は 2025 年 OOF(2007〜2024 学習の booster の 2025 予測)に生成器を当てる。生成器は 2026 年の組で推定=2025 年の模擬行に 2026 年の価格挙動を当てる時系列の逆転があるが、ラベル(2025 年の勝敗)には触れない。問題か。(c) ①-a の注入に R02 の horizon 分布を使うのは「運用時の h 分布」を学習に入れることになる。評価が同じ分布なら楽観側に見える経路はあるか。(d) その他、このリポジトリ固有の parity/fail-closed/契約の穴。

出力形式: 「採用すべき指摘」「反対(理由)」「保留(何を測れば決まるか)」の 3 節・合計 25 行以内の箇条書き。
