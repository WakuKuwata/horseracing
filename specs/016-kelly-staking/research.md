# Research: Kelly 賭け金最適化と bankroll backtest (016)

Phase 0。spec の各 FR と codex second opinion を技術判断に落とす。すべて betting/ 拡張 + probability(009/010) + 011/012 規約に依存。

---

## R1: Kelly fraction 公式（単一買い目）

**Decision**: 各買い目 c で decimal odds（払戻倍率）O(c) と的中確率 P_model(c) を用い、
edge(c) = P_model(c)·O(c) − 1、生 Kelly fraction f*(c) = edge(c) / (O(c) − 1)。
実効 fraction = clip(λ·f*(c), 0, cap_bet)。f*(c) ≤ 0 は見送り（stake=0、不保存）。
O(c) < O_min（最小オッズ閾値）は分母 (O−1) 不安定のため除外。

**Rationale**: decimal odds O で 1 単位賭けて当たれば O 倍戻る（net 利益 b=O−1）。標準 Kelly は
f* = (P·b − (1−P))/b = (P·O − 1)/(O − 1)。これは edge/odds の形で、本系の EV=P_model·O と整合
（edge = EV − 1）。fractional Kelly λ で分散・推定誤差感度を抑制（実運用の標準）。

**Alternatives**: 対数効用以外の効用（却下: Kelly=log 効用が長期成長最大で本 feature の目的に一致）。
固定額 flat（=011/012、baseline として残す）。

---

## R2: 同一券種内の相互排他配分（多項アウトカム Kelly）

**Decision**: 同一(race, bet_type)の採用買い目集合 {c} は**相互排他**（高々 1 つ的中）。
これを simultaneous Kelly として、期待対数成長
  G(f) = Σ_c P_model(c)·log(1 − S + O(c)·f_c) + (1 − Σ_c P_model(c))·log(1 − S),  S = Σ_c f_c
を制約 f_c ≥ 0, S ≤ cap_total の下で最大化する（G は concave、制約は線形 → 一意解）。
決定論的な凸最適化（射影勾配 / Newton、乱数初期化なし）で解く。これを **canonical 配分**とする。
計算簡便性のための **heuristic 配分**（各 f*(c) を個別計算 → 合計が cap_total 超なら比例縮小）も実装し、
両者の差（log 成長・stake 乖離）を backtest で計測・明示する（FR-004、logic_version に配分方式を記録）。

**Rationale**: codex BLOCKER #1。相互排他では各買い目の当選は他の全損を意味し、賭けは強い負相関。
個別 Kelly の単純合計は同時最適と一致せず**過大賭けバイアス**。期待対数成長の直接最大化が正しい。
concave なので解が一意・決定論的。

**Alternatives**: 完全独立近似（単純合計）→ 過大賭け、却下（heuristic として近似誤差を測る対象に格下げ）。
券種間（馬連と三連単など）の同時最適化 → 相関構造が複雑 → **deferred**（spec Assumptions）。

---

## R3: fractional Kelly λ・cap・最小オッズ・推定オッズ安全装置

**Decision**: 設定（既定値、すべて configurable・logic_version に記録）:
- λ_real = 0.25（quarter Kelly、実オッズ）
- λ_est = 0.10（推定オッズはより保守的）
- cap_bet = 0.05（1 買い目あたり bankroll 比上限）
- cap_total = 0.10（(race,bet_type) 合計上限）
- O_min = 1.5（最小オッズ閾値、分母爆発回避）
- min_edge = 0（実）／ min_edge_est > 0（推定、低 edge を追加除外）
推定オッズ経路は λ_est 適用 + min_edge_est フィルタ、設定で Kelly 完全無効化も可能。

**Rationale**: codex #2。推定オッズ（二重疑似）上の Kelly は f=edge/(O−1) の分母 O−1 が小さいほど
誤差敏感で過大賭けに直結。λ_est<λ_real、min_edge_est、O_min、cap で多層防御。憲法 V の誤読防止と
実運用破産回避の両立。

**Alternatives**: 推定オッズ時は常に Kelly 無効（過保護、ユーザーが評価できない → 設定可能な抑制に緩和）。
単一 λ（実/推定同一）→ 推定の危険を無視、却下。

---

## R4: 使用オッズ源とフォールバック（実 → 推定）

**Decision**: per-(race, bet_type, selection) で **012 実 exotic オッズ優先**、無ければ **010 推定オッズ**に
フォールバック。011/012 と同一の canonical_field / to_selection 単一経路（UNIQUE(race,bet_type,selection)
で実オッズを exact join）。row-level に odds_source・market_odds_used・estimated_market_odds_used・
is_estimated_odds・double_pseudo(=is_estimated_odds) を持つ。

**Rationale**: 012 の row-level 区別を踏襲。実オッズが最も信頼でき、推定は二重疑似。

**Alternatives**: 実オッズのみ（推定フォールバック無し）→ カバレッジが薄く US3 が成立しない、却下。

---

## R5: p≠q リーク境界

**Decision**: Kelly の確率は **必ず P_model(c)**（009 をモデル win 確率 p に適用）。市場 vote share q は
オッズ（O）の導出にのみ使い、**確率としては絶対に使わない**。Kelly fraction・stake・stake_fraction・
オッズ・q のいずれもモデルの特徴量・学習入力に出現させない。買い目決定は結果（着順）非参照、
結果は backtest 採点のみ。leak-guard test（import グラフ / 値の非循環）で機械検証。

**Rationale**: 憲法 II（NON-NEGOTIABLE）。011/012/013 と同一の p≠q 規律。codex #D の「P_model 過信 →
過大賭け」は edge haircut / 確率校正で将来対処（deferred、Assumptions）。

---

## R6: bankroll backtest と破産確率の評価

**Decision**: walk-forward（時系列順）に bankroll を逐次更新する**実経路**を主とする。各レースで Kelly stake
（または flat stake）を賭け、的中なら +stake·(O−1)、外れなら −stake、同着は按分。bankroll が ruin 閾値を
割ったら以降停止。計測指標:
- 終端 bankroll、対数成長率（Σ log(W_t/W_{t-1}) / N）、最大ドローダウン、分散、最大連敗
- **破産確率**: (a) 実経路の ruin 有無（0/1）に加え、(b) **block bootstrap**（時系列ブロック単位で
  リサンプリングし系列内相関・順序効果を保持）で複数経路を生成し ruin 割合を推定。
  単純 i.i.d. シャッフル（順序破壊）は使わない。
flat（011/012）と**同一条件**（同一買い目母集団・同一オッズ源・同一期間）で比較。実オッズ区間と
二重疑似（推定）区間を**分離集計**。success = flat に対しリスク調整後成長で優位（対数成長率が高く、
かつ最大DD・破産確率が許容内）。単なる ROI>1 は success としない。

**Rationale**: codex #3。単純 bootstrap は時系列性・regime・相関を壊し破産確率を楽観化。block bootstrap +
walk-forward 実経路の併用で頑健化。憲法 III（評価先行）の運用指標（回収率/的中率/最大DD/最大連敗/
見送り率）を Kelly 文脈に拡張。

**Alternatives**: 解析的 ruin 近似（Gambler's ruin）→ 多券種・非定常で前提が崩れる、補助に留める。
モンテカルロ（確率モデルから生成）→ 将来拡張、deferred。

---

## R7: スキーマ・永続化・再現性

**Decision**: **最小スキーマ変更** — `recommendations` に nullable 列 `stake_fraction Numeric` を追加
（migration **0006**）。Kelly の実効 fraction（λ·cap·配分適用後）を保持。flat（011/012）行は NULL の
まま（後方互換、既存行・既存コード不変）。Kelly 設定（λ_real/λ_est・cap_bet・cap_total・O_min・
min_edge・初期 bankroll・配分方式・odds_source・009/010 版）は `logic_version` に構造化して記録。
絶対 stake = stake_fraction × bankroll（config）で再現可能。pseudo_odds=1/P_model、pseudo_roi=edge=EV−1、
is_estimated_odds / market_odds_used / estimated_market_odds_used は 011/012 踏襲。double_pseudo は API と
同様 is_estimated_odds から導出（列追加しない）。backtest は 011 と同様**レポートを返す**（大量行を
永続化しない）。

**Rationale**: recommendations には **stake/fraction 列が存在しない**（011 の flat は per-unit 暗黙で
未保存）。Kelly の中核出力は per-row fraction であり、既存 nullable 列（pseudo_odds=1/P_model、
pseudo_roi=EV−1 は意味が埋まっている）への上書きは監査性（憲法 V）を損なう。nullable 列 1 本の追加が
最も後方互換かつ監査可能。012 が新テーブル(0005)を VI 下で正当化したのと同列に、本列追加も VI 下で
正当化（codex #E が kelly_fraction 列を妥当と評価）。

**Alternatives**: 完全無改変（既存列に fraction を詰める）→ 意味衝突・監査劣化、却下。新テーブル
`kelly_recommendations` → recommendations と二重管理・読取契約分裂、却下（1 列追加で十分）。

---

## 設計判断サマリ（codex second opinion 反映）

| 論点 | 採用 | codex 反映 |
|---|---|---|
| 単一 Kelly 式 | f*=(P_model·O−1)/(O−1)、λ·cap、O_min | R1 |
| 同一券種配分 | 期待対数成長最大化（canonical）+ heuristic（近似誤差を backtest 明示） | #1 → R2 |
| 推定オッズ安全 | λ_est<λ_real・min_edge_est・O_min・cap、無効化可 | #2 → R3 |
| 破産確率 | walk-forward 実経路 + block bootstrap（順序保持） | #3 → R6 |
| p≠q | 確率は P_model のみ、過信補正は deferred | #D → R5 |
| スキーマ | stake_fraction 列 1 本追加（0006）、config は logic_version | #E → R7 |
