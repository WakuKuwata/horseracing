# 128 実行前レビュー

2026-09-08、127の一次比較結果が出る前に、条件付きの次段を固定した。実装担当・独立担当・親の85テストがPASS。旧126/127とその上流を変更せず、128の独立領域だけへ実装した。

## 固定した実施条件

127のf04、finish_decomp、weight_deviationの全3結果、保存予測、receipt、summary、独立特徴量監査と独立予測監査を照合する。その後ADVANCE_TO_FULL_RESEARCHとなった全候補を登録順に選ぶ。候補0なら煙試験を含めてfitは0。良かった候補だけを先に採点して他を省略する進め方はしない。

全期間は2020-01-01〜2026-08-23、7年・全23030レース・winner NLL22990レース・715開催日。raw125列の同じseed42/900trees/8 OOFと比較する。基準の新学習は0で、元native cacheの全値・列・カテゴリ・recipe・train/race/fold・保存損失と来歴を確認する。

各候補2026→2025→2024→2020→2021→2022→2023を全て完遂してから報告し、次候補へ進む。途中年の効力・CI・NOT_PROVENによる打切りは禁止。構造/数値/来歴異常は停止し独立診断する。126/127と本学習を重ねず、最大2 worker・各1 thread。旧WORKの付替えは行わない。

必要改善幅δ0と既存small_gain_researchを使い、負の小幅差を品質確認後に保持できる。top2/top3、校正、直近3/5年、重要群とCIの不整合・非有限・不完全な結果を拒否する。特徴追加の差を季節・間隔補正・6モデル平均への上積みとして加算しない。

## 検証した境界

選択0/1/複数、候補ごと正確に7job、keyの一意性、禁止armとfull基準新fitの拒否、列/型/実学習params、recipeとnative境界、全cache/receipt/結果resume、重複実行、原raw125損失の一致、primary/最近年/全subgroupの値・CI・状態・残余リスクの改変を検証した。127の実際の特徴監査schema/hash/toleranceも照合した。

独立数値監査は0/1/複数候補・7fold/k1 noise・native110 metadata境界・scalar損失/校正・全recent/subgroup・開始終了SHAを検証する別methodで、全結果前に独立レビューして固定する。追加学習を行わず保存予測を検算する。

## ソース固定値

- config: `d0812c62d5bb4523d698ae2ebef6f907ddeec89c0f4050fba62d1c73a8f39666`
- source: `e794879ce53f24c51ae56c65f3fec22c2d87431167d25acbc1609b9bcb33b566`
- driver: `5cb4e3d1959ae265bf0849d88fa28ea3f6fc8b0470196b164d30ff7ce0231f98`
- tests: `b7878ec70403fba47320275c21de3ddfd3421beac82de7bfd447c7cb815c2058`
- 独立数値監査method（最終独立レビューPASS）: `5b59bcb02631f9615c415cfd282f8617e9856ff3668ba5df631c1084306c8fbd`

1候補7 outer=56 booster fits、最大3候補168。費用目安は1候補あたり2並列で約52〜60分に、準備・煙試験・評価・監査を加える。0候補なら本学習も煙試験も0。実測fit時間/RSSと全工程時間を混同せず記録する。本番切替・DB書込・年間確認枠予約は行わない。
