# 158 実装・検証計画

training/.venv、既存numpy/pandas。新規158ファイルのみ追加。モデル学習/再推論0、外部収集0、DB書込0。

1. export.pyで候補712の出馬ID/結果/公式額、保存MEV、旧run/prediction/feature時刻、chaos取得/保存時刻を同一read-only snapshotへ抽出。当該raceのrace_horses.oddsをSELECTしない。
2. inputs.pyで結果と元HTML払戻の整合・MEV quoteと計算時点・旧予測の判断時刻以前の全頭/世代/入力条件を検証。判断表と精算表を別出力にし、候補の全除外理由を保存。
3. run.pyは157の固定2腕/政策/会計/paired日bootstrapを使い、選抜まで結果表を渡さない。可否・対象範囲・時間差・数値を保存し、少数日の精度限界を残す。
4. 合成テストで未来予測・proxy・欠落・重複の拒否、旧run選択時点、確定価格/結果に対する判断不変性を確認。採点前にspec/plan/source/test/入力をSHA固定。
5. 別担当が主実装をimportせず、候補条件・選択run・確率・全買い目・公式会計・区間を再計算する。可否と有用性を分けて結果レビューにまとめる。
