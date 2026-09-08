# 124 追加残差補正の全品質確認結果

prior_gap と global_temperature の両候補を研究候補として保持した。元125＋gapへの平均winner NLL差は、それぞれ −0.000452316、−0.000074359。追加boosterも係数再fitも0で、121で保存した係数をそのまま使った。

全12比較（2候補×3seed×anchor/retained）を2020-01-01〜2026-08-23、全23,030レース・NLL22,990レース・715日で完了。2019は元121係数学習のwarmupだけ。A/Bとも全seedのrecent3/5、top2/top3、ECEに硬い品質失敗はなかった。

| 候補 | 基準 | 平均NLL差 | 平均top2差 | 平均top3差 | 候補平均ECE | ECE差 | 研究状態 |
|---|---|---:|---:|---:|---:|---:|---|
| prior_gap | anchor | -0.002860868 | -0.000174107 | +0.000008487 | 0.001800344 | +0.000290756 | MEAN_IMPROVEMENT |
| prior_gap | retained | -0.000452316 | +0.000029276 | +0.000073467 | 0.001800344 | +0.000012145 | MEAN_IMPROVEMENT |
| global_temperature | anchor | -0.002482911 | -0.000109348 | +0.000221042 | 0.001483203 | -0.000026386 | MEAN_IMPROVEMENT |
| global_temperature | retained | -0.000074359 | +0.000094035 | +0.000286022 | 0.001483203 | -0.000304996 | MEAN_IMPROVEMENT |

retained比ではtop2/top3がわずかに悪化したが固定許容幅内。温度補正の全期間NLL上積みは特に小さい。両候補を同時に足した効果や季節補正との累積効果は本124では測っていない。結果の優先順位は選ばず、両候補を保持する。

| 候補 | 基準 | seed | NLL差 | 個別研究状態 | total CI下限 | total CI上限 |
|---|---|---:|---:|---|---:|---:|
| prior_gap | anchor | 42 | -0.004068631 | RETAIN_SUPPORTED | -0.007622591 | -0.000583729 |
| prior_gap | anchor | 43 | -0.002146724 | RETAIN_UNCERTAIN | -0.005510305 | +0.001201207 |
| prior_gap | anchor | 44 | -0.002367251 | RETAIN_UNCERTAIN | -0.005888703 | +0.001146143 |
| prior_gap | retained | 42 | -0.000451607 | RETAIN_UNCERTAIN | -0.002264204 | +0.001365578 |
| prior_gap | retained | 43 | -0.000438440 | RETAIN_UNCERTAIN | -0.002253527 | +0.001378807 |
| prior_gap | retained | 44 | -0.000466900 | RETAIN_UNCERTAIN | -0.002286235 | +0.001355186 |
| global_temperature | anchor | 42 | -0.003672265 | RETAIN_SUPPORTED | -0.007141736 | -0.000138233 |
| global_temperature | anchor | 43 | -0.001785266 | RETAIN_UNCERTAIN | -0.005198417 | +0.001580895 |
| global_temperature | anchor | 44 | -0.001991202 | RETAIN_UNCERTAIN | -0.005438422 | +0.001452825 |
| global_temperature | retained | 42 | -0.000055242 | RETAIN_UNCERTAIN | -0.001784684 | +0.001674270 |
| global_temperature | retained | 43 | -0.000076982 | RETAIN_UNCERTAIN | -0.001806708 | +0.001653003 |
| global_temperature | retained | 44 | -0.000090852 | RETAIN_UNCERTAIN | -0.001819649 | +0.001638760 |

retained比の全6比較はRETAIN_UNCERTAIN。CIが0を跨ぐことは固定した小差研究基準では単独の拒否条件ではない。平均は各seedの損失・品質の等重み平均で、確率ensembleの評価ではない。平均CIは作っていない。

anchor比のcanonicalは全seed PASS、nkはseed43 PASS・42/44 NO_DECISION、recent_year_onlyは全seed INCONCLUSIVE_LOW_PRECISION。明示FAILはないが、anchorに対する直近年改善を確立した結果ではない。retained比では両候補のcanonical/nk/recent_year_onlyが全seed PASSだった。

固定117診断群の平均NLL差を以下に示す。結果後の新しい足切りや群別モデル条件には使用しない。

| 候補 | 基準 | 2026全体（2,296/70日） | 中山（300/25日） | 相対特徴一部欠損（1,538/70日） |
|---|---|---:|---:|---:|
| prior_gap | anchor | +0.003870469 | +0.037832694 | +0.010851067 |
| prior_gap | retained | -0.000023570 | -0.000384365 | +0.000252375 |
| global_temperature | anchor | +0.004684993 | +0.039838970 | +0.011648596 |
| global_temperature | retained | +0.000790954 | +0.001621911 | +0.001049904 |

2026はanchor比で両候補とも悪化。retained比ではprior_gapが全体・中山で小改善する一方、相対特徴一部欠損群では悪化。global_temperatureは3群すべて悪化した。全期間で保持されたことと、最新年・既知の弱い群が解消したことは区別する。

独立監査は保存raw cacheと保存係数から全headをscalar式で再構成し、NLL/top2/top3/ECE/recent3/5、主sample/total CI、平均と保持判定を確認した。全3seed PASS、最大scalar差8.881784197001252e-16。元121全eligible順序/NLL、元116各基準NLL、開始/終了のsource/report/summary SHAも一致。23境界・連携テストは実装担当と独立担当の両方で通過。

保存された12比較timerの合計は 391.645秒（6.527分）。prepare・最終source検証・書込み・summary・独立監査等は含まれず、研究全体の壁時間ではない。新booster0・係数再fit0。範囲と各比較の値はevidence/actual-cost.jsonに保存した。

履歴上の選択に使った期間の研究であり、can_adopt:false / eligible_for_verdict:false。各CIのsd_fold.001816/k1は移送仮定、bootstrapは補正係数固定条件付きで、係数再推定・選択・学習の全不確実性を実証したものではない。本番変更や確認枠予約は行っていない。
