# 151 実装計画

Python3.12 training/.venv、numpy/pandas/scipy/pytest。新規研究CLIのみ、新規booster学習0、DBアクセス0、本番変更0。既存dirty filesを保護し151の新規ファイルだけを追加。計算は1process・各1thread、24GiB以内。

1. inputs.pyで保存15seed・130全42cache/receipt・凍結係数・111snapshotを検証し、両regimeの補正後winを再構成。130既存NLLと一致を検証。両モデルの全馬完全一致で共通対象を作り、除外来歴を保存。
2. analysis.pyで結果を使わない購入関数と年次過去fit関数を実装。合成データで未来ラベル非依存・nested c=0同値・勾配・全頭正規化・field拒否・厳密閾値・100円/10頭上限・無購入母集団・比率再計算を確認。
3. 採点前にspec/plan/実装/テストと全入力SHA、比較・政策・bootstrap・判定・限界をprotocol.json/sha256へ保存。再実行は同一固定物だけ許可、既存結果を上書きしない。
4. 2022..2026の各年、同共通過去年でROI-onlyと追加2条件をfit。年別係数/fit期間/収束を保存。全馬予測・買い目・全レース損益・年度別・paired CI・感度・判定を出す。
5. 独立監査が別実装で入力母集団/混合予測/年次cutoff/選抜/会計/CIを確認。結果レビューに検証範囲と維持判断を記録。

設計second opinion: design_prediction_ablationが独立調査。共通2020以降fit、2年warmup、nested対照、preweight主/full感度、field集合一致、開催日paired、確定オッズの限界という指摘を採用。追加係数の正値やNLL単独をROIの証明にしない。
