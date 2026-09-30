# Implementation Plan: 137 期待回収率(市場連動モデル)の表示

**決定(ユーザー 2026-09-30)**: 選択肢 2 = **2007 年以降のデータのみで学習したモデル(mev-binary-v2)を使い、注記で「120% 超の馬の過去の回収率は約 100%」と正直に開示する**。憲法 I の例外は作らない。

## 0. 契約(全パッケージ共通・名前はこのとおりに実装する)

### 0.1 モデル artifact(作成済み)
- ディレクトリ: `/Users/kuwatawaku/workspace/horseracing/artifacts/market_ev/mev-binary-v2/`(gitignore・絶対パス)
- 中身: `model.spec.json`(objective=binary / features / cats / cat_maps)と `model_2019.txt` … `model_2026.txt`。**y 年のレースは `model_{y}.txt`(y−1 年までで学習)**。無ければ y より前で最も新しい年にフォールバックし、使ったファイル名を行に記録。
- model_version = ディレクトリ名 `mev-binary-v2`。
- 学習: `scripts/roi_explore/direct_return_model.py --rows artifacts/market_ev/rows_2007.parquet --start-year 2010 --objective binary --drop-groups sameday,weightlive --save-last-model …/mev-binary-v2/model`。

### 0.2 DB: migration `0018_market_ev_predictions`(down_revision `0017_purchase_records`)
テーブル `market_ev_predictions`(最新のみ・レース単位で置き換え・履歴なし=憲法 V):

| 列 | 型 | 制約 |
|---|---|---|
| race_id | TEXT | NOT NULL, FK races.race_id |
| horse_id | TEXT | NOT NULL(horses への FK なし:再生成可能な派生値なので 067 re-key の対象外) |
| model_version | TEXT | NOT NULL(model_versions への FK なし:本番の単一 active 前提に入れない) |
| horse_number | INTEGER | NULL |
| win_prob | NUMERIC | NOT NULL, CHECK (win_prob > 0 AND win_prob < 1) |
| odds_used | NUMERIC | NOT NULL, CHECK (odds_used >= 1.0) |
| expected_return | NUMERIC | NOT NULL, CHECK (expected_return >= 0)(= win_prob × odds_used を書き込み時に保存) |
| odds_observed_at | TIMESTAMPTZ | NOT NULL(計算時の race_horses.updated_at) |
| result_pending_at_compute | BOOLEAN | NOT NULL(計算時点で結果未確定なら true) |
| booster | TEXT | NOT NULL(例 `model_2026.txt`) |
| booster_sha256 | TEXT | NOT NULL |
| logic_version | TEXT | NOT NULL |
| run_id | UUID | NOT NULL(1 回の計算で同じ値) |
| computed_at | TIMESTAMPTZ | NOT NULL DEFAULT now() |

PK (race_id, model_version, horse_id)。INDEX `ix_market_ev_predictions_model_version_computed_at` (model_version, computed_at)。ORM クラス `MarketEvPrediction`(`db/src/horseracing_db/models/market_ev.py`・`models/__init__.py` で export)。

### 0.3 計算ジョブ(training)
- モジュール `horseracing_training.market_ev`(既存・未コミット): `load_rows(since="2007-01-01")` → `build_features` → `predict` に加え、`compute_and_persist(session, *, race_date_from, race_date_to, model_dir, model_version=None) -> dict` を追加。対象日範囲の `race_ok` レースを予測し、**レースごとに `DELETE WHERE race_id=:r AND model_version=:mv` → INSERT** を 1 トランザクションで行う。`HIGHLIGHT_THRESHOLD` 定数は削除(閾値の正本は API)。
- `LOGIC_VERSION = "mev-v1;features=roi-explore-2026-09;drop=sameday,weightlive;data>=2007"`。
- CLI: `python -m horseracing_training market-ev (--date D | --race-id R | --from D1 --to D2) --model-dir ABS [--model-version V] [--pending-only] [--database-url URL]`。`--pending-only` は結果未確定のレースだけを書き換える(確定済みレースは発走前オッズで計算した値を保持。全レース確定済みなら `SKIPPED: no_pending_races`)。同じ日付への書き込みは (model_version, 日付) の advisory lock で直列化。`--race-id R` はそのレースの開催日全体を計算。`--model-dir` は絶対パス必須・`/.claude/worktrees/` を含むパスは拒否。stdout 最終行 `OK: races=N horses=M from=D1 to=D2` または `SKIPPED: no_races_with_odds`。例外は非 0 終了。
- training/src では `recommendations` `kelly` `stake_fraction` `horseracing_betting` `model_calibration` `PCalibrator` `haircut` `p_prime` `apply_p_calibrator` `calibration_eval` を docstring 含め一切書かない(betting の leak-guard が training/src を走査する)。

### 0.4 API: `GET /api/v1/races/{race_id}/market-ev`
- 422 `invalid_race_id` / 404 `race_not_found` / それ以外は 200。`?model_version=` は取らない(勝率モデル選択と独立)。
- 閾値の正本: `api/src/horseracing_api/market_ev.py` の `MARKET_EV_THRESHOLD = 1.2`。`exceeds_threshold = expected_return > MARKET_EV_THRESHOLD`(厳密に超える)。
- 応答(discriminated union on `status`、全モデル `extra="forbid"`、中核数値は既定値なし):
```
HorseMarketEv { horse_id: str, horse_number: int|None, expected_return: float, odds_used: float, exceeds_threshold: bool }
MarketEvAvailable { status: "available", race_id: str, model_version: str, logic_version: str,
                    computed_at: datetime, odds_observed_at: datetime (行の最大),
                    odds_changed_after_compute: bool (現在の race_horses.odds が odds_used と 1 頭でも違う),
                    result_pending_at_compute: bool (全行 AND), threshold: float, is_pseudo: Literal[True],
                    horses: list[HorseMarketEv] (馬番昇順) }
MarketEvUnavailable { status: "unavailable", race_id: str, reason: "not_computed" | "field_changed" | "odds_unavailable", threshold: float }
MarketEvResponse = Annotated[MarketEvAvailable | MarketEvUnavailable, Field(discriminator="status")]
```
- 現在の started 馬の horse_id 集合と保存行の集合が違えば `unavailable(field_changed)`。行が無ければ `unavailable(not_computed)`。出走馬の現在の単勝オッズが欠けている(または 1.0 未満)なら `unavailable(odds_unavailable)`(計算ジョブはそのレースを計算しないので、残っている行は古い価格のもの)。**win_prob は返さない**(憲法 IV:正規化していない確率を 1着率として見せない)。
- 読み取り専用・ML import なし。no-write 境界テスト(文字列定数内の `insert/update/delete/create/drop/alter/truncate ` と `.add/.delete/.merge/.flush/.commit` 呼び出し)に抵触しない書き方。

### 0.5 front
- `useMarketEv(raceId)`(queryKey `["market-ev", raceId]`・retry false)を `RaceDetailPage` で呼び、`HorseEntriesTable` に prop `marketEv` で渡す。
- 列見出し「期待回収率」。モデル勝率の列がある場合はその直後、無い場合は単勝の直後。**並び替え不可**。`marketEv.status === "available"` のときだけ列を出す(勝率モデルの有無と独立)。`totalCols` を正しく増やす。
- 値は `PseudoValue kind="expected_return"`(ラベル「推定」・title「市場連動モデルの推定(勝率×単勝オッズ)。実績ではありません」)で `(v*100).toFixed(1)%`。取消馬・行が無い馬は「—」。
- `exceeds_threshold` の馬: 行クラス `entry--ev-over`(枠線・左の縦帯。緑/赤は使わない)+ セル内チップ「120%超」(`aria-label`/`title`「期待回収率が120%を超えています」)。`.good .bad .danger .success .profit .up .down` は使わない。表内に「買」の字を出さない。
- 注記コンポーネント `ExpectedReturnNote`(出走表の直前・常時表示)。available のとき:
  - 「期待回収率は、表の『モデル勝率』とは別の市場連動モデル({model_version})が、単勝オッズと各馬の過去成績から推定した勝率 × 単勝オッズです。」
  - 「オッズ取得 {odds_observed_at} ・計算 {computed_at}」。`odds_changed_after_compute` なら「計算後に単勝オッズが変わっています(表示は計算時のオッズ基準)」。`result_pending_at_compute=false` なら「結果確定後のオッズで計算した事後の参考値です」。
  - 過去検証(固定文言): 「過去検証(2010〜2026 年、各年を前年までのデータで学習): 期待回収率 120% 超の馬の単勝回収率は 100.8%(95% 区間 94〜108%)、2019 年以降は 99.5%。参考として全馬は 72%。120% を超えても利益は確認できていません。」
  - 直近の不確かさ(固定文言・レビュー指摘で追加): 「2025〜26 年は保存オッズに発走前の値が混ざるため検証の精度が落ちます。発走前オッズでの前向きの検証はまだ行っておらず、120%超の目印はその前に付けているものです。」
  - 「的中や利益を保証するものではありません。」
  - unavailable: not_computed →「このレースの期待回収率はまだ計算されていません」/ field_changed →「出走馬が変わったため再計算待ちです」/ odds_unavailable →「単勝オッズがそろっていないため表示していません」。エラーは中立表示。
- `forbiddenPhrases.ts` の `PROFIT_LANGUAGE` は変更しない。注記と列専用の `EXPECTED_RETURN_SCOPE = /妙味|危険|儲|edge|買うべき|勝てる|おすすめ|お得|利益が出/` を追加(回収率は許可)。
- `RefreshButton` 完了時に `["market-ev", raceId]` も invalidate。さらにレース更新ジョブの `followup_job_id`(=再計算ジョブ)を完了までポーリングし、終わったらもう一度 invalidate。MSW `happyHandlers` に既定ハンドラ(**unavailable/not_computed**。既存テストの % 文字列と衝突させない)。
- OpenAPI: `front/openapi.json` と `admin/openapi.json` をバイト一致で再生成、両方の `schema.d.ts` 再生成、`front/src/api/openapi.test.ts` のパス一覧に追加。

### 0.6 ops(自動再計算)
- job_type `expected_return`(CPU レーン)。1 ジョブ = 1 開催日。`ingestion_jobs.scope = "date"`・`scope_value = "YYYY-MM-DD"`。
- `enqueue_expected_return(session, race_date, *, source)`: advisory lock `expected_return:{date}`。**QUEUED のみ再利用**(RUNNING は古いオッズを読んでいる可能性があるので再利用しない)。
- トリガ: `run_one`(refresh_race)で、単勝オッズが 1 件以上書かれ(odds.written>0)、結果未確定で、corner_backfill 起点でなく、`CONFIG.expected_return_on_refresh`(env `OPS_EXPECTED_RETURN_ON_REFRESH`、既定 ON)のとき、そのレースの日付で enqueue。
- runner: `_training_market_ev(race_date)` = `uv run --project training python -m horseracing_training market-ev --date D --pending-only --model-dir {CONFIG.market_ev_model_dir} --database-url {owner url}`(cwd=training・VIRTUAL_ENV 除去・timeout 600 秒)。`CONFIG.market_ev_model_dir` は env `OPS_MARKET_EV_MODEL_DIR`、既定はリポジトリ直下 `artifacts/market_ev/mev-binary-v2` の絶対パス。rc≠0 → FAILED、`SKIPPED:` → SKIPPED、`OK:` → SUCCEEDED。
- worker: `_CLAIMABLE`・`_CPU_LANE`・dispatch・`_DETACHED_CHILD_GRACE_S`(≥ timeout+余裕)。
- ops の新 POST は作らない(ops-openapi 不変)。`GET /ops/v1/jobs/{id}` の既存フィールド `followup_job_id` に、refresh_race が積んだ再計算ジョブの id(summary の `expected_return_job_id`)も載せる。同時実行は expected_return 1 件まで(claim 側の上限)。admin の JobsPage 種別に `expected_return` を追加。
- 反映には `scripts/stack.sh restart worker` / `restart ops-api` / `restart api` が必要。

### 0.7 リーク境界
- `features/tests/unit/test_market_ev_leak_guard.py`: features/src と training の特徴量経路(dataset.py / predictor.py / target_encoding.py / win_model.py)と probability/src が `market_ev_predictions` `MarketEvPrediction` `horseracing_training.market_ev` を参照しない。
- migration head を固定している既存テスト(features 9 本・live 1 本)を 0018 に更新、db の `_TABLES_ADDED_AFTER_0012`(2 ファイル)に表名を追加。

## 1. 実装順
1. DB(0.2・0.7)
2. training(0.3)/ API(0.4)/ ops(0.6)を並列
3. front(0.5・API の OpenAPI 再生成後)
4. 全スイート実行 → ローカル DB へ migration → 9 月分を埋め戻し → api/worker/ops-api 再起動 → ブラウザで確認

## 2. 運用手順(quickstart)
1. `cd db && uv run alembic upgrade head`(ローカル DB)→ `scripts/stack.sh restart api`
2. 埋め戻し: `cd training && uv run python -m horseracing_training market-ev --from 2026-09-01 --to 2026-09-28 --model-dir /Users/kuwatawaku/workspace/horseracing/artifacts/market_ev/mev-binary-v2`
3. `scripts/stack.sh restart worker && scripts/stack.sh restart ops-api` → 以後はレース更新で自動再計算

## codex レビュー

**codex unavailable**(2026-09-30): 2 回試行。1 回目は結果が返らず、2 回目は Codex 側でモデル名が `horseracing_training` と解決され 400 で拒否された(ChatGPT アカウントで非対応のモデル名)。CLAUDE.md の再試行上限により、実装ワークフロー内の 2 系統レビュー(層間契約・表示規律/憲法)とセルフレビュー checklist で代替する。

### セルフレビュー checklist(高リスク変更の代替)
- [x] 閾値は API の 1 か所で厳密に `>`、単位は比(1.2)で表示時のみ %
- [x] `odds_changed_after_compute` は値比較(updated_at 比較にしない)・`field_changed` は started 集合の比較・`result_pending_at_compute` はレース単位・オッズ欠損は `odds_unavailable`
- [x] 憲法 II: 利用可能タイミング表を spec に記載・当日馬体重と同日先行レースは不使用・学習/提供のオッズ差を開示
- [x] 憲法 IV: win_prob を API/front に出さない(OpenAPI に出ないことをテストで固定)
- [x] 憲法 V: 行ごとに model_version / logic_version / booster / sha256 / odds_used / odds_observed_at / computed_at / run_id、履歴なし(レース単位置き換え)
- [x] ops: 1 日付 1 ジョブ・QUEUED のみ再利用・timeout 600 秒 < stale 900 秒・grace ≥ timeout・CPU レーン・確定済みレースは書き換えない(--pending-only)
- [x] 製品予測と研究予測のパリティ: 2025〜26 年 80,386 行中 80,384 行が 1e-9 未満で一致(差は同一馬の二重登録 1 レース)

## 実装レビュー(ワークフロー内 2 系統)の指摘と対応
| 指摘 | 重さ | 対応 |
|---|---|---|
| 日付単位の自動再計算が確定済みレースの発走前の値を上書きする | 中 | `--pending-only` を追加し ops から渡す。統合テストで確定済みレースの行が変わらないことを固定 |
| 注記に直近の不確かさ(2025〜26 のオッズ由来・前向き検証未実施)が無い | 中 | 固定文言を追加し、テストで文言を固定 |
| 同じ日付を手動と自動で同時計算すると一意制約で失敗しうる | 低 | (model_version, 日付) の advisory lock |
| 更新ボタンの再取得が再計算完了より先に走る | 低 | 再計算ジョブを followup_job_id で追ってから再取得 |
| 出走馬のオッズが後から欠けると古い値が出る | 低 | API で `odds_unavailable` |
| リーク検査が serving/betting/eval を見ていない | 低 | 走査範囲に追加 |
| 注記テストの色クラス検査に .up/.down が無い | 低 | 追加 |
