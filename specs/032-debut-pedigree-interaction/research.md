# Phase 0 Research: 低コスト×血統適性 交互作用 (032)

## R1: 何を作るか（5 列）
**Decision**: debut_pedigree group = `sire_debut_win_rate`(新情報・主役)+ `debut_x_sire_win_rate`・`debut_x_sire_dist_band_win_rate`・`lowhist_x_sire_win_rate`・`lowhist_x_sire_dist_band_win_rate`(ゲーティング交互作用・副次)。全 float64。
**Rationale**: codex の独立判断「既存特徴同士の単純積は GBM が木分割で学習済み=冗長」を受け、主役を **026 にない新情報**=種牡馬の他産駒デビュー戦勝率に置く。ゲーティングは GBM 冗長リスクを認めつつ「血統が履歴の薄い馬でだけ効く」非対称を浅い木で表現できる可能性を bundle で検証(030 前例=単独では落ちる群も bundle で採用)。
**Alternatives**: (a) 積のみの bundle → codex「15%未満」→ 却下。(b) damsire デビュー戦も → 母数小で deferred。

## R2: sire_debut_win_rate の作り方（新情報・リーク安全）
**Decision**: 各 horse の最初の STARTED 出走=debut run。debut-runs サブセットに 026 `_other_offspring`(sire 累積−自馬累積、daily cumsum−当日 で strictly-before・同日除外)を適用し、他産駒デビュー戦の勝率(o_cnt>=min_starts→o_wins/o_cnt、else NaN)。
**Rationale**: 026 は種牡馬の **総合** 勝率(全出走)のみ。デビュー戦特化は「早熟・仕上がりの早さ」という別シグナルで、市場が値付けしにくいデビュー馬に直結。031 の勝ち筋(モデルが持たない条件付き集約)と同型。自馬除外は 026 機構をそのまま使う(真のデビュー馬は自分の過去デビュー戦が無いので self 寄与=0、二重計上なし)。
**Alternatives**: 初 3 走平均など窓を広げる → 定義が曖昧・deferred。sire の総合勝率を流用 → 既存=新情報でない。

## R3: ゲーティング交互作用の作り方
**Decision**: is_debut/is_low_history(history group)× sire_win_rate/sire_dist_band_win_rate(026)の per-row 積。片側 NaN→NaN。再実装せず既存 as-of 列を掛ける。
**Rationale**: デビュー/低履歴は実質 0/1 ゲート。is_debut=1 のとき血統適性が「効く」非対称を明示。dist_band は自馬に距離実績が無いとき血統の距離適性が効く想定。GBM 冗長リスクは codex 指摘どおりだが、安価で leak-safe、OOS が採否を決める。
**Alternatives**: 連続な「履歴の薄さ」重み(1/(career_starts+1) 等)× sire → 過剰設計、まず 0/1 ゲートで検証。

## R4: リーク境界（憲法 II）
**Decision**: sire_debut_win_rate=他産駒の strictly-before デビュー戦のみ(自馬除外・同日除外)。ゲーティングは as-of 列の積のみ。今走 result/odds 非参照。
**Rationale**: 026 の `_other_offspring` は (sire 累積−自馬累積) で自馬を厳密控除し、daily cumsum−当日 で同日他産駒も除外。新ソース列なし(sire_name は 026 でロード&fingerprint 包含済み)。leak-guard test(自馬今走・同日他産駒・未来 不変 + grep)。
**Alternatives**: なし(機構は 026 で確立)。

## R5: 採用プロトコル（事前登録 bundle, codex 同型）
**Decision**: debut_pedigree を 1 bundle として features-009 vs features-010 を walk-forward OOS で評価(feature-eval 既定 --drop-groups=debut_pedigree)。ablation は diagnostic 専用。bundle 採用後に列を削るのは禁止。**SECONDARY=デビュー馬セグメント診断**(全体で薄くてもデビュー馬で効く可能性を market_edge/セグメントで記録)。
**Rationale**: 020/023/026/030/031 同型。codex 見積もりでは 031 より採用確率は不確実(全体 LogLoss はデビュー馬の出走比 10.5% に希釈される)→ セグメント診断を併記して「市場弱点で効くか」を可視化(採否は全体 OOS が決める=憲法 III)。
**Alternatives**: デビュー馬限定で学習/評価 → 母集団が変わり 009 比較不能・選択リーク懸念 → 却下。

## R6: 採用閾値
**Decision**: 020/023/030/031 と同型を流用(事前登録)。primary=平均 win LogLoss 改善 かつ ECE 非悪化。fold ガード=strict majority + worst-fold ECE 2e-3 + worst-fold dLogLoss 5e-3。
**Rationale**: 既存ゲートと整合。本 feature 用に緩めない(選択リーク回避)。

## 実データ結果（T013, 18 fold walk-forward OOS, baseline=features-009）
**bundle（debut_pedigree 全5列）= ADOPTED=True（僅差）**: win LogLoss 0.23200→**0.23193**(−0.00007, 031 の 1/10)・AUC 0.75143→0.75153・Brier 0.06205→0.06203・ECE 0.00878→**0.00858**(改善)・**10/18 fold**(strict majority=20>18 でぎりぎり通過)・worst_dLogLoss +0.00027(<5e-3)・worst_dECE +0.00101(<2e-3)・primary_pass=True。
**決定: 採用**(features-010=009+debut_pedigree, lgbm-032 再学習・active 昇格, lgbm-031 retired)。**codex の事前見立てが的中**: 「単純積は GBM 冗長・全体ゲインは debut 馬の出走比(~10.5%)に希釈・採用確率 20-35%」→ 採用側に僅差で着地。031(−0.00077, 17/18)に比べゲインは 1/10 だが、ECE 改善 + 全ガード通過で事前登録ゲートを機械的にクリア(数値を見てから閾値を動かさない=憲法 III)。実質価値はデビュー馬セグメント(95,070 頭・sire_debut カバレッジ 89.8%)。実 DB parity bit 一致(916k×86)。市場 q 超過は SECONDARY(採否外)。
**学び**: 「既存特徴の積=GBM 冗長」は概ね正しく、新情報 sire_debut_win_rate が主に効いた可能性が高い(ablation で確認可)。次の交互作用 feature(033 条件替わり)も同様に「未マージの新 base 列(027 dist/surface/going)」が主役で、積は副次と見込む。

## Codex second opinion（取得・反映済み）
- 「既存特徴同士の単純積は GBM 冗長」→ 主役を新情報(sire_debut_win_rate)に。ゲーティング積は副次・bundle で検証。
- 「032(条件替わり×能力)の積は冗長・採用確率 20-35%、新情報量では 低履歴×血統 > 条件替わり」→ ユーザー判断で **低履歴×血統を先(032)・条件替わりを後(033)** に順序確定。
- 「dist_change/surface/going(027)再導入 + hinge×末脚」は 033 へ分離(kitchen-sink 回避)。
- reconcile 差分: 当初の「is_debut×sire 積のみ」案を、新情報 sire_debut_win_rate を主役に格上げ。採用見込みを正直に低く見積もり、セグメント診断を SECONDARY に追加。
