# 157 実装・検証計画

training/.venvの既存pandas/numpy/scipy/LightGBM、1process・各1thread。新規157ファイルのみ追加し、既存dirtyコードと旧研究物を保護する。

1. export.pyで既存DBの必要列を同一READ ONLY/REPEATABLE READ transactionで抽出。設定・SQL・取得時刻・全parquet SHAを保存。外部データは取得しない。
2. data.pyで155保存元HTMLのSHA・馬ID/番号・結果・払戻を再照合しverified_rowsを作る。検証対象全頭の価格/人気を既存rawへ反映して履歴特徴を再構築。
3. legacy.pyで135owned/133capture/新snapshotの保存旧予測の全頭・bundle・時点を検証し、固定source優先順位で選ぶ。coverage/exclusionと重複差を保存。
4. 純関数・field不備・同着/元返し・欠落拒否・結果非依存の政策/特徴・paired日bootstrapの合成テスト。source/仕様/テスト/全入力と再構築dataを採点前にprotocol SHAへ固定。
5. 保存2026booster15本から確定価格でMEV再推論。固定151係数で同じ全頭・公式払戻を用いて2腕比較。全レース・買い目・数値/区間・条件を保存。
6. 独立担当が新しい財務集計実装をimportせず、全raceの正規化・選抜・公式会計・集計・日paired区間を再計算し、結果レビューに限界と維持判断を記録する。
