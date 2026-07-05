# Feature Specification: Materialized 特徴量の serving/training 結線 + 読み込み経路の単一ロード化

**Feature Branch**: `055-materialized-serving` / **Created**: 2026-07-05 / **Status**: Draft
**Input**: 予測(serving)と学習(training)の特徴量ビルドが毎回 2007 年からの全履歴を pandas で再計算しており(実測 59.2s・ピーク RSS 3.40GB、950,955 行×97 列、マシン 24GB)、予測 1 回に約 75 秒かかる。025 で構築済みの parquet materialization(bit-parity 検証済み・fail-closed staleness fingerprint)が serving/training に**未結線**のため使われていない。実測: materialized 読込(現行二重ロードのまま)は 22.3s/3.13GB — さらに読み込み経路の DB 二重ロード(特徴窓用 + fingerprint 検証用フルプール)を削減して ~13s/~2.5GB を狙う。

## 背景
- 025 は「単一 as-of 実装 + bit パリティ + fail-closed staleness」の読み込みインフラを完成させたが、read 経路は opt-in のまま serving(`run_serving`/`run_serving_backfill`)と training(`dataset`)のどちらにも結線されていない(025 deferred)。parquet は `features materialize` CLI で生成済み(133MB、2007-01-06..2026-07-05、74 列、features-012)。
- **二重ロードは偶然ではない**: fingerprint(`_hash_frame`)は `pd.util.hash_pandas_object` を使い **dtype 感受性がある**。materialize 時はフルプール `load_frames(None)` で計算しているため、検証時も同じフルプールロードを使うことでハッシュ一致を保証している。窓ロード(`end_date=`)は行集合・dtype がプール依存で変わりうる(025/026 で実際に static 列の int→float ドリフトが発生した前例)。単一ロード化には **fingerprint の値ベース dtype 安定化**(数値→float64 正準化・その他→str)が前提になる。
- serving backfill は日単位ループで毎日 build を繰り返す(044: range 一括 build は static dtype 懸念で回避・deferred)。fingerprint 検証は「同じ parquet × 同じソース状態」なら **run 1 回で十分**であり、日ごとに繰り返す必要はない。
- 憲法との関係: II(リーク境界不変 — 025 の as-of は per-row strictly-before でプール末尾非依存が実証済み)、III(bit パリティ非交渉 — materialized 経路 == in-memory 経路を `assert_frame_equal(check_exact=True, check_dtype=True)` で機械固定)、V(fail-closed — 古い parquet を黙って使わない)、VI(スキーマ変更なし・FEATURE_VERSION 不変 features-012)。

## User Stories
- **US1 (P1) serving/training への opt-in 結線**: serving `predict`/`predict-backfill` と training のデータセット構築(train-evaluate/model-eval 経路)に `--use-materialized` opt-in を追加し、`live refresh` は予測段へフラグを伝播する。既定は従来経路(後方互換・パリティ基準の維持)。効果: ビルド 1 回 59.2s→22.3s(2.7 倍)、backfill N 日は N×59s→N×22s。
- **US2 (P2) 読み込み経路の DB ロード削減**: (a) fingerprint を値ベースで dtype 安定化(数値列→float64 正準化・他→str 化してからハッシュ)し、ロード窓に依存しない一致保証に変える。(b) 単発 build はソースロード 1 回(fingerprint 検証と特徴窓を同一ロードから導出)、backfill は fingerprint 検証を **run 1 回**に削減(日ループでは再検証しない — 同一トランザクション/接続内でソースが不変である前提を明記)。目標: 単発 ~13s・ピーク ~2.5GB、backfill はフルプールロード 2N 回→O(1) 回。

## Requirements
- **FR-001 (bit パリティ非交渉)**: materialized 経路の特徴行列は in-memory 経路と完全一致(`assert_frame_equal(check_exact=True, check_dtype=True)`)。既存パリティテストを維持し、US2 のロード統合後も実 DB でパリティを再確認する。既存モデル lgbm-042 の予測 p はバイト一致(044 p-parity と同型)。
- **FR-002 (fail-closed 維持・自動フォールバック禁止)**: parquet 不在・staleness 不一致・feature_version 不一致は**型付きエラーで停止**し、メッセージで `features materialize` の再実行を案内する。in-memory への黙示フォールバックはしない(遅いだけでなく「opt-in したのに効いていない」を隠すため)。data_through より未来のレース行は 025 既存の has_future_rows fallback(単一レース計算)のまま。
- **FR-003 (既定は従来経路)**: すべての入口で `use_materialized` 既定 False。フラグ未指定の挙動・出力はバイト同等(serving/training の既存テスト無改修で緑)。
- **FR-004 (fingerprint の dtype 安定化)**: ハッシュ前に値を正準化(数値→float64、object/日付→str)し、「同じ値集合なら同じハッシュ」をロード窓によらず保証する。**既存 manifest は無効化される**(ハッシュ定義変更)ため、リリース後 1 回の `features materialize` 再実行を要求し、旧 manifest は fail-closed で検出される(黙って通らない)。
- **FR-005 (backfill の検証 1 回化)**: `run_serving_backfill(use_materialized=True)` は fingerprint 検証を run 開始時 1 回とし、日ループ内で再検証しない。予測値は日単位ビルドと bit 一致(as-of のプール末尾非依存は 025 のパリティで実証済み、static は各日の窓と同値になることをテストで固定)。
- **FR-006 (スコープ外)**: 新特徴・新ロジックなし。FEATURE_VERSION 不変(features-012)・スキーマ変更なし・API/openapi 不変・リーク境界不変(odds/結果を特徴に入れない既存 leak-guard 維持)。18-fold 再学習の方法論(fold 数・ゲート)には触れない。

## Success Criteria
- **SC-001**: 実 DB で materialized 経路のビルドが従来比 **2.5 倍以上高速**(ベースライン 59.2s → 目標 ~13-22s)かつピーク RSS が現行(3.40GB)以下。計測値を spec/summary に記録する。
- **SC-002**: 実 DB パリティ: `build_feature_matrix(use_materialized=True)` == in-memory が bit 一致(US2 のロード統合後に再確認)。lgbm-042 で同一レースの予測 p がバイト一致。
- **SC-003**: stale シナリオ(parquet 生成後にソース行を変更)で型付きエラー + 再 materialize 案内が出る(黙って古い値/黙ってフォールバックのどちらも起きない)。
- **SC-004**: `serving predict-backfill --use-materialized` が既 backfill 済み範囲の冪等通しで完走し、フルプールロード回数が run あたり O(1)(ログ/カウンタで確認)。features/serving/training/live スイート緑。

## Assumptions
- parquet の再生成タイミングはオペレータ責務: scrape/ingest 後に `features materialize` を 1 回実行(将来の `live refresh` 前段組込は Deferred)。生成コストは実測 ~63s、artifacts/ 配下・非コミット・DB から決定論再生成(憲法 V)。
- backfill 中にソース DB が変更されない(単一オペレータ・ローカル運用。変更された場合も次回 run の fingerprint 検証で fail-closed)。
- codex CLI は直近 4 feature で 3 回連続起動失敗のため見送り宣言 → single-opinion(025 の確立済みインフラの結線 + fingerprint 正準化のみ、新規設計面は小さい)。

## Deferred
`live refresh` への materialize 自動組込(scrape→materialize→refresh の連結)・in-memory への opt-in フォールバックフラグ・range 一括 build(044 deferred の解消)・増分 materialize(全再生成でなく差分追記)・state store 方式(馬/騎手/種牡馬の累積統計の増分更新 — 現状ピーク 3.4GB/24GB では過剰設計)・feature-eval/ablation CLI への結線
