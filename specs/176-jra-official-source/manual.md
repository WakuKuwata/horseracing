# 176 JRA 公式サイト経路の運用手順

netkeiba と並走する第 2 の情報源。手動実行のみ(FR-022)。入口は `scripts/jra_ops.sh <command> <date>`。
wrapper は `config/source-operations.json` の `jra_official_interval_seconds` を `HORSERACING_JRA_MIN_INTERVAL_S`
に写してから CLI を呼ぶ。**wrapper は便利のためで安全境界ではない**: CLI 自身が policy・env・共有リミッタ・DB を
検査し、どれかが欠ければ 1 件も送らずに exit 2 で止まる。

## 0. 導入(1 回だけ・開催日の最終レース後)

順序が大事(140 の教訓=ops の subprocess は再起動なしで新コードを読む)。

1. **migration を先に**: `cd db && uv run alembic upgrade head`(0023 と 0024 をまとめて)。新コードを
   0022 の DB で動かすと netkeiba の取り込みが全部失敗し、API の `/health` は DB が同梱の最新版でないと
   503 を返すため(2026-11-02 の再生確認・`result-review.md`)。0024 は空の表を 1 つ作るだけ。旧コードは
   0024 の DB でもそのまま動く。適用前後の `alembic_current`・既存表の行数を記録する。
2. SC-008 の基準を記録: 直近開催日 24 レースの active 予測と `artifacts/features.parquet` の fingerprint。
3. 共通コード(Phase 4 = 両経路の規則・来歴・結果の挿入方式)を稼働 checkout に反映 → `scripts/stack.sh` で
   api / ops-api / worker を再起動。
4. **1 開催日は読むだけ**(R2): netkeiba の通常運用のまま、`ingestion_jobs.summary` の
   `quarantined_collision` / `status_rejected` / `odds_partial_field` / `odds_conflicts` を集計する。
5. reader が稼働 wrapper に入ってから policy に `"jra_official_interval_seconds": 10` を足す(キーが無い間は
   JRA CLI は起動しない=安全側)。
6. 馬場(`jra-baba`)の取得は最後(R7)。表は手順 1 で作成済み。

ロールバック: JRA の取得を止める(policy のキーを外す)だけ。スキーマは残す。0023/0024 の downgrade は
`jra_official` の行が 1 行でもあれば中断する。

## 1. 開催日の流れ

| いつ | コマンド | 書く先 |
|---|---|---|
| 前日 10 時過ぎ(枠順確定後) | netkeiba の `entries` → `jra_ops.sh entries <day>` | races / race_horses / マスタ / 来歴 |
| 当日朝 | `jra_ops.sh fetch <day> --save-dir artifacts/jra_raw/<day>` → `jra_ops.sh audit <day> --from-dir artifacts/jra_raw/<day>` | なし(照合結果は標準出力) |
| 当日・必要なレースだけ | `jra_ops.sh odds <day> --race-id <RID> ...` | 単複オッズ・人気(オッズ順)・来歴 |
| 夜(全レース確定後) | `jra_ops.sh results <day>` | race_results / laps / 体重 / 単勝払戻 / 6 券種払戻 |
| 夜 | `jra_ops.sh baba <day>`(0024 以降) | jra_track_conditions |

- `entries` は出馬表と変更情報(取消・発走時刻変更・騎手変更)を同じ実行で取り、job は `jra_entries` と
  `jra_changes` の 2 本(取得件数は `jra_entries` 側に計上)。
- `results` は結果ページ 1 枚から結果と払戻を書き、job は `jra_results` と `jra_payouts` の 2 本。
  `payouts` は結果ページに払戻が無い日の補完。
- `--race-id` は書くブロックを絞るだけで、開催日ページは丸ごと取る(`odds` だけはレース単位で取る)。
- 1 日分(2 場・オッズ除く)の送信は robots 1 件込みで 15 件前後、全 24 レースのオッズ込みで 42 件前後
  (上限 20 / 50・`scrape/tests/integration/test_jra_request_budget.py`)。

## 2. 止まる・譲る

- **捕捉時間帯**(174 の `capture_only_dates` の日・各レースの判断時刻の前後): JRA 経路は送信ごとに確認し、
  時間帯に入ったら送らずに止まる(`stopped_for_capture_window=1`・exit 1)。時間帯が過ぎてから同じコマンドを
  再実行する(書込みは冪等)。
- **拒否**(403 / 429 / 503): 共有の cooldown を記録してその場で停止(exit 2)。cooldown が明けるまで
  netkeiba も JRA も送らない。ヘッダを変えて回避しない。
- **パラメータエラー**(400・200 のエラー画面・`error013` へのリダイレクト): 索引から 1 回だけ辿り直す
  (失敗した cname の再送はしない)。2 回目も失敗したら exit 2。
- **`unsupported_date`**: 索引にその日が無い(開催なし・公開前・公開終了)。job は SKIPPED・exit 0。

## 3. 読み方

- exit 0 = 全 job SUCCEEDED / SKIPPED、1 = PARTIAL(隔離・不一致・解析失敗・捕捉時間帯停止)、2 = FAILED。
- `--json` の `quarantined` は書かなかったレース(未知 ID・衝突・取消馬の馬番が復元できない)。
  衝突の典型は `nk:` サロゲート行が同じ馬番を持っている場合で、`merge_candidates` に対が出る
  (統合は 067 系の別 spec。この経路は書き換えない)。
- `mismatches` は報告のみ(静的項目の別値・マスタの同 ID 別名・結果の不一致)。
- 結果ページの単勝人気は保存しない(利用者決定 2026-11-02)。`popularity_mismatch` は件数だけ。
- `--dry-run` = 取得と解析まで。業務表・job・来歴は書かない(共有スロットの予約行だけ動く)。

## 4. 書かないもの

- `race_date < 2025-10-11`(JRA-VAN 期)のレースは公式経路から一切書かない(`skipped_pre_netkeiba_era`)。
- 単複オッズページの raw は archive にも `fetch` にも残さない(オッズ履歴を作らない・憲法 V)。
- 枠連は解析するが保存先が無い(`not_mapped_bet_types`)。
- 票数は公式サイトにも無い。
