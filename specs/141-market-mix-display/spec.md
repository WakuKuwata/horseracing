# Feature Specification: 期待回収率の市場混合版を並走表示する(②)

**Feature Branch**: `141-market-mix-display`
**Created**: 2026-10-19
**Status**: 実装完了・ローカル反映済(API 再起動 2026-10-19)・main 未コミット
**Input**: ユーザー「②(表示 EV の市場混合較正)に進めて」(`docs/roi-model-improvement-20261010/report.md` §3 の ②・§7 codex 採否)

## 背景(実測)

- 期待回収率(`mev-ens15-v1` の p̄ × 単勝オッズ)は周辺較正が市場より良い(ECE 0.0009)。だが**選ばれた馬では過大**: 確定オッズの walk-forward(2013〜2026-06)で EV 帯別 Σwon/Σp̄ は 1.0–1.2 で 0.91、1.2–1.5 で 0.78、>1.5 で 0.57。S3(EV>1.2)の表示平均 1.36 に対し真値は 1.05〜1.11。
- p̄ だけの 1 次元較正(isotonic・温度)は効かない。**市場 q との 2 次元混合** `p_mix = softmax_race(a·log q + b·log p̄)` で、S3 集合の Σwon/Σp_mix は 0.75→0.92、表示平均は 1.36→1.11(実測 ROI 1.07 と整合)、正規化 winner NLL は 1.9517→1.9510。係数は年次 walk-forward で安定(2013 年用 a 0.37・b 0.63 → 2026 年用 a 0.28・b 0.73)。
- 混合後でも EV>1.2 の Σwon/Σp_mix は 0.77(2,330 頭)で過大が残る。EV>1.0 は 0.95(24,737 頭・回収率 1.06・95% 区間 0.98〜1.14・現 S3 との重なり 91%)。
- 140 の実測: 判断時(発走前)の p̂_J は市場 q_J を超える情報をほぼ持たない(補正係数 b1≈0)。混合は市場へ寄せる向きなので判断時にも逆向きにはならないが、係数は確定オッズで fit したもの。
- codex レビュー(10/10): **主表示の置換はしない**。混合後の値は別列・別名で並走表示し、S1〜S5 の凍結表と前向き記録(現 EV 定義)の連続性を切らない。

## スコープ

1. **共有定義**(`eval/src/horseracing_eval/market_mix.py`): 年ごとの凍結係数(レース年 Y の係数は 2010〜Y−1 の OOF で fit)・版 `MIX_VERSION`・目印の閾値 `MIX_EV_THRESHOLD = 1.0`(厳密 `>`)・1 レース分の純関数 `mix_probabilities(win_probs, odds)`。
2. **係数の fit と再現**(`scripts/roi_explore/market_mix_20261019/`): 15 seed OOF(本番 ens15 とビット一致の研究 fit)から年次 walk-forward で a・b を fit し、10/10 報告の数値を共有定義の純関数で再現する。凍結係数は 2025・2026 年用。
3. **held-out 確認**(事前登録・一度だけ): 2026-08-10〜2026-10-18 の確定オッズ(保存単勝×100 が公式払戻と一致するレースのみ)で、本番の as-of 特徴から ens15 p̄ を再計算し、混合後と生の正規化 winner NLL・EV 帯別較正を比べる。
4. **API**(`GET /api/v1/races/{id}/market-ev` に純追加): 各馬に `expected_return_mix`(nullable)・`exceeds_mix_threshold`、レース単位に `mix`(`available` + `version`・`coef_year`・`a`・`b`・`threshold`、または `unavailable` + `reason`)。値は保存済み `win_prob`・`odds_used` から読み取り時に計算する(保存しない)。
5. **front**: 出走表に「期待回収率(市場混合)」列を追加(並び替え不可・推定バッジ)、100% 超に中立の目印(色・塗りなし)。注記に定義・検証の固定文言・限界を追加。

## 非対象

- 主表示の置換・S1〜S5 の定義変更・前向き記録(attention_picks)への混合版の追加。
- 判断時専用の係数(140 の ①-a/①-c は別系列)・券種の拡張・閾値 1.2 の混合版の目印。
- 2027 年用係数(12 月に年次更新。定義に年が無ければ `unavailable(no_coefficients)`)。

## Requirements

- **FR-001**: 混合は 1 レースの全 started 馬(保存行=現在の started 集合が一致するときだけ)で計算する。`q_i = (1/o_i)/Σ(1/o_j)`(`o = odds_used`)、`s_i = a·log q_i + b·log p̄_i`、`p_mix_i = exp(s_i)/Σexp(s_j)`。`expected_return_mix_i = p_mix_i × o_i`。p̄ のレース内正規化は softmax で打ち消されるので行わない。
- **FR-002**: 係数はレースの開催年で引く。年が定義に無い・p̄ が 0 以下・オッズが 1.0 未満なら `mix.status = "unavailable"`(`no_coefficients` / `invalid_inputs`)で、各馬の `expected_return_mix = null`。現行の期待回収率と取り違えないため、黙って生の値を代入しない。
- **FR-003**: 係数表は凍結定数(版・fit 窓・行数・入力 sha256 を併記)。fit スクリプトの出力と定数が一致することをテストで固定する(入力 artifact が無い環境では skip)。
- **FR-004**: `win_prob` と `p_mix` は応答に含めない(憲法 IV と 137 の方針を維持。混合後は正規化済みだが表示は EV 単位に統一)。
- **FR-005**: 主表示(`expected_return`・`exceeds_threshold`・閾値 1.2・S1〜S5)と保存行は一切変えない。
- **FR-006**: held-out 確認の規則(事前登録・`protocol.json` + sha256 を実行前に凍結): (i) 混合後の正規化 winner NLL の paired 差(混合−生)の点推定 ≤ 0、(ii) EV_mix>1.0 の Σwon/Σp_mix を区間つきで記録(判定に使わない)、(iii) 帯別の Σwon/Σp を記録。(i) が成り立たず、かつ 95% 区間の下限が 0 を超える(有意に悪化)なら、列を出さない(`MIX_DISPLAY_ENABLED = False`)。
- **FR-007**: 注記は固定文言: 定義(市場の評価と混ぜて過大を抑えた値)・過去検証(混合後 100% 超の回収率と推定/実際の比・120% 超では過大が残る)・判断時オッズの限界・「的中や利益を保証するものではありません」。利益を約束する表現・買いを勧める表現・損益色・並び替えは置かない。
- **FR-008**: OpenAPI は純追加(既存フィールドの型・必須性を変えない)。front/admin の snapshot と生成型を同期し drift-check を緑にする。

## Success Criteria

- **SC-001**: 共有定義の純関数で 10/10 報告の walk-forward 数値(正規化 winner NLL 1.9510・EV_mix>1.0 の 24,737 頭・Σwon/Σp 0.953)を再現する(浮動小数の許容内)。
- **SC-002**: held-out 確認を一度だけ実行し、結果を `results/` に残す(FR-006 の規則で列の表示可否を決める)。
- **SC-003**: API が混合後の値を返し、保存行から手計算した値と一致する(統合テスト)。既存の応答フィールドは不変。
- **SC-004**: front に列と注記が出て、pseudo バッジ・中立表示・禁止語の不変テストが緑。
- **SC-005**: 既存テスト(eval・api・front・admin・training の market_ev 周辺)が緑。

## 実測結果(2026-10-19・詳細 plan §4)

- **SC-001 達成**: 10/10 の walk-forward を共有定義で完全再現(混合後 EV>1.0 は 24,737 頭・Σwon/Σp 0.953・正規化 winner NLL 1.951682→1.951013)。
- **SC-002**: held-out(2026-08-10〜10-18・593 レース)で混合−生の正規化 winner NLL は −0.00135 [−0.00423, +0.00141]。悪化は見られず表示可。**ただしこの規則は検出力が低く、ほぼ必ず通る**。
- **開示**: held-out の「事前登録」は自己申告である。protocol・script・実行が同じ分のうちに作られ、実行前にコミットしていない(script の sha256 は plan §3 に記録)。
- 注記には、境界 1.0 を選んだのと同じ標本の回収率は出さない(推定/実際の比 0.95 のみ)。
- 表示値は発走前オッズで計算され、係数は確定オッズで fit した。発走前での較正の記録は未実施。
