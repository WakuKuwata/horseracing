# Feature/CLI Contract: ペース/時計シグナルの特徴量化 (023)

スキーマ変更なし。契約は (a) registry の新特徴メタ、(b) loader の SELECT 拡張、(c) 評価 CLI（020 再利用）。

## 1. registry 契約（features）
`FEATURE_GROUPS` に追加（020 と同じ FeatureMeta(source, timing, missing_policy) 形式）:

| feature | group | source | timing | missing |
|---|---|---|---|---|
| `rel_last3f_avg` | pace_time | 過去 race_results.last_3f | POST_RESULT(as-of, 出走表前確定) | Unknown |
| `rel_last3f_best` | pace_time | 〃 | 〃 | Unknown |
| `rel_time_avg` | pace_time | 過去 race_results.finish_time | 〃 | Unknown |
| `finish_diff_avg` | pace_time | 過去 race_results.finish_time_diff | 〃 | Unknown |
| `finish_diff_best` | pace_time | 〃 | 〃 | Unknown |
| `rel_corner_pos_avg` | position_style(任意) | 過去 race_results.corner_orders | 〃 | Unknown |
| `style_*` | position_style(任意) | 過去 race_horses.running_style | 〃 | Unknown |

- `FEATURE_VERSION` = `features-006`。
- すべて `model_input_features()` に含まれる（win モデル入力）。odds/今走結果は含めない（leak-guard）。
- 欠損は Unknown（null）、0 代入禁止。

## 2. loader 拡張契約（features/loader.py）
`load_frames` の SELECT に追加（DB 構造不変・read-only）:
- race_results: `finish_time`, `finish_time_diff`, `corner_orders` を追加。
- race_horses: `running_style` を追加。
- 既存の `end_date` フィルタ・2007+ scope を維持。

## 3. 評価 CLI 契約（training/cli.py、020 再利用）
- `feature-eval [--from --to]`: 候補=features-006 全特徴、baseline=`drop_features=(pace_time + position_style の全列)`。AdoptionReport（**strict majority**・worst-fold LogLoss 上限・条件別差分を含む）を出力。
- `feature-ablation [--groups]`: pace_time / position_style group 寄与（diagnostic）。
- `feature-diagnostic`: market_edge（市場超過診断、SECONDARY）。

## 4. 不変条件（テストで保証）
- **leak**: 各特徴は今走結果・同走馬今走値・同日他レース・未来年基準の変更に対し不変（FR-002）。
- **cutoff**: 対象レース当日以降のデータ変更で不変（FR-003）。
- **正規化**: 異なる距離/馬場の同等パフォーマンスが正規化後に近づく（FR-006、条件差吸収）。
- **採用**: baseline 未超過なら adopted=false（false positive なし）。strict majority（偶数 fold で半数通過しない）。
- **スキーマ**: db migration head 不変、新 ORM テーブルなし。
