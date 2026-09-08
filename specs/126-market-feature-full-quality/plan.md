# 126実施計画

1. 122実行前の条件を変えず、2018通過候補の次段を設計する。全7fold案とrecent3足切り案を独立比較し、全7fold完遂案を推奨（レビュー回答済み）。
2. 122の3結果・summary・入力audit・独立レビュー完了を待つ。ADVANCE_TO_FULL_RESEARCHのみ選択し、0候補なら新fitなし。結果によって窓・品質基準・候補順を変更しない。
3. 別126 script/configで7fold×選択候補を登録する。CLI prepare/smoke/train/evaluateと完了resumeを設計し、122 source/configを直接変更しない。
4. prepareでは122が固定した152列研究行列をread-only inputとし、baselineの125列値/カテゴリ/recipe/全レース/fold/train集合と元113 native7cacheのreceipt/SHAを検証する。全23030/eligible22990/715日を元116と結び、式やパラメータに新しい候補変更がないことを確認する。
5. 構成別実効列126/131/125、colsampleの実学習パラメータ、全同scopeのraw125baseline、OOF/fit thread1、cache/結果改変、非eligible集合差、loss再計算、候補選択固定、完全同差DEFER等をテストする。
6. 独立レビュー後freeze。親が新126領域のsmokeでbaseline＋選択候補の5trees/2 OOF配線を確認する。0候補ならsmoke fitも0。煙試験から効力を判断しない。
7. 親が各候補の2026→2025→2024→2020→2021→2022→2023を2 workersで完遂し、その7foldを公式paired_eval+small_gain_researchで評価してから次候補へ進む。途中の点差・CI・NOT_PROVENによる足切りなし。入力/recipe/OOF/数値等の異常停止は独立診断後に再開する。
8. 全候補の報告・CI/recent/subgroup・来歴・残余リスクを保存し、seed42全期間の研究保持を記述する。追加seedやgap/ensembleとの累積効果は別研究とする。

driverはscripts/market_feature_full_quality.py。122の結果前に候補集合・選択規則をconfigへ固定し実装/テスト中。run-freeze・新学習cacheは未作成、fit未実行。旧110〜125は不変。
