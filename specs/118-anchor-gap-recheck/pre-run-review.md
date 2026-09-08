# 118 実行前レビュー

2026-09-08。独立agentが設計・実装・実データの来歴を確認し、実行阻害事項なし。親と独立agentの両方で62テストが通過した。ここからdriver・summary・config・旧110〜117のsource編集を停止し、親がprepare→smoke→2追加fit→6評価→集計を実行する。

- 追加fitはseed43/44のanchor2019のみ。138列・900trees・8OOF・同一mask・全過去年のtrain集合を維持する。
- seed42は原113/110、43/44の2020年以降は原116cacheを、原recipe・列順・identityのまま読み取る。
- 新gammaは各seedのanchor予測の厳密な先行年のみから推定し、retained側は元115/116のpruning係数を使う。係数の元モデル・path・SHAを両側別々に保存する。
- 旧全レースのNLL一致と、凍結cache・係数・entrypointによるwin/top2/top3再現をprepareで検証する。過去の全馬確率ファイルとの直接比較とは扱わない。
- 候補保持と研究優先構成を分離する。全seed改善・多数決・各seedの有意差は追加要求しない。品質違反を平均で隠さず、不完全な証拠では集計を止める。
- 117の固定群による3診断は表示のみ。群別モデル切替・追加採用条件・平均CIを作らない。
- 独立実データ確認で96個の旧証跡、117独立監査のPASSと各SHA、3seed旧係数、snapshotと年別母集団の結合を確認した。

初回driverテストの2件は、116/118のcache identityを比較する際に同じ偽freezeを両者へ渡したfixtureの不備だった。独立診断後、旧116config/sourceを区別するテストfixtureだけを修正した。実験は起動前で、旧cacheや学習recipeの変更はない。

- config hash: `24cb6da209d8697379047412272027211f9c8708944c568cf639690470012bf6`
- source hash: `73d3ed603b553559ebed0705de51a471289a0550456aa99229e0f240c996c649`
- driver SHA: `e43e9f801b9585d927a775b11bc206ead8f36eeeebc0238470563b0149a827b3`

歴史研究としてのみ実施し、本番採用・年枠予約は行わない。移植したノイズ・係数固定bootstrapは、両gammaの再推定不確実性と選定効果の全体をカバーしない。
