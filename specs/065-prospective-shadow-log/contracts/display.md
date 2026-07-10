# Contract: display — shadow-log panel (front, read-only)

## front `ShadowLogPanel.tsx`

- `GET /api/v1/shadow-log` を読み、**prospective 実績**を表示。
- **正直ラベル(常時)**: 「real 約定可能オッズ・前向き(prospective)・closing でない・将来の利益を約束しない」。retrospective closing backtest(049/064 の RecommendationPanel)とは**別セクション/別コンポーネント**。
- 指標: 凍結オッズ realized 回収率・的中率・確定 bet 数・未確定(集計待ち)数・時系列(by_month)。
- **空状態を正直に**: prospective データ0のとき「まだ前向きデータが無い/収集はこれから」を表示(偽の数字を出さない・FR-006)。
- 文言規律(049/064 継承): 利益語・損益色・ランキング禁止。回収<1 を中立事実として提示。
- 疑似値は含まない(win real のみ)が、万一混ざれば既存 PseudoBadge 規律。

## テスト(Vitest+RTL+MSW)

- `test_shadow_log_panel_honest_labels`: prospective/real-bettable-odds/closing でない/利益を約束しない のラベルが常時。利益語・損益色なし。
- `test_shadow_log_panel_empty_state`: データ0で「収集はこれから」を正直表示・偽集計なし。
- `test_shadow_log_separated_from_closing_backtest`: closing backtest と別セクションで、両者が混ざらない。
- OpenAPI 由来型で描画・drift-check 緑。
