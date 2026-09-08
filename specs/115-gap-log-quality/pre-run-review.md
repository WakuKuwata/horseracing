# 実行前の独立確認

2026-09-07、設計・実装を独立レビューし、未解決の阻害事項なし。実装担当の全変更停止後に親と独立担当が40件のテスト通過を確認した。

113の結果がまだ存在しない時点で、[事前登録](pre-registration.json)に115のsource/config hash、構成の選択規則、保持条件を保存した。113の結果完成後、これと同じsource/configで実行する。

確認した重要点:

- 113が両比較で保持されたときだけstack、その他はpruningを基準にする固定分岐。
- 元113のfactory・recipe・cache identityと2007年以降の学習集合を保持する。
- 2019は係数の初期fit専用、2020年以降は厳密に先行年だけでfitする。
- 元確率を再clipすると補正ゼロでも値が変わるため、検証済みqからeps=0で上位着順を組み立てる。
- 実paired_evalに接続し、補正前後で異なるrecipe metadata/hashと全品質レポートが生成される。
- 完了後の再開も係数・receipt・freeze・母集団・結果へ連結し、変更や欠落を検知する。
- 2比較を両方報告し、両方が保持条件を満たした場合だけ補正を保持する。研究成果は本番採用不可。

config hash: `6547759b6ebc633a386dbdf0539d4fea8a0a5f7d8eb72cac687bf1d971d37196`。
source hash: `912f634e752d94772ed729f7f8858ee7ab6cbd95d6f0576ef626f19effaf75ba`。

表示するv4 total CIのseed-noise値は移送仮定。補正係数の再fitや構成選択の不確かさを実証的に網羅したCIとは扱わない。
