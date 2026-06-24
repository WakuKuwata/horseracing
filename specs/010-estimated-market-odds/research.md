# Research: 推定市場オッズ変換

Phase 0。NEEDS CLARIFICATION なし。codex の市場モデルレビューを反映した設計判断を記録する。

## R1. 市場含意 win 確率 q(投票シェア)

- **Decision**: `q_i = (1/odds_i) / Σ_{j∈母集団}(1/odds_j)`。これはパリミュチュエルの**投票シェア**(`odds_i=R/s_i`
  なら `q_i=s_i`)であり、**真の勝率でもモデル確率 p でもない**。favorite-longshot bias を含む。命名は `market_implied`/
  `q`、保存・メタで p と区別する。
- **Rationale**: codex — オーバーラウンド `Σ1/odds=1/R` を除去すると市場の投票シェアになる。これを「校正済み勝率」と
  呼んではならない。既存 `MarketBaseline`(eval/baselines.py)も `1/odds` 正規化を参照線専用として扱う。
- **Alternatives considered**: bias 補正 q → 補正則が別問題(将来)。生の投票シェアを正とする。

## R2. 単勝オッズ復元

- **Decision**: 推定単勝オッズ `hat_odds_i = (1−takeout_win)/q_i = R·S·odds_i`(`S=Σ1/odds`、`R=1−takeout_win`)。
  控除率=実オーバーラウンド(`R·S=1` ⇔ `S=1/R`)のとき `hat_odds_i = odds_i` を厳密復元する。
- **Rationale**: codex 代数 — 全馬同率 `R·S_r` の誤差。復元性は変換の健全性チェック(単勝は実 odds がある)。
- **Alternatives considered**: なし(数学的性質)。

## R3. 市場含意 q への Plackett-Luce 適用(疑似性)

- **Decision**: `q`(Σ=1)を Feature 009 `joint_probabilities` に入力して市場含意 exotic 確率 `P_market(c)` を得る。
  推定 exotic オッズ `= (1−takeout_券種)/P_market(c)`。これは**単勝市場から PL で外挿した推定**であり、実 exotic 市場
  (券種別独立プール)とは乖離しうるため**「推定/疑似」として明示**する(憲法 V)。ワイドは `Σ_k trio`(009 と同じ)。
- **Rationale**: codex — PL は整合的順位分布を作る標準近似だが exotic 実価格の定理ではない。実 exotic オッズ取得後に
  価格復元評価が可能(将来)。
- **Alternatives considered**: 独立積で exotic 推定 → 整合性破壊(009 で禁止済み)。PL を採用。

## R4. モデル p と市場 q の分離

- **Decision**: 変換は**市場オッズのみ**を入力とし、予測モデル確率 p を一切参照しない。q は別型 `MarketImpliedWinProbs`/
  別命名で扱い、推定オッズは `is_estimated_odds=true`(将来 recommendations)で実オッズと区別。将来 EV は
  `EV(c)=p_b(c)·hat_odds_q,b(c)−1`(p=009 モデル確率、hat_odds=本フィーチャーの市場推定)。
- **Rationale**: codex 最重要 — p と q を同一列/オブジェクトに混入させると EV が `p_model×p_market` になり破綻。
- **Alternatives considered**: なし。分離は必須。

## R5. 控除率(takeout)

- **Decision**: 券種別控除率を `dict[bet_type, payout_rate R_b]` で持ち、JRA 既定(平成26年6月7日以降): 単勝/複勝 20%
  (R=0.80)、馬連/ワイド 22.5%(0.775)、馬単/三連複 25%(0.75)、三連単 27.5%(0.725)。**設定可能**で、使用値を
  logic_version に含める。複勝の `(1−takeout)/P(place)` は**粗い近似**(実払戻はプール分配・払戻対象頭数依存)と明示。
- **Rationale**: codex — JRA 公式表に一致。控除率は時点依存(改定リスク)→設定可能 + 監査(logic_version)。
- **Alternatives considered**: 単一控除率 hard-code → 券種差・時点差を無視(却下)。

## R6. 数値・整合性

- **Decision**: `odds_i>0` のみ q 母集団に含める(欠損/0/負は除外)。取消・除外も除外。除外後 `S=Σ1/odds>0` を確認し
  再正規化。残存<必要頭数や `S≤0` は推定不能を返す。`P_market(c)→0` のとき**推定オッズ(派生値)を上限 cap または
  None**。**確率本体(P_market)は cap しない**(Σ=1 等を壊さない)。
- **Rationale**: codex — 確率 cap は整合性破壊。派生値だけ cap。母集団除外・再正規化は憲法 IV。
- **Alternatives considered**: 確率 floor → 整合性崩壊。派生 cap を採用。

## R7. 評価先行(検証 harness)

- **Decision**: 過去データで (a) **単勝オッズ復元誤差**(レース単位 `|log(R_win·S_r)|`、`mean_i|hat_odds_i/odds_i−1|`、
  `max_i|hat_odds_i−odds_i|`)、(b) **市場含意 q の校正**(実勝馬に対する NLL/Brier、`eval.metrics` 流用)を計測。実
  exotic オッズが無いため exotic は結果校正のみ(価格復元は将来)。**全出力を疑似評価として明示**。
- **Rationale**: codex/憲法 III — 検証可能な範囲で変換の健全性を示す。ROI は推定市場オッズの疑似評価。
- **Alternatives considered**: 実 exotic 価格復元 → データ無し(将来)。単勝復元 + q 校正に限定。
