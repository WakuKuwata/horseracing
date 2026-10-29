# 174 plan — 発走直前(T−3 分)の判断記録

2026-10-29。codex unavailable(400)→ セルフレビューを各節に併記。実装は本 plan の D1〜D12 に従う。

## 0. 変更範囲

| パッケージ | 変更 |
|---|---|
| training | `market_ev.build_features` から「race-level market structure」ブロックを純関数 `market_structure(df)` に抽出(呼び出し元は同関数を呼ぶ・出力バイト不変=パリティテスト)。T−3 で市場 12 列だけ再計算するため |
| db | migration 0022 `decision_quotes`(append-only トリガ・`UNIQUE(race_id)`)+ ORM `DecisionQuote`。features/live の head 断言を 0022 に更新 |
| live | `near_post.py`(窓・衝突割当・当日スケジュール・捕捉・予測・保存)+ CLI `near-post-capture`。依存に `horseracing-training`・`horseracing-eval` を明示(serving 経由で既に解決済み) |
| scripts | `source_operations_172.py` に任意キー `capture_only_dates`/`capture_only_approved_by`/`capture_only_approved_at`(3 つ同時に有無・日付は ISO・重複なし)。`collection_paused=true` は不変 |
| docs | `specs/174/manual.md`(前日 entries・当日捕捉・夜 results の手順と確認項目) |

API・front・ops・scrape のコードは変更しない(読み取りページは後段 spec)。

## 1. 設計決定

- **D1 配置**: 捕捉は `live/`(086 の礼儀・advisory lock・締切を同パッケージで流用)。予測は `horseracing_training.market_ev` の `predict_ensemble`/`predict`(本番と同じ booster 選択・`race_ok` 規則)と `horseracing_eval.market_mix`。live は serving→training を既に持つので新しい依存方向は生まれない。
- **D2 表**: `decision_quotes`(1 レース 1 行・`UNIQUE(race_id)`・UPDATE/DELETE/TRUNCATE を拒否するトリガ)。void 行は作らない。捕捉後 3 分以内の出走取消は、読み取り時に `field_digest` と現在の started 集合を比べて `field_changed_after_capture` として件数化する(138 D11 と同型)。
- **D3 窓と衝突**: `target = post_time − 180 s`。同じ post_time のレースは race_id 昇順で 2 本目以降を `−240 s`・`−300 s` に割り当て(`capture_target` に記録)。実際の捕捉は `[target − 60 s, target + 60 s]` の中でだけ行い、外れたら `skipped(window_missed)`。発走後・結果確定は捕捉しない。
- **D4 特徴**: 当日朝(プロセス起動時)に `load_rows(through=date)`→`build_features` を 1 回だけ回し、対象日のレース行を保持(`features_sha256` を canonical CSV で取る)。T−3 では該当レースの行の `odds`/`popularity` を quote で置き換え `market_structure` だけ再計算する。履歴側の as-of 列は odds に依存しないので朝の値のまま(140 `build_judged_features` と同じ前提)。朝の構築後に出走表が変わったレース(started 集合が特徴行と不一致)は `skipped(field_mismatch)`。
- **D5 予測と保存値**: 馬ごとに `win_low/win_high/place_low/place_high/popularity`(quote)、`p_ens/ev_ens`(15 seed)、`p_single/ev_single`(`mev-binary-v2`・S5 用)、`p_mix/ev_mix`(141・係数が無い年は null と `mix_status`)。行には `captured_at`・`official_at`(payload)・`seconds_to_post`・`capture_target`・`post_time`・`n`・`quote_digest`・`field_digest`・`features_sha256`・`ensemble_manifest`/`sha256`・`single_booster`/`sha256`・各 version・`logic_version`・`policy_version`・`run_id`。精算値は保存しない。
- **D6 プロセス**: `live near-post-capture --date D [--dry-run] [--policy PATH] [--database-url]`。起動時に (a) policy の `capture_only_dates` に D が含まれる (b) D が今日(JST) (c) robots cache が有効(失効なら拒否・171 の手順を案内)を満たさなければ終了コード 2。対象=D の `post_time` が既知で結果未確定・平地のレース。各レースは独立トランザクション。`FetchRefused` は当日のプロセスを停止し残りを `skipped(source_refused)`。最終レース後に要約 JSON を出して終了(翌日に持ち越さない)。
- **D7 dry-run**: fetch も保存もせず、各レースの target・窓・適格判定だけを出す。試運転 1 日目に使う。
- **D8 締切**: 1 レース 18 秒(086 と同じ・`make_capture_fetcher(deadline=…)`)。fetch は DB 背後の共有リミッタ(60 秒)経由。予測は朝にロード済みの booster で ms 単位。
- **D9 テスト**: training=`market_structure` のパリティ(合成 500 行で `build_features` 前後バイト一致)・db=列/UNIQUE/トリガ(testcontainers)・live 単体=窓・衝突割当・policy/日付ゲート・JSON 形・mix 無し年・fake fetcher/fake predictor での捕捉→保存・`window_missed`/`field_mismatch`/`source_refused`・再捕捉拒否・dry-run が fetch 0・scripts=policy 任意キーの検証。
- **D10 読み取り**: 本 spec では作らない。後段で `GET /api/v1/decision-quotes/...` と B8 と同型のページ。
- **D11 憲法**: V は 1.0.1 の注記で読みを固定。II=保存値は特徴に流入しない(leak-guard テスト: `features` が `decision_quotes` を読まない)。III=測るものだけ事前登録(spec FR-010)。
- **D12 運用**: capture-only の日は、前日に `scrape list-races --date D` と各レースの `scrape-entries`、当日に本 CLI、夜に `scrape-results`(公式払戻)を人が順に実行する。worker と ops の更新 POST は止めたまま。

## 2. 受入(前向き・毎開催日に要約 JSON に残す)

窓内捕捉率(≥95%)・`official_at − captured_at` の中央値(≤1 分)・再捕捉 0・`features_sha256` と model sha で行の再現が可能。

## 3. セルフレビュー項目(実装後に result-review に記録)

集合の一致(started 集合=quote 集合)/市場 12 列の再計算が `build_features` と同一関数/締切と礼儀が 086 と同じ経路/policy と日付のゲートが fail-closed/dry-run が外部 req 0/append-only が DB で強制/features の leak-guard。
