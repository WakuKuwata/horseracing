# CLI and artifact contract

help、明示command/output、9/6 cutoff。最新自動検出なし。期間外/日付不明/SHA不一致/予測部分欠損を拒否。
固定inputとfreezeのSHAを予測/本体/summaryへ結合。書込はexclusive、失敗・部分成果物も保全。
Step1はbooster fit禁止。Step2は固定6job。Step3候補seed42+1/7固定、8/23まで同学習条件。
full/preweight同fit。primary preweight。head sum[1,2,3]許容atol1e-8、範囲順序有限を検査。
complete_results started-all採点、勝率NLLは単独勝者。topk guard margin.0005、ECE margin.001/ceiling.05。未計測guardをPASSにしない。
旧126raw・132seed42再現と新本体の保存前後parityを必須にする。
