# Research: 買い目パターン採否ゲート(109)

Phase 0。spec の未確定を実コードと実 DB で解いた決定の記録。番号は plan から参照される。

## D1. データ源と母集団

**決定**: モデル状態は 108 の OOF 束 `artifacts/oof/8bdde268…/bundle.json`(schema v1・`predictions[race_id][horse_id] = {win, top2, top3}`・19 fold・64,542 レース・`per_fold[].train_through` で strict-past を attestation 済み)から読む。市場と結果は DB(`race_horses.odds/popularity/horse_number/sex/entry_status`、`race_results.finish_order/result_status`、`races.race_date/track_type/distance/race_class/venue_code`)。読み方は `scripts/policy_gate_pl_topk.py::load` と同型(束 × race_horses × race_results の left merge)。

**母集団(FR-013)**: 次を全て満たすレース。(a) 束に収録 (b) `track_type ∈ {芝, ダ}`(障害は除外。障害誤ラベルは修正済みだが対象外) (c) started 馬全員に odds があり束に p がある (d) `finished かつ finish_order=1` の馬がちょうど 1 頭。(d) で同着 83 レースと勝者不在 34 レースが結果後除外になる。年別レース数は DB 実測 3,451〜3,456/年(2026 は 2,406)を凍結設定 `expected_races_per_year` に記録し、母集団との差は記録のみ(拒否条件にしない)。除外理由は結果前(束外 / 障害 / オッズ欠損 / p 欠損)と結果後(勝者不在 / 同着)に分けて流れ図を証拠に残す。

**理由**: 束は再学習ゼロで現行世代の strict-past p を全史で与える唯一の材料(2.6 時間分)。母集団を hash で固定すれば全パターンと対照が同じレース集合を見る。

**代替**: `race_horses.popularity` を市場状態に使う案は却下(popularity は netkeiba 期と JRA-VAN 期で定義が揺れる。q = devig(1/odds) は全期間で同じ定義)。

## D2. 評価規約の実装対応

| spec | 実装 |
|---|---|
| 100 円 flat・払戻 = 的中時 odds×100 | 賭け行 `stake=1, payout = odds if won else 0`(円換算は表示のみ) |
| 開催日クラスタ bootstrap 20,000 回 seed 固定 | `horseracing_eval.bootstrap.race_day_cluster_ratio_bootstrap_ci_v1(num_by_day, den_by_day, b=20000, seed=…)`(ratio bootstrap=分母も再標本化。既存関数・改修なし) |
| 週/月ブロック感度(FR-004a) | **ベクトル化した** `race_block_ratio_bootstrap_ci_v1(block=race_day\|iso_week\|calendar_month)` を eval に追加。固定ブロックのクラスタ bootstrap(moving-block ではない)。**109 の唯一の bootstrap 実装で recompute も同じ関数を呼ぶ**。契約テスト: `block=race_day` は既存の日クラスタ版と同 seed で 1e-12 一致(加算順が違うのでビット一致は要求しない)・同一入力の再呼び出しはビット一致 |
| 降格(的中<20 / 最大 1 的中寄与>50% / 開催日<200) | `exotic_portfolio.score_cell` の `MIN_HITS`/`MAX_SINGLE_HIT_SHARE` 規則を流用し、開催日下限を追加 |
| 片側 p 値(FR-003) | **帰無中心化**: 反復 `R*_b` と点推定 `R̂` から `p⁺ = (1 + #{R*_b − R̂ ≥ R̂ − 1}) / (B+1)`(帰無 ROI=1 の basic-bootstrap 片側 p 値・codex plan #12)。全生存者の日ブロック抽選は反復ごとに同期。分母ゼロの反復が生じるパターンは NO_DECISION とし Holm 上は p=1 で族の大きさを維持。既存の `mean(reps ≤ 1.0)` は percentile 型なので **使わない** |
| futility 検定(RULED_OUT) | `p⁻ = (1 + #{R̂ − R*_b ≥ c − R̂}) / (B+1)`、c = 1+δ = 1.02。帰無「ROI ≥ c」。同じ生存者集合で Holm(codex plan #13)。参考として studentized single-step max-T の同時上限 `U_i = R̂_i + c_α s_i` も証拠に併記 |
| Holm(FR-003) | step-down の順序は `exotic_portfolio.holm_adjust` と同じだが、既存関数は初回失敗で break するので流用せず新実装(降格・NO_DECISION は p=1 で m に数え break しない=D10)。生存者 m と α=0.025 |

**ビット一致再計算(FR-008)**: 証拠は賭け行(列は contracts/evidence.md が正本: `pattern_id, race_id, horse_number, horse_id, race_date, odds, q, p, won, payout, stake`)を `evidence/<window>-bets.parquet` に保存し、`recompute` サブコマンドが証拠だけから点推定・CI・p 値を再計算して verdict と一致することを確認する(100 US1 と同型)。

## D3. 生存規則と時間分割(FR-015)

**決定**: screening 窓を発見期 2008-01-01〜2013-12-31(開催日 641)と資格期 2014-01-01〜2018-12-31(548)に分ける。発見期で `点推定 ≥ 1.00 かつ非降格` → 資格期で `点推定 ≥ 1.00 かつ非降格`(「同方向」は両期 ≥1.00 に含意されるので独立条件にしない)。順位は `min(LCB_発見, LCB_資格)` の降順で上限 5。同一マスク(賭け行集合が完全一致)は 1 本に統合(識別子は辞書順最小)、Jaccard 類似 > 0.9 の組は順位上位のみ残す。

**理由**: codex #2/#3。`点推定 ≥ 1 かつ CI 上限 > 1` は実質 `点推定 ≥ 1` で、高分散セルのノイズ上振れが 5 枠を埋める。時間分割再現はレジーム固有ノイズと勝者の呪いに直接効き、確認窓に触れない。

**代替**: screening に Bonferroni(393 本で α=0.025/393)は検出力を潰しすぎるので不採用。

## D4. パターン族の正確な列挙(FR-011)

軸と帯。`H` = 馬の選び方 3 種 {`fav`: 1 番人気(最低オッズ 1 頭) / `cap11`: odds<11 の全馬 / `cap21`: odds<21 の全馬}。レース単位条件は H と組で 1 パターン。馬単位条件はその条件を満たす馬を全て買う(自己選択)。

**文脈軸(C)**
- レース単位(15 セル × H 3 = 45): 芝/ダ(2)・距離帯 ≤1400 / 1401-1800 / 1801-2200 / >2200(4)・クラス 新馬 / 未勝利 / C1(1勝・１勝・500万)/ C2(2勝・1000万)/ C3(3勝・1600万)/ OP(ｵｰﾌﾟﾝ・OP(L)・G3・G2・G1)(6)・頭数 ≤8 / 9-13 / ≥14(3)
- 馬単位(13 セル、単独 13 + × H 3 = 39・計 52): 牝馬×夏(6-9 月)/ 牝馬×非夏 / 牡セ×夏 / 牡セ×非夏(4)・間隔帯(前走 started からの日数)≤13 / 14-27 / 28-69 / ≥70 / 初出走(5)・前走着順帯 1-3 / 4-5 / 6-9 / ≥10(4。初出走は間隔帯の「初出走」で表現し重複させない)

**市場軸(M)**
- 馬単位(10 セル・自己選択): 買う馬のオッズ帯 <3 / 3-6 / 6-11 / 11-21 / 21-51 / 51+(6・`policy_gate.ODDS_BANDS` と同一)・市場勝率帯 q ≥0.30 / 0.15-0.30 / 0.05-0.15 / <0.05(4・047 と同一境界)
- レース単位(6 セル × H 3 = 18): 1 番人気の q ≥0.50 / 0.30-0.50 / <0.30(3)・正規化エントロピー(066 `dispersion_bands.normalized_entropy`)の 3 分位(境界は p 分位と同じ厳密過去 expanding 方式。発見期全体からの固定は当時未知の将来分布を使うので採らない・codex plan #5)(3)

**モデル軸(X)**(全て馬単位・自己選択・尺度頑健、12 セル)
- レース内 p 順位 1 / 2-3 / 4+(3)
- p/q 比 ≥1.5 / 1.2-1.5 / 1.0-1.2 / 0.8-1.0 / <0.8(5)
- **厳密過去**の p 分位 上位 10% / 10-25% / 25% 未満(3)。境界はそのレース日より前の全 OOF p の累積分布から取る(expanding・warm-up = 2008 年は履歴不足で欠損=買わない)。年内全体の分位は将来の共変量を含むので使わない(codex plan #4)
- EV = p×odds ≥ 1.0(1・現行政策と同じ述語)

**2 軸積**
- C × H は上記に含む(45 + 39 = 84)
- M × X: 馬単位市場 10 × X 12 = 120、レース単位市場 6 × X 12 = 72(レース単位市場 × X は X が馬を選ぶので H 不要)。計 192

**文脈 × 現行 EV 政策(C×EV)**(codex plan #2: 「この条件下で EV 馬を買う」が無かった): レース単位 15 + 馬単位 13 = 28。EV 述語は X の `ev ≥ 1.0` と同一。

**格言積み上げ(F)**: 4 因子 {牝馬×夏 / NOT 叩き 2 走目(`tataki_2` = 前走が前々走から 70 日超の休み明け、`folklore_candidates.sql` の定義を流用)/ 間隔 28-69 日 / 前走 6-9 着} の非空部分集合 15 × H 3 = 45(AND 結合)。うち単一因子 {牝馬×夏, 間隔 28-69, 前走 6-9} × H の 9 本は C×H と構造的に同一なので **列挙時に除去**(codex plan #1)→ 36。

**総数**: C 単独 13 + C×H 84 + C×EV 28 + M 単独 10 + M×H 18 + X 単独 12 + M×X 192 + F 36 = **393 本 ≤ 400**。対照は `no_bet` / `favorite` / `cap11_all` / `cap21_all` の 4 本(族外・記述的)。`ev1_all` は X の EV セルの別名として表示のみ(独立対照にしない)。

**帯と同順位の固定(codex plan #3)**: 帯は全て `[lo, hi)`。odds `[0,3) [3,6) [6,11) [11,21) [21,51) [51,∞)`、q `[.30,∞) [.15,.30) [.05,.15) [0,.05)`、p/q `[1.5,∞) [1.2,1.5) [1.0,1.2) [.8,1.0) [0,.8)`。fav と p 順位の同率は `horse_number` 昇順で一意化。分位の境界値は上側の帯へ。「レース単位 15 セル」は 2+4+6+3 の one-way セルであり直積 144 ではない。F の「非空」は述語集合が非空の意味で、実現賭け数の非ゼロは別途(賭けゼロは NO_DECISION 未発火)。

**述語の欠損**: 帯を判定できない行(前走なしの前走着順、sex 欠損等)は「買わない」。各述語に `available_at ∈ {pre_entry, post_draw, closing}` を付け、odds/q/p 順位/EV を読む述語は `closing`(束の p は closing を読まないが、EV と p/q 比は odds を読むので closing)。

**頭数と過去走の as-of(codex plan #7)**: 頭数は登録頭数(`race_horses` の全行=取消含む)で定義し、取消馬は買わない(取消後の頭数でセルを付け替えない)。間隔・前走着順・叩き 2 走目・性別は現在の DB 値(訂正履歴なし)で作り、限界として verdict に記す。

**理由**: codex #14(端点・欠損・境界の列挙と exact hash)。帯の境界は既存の事前登録値(047 q 帯・064 オッズ帯・格言 SQL)を流用し新規の恣意を入れない。

## D5. モデル状態の尺度(FR-011)

**決定**: 生の p の絶対帯は使わない。順位・p/q 比・fold 年内分位のみ。**理由**: 19 fold で校正が動くと絶対帯が年を識別する(codex #14)。束の p は arm E の OOF isotonic 校正後だが fold 間の一致は保証されていない。年別の賭け数・払戻集中を各パターンの必須報告にする。

## D6. 自己検証の合成データ(FR-007)

**決定**:
1. 実データの構造を固定: レース集合・各馬の odds・q・全パターンの買いマスク `b_{rh}`・払戻倍率 `d_{rh}=odds`。
2. 目標回収率 ρ を与えたとき、各レースの勝者分布 `π_r` を `π_{rh} ∝ q_{rh}·exp((λ₀ + u_{day(r)})·s_{rh})`(`s_{rh} = b_{rh}·d_{rh}`)の族から取る。これは制約 `Σ b d π / Σ b = ρ` の下で q からの KL を最小にする解(codex plan #8: 長いオッズが強く動くのはバグでなくこの解の性質)。エッジの形: **KL 最小 ROI 傾き**(旧「一様」)/ 高オッズ集中 = 傾きの対象を odds 上位 20% の賭けに限定 / 開催日集中 = 対象を開催日の 10% に限定。感度として **オッズ中立傾き** `π ∝ q·exp(γ·b)`(選ばれた馬の相対 q を保つ)を加え、ρ が達成不能なら「実現不能」と報告。
3. 同日相関: `u_d ~ N(0, τ²)`。**是正(2026-09-06・自己検証の初回実行で検出)**: codex plan #9 の式どおり u_d をオッズ比例の傾き `s=b·d` に掛けると、τ=0.022 でも単勝 300 倍の馬で指数が ±6.6 になり、5 点 Gauss-Hermite の周辺と実際の抽選の乖離が大きく「境界帰無 ρ=1.00」の実現回収率が 1.33〜1.40 になった(サイズ検定が 11〜27% の誤採用を示したが、ゲートは真の利益を正しく採用していただけ)。日効果は **選択指標 b に掛ける一様な対数シフト** `exp((λ₀·s + u_d·b))` に変更し、τ は同じスケールで推定(実測 τ=0.755・cap21_all の日別払戻分散 1713 に対し categorical 586 を再現)。`fit_tilts` は λ₀ を解いた後に 30 回の抽選で周辺回収率を検査し、乖離が 0.02 超なら実行不能と報告する。修正後の境界帰無の誤採用率は代表 5 本で 0〜3%(各 200 反復)。この修正は判定統計と注入(T023/T027/T028)の範囲内であり、列挙・導出列・凍結物には触れていない。τ は日別払戻総額の条件付き分散 `V_d = Σ_r [Σ_h s²π − (Σ_h sπ)²]`(categorical・払戻不均一)を基準に、発見期の実測の日別払戻分散との差を simulation matching で合わせて推定(「二項超過」は不適切・codex plan #9)。**λ₀ は u を積分した周辺期待 ROI が ρ になるよう最後に解き直す**(u=0 で解いてから足すと非線形性で周辺 ROI がずれる)。
4. 各レースで勝者 1 頭を categorical 抽選(賭けごとの独立 Bernoulli は禁止=レース内排他性を壊す)。
   **入出力は numpy 配列と dict に限定**(eval 環境に pandas/pyarrow は無く追加もしない。DataFrame と parquet の I/O は `scripts/` 側=analyze 2 周目 I3)。
5. 外側の反復: サイズ検定は 5,000 回、検出力曲線(ρ ∈ {1.00, 1.025, 1.05, 1.075, 1.10, 1.15})はパターン別に 2,000 回。**サイズ検定の内側 bootstrap は主判定と同じ 20,000**(m=5 の最初の Holm 閾値 0.005 に対し 2,000 回では裾の期待件数 ≈10 で校正不能・codex plan #11)。検出力曲線の内側は 5,000(gate-config に別キーで記録)。境界帰無は代表 5 本を生存者集合とみなし 2 構成: (i) 全体境界=対象 i を 1.00・他 ≤ 1.00 (ii) 部分帰無=対象 i を 1.00・他の 1 本を 1.10・残り ≤ 1.00。制約つき KL 最小化で構成し、実行不能な構成は無効報告(codex plan #10)。「全 393 本の同時帰無」は m も生存者集合も定義できないので行わない(screening は検定ではない)。合否は対象が ADOPT_CLOSE になる率の二項片側 95% 下側限界 ≤ 2.5%(上側限界だと正しいゲートでも半分の確率で落ちる=analyze 2 周目 U1)。サイズ検定は生存者集合を固定した条件付き検定であり、screening を含む end-to-end 頻度は主張しない。判定関数は本番と同一(`decide()`・週/月ブロック感度込み)。
6. 対象パターン: 凍結済み 393 本から賭け数の分位 5 点(最小・25%・中央・75%・最大)の代表パターンを固定し、検出力曲線とサイズ検定の両方をこの 5 本で行う。
7. 0.796 床(ρ=0.796)での誤採用ゼロは陰性対照として併記。

**事前概算(codex #9)**: 確認窓 SE 0.022〜0.024・FWER 0.025・m=5 の最初の Holm 閾値 0.005 で 80% MDE ≈ (z_{0.995}+z_{0.80})·SE ≈ 3.42·SE ≈ 0.075〜0.082。ρ=1.05 の検出力は 31〜38%。**この数値を「要求」にしない**。

## D7. 同着と払戻近似(FR-018)

**決定**: 主判定は同着レースを母集団外(D1(d))だが、行は `n_winners` 列つきで配列に保持しマスクで除外する(感度で戻せるように)。感度 2 種を全生存者で実行: (i) 同着レースを含め勝者全員に `odds/n_winners` で払戻 (ii) 同着レースを含め `odds/max(2, n_winners)`(保守的下限)。bets parquet は `dead_heat, n_winners` を持ち、`recompute` は感度 4 版(週/月ブロック・等分・下限)まで再現する。ADOPT_CLOSE は主判定と感度 2 種と bootstrap 感度(週/月)の **全てで Holm の結論が維持**される場合のみ。1 つでも反転すれば NO_DECISION(codex #13)。同着は 83 レース/19 年で影響は小さいが規則として固定する。

## D8. 賭けの正準キーと独立集計(FR-022)

**決定**: 賭けの識別子は `(race_id: str12, horse_number: int)`。各パターンの選択集合の hash(sorted tuple 列の stable_hash)を証拠に残す。集計は主実装(pandas groupby)と独立に、純 Python の dict 累積で `n_bets / n_hits / Σstake / Σpayout` を再計算して一致を assert する。**理由**: 過去の frozenset 反復順バグは「もっともらしい負け」に化けた(exotic ポートフォリオ)。結果並べ替え不変(FR-009)は選択の結果非依存を、第二集計は突合の正しさを、それぞれ別に守る。

## D9. 対照の扱い(FR-005)

`no_bet` は stake=0 で ROI 未定義 → 証拠には `roi=null, accounting_sentinel=1.00` と書く。`cap21_all` / `favorite` / `ev1_all` は記述的参照で、確認窓の既知値(≈0.81 / ≈0.78 / ≈0.72)との整合を SC-002 で検査するが、候補の判定式には入らない。

## D10. verdict の状態機械(FR-003/017)

```
screening(発見期)   → 点推定<1 or 降格          → SCREENED_OUT(reason=discovery)
screening(資格期)   → 点推定<1 or 降格          → SCREENED_OUT(reason=qualification)
順位 6 位以下 / 重複統合                         → SCREENED_OUT(reason=rank|duplicate)
確認窓 → 降格 or 感度分割 or 実行不能             → NO_DECISION
確認窓 → Holm 棄却 かつ 感度全一致                → ADOPT_CLOSE
確認窓 → 利益検定非棄却 かつ futility 検定 p⁻(c=1.02) が Holm 棄却 → RULED_OUT
確認窓 → それ以外                                → NOT_ADOPTED
```
Holm の適用: 降格・NO_DECISION の生存者は p=1 として m に数え、既存 `holm_adjust` のように途中で break しない(降格 1 本が他の判定を巻き添えにしない=analyze 2 周目 A1)。状態の優先順位は固定(codex plan #14): SCREENED_OUT → NO_DECISION(実行不能・降格・感度間で下記分類が不一致)→ ADOPT_CLOSE(利益検定 p⁺ が全分析版で Holm 棄却)→ RULED_OUT(利益検定非棄却 かつ futility 検定 p⁻(c=1.02)が Holm 棄却)→ NOT_ADOPTED(どちらも非棄却=「利益を示せず 2% 超も排除できない」不精確域)。ADOPT の帰無は 1.00、δ は futility 専用(ADOPT を「δ 超」にする案は採らない=最小経済効果の要求ではなく否定の単位として δ を使う)。真の ROI が 1.00〜1.02 にあると両検定が棄却されうるが、優先順位で ADOPT_CLOSE が勝つ(矛盾ではなく「有意にプラスだが 2% 未満」)。全体結論は状態の件数で書く。

## D11. 計算量

screening: 393 本 × 20,000 反復。各パターンを日別(payout, stake)ベクトル(641 / 548 日)に落とし、反復ごとの日サンプリングを全パターン共通の index 配列で行い `(393 × 日数)` 行列との積で一括計算 → 数十秒。既存の `race_day_cluster_ratio_bootstrap_ci_v1` は Python ループなのでこの規模には使えず、ベクトル化した新関数を唯一の実装にする(D2)。確認窓 5 本 × 20,000 × 828 日は瞬時。

自己検証(analyze 3 周目 R3 で算術を訂正): 判定は本番と同じ `decide()` を同じ分析版集合(日クラスタ+週/月ブロック=3 版。同着感度は合成では発生しないので不要)で行う。サイズ = 5 対象 × 2 構成 × 外側 2,000 × 内側 20,000 × 3 版 × 5 本 × 828 日 ≈ 5×10¹² 演算相当だが行列演算で 1 外側反復 ≈ 0.3 秒 → 約 1.7 時間。検出力 = 6 ρ × 5 本 × 4 形 × 外側 1,000 × 内側 5,000 × 3 版 → 1 反復 ≈ 0.07 秒 × 120,000 ≈ 2.3 時間。**合計 4 時間級**。外側反復を 5,000→2,000 / 2,000→1,000 に落としたのは計算量のため(2,000 回でも真のサイズ 2.5% で期待 50 件・SE 0.35pt=合否に十分。codex plan #10 の 5,000 は部分採用として D14 に記録)。T033 の前に外側 20 反復の実測から総所要時間を外挿し、対象ごとの checkpoint(`evidence/selftest-partial-<i>.json`)から再開できるようにする。

## D12. 置き場所

- 純 scorer(母集団・パターン適用・集計・bootstrap・p 値・Holm・降格・状態機械)= `eval/src/horseracing_eval/buy_pattern_gate.py`(betting / training 非 import。`policy_gate.py` と同じ規律)
- パターン族の定義と述語 = `eval/src/horseracing_eval/buy_patterns.py`(帯の境界を定数で持ち、列挙関数が `patterns.json` を生成。hash は生成物に対して取る)
- 駆動(束 + DB ロード・DataFrame 構築・parquet 書き出し・窓・凍結 hash 照合・証拠と verdict の書き出し)= `scripts/buy_pattern_gate.py`(107 の driver 型。pandas/pyarrow を持つ `training` 環境で実行。eval 側は numpy+dict のみ)。CLI 統合テストは `scripts/tests/test_buy_pattern_gate_cli.py`(107 は eval/tests/unit に driver テストを置いたが、本 driver は pandas を import するので eval 環境では読めず、eval の conftest は testcontainer で `DATABASE_URL` を上書きする。`scripts/tests/pytest.ini` を置いて rootdir を固定し `cd training && uv run pytest ../scripts/tests` で収集されることを T002 で確認。実 DB と束が無ければ skip)。
- **DB は常時動く**(ops worker が取込を続ける・088 D12)ので、`screen` が構築した行を `artifacts/109/rows-<window>.parquet` に固定し `rows_hash` を `population.json` に記録、`confirm` と `recompute` はそのスナップショットを読む(再構築しない)。窓ごとの sorted `race_date` の全体集合(bootstrap の universe)も `population.json` に保存し、`recompute` はそれを使う(賭けのない日が落ちてビット一致が崩れるのを防ぐ)。`n_days` = そのパターンが stake>0 の日数。
- **証拠の置き場**: 賭け行 parquet は 10⁷ 行級になるので `artifacts/109/`(gitignore 済み)に置き、`evidence_refs` の sha256 で参照する。git にコミットするのは summary JSON・population.json・survivors.json・selftest.json・verdict.json と、生存者と対照の確認窓 bets parquet のみ。
- **clean tree の要求範囲**: `freeze`・`screen`・`confirm`(いずれも非 smoke)は clean tree を要求する。したがって selftest.json と screening 生成物はそれぞれ次段の前にコミットする(tasks に中間コミットを置く)。108 が憲法 V の穴として直した「clean-tree ガードが発火しない」を繰り返さない。
- **凍結後に触ってよいコードの範囲**: T033 でゲートを直す場合、修正は判定統計・注入(score / synthesize / estimate_day_effect)に限り、列挙と導出列(buy_patterns・derive)は不可。`selftest` は最初に実データを読む段なので全パターンの screening 窓 `selection_hash` を記録し、`screen` で一致を照合する。実行時の `git rev-parse HEAD` と dirty フラグを `run_code_sha` として証拠と verdict に別記録(凍結 `code_sha` は列挙コードの意味=analyze 2 周目 I4)。
- 凍結物 = `specs/109-buy-pattern-gate/gate-config.json` / `patterns.json`(生成後に hash を gate-config に転記)/ `population.json`(母集団 hash と流れ図)。**列挙と freeze は結果を読まないので Foundational で行い、自己検証は凍結済みの実マスクで行う**(analyze C1/I1 で tasks の逸脱を是正)
- 証拠 = `specs/109-buy-pattern-gate/evidence/`(`{screening-discovery,screening-qualification,confirmatory}-{bets.parquet,summary.json}` / selftest.json / survivors.json / freeze.json / preconditions.json)
- verdict = `specs/109-buy-pattern-gate/verdict.json`(append-only。既存パスがあれば拒否)

## D13. 製品差分ゼロの担保(FR-021)

変更は `eval/`(新規 2 モジュール + bootstrap のブロック版 1 関数 + テスト)と `scripts/` と `specs/` のみ。`db/ api/ front/ betting/ probability/ serving/ features/ ops/ admin/ training/src` に差分なし。migration なし。

## D14. codex レビュー(plan 段階・2026-09-05)

spec 段階の 15 件は [codex-review.md](codex-review.md) に記録済み。plan 段階の再レビュー(D4 列挙 / D6 注入 / D10 状態機械)は **14 件・採用 13 / 部分採用 1 / 不採用 0**。採否表は codex-review.md の第 2 表。本 research への反映: D2(p 値の +1 補正・同期抽選・futility 検定)、D4(重複 9 本除去・C×EV 28 本追加・帯と同順位の固定・厳密過去分位・登録頭数)、D6(KL 最小傾きの命名・オッズ中立感度・λ₀ の解き直し・τ の categorical 分散・サイズ検定 B=20,000・制約つき境界帰無)、D10(優先順位・δ は futility 専用)。部分採用 = #7(as-of 再構築: 訂正履歴が無いので頭数のみ登録頭数に是正し、過去走系は限界宣言)。analyze 3 周目 R3 で #10 の外側反復数(5,000)を計算量のため 2,000(サイズ)/ 1,000(検出力)に下げた=部分採用に変更(理由は D11)。
