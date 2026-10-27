# Implementation plan: 142

Python 3.12、training/.venv、pandas/numpy/scipy/LightGBM/pytest。ローカル研究CLI。DB・本番書込みなし。24GiB、最大2 worker・各1thread。共有作業領域の既存変更を保護し、新規142ファイルだけを編集する。

## Components

- scripts/roi_explore/roi_improvement_142/policies.py: ラベル非参照の購入配分・精算・同期bootstrap。
- scripts/roi_explore/roi_improvement_142/models.py: 過去限定カテゴリ変換、探索、earlystop/refit、完全group softmax、保存本体。
- scripts/roi_explore/roi_improvement_142/run.py: 入力SHA/凍結protocol・現行OOFと年次混合・公式払戻部分集合・研究レポート。
- scripts/tests/test_roi_improvement_{policies,models,run}_142.py。
- artifacts/142-roi-policy-model-improvement/: 入力来歴、protocol、モデル、予測、集計。追記出力。

## Evaluation

購入比較は2019〜2026-06-26主、2013〜2018副、年別を全表示。入力自体に発走前と確定オッズが混ざる既知の期間2026-06-27以後は採点しない。2016〜2018はHPOの探索にのみ使用し、外側の結果を選択へ戻さない。過去の外側も別研究で参照済みのため完全未使用とは呼ばない。新収集を前提にしない。

主評価で高EV政策の利益改善が見えても、その政策の発走前実行可能性や将来利益を証明しない。公式払戻部分集合と保存オッズ精算を分ける。候補モデルの市場混合係数は新たにfitしないため、新モデルの政策比較はraw EV定義で揃え、141専用係数を流用しない。

## Review

独立agent: 141完成済み、既存固定100円比較では投資額が異なる、共通日bootstrap/現金保持、現行mix係数の新モデル移送禁止を指摘。すべて本設計へ反映。既存133/140/141のprotocolと成果物を維持。

凍結前の実装監査で、(1)不完全レースを部分的に残さないこと、(2)実行前後のinput/source/protocol再検証、(3)途中再開のidentity照合と新attemptログ、(4)selector/screenを含む費用集計、(5)paired/summaryの極小確率NLLを同じclipへ統一、を反映した。対象40テスト成功後にprotocolを凍結。実行後の全期間数値・516receipt・1,548artifact SHA・代表保存本体再生まで独立監査した。

## Result

2026-10-19完了。policy4案＋人気参照、固定6候補screen、15seed×8年×3armの比較を実行。新モデル2案の改善は確認できず採用しない。交差購入条件の点推定は良いが、差の補正区間が0を含むため研究候補に留める。詳細はresult-review.md。新規収集の保留と本番条件を維持。
