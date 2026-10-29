# 175 判断記録(decision_quotes)の採点 — 読み取り専用の採点 CLI と候補の同一 quote 採点

**Feature Branch**: `175-decision-quote-scoring`(174 と同じく main 上で作業・push なし)
**Created**: 2026-10-29
**Status**: 実装完了(2026-10-29・未コミット・記録は result-review.md・codex unavailable=400 継続のため §10 のセルフレビュー checklist で代替)
**Input**: 利用者「ROI モデルを採用したので精度を良くするための検証・評価をどこから始めるか」→ 本セッションの判断「NLL 系の精度には伸びしろが無く、欠陥は選ばれた馬の過大と支払価格のずれに集中している。採否できる唯一の証拠は 174 の `decision_quotes` で、その採点経路が未実装」→ 利用者「はい、始める」。

## 1. 目的

174 が開催日ごとに残す **発走 3 分前の判断記録**(1 レース 1 行・全馬の quote と同時刻の p̂/EV・append-only)を、**結果を見る前に固定した手順**で採点し、

1. 表示中の 2 列(期待回収率 120% 超・市場混合 100% 超)の前向き回収率と区間(=凍結規則 `display-update-rule-v1` の「現行」側の数値)、
2. T−3 時点の較正(Σwon/Σp̂ を EV 帯・オッズ帯別)と価格ずれ(T−3 → 確定)の大きさ、
3. 事前登録した候補を **同じレース・同じ quote** で精算した「候補 − 現行」の差と区間、凍結規則による三値(`insufficient` / `keep` / `replace`)

を出す。利益の改善は約束しない。規則を満たさない限り表示は変えない(C14・B1)。

## 2. 位置づけ(何がもう決まっているか)

- **証拠**: `decision_quotes`(migration 0022)。列は `field`(馬ごとの `win_odds`/`popularity`/`place_low`/`place_high`)・`pred`(馬ごとの `p_ens`/`ev_ens`/`p_single`/`ev_single`/`p_mix`/`ev_mix`)・`captured_at`/`official_at`/`post_time`/`seconds_to_post`・`field_digest`/`quote_digest`/`features_sha256`・artifact の名前と sha・`mix_status`/`mix_coefficient_year`・`logic_version`/`policy_version`。精算は保存しない(174 FR-004)。
- **規則**: `eval/display_update_rule.py` と `specs/174/display-update-rule.json`(sha 固定)。証拠は decision_quotes のみ・精算は公式単勝払戻(1 目印 100 円)・300 点以上・候補 − 現行の回収率差の開催日ブロック bootstrap 95% 下限 > 0 で `replace`。D17(年次更新)は規則の外。
- **流用する実装**: B8 `api/market_ev_prospective.py` の抽出規則(平地・quote 来歴・単一 snapshot・観測 ≤ 計算 < 発走・出走馬一致・値域・結果と公式払戻の整合=レース単位除外)と集計(100 円×目印・`race_block_ratio_bootstrap_ci_v1`・b=20000・seed=20260905・開催日ブロック)。138 の `attention_rules.CHECKPOINTS[0]=300`・`race_payout_consistent`。156 の精算定義(同着は各勝馬の公式額)。174 の `DayFeatures`/`rows_with_quote`/`ProductionPredictor`(本番と同じ経路で quote から p̂ を出す)。
- **閉じている軸(本 spec で再び測らない)**: 締切オッズでの買い方探索(109・2026-09-30)・判断時モデル(140 ①-a/①-c・173)・NLL レバー(rounds/seed/isotonic/TE・2026-10-10)。
- **検出力の事実**: 1 点の SD は 6〜7(100 円・平均オッズ 33 倍)。B8 の実測密度(120% 超=8 点/38 レース・混合=4 点/12 レース)を 2026 年の残り 19 開催日(約 680 レース)に当てると 120% 超 ≈140 点・混合 ≈230 点で、**300 点は年内に届きにくい**。したがって v1 で年内に答えが出るのは較正と価格ずれで、回収率の判定は 2027 年に持ち越す。これを隠さない(SC-005)。

## 3. スコープ

- 含む: eval の純関数(採点・分類・差の区間・診断)・live の読み取り専用 CLI(DB ロード・候補の再現・JSON 出力)・候補の事前登録ファイル・テスト・運用文書。
- 含まない: 読み取り API/front ページ(後段・eval の純関数をそのまま API から呼べる形にしておく=B8 と同型)・新しい閾値や目印の探索・候補の学習そのもの(各候補の学習は別 spec/研究で行い、本 spec は「登録された artifact を同じ quote で採点する」まで)・複勝 quote の評価(保存はあるが v1 は単勝のみ)・T−10 の 2 点目。

## 4. ユーザーストーリー

### US1 現行 2 列の前向き採点(P1)

利用者は開催日の夜(results 取込後)または任意の日に `live near-post-score --from 2026-10-31 --to <日付>` を実行し、decision_quotes の各行がどの段で除外され、何点が精算され、2 列の回収率と区間がいくらかを JSON と標準出力で見る。

- **Independent Test**: 174 の試運転 2 日(10/31・11/1)の行だけで実行でき、funnel の合計が行数に一致し、点数が 300 未満なら三値が `insufficient` になる。
- **受入シナリオ**:
  1. Given 10/31 の active 行 N 件と公式払戻、When 採点、Then funnel の各段の合計 = N、`eligible` の各レースは 2 列のどちらかに「目印の馬」として 0 頭以上を寄与、回収率 = Σ払戻/Σ100 円、区間は 138 と同じ bootstrap(impl/b/seed/block を出力に記録)。
  2. Given 払戻未保存のレース、When 採点、Then そのレースは **レース単位で** `payout_race_missing` に落ち、勝ち馬だけを除くことはない(156/B8 と同じ)。
  3. Given 同着のレース、When 採点、Then 目印の各勝馬にそれぞれの公式額(139)。
  4. Given 点数 < 300、When 規則を適用、Then `insufficient`(表示は不変)。`diff_ci_low` は候補が無いので要求しない。

### US2 T−3 の較正と価格ずれの診断(P1・判定に使わない)

利用者は同じ実行で、(a) EV 帯(≤0.8 / 0.8〜1.0 / 1.0〜1.2 / >1.2)とオッズ帯(<5 / 5〜10 / 10〜20 / 20〜40 / ≥40)ごとの Σwon/Σp̂ と開催日ブロック区間(3 系統 ens15/単 seed/混合)、(b) 馬ごとの価格ずれ log(O_F/O_J)(O_J=保存 `win_odds`・O_F=結果ページ由来の確定単勝=139 `apply_final_odds` 後の `race_horses.odds`)の分位(全馬・目印の馬・人気帯別)、(c) 価格保持率=T−3 の目印のうち p̂ を固定して O_F で再評価しても閾値を超える割合、を見る。

- **Why**: 10/10 の記録「期待回収率の改善は NLL でなく較正を帯別・時点別に測る」と、173 の「支払価格のずれが正体」を、初めて **買える価格の時点**で測る。年内に答えが出るのはこれ。
- **Independent Test**: 合成の 2 レース fixture で Σwon/Σp̂ と log 比が手計算と一致する。
- **受入シナリオ**:
  1. Given 確定単勝が無い(`win_odds_official_at` の来歴が発走前のまま/結果未取込)レース、When 価格ずれ、Then そのレースは `closing_unavailable` として件数だけ出し、ずれの分布に入れない。
  2. 出力のどこにも「採用」「改善」「利益」の語を使わず、較正と保持率は `diagnostic: true` を付ける(021/064 の表示規律を数値出力にも適用)。

### US3 候補を同じ quote で採点する(P2)

利用者は候補を `specs/175-decision-quote-scoring/candidates.json` に **結果を読む前に**登録し(名前・種類・artifact のパスと sha256・目印規則・登録日・学習データの終端)、`--candidate <name>` を付けて実行すると、各適格レースについて朝の特徴行を再構築し、保存 quote を差し替えて現行(ens15)を再予測し、保存 `pred.p_ens` と一致したレースだけで候補を予測・精算し、候補 − 現行の回収率差と区間、三値を得る。

- **Why**: C14 が要求する「同じレース・同じ価格」での比較は、保存行に候補の p̂ が無い以上、入力の再現でしか成立しない。再現の成否はレースごとに機械判定し、できないレースは対から外して件数を開示する(黙って近似しない)。
- **Independent Test**: 候補=現行そのもの(恒等候補)を登録して実行すると差が厳密 0・三値は点数次第で `insufficient` か `keep`。
- **受入シナリオ**:
  1. Given 再構築した特徴行+保存 quote で現行の p_ens が保存値と **ビット一致**しないレース、When 候補採点、Then そのレースは `not_reproducible` に落ち、対の集計から外れ、件数と最大差を出力する。
  2. Given 登録日より前に捕捉された行、When 候補採点、Then その点は「登録前」として別掲し、**三値の判定には登録日以降の点だけ**を使う(事後選択の封鎖)。
  3. Given 候補の artifact の sha256 が登録と違う、When 実行、Then 採点せず終了コード 2。
  4. 候補と現行は同じレース集合・同じ目印本数規則(100 円×閾値超)・同じ bootstrap の draw(同 seed・同じ開催日ブロック集合)で差を取る。

## 5. 機能要件

**抽出(US1)**
- FR-001 候補行は `decision_quotes` の全行(表は `UNIQUE(race_id)` 無条件+append-only で 1 レース生涯 1 行・status 列は無い=捕捉後の取消や構成変更は保存行では分からず、読み取り時に現在の started 集合と照合して除外する)・`race_date ∈ [from, to]`。平地のみ。
- FR-002 除外は **先に失敗した 1 段**に 1 回だけ数える(B8 と同じ順序を基に decision_quotes 用に置換): `jump_race` → `window_out`(`capture_target`=`t_minus_<k>` の k×60 秒を目標とし `|seconds_to_post − k×60| > 60`。同時刻衝突で T−4/T−5 に割り当てられたレースも自分の目標で判定)→ `official_after_capture`(`official_at > captured_at`)→ `captured_after_post`(`captured_at ≥ post_time`)→ `field_mismatch`(保存 `field` の (horse_id, 馬番) 集合 ≠ 現在の started 集合、または `field_digest` 不一致)→ `invalid_values`(win_odds < 1・p̂ ∉ (0,1)・EV 非有限)→ `pending_result` → `payout_race_missing` → `payout_inconsistent`(`race_payout_consistent`)→ `eligible`。funnel の合計 = 候補行数(テストで固定)。
- FR-003 列の定義は表示と同一: 120% 超=`pred.ev_ens > 1.2`(`MARKET_EV_THRESHOLD` を API から読まず eval の定数として同値をテストで固定)・混合 100% 超=`mix_status='available'` かつ `pred.ev_mix > 1.0`(`market_mix.exceeds_mix_threshold`)。`mix_status≠available` のレースは混合列で `n_races_mix_unavailable` に数え、目印 0 頭の適格レースとして残す(B8 と同じ)。
- FR-004 精算: 目印の馬 1 頭につき 100 円、払戻 = その馬番の公式単勝払戻(`official_win_payouts.payout_yen`・100 円あたり)、同着は各勝馬の額。回収率 = Σ払戻/Σ100 円。区間 = `race_block_ratio_bootstrap_ci_v1`(impl/b/seed/block を `display_update_rule.BOOTSTRAP` から読む・再宣言しない)。
- FR-005 規則の適用: `display_update_rule.decide(n_points, diff_ci_low)` を呼び、出力に `rule_version`・凍結 JSON の sha256(ファイルから読んで照合・不一致は終了 2)を記録する。

**診断(US2)**
- FR-006 較正: 系統ごと(ens15/単 seed/混合)に EV 帯×オッズ帯の Σwon/Σp̂・点数・開催日ブロック区間。帯の境界は本 spec で固定(§4 US2)し、結果を見て動かさない。
- FR-007 価格ずれ: O_F の出所は `race_horses.odds` かつ `races.win_odds_observed_at IS NOT NULL AND win_odds_official_at IS NULL`(139/140 の確定来歴=`apply_final_odds` が書く形。両方 NULL は来歴なし=使わない)かつ結果あり。満たさないレースは `closing_unavailable`。出力は log(O_F/O_J) の p10/p50/p90・sd・系統成分(平均)を全馬/目印の馬/人気帯(1〜3・4〜8・9+)別。
- FR-008 価格保持率: 目印の馬のうち `p̂_J × O_F > 閾値` の割合(p̂ 固定の近似であることを出力に明記)。173 の「再予測による保持率」は US3 の再現経路で任意出力(`--retention-replay`)。
- FR-009 診断の出力には `diagnostic: true` と「判定に使わない」を機械可読で付け、CLI の終了コードや三値に影響させない。

**候補(US3)**
- FR-010 候補登録ファイル `candidates.json`: `name`・`kind`(`ensemble_dir` / `postprocess` / `single_dir`)・`artifact_path`(絶対パス・gitignore 配下可)・`artifact_sha256`(ディレクトリは manifest の sha)・`mark_rule`(既定=現行と同じ閾値)・`registered_on`・`train_through`。sha は実行時に照合(FR-010a: 不一致で終了 2)。
- FR-011 入力の再現: `build_day_features(day)`(174 と同じ `load_rows(through=day) → build_features`)→ 当該レースの行に保存 `field.win_odds`/`popularity` を差し替え(`rows_with_quote`)→ `ProductionPredictor.predict` で現行 p_ens → 保存 `pred.p_ens` と全馬ビット一致(`==`)で `reproducible`。一致しないレースは `not_reproducible`(件数・最大 |Δ| を出力)。`features_sha256` の一致は併記するが条件にはしない(sha は当日の全行に対する値で、後日の人気・馬場更新で変わり得る=174 result-review の実測 ≤0.0027)。
- FR-012 候補の予測は再現できたレースの同じ行(同じ quote)に対して行い、目印・精算・区間は FR-003/004 と同じ。差 = 候補の回収率 − 現行の回収率を **同じ bootstrap の draw**で区間化する(`race_block_ratio_bootstrap_ci_v1` は複数行を同期再標本化するので、[現行, 候補] の 2 行を 1 回で渡し `replicates` の行差を取る。別々に呼んで区間を引き算しない)。
- FR-013 三値は登録日以降に捕捉された対だけで `decide`。登録前の対は別掲(`pre_registration_points`)。
- FR-014 候補は同時に最大 2 本まで登録でき、ある候補が `keep` になったあと同じ点で再判定しない(規則の文言)。再登録は新しい `name` と登録日で行い、旧候補の記録は残す。

**不変条件・境界**
- FR-015 読み取り専用: DB への書き込みゼロ(`hr_manual_observer` role で実行できることをテスト)。出力は `artifacts/near_post/score/<from>_<to>_<run_id>.json` に新規ファイルとして書き、既存ファイルは上書きしない。
- FR-016 憲法 II: 採点の出力・候補の p̂ を特徴に戻さない(features の leak-guard に `decision_quotes` 既存+本 spec の出力ディレクトリ非参照)。
- FR-017 憲法 III: 帯・閾値・bootstrap・三値規則は結果を読む前に固定(本 spec と凍結 JSON)。候補登録は結果を読む前(FR-013 で機械強制)。
- FR-018 憲法 V: 精算・診断は保存しない(読み取り時計算)。再実行は決定論(同 seed)で同じ JSON を出す(テスト)。
- FR-019 eval の純関数は DB・training に依存しない(predictor-agnostic・入力は plain dict/dataclass)。候補の再現(training artifact)は live 側に置く。API 後段は eval の同じ関数を呼ぶ(B8 と同型の「per-race と同じ builder」)。
- FR-020 出力の語彙: 「採用」「改善」「利益」を使わない。回収率は `recovery_rate`、目印は `marked`、差は `diff`。

## 6. 成功基準

- SC-001 10/31 の試運転の行だけで CLI が完走し、funnel の合計 = 行数、`eligible` レースの 2 列の点数と回収率が手計算(SQL で Σ払戻/Σ100 円)と一致する。
- SC-002 合成 fixture(3 レース・同着 1・払戻欠落 1)で funnel・精算・区間が期待値どおり。bootstrap の impl/b/seed は `display_update_rule.BOOTSTRAP` と同値。
- SC-003 恒等候補(現行と同じ artifact)で差が厳密 0、`not_reproducible` の件数が実 DB の 10/31〜11/1 で報告される(件数そのものは測定・閾値なし)。
- SC-004 読み取り専用 role で実行でき、DB の行数が実行前後で不変。
- SC-005 出力の先頭に「300 点未満のあいだ三値は insufficient・年内の見込み点数(§2)」を機械可読+文章で出す。
- SC-006 テスト: eval 単体(funnel 順序・精算・差の区間・決定論)・live 統合(testcontainers で decision_quotes+payouts から集計)・ruff 緑・既存テスト不変。

## 7. 前提と既定(利用者が変えない限りこのまま進める)

- A1 置き場所: 純関数は `eval/src/horseracing_eval/decision_quote_scoring.py`、CLI は `live near-post-score`(live は db/training/eval に依存済み・near_post の再現部品を持つ)。
- A2 候補の三値は登録日以降の点だけ(FR-013)。**登録は 10/31 より前に済ませるほど無駄が少ない**。今すぐ登録できる候補は「148 の予測後全頭正規化」(`p_norm_i = p_i / Σ_started p`・EV_norm = p_norm × odds > 1.2・現行 ens15 の後処理で学習不要=`kind: postprocess`・artifact はコード sha)。PL top-3 教師は学習が要るので第 2 候補の枠を空けておく。
- A3 O_F は 139 の確定単勝のみ(他の代理は使わない)。
- A4 朝の特徴行 parquet の保存(live 側の小変更=`artifacts/near_post/<day>/features.parquet`+sha)は **本 spec の任意の前提**。あれば FR-011 の再現が sha 一致で済み `not_reproducible` が減る。10/31 の前に入れるかは利用者判断(§9)。

## 8. 限界(出力にも書く)

- 300 点未満では回収率の判定は構造的に出ない。年内の見込み点数は §2。
- `not_reproducible` のレースは候補比較から消える。消えた割合が大きければ A4 を入れる。
- 価格保持率(FR-008)は p̂ 固定の近似。正確な保持率は再予測(US3 任意出力)。
- 較正の帯別区間は点数が少ないあいだ広い。帯を結果で併合しない。
- decision_quotes は 1 レース 1 行なので、同じレースの複数時点(chaos 行・市場 EV 保存行)との価格差は別の読み(本 spec では扱わない)。

## 9. 利用者の決定(2026-10-29・全部推奨どおり)

1. **A4 朝の特徴行 parquet 保存を 10/31 前に入れる**(live の書き込みはファイルのみ・DB 不変・捕捉の判定順に触れない)。
2. **第 1 候補=148 の全頭正規化を 10/31 前に登録する**(学習不要・`kind: postprocess`・artifact はコード sha)。第 2 候補の枠は空けておく。
3. **混合列の候補は v1 で持たない**(候補は 120% 列に限る・混合係数は D17 の外側規則で動く)。

## 10. セルフレビュー checklist(codex unavailable の代替・2026-10-29 実施)

- [x] 同じ契約の二重実装: 抽出規則は B8(`StoredMarketEv` 行)と入力型が違う。plan で「述語を入力型非依存の関数に抽出し B8 と共有」か「別実装+同一 fixture の parity テスト」を選ぶ(未決=plan の論点 1)。
- [x] 事後選択の経路: 帯・閾値・候補・登録日は FR-006/010/013/017 で結果より前に固定。
- [x] 分母: 目印本数×100 円。差の区間は同期再標本化(FR-012・実装の `race_block_ratio_bootstrap_ci_v1` が複数行を同じ draw で扱うことを確認)。
- [x] 来歴: O_F は `observed_at IS NOT NULL AND official_at IS NULL`(`apply_final_odds` の書き方を確認して FR-007 を修正)。`official_at ≤ captured_at` の向きは FR-002。
- [x] 保存行の実態: `decision_quotes` に status 列は無く UNIQUE(race_id) は無条件(174 spec の「void 追記」は実装に無い)→ FR-001 を実装に合わせ、構成変更は読み取り時の照合で除外。
- [x] 読み取り専用・決定論: FR-015/018。
- [x] 検出力の正直さ: §2・§8・SC-005。
- [x] 語彙: FR-020(出力に限る。spec 本文の「採用」は既存文書の参照)。
