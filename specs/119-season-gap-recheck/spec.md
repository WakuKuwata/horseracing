# 119: 季節×性別の追加効果

独立設計レビューで合意した3項joint補正を、raw125列pruningのseed42/43/44予測に適用する。hの順序を[log1p(gap),female sin(theta),female cos(theta)]、theta=2π(DOY−1)/(365または366)に固定する。femaleは牝1、牡/セ0、欠測NaN。欠測成分だけ指数offset0とし、他の成分を残す。

2019をwarmupとし、2020..2026の各係数ベクトルを、そのseedの厳密先行年のraw pruning予測から同時推定する。元gap-only補正には2019用gammaがないため、補正済み予測からの逐次追加にはしない。比較対象の旧115/116gap gammaは再推定せず保持する。測るのは季節2項とgap係数再調整を合わせた増分であり、gap固定下の季節効果とは呼ばない。

## 固定契約

- 追加booster fitは0。旧110〜118、package、cache、係数、reportを変更しない。
- 同seedのraw138 anchorとの比較IDはanchor、既保持pruning＋gapとの比較IDはretained。3seed×2比較。
- 全23,030評価レース、22,990eligible、715日。2020-01-01..2026-08-23。同一race/date/eligible/orderを確認する。
- vector shape(3,)、成分順[gap_log,female_sin,female_cos]、各2020..2026の7ベクトルをrecipe/receipt/hashへ明示する。
- 既存fit_gamma(k=3,ridge1e-6,max_iter50,tol1e-9)とfit_diagnosticsを使用する。目的関数<=1e-8、勾配最大絶対値<=1e-5、有限値と時系列を検査し、結果を見て設定を変更しない。
- eps0でwin確率を正規化し、Harville top2/top3を組み立てる。shape、馬集合、有限・内点・総和・top-k整合を確認する。
- 旧anchor baseline損失は元116 anchor.active、retained baseline損失は元116 anchor.candidateと全eligible raceで完全一致。candidate損失は両比較で完全一致。
- delta0/B4000/alpha.0125/bootstrapseed20260907/sd_fold.001816/k_seeds1と既存品質条件を維持する。

## 判断と診断

両比較の等seed平均NLL差が負かつ品質を満たす場合に季節増分を保持する。各seedの点差DEFERやNOT_PROVENだけでは拒否しない。数値が揃う品質BLOCKEDはREVIEW_REQUIRED、欠損seed・不正数値・来歴不一致は集計停止。全seed負、多数決、平均CIは要求しない。seed平均は確率ensembleではない。

prepareで性別識別性の監査と固定群属性を保存する。群は既知性別でno_female/mixed/all_female、性別欠測を含む場合missing_sexを優先する。全期間と2026の件数・差、既存117の2026/中山/部分観測群を記述し、追加gateや群別モデル選択には使わない。全既知牝馬/全非牝馬の季節項不変性は、gap係数を同じにした対照でテストする。旧gap-onlyとの比較ではgap係数も変わるため、その不変性を要求しない。

source/runtime/upstream、旧両baseline、118独立レビューのmethod/freeze/summary/6report/evidence、119集計script、季節入力監査SHAを結果前に固定する。40非eligibleレースも含め、新各reportのfull race hashを元116の対応reportへ厳密一致検証する。candidate joint係数とretained旧係数の来歴を分けて検証する。CIの移植ノイズは両側係数推定・選択不確実性を網羅しない。

成果はhistorical researchでcan_adopt=false、eligible_for_verdict=false。本番変更・年枠予約はしない。prepare/evaluateは親のみ実行する。
