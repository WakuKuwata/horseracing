# Research: モデル確率校正と edge haircut (017)

Phase 0。013(市場 q 校正)の機構を**モデル p 側**に転用しつつ、codex 指摘(joint 非保証 / 選択リーク /
役割分離・二重補正)を設計に反映する。betting/probability 拡張、016(Kelly)・009(結合確率)依存。

---

## R1: 校正手法（power/temperature を MVP、beta 候補、isotonic は gated）

**Decision**: モデル win 確率 p の校正は **power/temperature 族 p'_i ∝ p_i^γ**(γ=1/T)を canonical method とする。
γ は **race-normalized conditional-logit winner-NLL の MLE**(golden-section、決定論)で学習 — 013 の
`fit_power_gamma`/`_golden_min` 機構を p に転用。過信(overconfidence)は γ<1(=T>1)で緩和される。
beta calibration は候補。**isotonic は ranking 保存検査 + 最小サンプルを通った fold のみ**採用(codex #A:
小データ・人気薄帯で段差/ranking 破壊、Kelly は絶対確率に依存)。

**Rationale**: 013 が q'∝q^γ を採ったのと同型。power/temperature は単調でレース内正規化と両立し ranking を
保存(Kelly に必須)。golden-section MLE は乱数なしで決定論(SC-003)。

**Alternatives**: isotonic 無条件採用 → ranking 破壊で却下(gated に降格)。Platt(sigmoid)→ 多クラス
レース内正規化と相性が悪く power に集約。

---

## R2: marginal 校正 → 009 伝播（joint 非悪化を採用条件に）

**Decision**: marginal win p を校正し p' を **009 結合確率エンジン**に通して全券種 P_model' を得る。
**marginal 校正は joint 校正を保証しない**(PL/Harville は非線形)ため、**009 後の券種別 reliability
(exacta/trifecta 等の winner NLL/Brier)を before/after で測り、joint 悪化が無いことを採用条件に含める**
(codex #B、FR-005/SC-005)。joint 直接校正は組合せ爆発のため deferred。

**Rationale**: スキーマ最小・009 入力契約を壊さない。codex の「marginal 改善で joint 改善前提は危険」を
ゲート化。

**Alternatives**: joint 直接校正 → 三連単 ~4900 通りで非現実的、deferred。

---

## R3: walk-forward リーク境界（選択も窓内、fallback 明文化）

**Decision**: 校正器は 013 の `race_before`(date, race_id 厳密前)/`load_samples` を転用し、**対象レース開始
より厳密に前**のサンプルのみで学習。**方式・ハイパラ選択も各 fold の学習窓内**で行う(選択リーク禁止、
codex #D)。学習窓が基準(min_races / min_wins / per-band min、設定可能)未満なら **temperature のみ →
identity フォールバック**(γ=1)に降格(codex #2)。同着は教師から除外(件数 surface)。

**Rationale**: 035/036 の「入力非リークでも選択でリーク」前例を回避。小データ過学習を fallback で防ぐ。
p は pre-race 特徴由来で odds ほどの leak 非対称性は無いが、校正器自体の過学習は別問題。

**Alternatives**: 全期間共通校正(選択リーク) → 却下。

---

## R4: edge haircut（役割分離・独立 on/off）

**Decision**: Kelly の f* 計算前に edge を保守化。**relative: edge_adj=(1−h_rel)·edge**(既定)と
**absolute: edge_adj=edge−h_abs** を選択可。haircut は kelly_sizing 内で適用。**校正と haircut は独立
on/off**。役割を分離: **校正=系統的過信の補正**、**haircut=残差・推定誤差・モデルドリフトのリスク予算**
(codex #C)。edge_adj ≤ 0 は見送り(016 の負 edge 規則を踏襲)。

**Rationale**: 校正後も残る誤差に薄い保険。relative を既定にするのは absolute が低 edge 帯を一律に殺し
採用判断が不安定になるため(codex #C)。

**Alternatives**: haircut のみ(校正なし)→ 系統誤差を直さず非効率。固定 h のみ → 不確実性連動は deferred。

---

## R5: 評価ゲート（NLL/Brier 主・ECE 補助・必須ガード）

**Decision**: PRIMARY = race-normalized p' 上の **NLL/Brier 改善(主) + ECE/reliability(補助)**。
overconfidence 指標: **reliability slope・上位確率帯の over/under・calibration-in-the-large**。
**必須ガード(MUST)**: ①009 後 joint reliability 非悪化(R2)、②Kelly リスク非悪化(R6)。
ECE はビン依存のため単独採用しない(codex #E)。reliability/ECE は 013 の固定ビン `DEFAULT_BINS` を転用。

**Rationale**: codex #E「ECE 改善でも Kelly 悪化、NLL 改善でも逆」の逆転を必須ガードで捕捉。

---

## R6: Kelly diagnostic + 2×2（p×q）

**Decision**: 016 の bankroll backtest を拡張し **「生 Kelly」「校正のみ」「校正+haircut」を同一条件比較**
(最大DD・破産確率・分散・対数成長率)。success = 校正改善 かつ Kelly リスク非悪化(成長維持で破産/DD
低下)。さらに **2×2(raw/cal p × raw/cal q)** で EV・edge 分布・Kelly リスクを比較し二重補正(edge 過縮小)
を検出。**順序: q 校正(013)で O_est 確定 → p 校正 P_model' と結合**。p 校正結果は market odds 推定側に
戻さない(p≠q、codex #F)。

**Rationale**: codex #C/#F。両側校正が同じ realized 結果を教師にする二重吸収を 2×2 で可視化。

**Alternatives**: p のみ評価 → 013 併用時の過縮小を見逃す、却下。

---

## R7: スキーマ・統合・再現性

**Decision**: **スキーマ変更なし**(013 同様)。校正パラメータは logic_version に格納。p 校正器 + haircut を
**opt-in** で 009 消費側(exotic_ev/kelly_recommend/kelly_backtest)に渡す(013 が estimate_market_odds に
calibrator を opt-in したのと同形)。canonical field の p_norm を校正器で p'_norm に変換し 009 入力と一致
(FR-004)。logic_version に **校正方式・params(γ)・校正窓・選択方式・haircut(type,h)・base model_version**
を記録。p'・haircut・調整後 edge・Kelly fraction は features/training に戻さない(leak-guard)。

**Rationale**: 憲法 V/VI。recommendations.stake_fraction(016, 0006)は既にあり Kelly stake は記録済み。校正
情報は logic_version で十分(per-row の追加列不要)。

**Alternatives**: 校正 ID 列追加 → logic_version で再現可能なため不要、却下。

---

## 設計判断サマリ（codex second opinion 反映）

| 論点 | 採用 | codex |
|---|---|---|
| 校正手法 | power/temperature(MVP)・beta 候補・isotonic gated・identity fallback | #A → R1 |
| marginal→joint | 009 後 joint reliability 非悪化を採用条件に | #B → R2/R5 |
| 選択リーク | 方式/ハイパラ選択も fold 窓内、小データ fallback | #D/#2 → R3 |
| 校正 vs haircut | 役割分離・独立 on/off・relative 既定 | #C → R4 |
| eval ゲート | NLL/Brier 主・ECE 補助・joint/Kelly 非悪化を必須ガード | #E → R5 |
| 013 併用 | 2×2(p×q)で二重補正検出・順序固定・p を市場側に戻さない | #F → R6 |
