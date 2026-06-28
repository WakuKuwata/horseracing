# Quickstart: ペース/時計シグナルの特徴量化 (023)

実データ（horseracing DB, [[local-db-setup]]、2007–2024 ingest 済・馬番修正済 c8cd98b）で各 US の受入を確認する検証ガイド。実装詳細は tasks.md。

## 前提
- `DATABASE_URL=postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing`
- db head 不変（スキーマ変更なし）。loader 拡張で finish_time/finish_time_diff/corner_orders/running_style を読む。

## US1: リーク安全な as-of 特徴
1. features の単体/統合テストで:
   - cutoff: 対象レース当日以降のデータを変更 → 各 pace_time 特徴が不変。
   - leak: 今走の last_3f/finish_time/finish_time_diff/corner_orders/running_style、**同走馬の今走値**、**同日他レース**、**未来年の時計基準** を変更 → 各特徴が不変。
   - Unknown: 新馬（過去走なし）の特徴は null（0 でない）、中止走は集計から除外。
2. 期待: SC-001/SC-002。

## US2: 条件正規化
1. 距離・馬場の異なる過去 2 走で相対的に同等の上がり → 正規化後の特徴差が生秒差より小さい（条件差吸収）。
2. 正規化基準が過去レースのみから作られる（同走馬今走値・今走結果を含まない）。
3. 期待: SC-003。

## US3: 採用判定 + 市場超過診断（実データ、~17 分/コマンド）
1. `training feature-eval`（候補=features-006 / baseline=features-005）→ AdoptionReport:
   - PRIMARY=平均 win LogLoss 改善 かつ ECE 非悪化、**strict majority** fold、worst-fold LogLoss 上限内、条件別（距離帯/芝ダ/going/年/q帯）差分。
   - baseline 未超過なら adopted=false（false positive なし）。
2. `training feature-ablation` → pace_time / position_style group 寄与（diagnostic、採否に使わない）。
3. `training feature-diagnostic` → market_edge（p−q gap・edge bucket 実現勝率）。**「絶対改善≠市場超過」を確認**（市場織り込み済みで超過ゼロでも想定内）。
4. 期待: SC-004/SC-005。

## 横断ゲート
- leak-guard test 緑（今走/同走馬/同日/未来基準）。
- スキーマ変更 0（db head 不変、`__tablename__` 追加なし）、feature_version=features-006（SC-006）。
- lint/test: `uv run ruff check` + `uv run pytest`（features/eval/training）緑。
- 採否は OOS win 改善が gate。市場超過は努力目標（届かなくても絶対品質向上として評価、届かなければ次候補=条件替わり等へ）。
