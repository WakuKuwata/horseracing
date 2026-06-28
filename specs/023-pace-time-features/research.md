# Research: ペース/時計シグナルの特徴量化 (023)

codex second opinion を踏まえた技術判断。各項 Decision / Rationale / Alternatives。

## R1: 正規化方式（P0, 最重要）
- **Decision**: **レース内相対化を主**。各過去レースで last_3f / finish_time を「そのレースの平均（または上位基準）との差」に変換し、馬ごとに as-of 集計。**着差 finish_time_diff を併用**（勝ち馬相対で扱いやすく、メンバー強度の影響を一部吸収）。条件別 z-score（距離帯×芝ダ×going）は補助で、基準の平均/分散を **as-of（対象レースより前）分布のみ** から作る。少数サンプル条件は null か粗い条件にフォールバック。
- **Rationale**: 生秒は距離/馬場/年代で水準が違い無意味になりやすい。レース内相対はその過去レース内に閉じるためリーク面が小さい。全期間 z-score は未来時計水準の混入（リーク）。
- **Caveat（codex P0）**: レース内相対は「強メンバー戦で好走した馬」が相対不利に見える逆転を生む → 着差併用と、必要なら race_class での粗い補正で緩和。
- **Alternatives**: 生秒（却下: 水準差支配）。全期間 z-score 先計算（却下: リーク）。本格スピード指数（deferred: 過剰）。

## R2: リーク経路と防止（P0）
- **Decision**: **正規化済みの「過去走 row」を先に構築** → その row だけを `_cumulative_before`（daily cumsum−当日）+ `merge_asof(allow_exact_matches=False)` で as-of 集計（004/020 機構転用）。今走 row は集計経路に入れない。
- **典型リーク経路（明示して塞ぐ）**: (a) 対象レースの同走馬の今走時計でレース内平均を作る、(b) 条件別 z-score を全期間で先に作る、(c) 同日他レースを基準に含める、(d) running_style の今走値を pre-race 属性化。
- **leak test**: 今走結果の変更だけでなく、**同走馬の今走値・同日他レース・未来年の時計基準** を変更しても各特徴が不変であることを検証。
- **危険点**: 現 loader は last_3f までしか読まない。finish_time/finish_time_diff/corner_orders/running_style の **loader 追加箇所が最大の危険点** → 追加直後に leak test を固める。

## R3: corner_orders / running_style（P1）
- **Decision**: MVP の主対象から外し **position_style group（任意）** に分離。ablation で寄与を確認し、寄与が無ければ採用しない。使う場合は position/field_size・最終コーナー相対位置・位置取り変化・過去脚質分布に圧縮、欠損は除外（0 代入禁止）。
- **Rationale**: 通過順位は枠/展開/距離依存でノイズ大、脚質は事後分類で主観・表記揺れ → 過学習源。上がり3F/着差に比べ情報密度が低い。
- **Alternatives**: 主特徴に含める（却下: ノイズ）。完全除外（保留: ablation で判断）。

## R4: 市場織り込みリスク（P1）
- **Decision**: 023 は **win 絶対品質向上の小さな候補**として進め、市場超過は主目的にしない。market_edge で診断のみ。
- **Rationale**: 時計/上がりは競馬で最注目の公開指標 → 市場 q に強く織り込み済みの公算。020 同様「LogLoss 微改善・市場超過ゼロ」が現実的にあり得る。
- **次候補（別 feature, spec deferred）**: 条件替わり（前走不利/展開ミスマッチからの距離・馬場替わり）、距離短縮/延長×上がり性能の相互作用、トラック/開催日バイアス逆行好走 — 市場が過小評価しやすい相互作用系。

## R5: 採用ゲート（P0）
- **Decision**: 020 ゲートを流用しつつ、(a) **strict majority**（`n_win > n_folds/2`、偶数 fold で半数通過を防ぐ）、(b) **worst-fold LogLoss 悪化上限**、(c) **条件別（距離帯/芝ダ/going/開催年/q bucket）LogLoss・ECE 差分** を AdoptionReport に追加。候補事前固定・fold 内ハイパラのみ（選択リーク禁止）。
- **Rationale**: ペース/時計は条件依存が強く、全体平均が条件別の崩れを隠す。020 実装の `n_win*2>=n_folds` は偶数 fold で半数通過になる。
- **Alternatives**: 020 ゲートそのまま（却下: 条件崩れ・半数通過を見逃す）。

## R6: 既存資産の再利用
- as-of 機構: 004 `history._cumulative_before` + `merge_asof`、020 の extra_features/human_form パターン。
- 評価: 020 の `eval/feature_eval`（AdoptionReport）・`ablation`・`market_edge`（PREDICTOR-AGNOSTIC）。`predictor.drop_features` で baseline 構築。
- registry: 020 の `FEATURE_GROUPS` + `FEATURE_VERSION` を拡張（features-006）。
- スキーマ: 変更なし（loader の SELECT 追加のみ、DB 構造不変）。
