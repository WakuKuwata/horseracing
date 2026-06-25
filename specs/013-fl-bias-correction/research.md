# Research: 人気-不人気バイアス補正

010/012/憲法 II/III を踏まえた設計判断。codex second opinion（plan.md の表）を反映。CRITICAL は「正規化後を校正対象に」「エンジン
整合」で解消。

## R1. 正規化後 q' を校正対象に（CRITICAL）— べき乗が正準

- **Decision**: FL 補正は各馬の q_i に単調写像 `g` を適用し、レース内で `q'_i=g(q_i)/Σ_j g(q_j)` に再正規化する。**校正の学習・評価は
  この正規化後 q' に対して**行う（per-horse の生 `g(q_i)` を真値扱いしない）。**正準方式はべき乗 `q'_i ∝ q_i^γ`**（`g(q)=q^γ`、γ>0）で、
  **γ をレース集合の勝者尤度（conditional logit）MLE** で 1 次元最適化して学習する。
- **Rationale**: codex CRITICAL — per-horse marginal を校正してから再正規化すると、再正規化後の値はもはや校正した marginal では
  ない。レース正規化はそもそも field 文脈（他馬）に依存するため、正準的には**正規化後の race-level 変換**を勝者尤度で学習するのが正しい。
  べき乗→再正規化は FL バイアス補正の古典形（Ali 1977 / power model）で、γ>1 が favorite を相対的に強める。1 次元 MLE は安定・決定論的。
- **γ MLE の正則性/退化（codex HIGH）**: 目的 = `Σ_races −log( q_w^γ / Σ_j q_j^γ )`（勝者 w の conditional-logit 尤度）。
  - **探索範囲**: γ ∈ `[GAMMA_MIN, GAMMA_MAX]`（既定 `[0.1, 5.0]`）で有界 1 次元最小化（黄金分割等、決定論・seedless）。
  - **情報レースのみ使用**: 有効馬 ≥2 かつ q が全馬同一でない（同一なら γ に対し勾配 0＝非情報）レースに限定。
  - **退化フォールバック**: 情報レースが 0 件なら **γ=1（恒等、補正なし）** + 不十分マーク（fail-fast せず後方互換）。
- **Alternatives**: isotonic を per-horse marginal にフィット → 再正規化で崩れる（CRITICAL）。**MVP では power のみ**実装し、isotonic/
  log-log は正規化後目的でのアルゴリズムが非自明なため将来（method 引数は受けるが未実装は明示エラー）。
- **field 文脈**: 正規化 `/Σg` が field 文脈を内包。必要なら field_size 層別で校正の安定性を評価。

## R2. エンジン整合・q' 注入口（CRITICAL/HIGH）

- **Decision**: 009 engine は入力を `renormalize → clip[eps,1−eps] → renormalize`（`engine._normalize_clip`）する。`apply_g` は
  **この同一手順を末尾に適用**して q' を生成する（=エンジンに対し**冪等/idempotent**、`_normalize_clip(q')≈q'`）。これにより評価した
  q' と実際にエンジンが使う q' が一致（評価=使用）。**極小テール**（q'_i < eps）が出るとエンジンが clip するため、apply_g 側で同じ
  clip を先に当てて冪等性を保証し、clip 発火時の挙動（端点へ寄せて再正規化）を明示。`market_odds.estimate_market_odds` に **補正経路を
  opt-in 追加**（`calibrator` 引数）し、**生 q 経路は後方互換**で維持。field_size は**補正後の有効出走集合**から導出。
- **Rationale**: codex CRITICAL — 評価した q' と実際にエンジンが使う q' がズレると評価が無意味。整合させ「評価=使用」を保証。010 の
  既存 API を壊さず opt-in で追加（VI）。
- **Alternatives**: エンジンを書き換えて正規化を外す → 009 の不変条件（Σ=1）を壊す。却下。

## R3. リーク防止・選択リーク（HIGH/II）

- **Decision**: (a) 学習窓の strictly-before 境界は **`(race_date, race_id)` の辞書順**で `(race_date, race_id) < (target_date,
  target_race_id)`（`race_date` は常在で決定論。`post_time`（nullable）が両側で非 null なら intra-day を `post_time` で精緻化）。
  日付単位 `<=` は使わない（当日結果リーク防止）。学習/評価窓は非重複（重複は ERROR）。(b) **方式・γ・探索範囲の選択は学習窓内**
  （勝者尤度 MLE / nested walk-forward）で行い、最終評価期間を選択に使わない。(c) `q'`・オッズを **win モデルの特徴量に一切流さない**
  （`features`/`serving` に渡さない）。
- **Rationale**: codex HIGH — 「評価で方式を選ぶ」は test リーク。同日タイの `<=` は当日結果リーク。q→q' は市場オッズ側の変換であり
  モデル特徴ではない（odds は元々非特徴量、憲法 II）。
- **Alternatives**: 日付単位 split → 同日リーク。却下。

## R4. closing-odds の限界（HIGH）

- **Decision**: `race_horses.odds` は確定/締切寄り（008 は result-pending のみ上書き、JRA-VAN 確定を保護）。本フィーチャーは
  **回顧的（retrospective）市場校正研究**としてこの odds で学習・評価する。**運用（operational）**では出走前オッズ（008/012）を使い、
  締切オッズで学習した校正が朝オッズへ完全転移する保証はないことを限界として開示（既存 007/010 の closing-oracle 簡略化と同じ前提）。
  post-start オッズを deployable EV の入力にしない。
- **Rationale**: codex HIGH。透明性のため retrospective/operational を区別。
- **Alternatives**: 朝オッズ専用に限定 → 現状データに前売り履歴が乏しく評価不能。回顧研究を明示して進める。

## R5. 方式・テール・外挿（MED）

- **Decision**: べき乗（正準、外挿安定・単調保証 γ>0）。isotonic は per-horse 単調診断の代替で、**学習レンジ外は端点クリップ + 範囲外
  件数を報告**、疎テールは最小サンプル・テール統合。方式比較は nested walk-forward。
- **Rationale**: codex MED — isotonic OOR 平坦化・疎テール不安定。べき乗をテールフォールバックに。

## R6. 評価設計（ECE/帯/不足データ/同着）（MED/LOW）

- **Decision**: ECE/信頼性は**正規化後 q'** に対し、**固定ビン境界**・空ビン処理・clip を定義して計算。人気帯は**固定境界**（popularity
  または q 分位の固定エッジ）でサンプル数併記、過小帯は統合。勝者なし/空は fail-fast or 不十分マーク。**同着は除外し件数明示**
  （既存 market_calibration と同方針）。
- **Rationale**: codex MED/LOW — 未定義 ECE/動く帯/疎帯/同着の扱いを固定。

## R7. 採否ゲート（MED/III）

- **Decision**: **採否は勝率校正（NLL/Brier/ECE、人気帯別）**で判断（補正なし=生 q を baseline）。012 乖離（実 exotic は独自の控除/
  偏りを持つ）は**診断補助のみ**で唯一の成功条件にしない。
- **Rationale**: codex MED — 乖離は偏ったターゲット。直接の真値（実現勝率）で採否。

## R8. 監査・特徴量ガード（MED/V/II）

- **Decision**: 方式・γ・学習窓・サンプル数を **logic_version 相当メタ + 校正器 artifact** に記録（**スキーマ変更なし**、オッズ履歴を
  作らない）。**リーク・ガードテスト**で odds/q/q' が win モデル特徴に入らないことを assert。
- **Rationale**: codex MED — 監査喪失・特徴量混入の防止。

## まとめ（設計判断 → 要件）

| 研究項目 | 対応 FR / SC |
|---|---|
| R1 正規化後校正(べき乗) | FR-001 / FR-002 / SC-001 |
| R2 エンジン整合・注入口 | FR-005 / SC-003 |
| R3 リーク・選択 | FR-001 / FR-003 / FR-004 / SC-002 |
| R4 closing-odds | Assumptions / SC-003 |
| R5 方式・テール | FR-004 / SC-006 |
| R6 ECE・帯・同着 | FR-007 / FR-010 / SC-004 / SC-007 |
| R7 採否ゲート | FR-007 / FR-008 / SC-004 / SC-005 |
| R8 監査・特徴量ガード | FR-003 / FR-004 / FR-009 / SC-002 / SC-006 |
