# 116: 出走間隔補正のseed再現性確認

115で保持された125列モデル（相対能力13列削除）＋gap-log確率補正を、学習seed42/43/44で対応比較する。モデル・特徴量・補正式を再選択しない。deployment seedは42に固定する。

## 固定範囲

- 111の同じ歴史snapshotを使用する。新規DB snapshot・未使用期間の主張をしない。
- 学習recipeは900trees、8 OOF、weight mask seed20260810、学習seed以外すべて同一。anchor138列、pruning125列。
- seed42は115の係数・両評価・receiptと元110cacheを読み取り再利用する。全eligible raceのwinner NLLを補正から再生成し、元115両evidenceとの完全一致を確認する。bootstrapの再実行は不要。
- seed43/44各々はpruning2019..2026の8fitとanchor2020..2026の7fit、計30新fit。anchor2019は補正warmupに不要なのでfitしない。
- gammaは各seed自身のpruning予測を使い、2019をwarmupとして、評価年より厳密に前の年だけでfitする。115の数値診断、raw確率、assembly eps=0を保持する。
- 評価期間2020-01-01..2026-08-23。各seedで補正モデル対同seed pruning（増分）および同seed anchor（全体）をpaired評価する。
- 各seedのv4品質gateはdelta0、B4000、alpha.0125、bootstrap seed20260907、sd_fold.001816、k_seeds1を維持する。再学習seed平均に置き換えたCIとは呼ばない。
- 各seedの保持状態は独立して記録する。seed全ての改善を追加条件にしない。seed平均の判断規則は親の別集計scriptを結果確認前に固定する。平均損失は確率アンサンブルではない。

## 証跡と実行

旧source・package・cache・reportは変更しない。新driver/config/runtime/115 upstream証跡をprepareで凍結する。新cacheはjob/key/trainhash/feature columns/recipe/OOF/全確率/母集団とreceipt SHAを照合する。最大2独立worker、各1thread。完了receiptのあるjobのみ再開時に再利用する。未receiptの出力は保存して診断する。

smokeは43/44×anchor/pruningの4構成を5trees/2OOFで構造確認し、gamma0による補正配線を確認する。効力判定をしない。prepare・smoke・train・evaluateは親がレビュー完了後に実行する。

全成果はhistorical development full informationの研究結果であり、can_adopt=false、eligible_for_verdict=false。年枠の予約と本番変更は行わない。

## 固定した集計規則

42/43/44の等重み平均で、増分・全体の両winner NLL差が負、平均top2/top3差が各.0005以下、平均candidate ECEが.05未満、平均ECE差が.001以下なら平均改善として保持する。必要数値が揃う個別seedの品質BLOCKEDはREVIEW_REQUIREDとする。seed欠損・不正数値・来歴不一致では集計そのものを停止し、不完全な平均を出さない。個別の点差DEFERや部分集団NOT_PROVENだけでは平均改善を拒否しない。全seed負、多数決、平均CIは要求しない。3seed標準偏差は記述統計として示し、seed42やアンサンブルのノイズに換算しない。

6reportの全評価集合とeligible race ID/date/orderを完全一致検証してから平均する。提供用seed42の最近年・部分集団の不確実性は残余リスクとして表示し、平均改善で解消したとはしない。集計script `scripts/gap_seed_summary.py` のSHAも116の実行source hashに含め、結果確認前に固定する。
