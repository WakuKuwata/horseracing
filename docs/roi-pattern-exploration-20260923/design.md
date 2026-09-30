# ROI>1.0 買い方の広域探索 — 設計(2026-09-23)

**問い**: アプリの規約(事前登録ゲート・単勝のみ・現行モデル)に縛られず、「一定のルールや条件」で ROI ≥ 1.0 になる買い方が存在するか。モデルを使う/使わないを問わず、先にパターンを多数定義してから検証する。

**位置づけ**: 109(凍結 393 本・単勝・2008〜)は生存 0。今回は (a) 発見期を 1986 年まで拡張(市場のみのパターンは 3 倍の標本) (b) 券種を複勝・組合せに拡張(実配当 2025〜26) (c) データマイニング型(貪欲探索・ROI 直接学習モデル)も許容 (d) 資金配分の変種(均等/逆オッズ/エッジ比例) を加える探索。**探索的**であることを明示し、勝者の呪いは時間分割(発見→資格→確認)と帰無シミュレーションで制御する。

## 1. データ(単一 parquet `artifacts/roi_explore/rows.parquet`)

母集団: `race_horses.entry_status='started'` かつ平地(track_type≠'障')かつオッズあり。結果は精算のみに使う。

### 1.1 レース文脈(pre_entry で既知)
| 列 | 意味 |
|---|---|
| year, month, dow | 年・月・曜日(0=月) |
| venue_code | 競馬場コード(01 札幌…10 小倉) |
| race_number | レース番号 1..12 |
| is_last_race, is_first_race | 同日同場の最終/第 1 レース |
| distance, dist_band | 距離 m・帯(sprint<1400 / mile 1400-1799 / mid 1800-2199 / long ≥2200) |
| track_type | 芝 / ダ |
| going | 良 / 稍 / 重 / 不 |
| weather | 晴 / 曇 / 雨 / 小雨 / 雪 等 |
| race_class_canon | debut / maiden / C1 / C2 / C3 / OP(109 の canon_class) |
| grade | G1/G2/G3/L/None |
| is_graded | 重賞か |
| prize_money | 1 着本賞金(万円) |
| field_size | 出走頭数(started) |
| n_fav_under_2, n_odds_under_10 | オッズ 2 倍未満頭数 / 10 倍未満頭数 |
| fav_odds, second_odds, odds_gap12 | 1 番人気オッズ・2 番人気オッズ・差 |
| fav_q | 1 番人気の devig 市場勝率 |
| q_entropy_norm | 市場 q の正規化エントロピー(0〜1・荒れ度計 066) |
| day_prev_n | 同日同場でこれより前のレース数(=race_number−1) |
| day_prev_fav_win_rate | 同日同場・先行レースで 1 番人気が勝った割合(先行 0 なら NaN) |
| day_prev_winner_pop_mean | 同日同場・先行レース勝者の人気平均 |
| day_prev_winner_style_front_share | 同日同場・先行レース勝者が逃げ/先行(running_style ∈ {逃げ,先行})だった割合 |

### 1.2 市場(closing で既知=価格リークあり・109 と同じ限界)
| 列 | 意味 |
|---|---|
| odds | 確定単勝オッズ |
| q | (1/odds)/Σ(1/odds) |
| popularity | 人気(DB 値) |
| odds_rank | オッズ順位(同値は馬番) |
| q_share_of_fav | q / fav_q |

### 1.3 馬の静的属性(post_draw で既知)
sex(牡/牝/セ), age, frame(枠 1..8), horse_number, weight(馬体重), weight_diff(増減), jockey_weight(斤量), jockey_id, trainer_id, sire_line, damsire_line, is_debut, career_starts(厳密前の出走数)

### 1.4 馬の履歴(strictly-before。同日除外。DB 現在値から作るので訂正履歴なし)
| 列 | 意味 |
|---|---|
| career_wins, career_win_rate, career_top3_rate | 通算 |
| prev_finish, prev2_finish, prev3_finish | 直近 3 走の着順(完走のみ数値・それ以外 NaN) |
| avg_last3_finish, best_finish_last5, wins_last5, top3_last5 | |
| prev_popularity, prev_odds, prev_q | 前走の人気/オッズ/市場勝率 |
| prev_finish_pct | 前走着順 /(前走頭数) |
| prev_beat_market | 前走 (人気 − 着順)(正=人気以上に走った) |
| prev_field_size, prev_distance, prev_track_type, prev_venue_code, prev_class_canon | 前走の条件 |
| dist_change, class_change | 距離差(m)・格の上下(+1 昇級 / 0 / −1 降級) |
| days_since_last | 間隔(日) |
| tataki_2 | 前走が 70 日超休み明け(叩き 2 走目) |
| prev_weight, weight_change_vs_prev | 前走体重・差 |
| prev_running_style | 前走の脚質(逃げ/先行/差し/追込) |
| prev_last3f_rank | 前走の上がり 3F 順位(完走馬内) |
| prev_margin_sec | 前走の 1 着との着差(秒・勝ちは負) |
| last_won | 前走勝ち |
| jockey_change | 前走と騎手が違う |

### 1.5 騎手・調教師の as-of(strictly-before・同日除外・365 日窓と通算)
jockey_win_rate_365, jockey_starts_365, jockey_win_rate_all, trainer_win_rate_365, trainer_starts_365, combo_starts_all(騎手×調教師), combo_win_rate_all, jockey_wins_today_before(同日同場の先行レースで勝った数)

### 1.6 モデル(2008〜2026・108 の全史 OOF 束・arm E・strict OOS)
p(win), p_top2, p_top3, p_rank(レース内 1 位=1), ev = p×odds, p_over_q, model_fav_is_market_fav, p_gap12(モデル 1 位と 2 位の差)

### 1.7 結果(精算専用・述語に使ってはならない)
won, finish_order, n_winners(同着), place_hit(複勝圏=3 着以内・7 頭以下は 2 着以内), 配当: div_place, div_quinella, div_wide, div_exacta, div_trio, div_trifecta(2025〜26 のみ・組合せキーつき)

## 2. パターン DSL(JSON)
```json
{"pattern_id": "M.band.odds_3_6", "family": "market_band", "label_ja": "単勝 3〜6 倍の全馬",
 "bet_type": "win",
 "race_filter": [{"field":"track_type","op":"eq","value":"芝"}],
 "horse_filter": [{"field":"odds","op":"in_band","lo":3.0,"hi":6.0}],
 "select": {"rule":"all"},
 "stake": "flat"}
```
- `op` ∈ eq / ne / in / not_in / ge / gt / le / lt / in_band(lo≤x<hi) / is_true / is_false / isnull / notnull
- `select.rule` ∈ all / top_k(by, k, asc) — レース内で horse_filter 通過馬を `by` で並べ上位 k 頭。asc=true は小さい順
- `stake` ∈ flat(100 円) / inverse_odds(払戻が揃うよう 100/odds 比例・合計 100 円×頭数に正規化) / edge(ev−1 に比例・モデル用)
- 組合せ券種(2025〜26 実配当のみ): `bet_type` ∈ place / quinella / wide / exacta / trio / trifecta。`combo` ∈ {"type":"box"}(選択馬の全組合せ) / {"type":"axis","axis":"first_selected","targets":"rest"}(1 頭軸流し)。選択馬集合は win と同じ race_filter+horse_filter+select で作る
- 欠損は条件不成立(買わない)。結果列は述語に使えない(機械検査)

## 3. 評価契約
- 1 点 100 円(stake 変種は相対重み)。回収率 = Σ払戻 / Σ賭け金。払戻: 単勝=確定オッズ×100(同着は 0 として除外・件数開示)・複勝/組合せ=公式配当(円/100 円)
- 窓: **市場・文脈系**(モデル不使用): 発見 1986–2007 / 資格 2008–2018 / 確認 2019–2026-09-22。**モデル系**: 発見 2008–2013 / 資格 2014–2018 / 確認 2019–2026。**組合せ・複勝**(実配当): 2025 / 2026 の 2 分割のみ(検出力不足を明示)
- 生存規則: 発見期 点推定 ≥ 1.00 かつ 的中 ≥ 20 かつ 開催日 ≥ 100 かつ 最大 1 的中の払戻寄与 ≤ 50% → 資格期でも同条件 → 確認窓で開催日クラスタ bootstrap(10,000 反復)の 95% CI 下限 > 1.00 を「候補」、点推定 ≥ 1.00 を「弱い候補」
- 帰無シミュレーション: 各レースの勝者を市場 q から抽選した合成結果で全パイプラインを 200 回流し、「確認窓まで到達する本数」の分布を出す(多重比較の実効偽陽性)
- 対照: 買わない 1.00 / 1 番人気 / 21 倍未満全馬 / 全馬(床)
- 参考指標: 年別 ROI・勝ち年数・leave-one-hit-out ROI・MDE

## 4. 探索アーム
A. 手設計パターン(Workflow の 8 レンズが DSL で列挙・想定 1,000〜3,000 本)
B. 貪欲結合探索(発見期で ROI 最大化する条件の結合を beam 幅 20・深さ 3 で探索・最小支持 2,000 賭け→資格・確認で検証)
C. ROI 直接学習(LightGBM 回帰: 目的=純収益(payout−100)・特徴=1.1〜1.6 の全列・年次 walk-forward 再学習・予測純収益 > 0 の馬を買う。閾値は fit 内で固定)
D. 組合せ・複勝(実配当 2025〜26・box/軸流し・人気構成ルール)

## 5. 言えないこと(最初から開示)
closing 価格で選定=closing-line oracle(実運用は不利側)・公式払戻でなくオッズ×100 の近似・同着除外・過去走由来は現在 DB 値・帰無シミュレーションは市場 q 抽選(真の勝率でない)。

## 6. codex レビュー反映(2026-09-23・設計凍結前)

codex(read-only)の指摘のうち採用したもの:

1. **帰無シミュレーション(勝者 ~ 市場 q)は利益の検定ではない**。q が正しい世界は E[ROI]≈1/overround<1 なので「ROI≤1」の最不利帰無より甘い。→ **探索挙動の診断に格下げ**し、確認窓の判定は **ROI=1 に帰無中心化した開催日クラスタ bootstrap の片側 p 値 + 確認窓に到達した凍結集合に対する Holm(FWER 片側 2.5%)** に置き換える(109 FR-003 と同型)。「候補」= Holm 棄却。CI 下限>1(未補正)と点推定≥1 は診断として併記。
2. **確認窓 2019–2026 は完全な未使用窓ではない**(本リポジトリの過去 verdict がこの窓の対照・オッズ帯の値を参照している)。市場のみのパターンにとって発見期 1986–2007 は真に未使用。確認窓の独立性は「設計者(エージェント)が結果を見ていない」までで、真の独立確認は前向き(065/106)にしか無いと明記。
3. **列の利用可能時刻ラベルの是正**: `fav_odds`/`second_odds`/`odds_gap12`/`fav_q`/`q_entropy_norm`/`n_fav_under_2`/`n_odds_under_10`/`field_size`(started)/`is_last_race`(DB 上の最終番組) は **closing 由来**。`weight`/`weight_diff` は当日計量後。§1.1 の「pre_entry」表記はこれらに当てはまらない。
4. **同日先行レース情報**はレース番号順=時刻順(同一場)を前提にする。公開時刻は保存していないので「結果確定が次の発走前に公表される」を仮定として明記。
5. **控除率の年代差**: 1986–91 は Σ1/odds≈1.335(払戻 ≈75%)・1992–2004 ≈1.269・2005〜 ≈1.25–1.26。発見期は異なる制度の混合であり、年別 ROI と年代別の床を併記する。
6. **同着レースは母集団外(賭け金も払戻も除外・173 レース=0.13%)**。結果による標本選別だが規模が小さいことを開示。
7. **アーム D(複勝・組合せ)は帰無シミュレーション対象外**(単勝勝者の抽選では着順同時分布が決まらない)。固定戦略の実測系列の直接検定のみ。2026 年は cross-pool/exotic-portfolio の過去測定で一部使用済み。
8. **アーム C の限界**: ラベル分散 ∝ odds² なので「予測純収益>0」の選抜は推定誤差の上振れを選びやすい。τ は事前固定・オッズ帯別の実現 ROI と分位単調性・払戻集中度を報告。closing オッズを特徴に使うので oracle 戦略の学習である。ハイパーパラメータは事前固定(チューニングなし)。
9. **新規列**: 騎手・調教師の市場超過勝ち数 `jockey_excess_365/all`・`trainer_excess_365/all`(= 勝ち数 − Σq。勝率は有力馬騎乗の反映にすぎないという指摘への対応)。
10. **賭け金規則**: `edge` は max(ev−1,0) で非負化・レース内総額が 0 なら均等。100 円単位への丸めは行わない(連続額の理想化評価)。「買わない=1.00」は資金維持の基準であって賭け金 ROI(0/0)ではない。

不採用/保留: 帰無反復を数千〜1 万回に増やす(診断に格下げしたので 200〜500 回で十分)・購入締切時点スナップショットでの再評価(データが 2026-08 以降の 480 レースしか無く本探索の外・065 の領分)。
