# 診断計画

1. 116保存証跡と111 snapshotの構造を確認し、独立意見を反映した固定分割をspec/configへ記録する。
2. 新規scriptだけで対応損失、開始前の群属性、寄与分解、日別感度、特徴量分布を出す。学習・現在DB・当該レース市場値を使わない。
3. 境界テストと独立コードレビュー後にprepare→run。旧source/configは編集しない。
4. 別agentが元as-of値から13列を独立再構成して入力異常を監査する。
5. 診断数値・完全性・解釈を独立レビューし、READMEと結果レビューへ全体の結論と次の優先調査を記録する。

実行環境は `training/.venv/bin/python`。成果物は `specs/117-pruning-2026-diagnostic`、作業証跡は `artifacts/117-pruning-2026-diagnostic`。研究候補の保持判断は116のままとし、117は診断結果を追加する。
