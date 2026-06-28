# API Contract: 意思決定支援の表示強化 (021)

014 の read-only 規約を厳守（新規書き込み経路なし）。すべて `GET`、`/api/v1` 配下。型は committed `front/openapi.json` に反映し drift-check（憲法 VI）。

## 1. 拡張: `GET /api/v1/races/{race_id}/predictions`（US1 + US3）

既存レスポンス `PredictionResponse.horses[]` の各 `HorsePrediction` に追加:

| field | type | 意味 / 規約 |
|---|---|---|
| `win` | `number\|null` | モデル p（既存）。009 canonical field 正規化済み |
| `market_win_prob` | `number\|null` | 市場推定 q。**p と同一 canonical field** の win オッズ→`market_implied_win_probs`(010)→field 上で再正規化。有効オッズなしは `null`（0 補完しない）。pseudo（vote-share, FL bias 含む） |
| `data_backing` | `"weak"\|"medium"\|"strong"\|null` | リーク安全なデータ裏付け（事前情報のみ）。US3 採用条件未達なら全 `null`/省略 |

レスポンス・メタ（既存 `run` audit に加え、レース単位で）:
| field | type | 意味 |
|---|---|---|
| `market_prob_source` | `"win_odds_vote_share"` | q の出所ラベル（pseudo） |
| `canonical_consistent` | `boolean` | p と q が同一 canonical field か（false なら front は乖離表示を抑制, R1） |
| `odds_as_of` | `datetime\|null` | win オッズの `updated_at` |
| `odds_source` | `"final"\|"prerace"\|null` | 確定/事前推定（V/R10） |

不変条件: `Σ market_win_prob ≈ 1`（canonical field 上、`canonical_consistent=true` 時）。`market_win_prob` は `win`(p) と別フィールドで決して混同しない（p≠q）。

エラー（既存踏襲）: invalid race_id → 422、race not found → 404、予測 run なし → 200 typed-empty。

## 2. 新規: `GET /api/v1/models/{model_version}/calibration`（US2）

walk-forward OOS reliability を `model_versions.metrics_summary`（JSONB）から read（再計算しない）。

レスポンス `CalibrationResponse`:
| field | type | 意味 |
|---|---|---|
| `model_version` | `string` | 対象モデル |
| `oos` | `boolean` | walk-forward OOS か（常に true、in-sample は出さない, R2） |
| `source` | `"walk_forward_oos"` | 出所ラベル（pseudo/diagnostic 表示用） |
| `valid_years` | `int[]` | OOS 評価期間 |
| `n_total` | `int` | 総サンプル数 |
| `ece` | `number` | 全体 ECE（記述的診断） |
| `bins` | `Bin[]` | reliability bins |

`Bin`:
| field | type | 意味 |
|---|---|---|
| `pred_lo` / `pred_hi` | `number` | bin の予測確率範囲（等幅） |
| `pred_mean` | `number\|null` | bin 内予測平均 |
| `realized_rate` | `number\|null` | bin 内実現勝率 |
| `realized_ci_low` | `number\|null` | realized_rate の Wilson 信頼区間 下限（件数考慮、FR-006b の不確実性, analyze U1） |
| `realized_ci_high` | `number\|null` | realized_rate の Wilson 信頼区間 上限 |
| `count` | `int` | bin 件数（**必須表示**、少数 bin は suppressed フラグ） |
| `suppressed` | `boolean` | 件数不足で抑制（R5） |

エラー: invalid/unknown model_version → 404；metrics_summary に reliability 未収録 → 404 typed（"calibration_unavailable"、サイレント空でなく明示）。

注: 本 contract に EV/控除率フィールドは含めない（EV 表示は 021 スコープ外, analyze G1/FR-018）。q' (013) フィールドも含めない（生 q のみ, FR-002a 将来適用）。predictions の q 追加は per-horse win/q のみで 009 joint には影響しない（joint は既存 `?bet_type=` 経路のまま不変, analyze A1）。

## 3. 横断規約
- **read-only**: 上記いずれも `GET` のみ。`generate_*`(write) を呼ばない（014 規約）。
- **pseudo ラベル**: `market_win_prob`/`market_prob_source`/calibration `source` は front 単一 PseudoBadge 経路でラベル（V/R4）。
- **契約先行**: 本契約 → `openapi.json` 再生成 → 生成型 → front 実装、drift-check 必須（VI）。
- **leak**: 上記レスポンス値はモデル特徴に流用しない（leak-guard, II/R9）。
- **依存**: api は db + probability のみ（eval/betting に依存しない）。reliability は永続 JSONB の read で eval 不要。
