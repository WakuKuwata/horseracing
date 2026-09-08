# 118: 相対能力13列を残したanchor＋出走間隔補正

138列anchorにgap-log確率補正を加え、seed42/43/44で既存anchorと既保持pruning125列＋gapの両方に対応比較する。117で見た2026の相対列削除の悪化を踏まえた、新たな歴史研究である。

## 固定範囲

- 111の同一snapshot、全23,030評価レース・22,990eligible・715日、2020-01-01..2026-08-23。2019は係数warmupのみ。
- seed42のanchor2019..2026は113の原110cache読み取りreplay。43/44のanchor2020..2026は116の原cache/recipe/identityを保持する。
- 不足するanchor2019のseed43/44だけを118独立cacheへ追加fitする。各900trees・8OOF・maskseed20260810・同じ全過去年train集合・138列を維持し、2worker各1threadを上限にする。
- candidate gammaは各seedのanchor予測から厳密に前年までのデータだけで再推定する。g115.fit_coefficients、ridge/max_iter/tol/数値診断・eps0・Harville top2/top3を保持する。
- retained pruning＋gapの元115/116gammaは再推定しない。candidateとretainedのbase recipe・係数SHAは別々に保存・検証する。
- 比較ID `anchor` はcandidate対同seed無補正anchor。`retained` はcandidate対同seed既保持pruning＋gap。
- 新6reportのeligible ID/day/orderが完全一致、candidate損失が両比較で完全一致、anchor baseline損失が元116 anchor.active、retained baseline損失が元116 anchor.candidateと全レースで完全一致することを確認する。
- 旧retained予測は旧cacheと凍結係数・同じTiltFactoryから再現する。win/top2/top3の対応replayと確率整合性を確認する。元成果物に全馬確率が保存されていない場合、過去アーカイブ確率との直接比較とは呼ばない。

## 判定と証拠

delta0/B4000/alpha.0125/bootstrapseed20260907/sd_fold.001816/k_seeds1と品質guardは116と同じ。各seedのCIは3seed平均のCIではない。retained比較は両側がgamma推定を含み、移植ノイズ・係数固定bootstrapが両gammaの再推定不確実性や選択効果を網羅するとは主張しない。

集計はcandidate_retentionとpreferred_research_configurationを分離する。anchor比の等seed平均が改善し品質を満たせば代替候補として保持し、retained比でも改善すれば研究優先候補にする。いずれか比較のseed品質BLOCKEDまたは平均品質逸脱はREVIEW_REQUIRED。seed欠損・不正数値・来歴不一致は集計停止。全seed改善・多数決・平均CIは要求しない。seed42の残余リスクは平均で解消しない。

116summary/各report/evidence/coef/receiptと、117freeze/診断JSON/固定race属性/特徴量監査/reviewのSHAを固定する。118集計scriptのSHAもsource hashに含め、結果前に凍結する。smokeは43/44anchorの2構成、2008年1月、5trees/2OOF/gamma0の配線検証で効力判定をしない。

すべて歴史研究でcan_adopt=false、eligible_for_verdict=false。旧110〜117、package、モデル、本番、年枠は変更しない。prepare/train/evaluateは親のみ実行する。
