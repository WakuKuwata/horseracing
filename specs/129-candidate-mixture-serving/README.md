# 129 最良 6 モデル構成の候補化と並行予測

研究 125 の `new_joint_mixed6` を shadow 専用の 6 member bundle として新規学習し、本番 active(lgbm-094-cap900 + 凍結 stage 割引)と同一の発走前入力で並走記録する土台。**本番は不変・採用判定は未実施(NOT_READY)**。

- 結果と限界: [result-review.md](result-review.md)
- 手順: [quickstart.md](quickstart.md)
- 設計判断・codex 不可の記録・セルフレビュー: [research.md](research.md)
- 契約: [contracts/bundle.md](contracts/bundle.md) / [contracts/shadow-confirmation.md](contracts/shadow-confirmation.md)
- 成果物: `artifacts/129-candidate-mixture-serving/`(bundle.json・members/・receipts/・anchor/・shadow/・confirmation/)
- 証拠: `evidence/`(annual-serving-parity・power-development・power-plan・serving-compatibility・noise-justification・confirmation-readiness*・validation・final-integrity)

次にやること: (1) codex 復旧後に power/noise/compatibility の独立レビューを取得し preflight → reserve、(2) 開催日ごとに出馬表取込後・体重公表前の `capture --classification prospective`。
