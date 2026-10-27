# Research command and artifact contract

help、明示入力/出力、固定cutoffを持つ。日付不正/範囲超過/SHA不一致/部分cacheを拒否。最新DBや再学習への暗黙fallbackなし。
experiment.jsonを採点前に固定しsource/input SHAを記録。既存結果上書きなし。
Topk3案・2regime、Colsample4案full-only、Style2024/2026は固定。
研究のtop2/top3非劣性目安は損失差<=.0005。ECEは既存small-gain基準か記述診断として明示。未実装guardをPASSとしない。
構造smokeと本比較を分離し、smoke精度で候補選択しない。
