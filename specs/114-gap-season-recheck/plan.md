# 実施計画

1. 独立レビューで081との意味差、母集団、先行年fit、sample CIの限界を確認する。
2. 3候補と日付・bootstrap設定を固定し、scriptsのみで入力検査・freeze・probeを実装する。
3. 境界テストと親レビュー後、prepareで113/111/110の元証跡を検証し114入力を凍結する。
4. runで単一プロセス・3候補順次の残差probeを行い、全結果と年別差・係数・数値検査を保存する。
5. 親が結果をレビューして次の研究候補の優先度を更新する。これは正式採用判定ではない。

実行環境はtraining/.venv Python。既存eval.residual_probeとbootstrapを再利用し、production source、特徴量registry、過去調査、DB、本番モデルは変更しない。113の長期学習を優先し追加メモリを概ね2GB以下に抑える。
