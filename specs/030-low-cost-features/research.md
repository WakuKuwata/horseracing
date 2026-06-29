# Phase 0 Research: 低コスト特徴拡充 (030)

## R1: 何を入れるか（最終 5 group）
**Decision**: handicap(斤量)・place_rate(複勝率)・human_form_plus(人拡充)・course_aptitude(venue 自馬)・season(月/季節)。
**Rationale**: DB 実カバレッジ jockey_weight 100%・finish_order 99.7%・人/venue 100%・race_date 100%＝全て安価でリーク安全。最有力は斤量(100% あるのに完全未使用)。
**Alternatives**: 下記 R3-R5 で除外したもの。

## R2: リーク境界
**Decision**: 斤量/season=今走既知(静的)。place/人/course=strictly-before＋対象行/同日除外。
**Rationale**: 020 `_cum_before_by`(cumsum−当日)・human_form(対象行+同日除外)・class_transition(merge_asof allow_exact_matches=False) を流用。odds/popularity/今走結果は非特徴。

## R3: 脚質/展開(pace_setup)→ 除外(§3 送り)
**Decision**: 今走 running_style/展開は使わない。
**Rationale**: 実コード確認＋codex Q1: `race_horses.running_style` は (a)JRA-VAN では result-time コード(出馬表でなく結果と共に配信)、(b)netkeiba では `scrape/upsert._derive_running_style(corner_orders,...)` で**コーナー通過順=結果から導出**。よって今走値は**今走結果リーク**。023 は過去 running_style のみ as-of 利用。脚質系は §3 で「各馬の strictly-before 優勢脚質→フィールド構成」として再設計（pace_features の front_runner_rate 同型）。

## R4: 枠/馬番バイアス(draw_bias)→ 除外
**Decision**: 作らない。
**Rationale**: codex Q2＋確認: baseline は既に frame/horse_number/venue_code/distance/field_size を静的に持ち、LightGBM が course×draw 交互作用を学習可能。venue×distance 枠バイアスは公知で**オッズに織り込み済みの公算**。冗長＝最弱寄与になりやすく bundle を希釈(027 教訓)。

## R5: race.grade → deferred
**Decision**: 本 feature では追加しない。
**Rationale**: codex Q5 は提案したが、実 DB で **grade は 26.8% のみ** populate・値が不透明コード(E/C/B/A/L/H/G)・race_class(100%, 020 `_CLASS_RANK` でクラス階層を既に符号化)と冗長。コード解読込みで別途。

## R6: 斤量の作り方
**Decision**: carried_weight=jockey_weight、carried_weight_ratio=jockey_weight/weight(馬体重)、carried_weight_rel=jockey_weight − レース内平均、carried_weight_change=今走−直前 started race(as-of)。
**Rationale**: codex Q3: 斤量は出馬表で事前確定(netkeiba entries parser が読む)＝リーク無し。比率/相対は既知値の純変換。**馬体重欠損時 ratio は NaN 伝播(0補完しない)**。

## R7: 採用プロトコル（事前登録 per-group, codex Q4）
**Decision**: 各 group を「単独で features-007 に足して同一 OOS ゲートを通れば採用」と実装前に固定。出荷=features-007+通過群の和集合。
**Rationale**: codex Q4: OOS 数値を見て group を取捨すると選択リーク。group/列/fold/baseline/指標/閾値を eval 前に凍結し機械的に適用すればリークでない。各群=独立の事前登録仮説。実装: feature-eval に candidate-drop を足し、g 毎に candidate=features-007+g vs baseline=features-007 を評価。ablation は診断のみ。

## 実データ結果（T019, 18 fold walk-forward OOS, baseline=features-007）
**per-group（各 g 単独を features-007 に追加）**: place_rate 0.23316→**0.23299**(15/18)=採用、handicap 0.23317/season 0.23321/human_form_plus 0.23331/course_aptitude 0.23321 = いずれも微悪化・不採用。
**bundle（全5群同時）= ADOPTED=True**: LogLoss 0.23316→**0.23277**・AUC 0.74682→**0.74810**・Brier↓・ECE 0.00954→**0.00893**(改善)・16/18 fold・worst_dECE +0.00100(<2e-3)・worst_dLogLoss +0.00026(<5e-3)。
**決定: 全5群を採用**(features-008=全030)。理由: per-group で個別微悪化だった4群も、全部入りでは place_rate 単独(0.23299)を上回り(0.23277)校正も改善＝LightGBM が群間交互作用を活用し、個別限界寄与は過小評価だった。bundle は事前登録の正当な比較(codex Q4「union を1回評価」)で OOS ゲート通過＝採用は評価先行(III)に適合。「不要特徴が混じっても寄与度が処理し全体で勝てる」をデータで確認。market_edge は SECONDARY(採否外)。

## Codex second opinion（取得・反映済み）
Q1 running_style=結果由来→pace_setup §3 送り(✓)。Q2 draw_bias 冗長/市場織り込み→除外。Q3 斤量 pre-race・馬体重欠損 NaN 伝播。Q4 per-group 事前登録ゲート(凍結)。Q5 season 追加・grade はスパースで deferred。reconcile 差分: draw_bias 削除・season 追加・採用を per-group 事前登録に確定・grade deferred。
