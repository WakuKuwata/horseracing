# Feature Specification: 製品を実データで通す — 買い目(推奨)生成 (Recommendation Generation)

**Feature Branch**: `043-recommendation-generation`

**Created**: 2026-07-02

**Status**: Draft (codex second-opinion 反映済み)

**Input**: 「製品を実データで通す」— read-only 製品(021/040 表示)は完成したが `recommendations` テーブルが 0 行で、買い目・EV・Kelly が画面に一切出ない。生成ロジック(011 exotic EV / 016 Kelly)は betting に実装済みだが製品フロー(ops/front)に結線されていない。予測(028)と同型の経路で推奨を生成・永続化し、既存の read-only 表示に実データを流す。

## 背景と目的

製品目的は「人間が予測・確率・期待値とその信頼性を見て自分で判断する正直な意思決定支援」(021)。021/040 で予測 p・市場 q・校正・予測根拠の表示は完成したが、**`recommendations` テーブルが空**で RecommendationPanel は常に空。「機能は作ったが中身が空」という製品ギャップを埋める。

生成ロジック(011 exotic EV / 016 Kelly)は実装済み。欠けているのは製品フローからの起動と**読み出し側の正しさ**。

### codex second-opinion で判明した前提の是正(重要)

素朴に「生成ボタンを足す」だけでは**壊れた状態で出荷**される。実査で以下が判明:
1. **読み出しが prediction_run で絞られていない** — `api.queries.exotic_recommendations` は race_id + 券種で全行を返し、prediction_run でフィルタしない。append-only の再生成で重複行がそのまま画面に出る。
2. **`stake_fraction`(Kelly)が API schema/画面に露出していない** — Kelly を生成しても表示されない。
3. **exotic 生成と Kelly 生成が別々の Recommendation 行を作る** — 両方呼ぶと同一買い目が二重に見える。
4. betting CLI の `--race-id` は「最新 computed_at の run」を選び、**API の active-model→最新 run 選択則と一致しない**。

→ 本 feature は「**読み出しの正しさを先に直し**、単一の一貫した推奨セットを生成・永続化して表示する」。ops→betting は 028 と同型の subprocess(ML import 境界維持)。

## 制約(既存アーキテクチャ)

- **ops は ML を import してはいけない**(test_boundary)。028 predict は serving CLI を `uv run --project serving` の subprocess で実行。**betting も backtest 経由で serving を transitively import**するため、推奨起動も **betting CLI を subprocess 実行**(028 の cwd/VIRTUAL_ENV strip/timeout/exit-code マッピングを忠実に踏襲)。
- **read-only 境界(014)不変**: 書き込みは ops 経路のみ。014 は SELECT のみ(query/schema の**是正**は read-only のまま行う=新規書き込みエンドポイントは足さない)。
- **recommendations は append-only**(監査、憲法 V)。重複回避は「**選択 run に推奨が既にあれば生成をスキップ(冪等)**」+「**読み出しを選択 run で絞る**」で行い、スキーマ変更(batch id 等)はしない。
- **p≠q 分離・pseudo/推定オッズラベル・real vs estimated 区別**は betting/014/015 で既存。踏襲のみ。
- **単一の推奨セット**: exotic と Kelly を両方別行で作らず、**一貫した1セット**(EV 選定に Kelly stake を付与)を生成する。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 推奨が正しく表示される(読み出し是正 + 1レース生成) (Priority: P1)

予測(prediction_run)とオッズがあるレースに対し、単一の推奨セット(EV 選定 + Kelly stake)を生成・永続化すると、レース詳細の RecommendationPanel が **表示対象 run のみ**の推奨(券種・組合せ・推定 EV・pseudo-ROI・**Kelly stake**・real/推定バッジ)を**重複なく**表示する。

**Why this priority**: 本 feature の核。生成しても読み出しが壊れていれば意味がないため、**読み出し是正(run フィルタ・stake_fraction 露出・行 key)を含む**。まず CLI/手動で1レース生成し E2E を通す(codex MVP)。

**Independent Test**: 予測+オッズありレースで推奨を1回生成 → API/画面が表示対象 run の推奨のみを非空・重複なしで返し、Kelly stake と real/推定バッジが出る。2回生成しても画面は重複しない。

**Acceptance Scenarios**:

1. **Given** 予測+オッズがあり推奨を生成したレース, **When** レース詳細を表示, **Then** 表示対象 prediction_run に紐づく推奨のみが表示され、券種・EV・pseudo-ROI・Kelly stake・real/推定バッジが出る
2. **Given** 同一 run で2回生成(または複数 run 分の行が存在), **When** 表示, **Then** 表示対象 run の1セットのみが出て重複しない
3. **Given** Kelly stake を持つ推奨, **When** 表示, **Then** stake_fraction が画面に出る(以前は API 未露出で出なかった)
4. **Given** 推定オッズ(double-pseudo)の推奨, **When** 表示, **Then** pseudo が必ずバッジ表示される(015 継承)

---

### User Story 2 - レース詳細から推奨生成をオンデマンド起動 (Priority: P2)

ユーザーがレース詳細で「買い目生成」ボタン(028 予測ボタンと別)を押すと、そのレースの表示対象 prediction_run に対し推奨が生成・永続化され、完了後に RecommendationPanel が自動 refetch して表示される。

**Why this priority**: US1 の読み出し是正+生成ロジックが正しくなった上で、製品からワンクリック起動できるようにする増分。

**Independent Test**: 予測済みレースで「買い目生成」→ ジョブ完了 → recommendations が永続化され画面が更新。オッズ無/予測無は skipped(理由)で終了し予測表示は無影響。

**Acceptance Scenarios**:

1. **Given** 予測+オッズありレース, **When** 「買い目生成」を押す, **Then** 受付→実行→完了し、RecommendationPanel が実データ表示に更新される
2. **Given** オッズ無レース, **When** 押す, **Then** `skipped`(オッズ未取得)で正常終了(succeeded+0行の曖昧状態にしない)、予測表示は無影響
3. **Given** 予測無レース, **When** 押す, **Then** `skipped`(予測が先に必要)
4. **Given** 選択 run に推奨が既にある, **When** 押す, **Then** 冪等に skip(重複を作らない)
5. **Given** ジョブ状態遷移, **When** 変化, **Then** 受付/生成中/完了/一部/失敗/対象なし を1ラベルで表示(028 同型)

---

### User Story 3 - 既存予測済みレースへの一括生成(backfill) (Priority: P3)

運用者が、予測+オッズがある過去/直近レース群に一括で推奨を生成できる。冪等(既存 run はスキップ)で、生成/スキップ理由別件数を監査集計で返す。

**Why this priority**: 製品を実データで動く規模に広げる(既存 race_predictions 活用・netkeiba 不要)。

**Independent Test**: 日付範囲指定で一括生成 → 対象の recommendations が埋まり、生成+スキップ(オッズ無/予測無/既存)件数が集計され合計一致。1レース失敗で全体中断しない。

**Acceptance Scenarios**:

1. **Given** 予測+オッズありレース群, **When** 一括生成, **Then** 各レースに1セット永続化、件数=生成+スキップ(理由別)で集計一致
2. **Given** 既に推奨のある run, **When** 一括生成, **Then** 冪等スキップ(重複作らない)
3. **Given** 1レース失敗, **When** 一括処理中, **Then** エラー計上して残り継続

---

### Edge Cases

- **オッズ未取得 / 予測未生成**: 生成せず `skipped`(理由付き)。予測・根拠表示は無影響。
- **取消/除外馬**: canonical field(有効 p かつ有効オッズ)から除外(011 既存)。
- **real exotic odds 無し**: 推定 O_est(double-pseudo)にフォールバック+pseudo ラベル。real も無い券種はスキップ。
- **再生成の重複**: append-only 維持、選択 run に既存なら生成スキップ(冪等)+ 読み出しを選択 run で絞る。
- **exotic 成功 / Kelly 失敗の partial**: 明確な状態(partial + 理由)にする。
- **ops 境界**: 生成は ML 非依存の subprocess(betting CLI)経由。
- **prediction_run 選択の一致**: 生成も読み出しも API の select_prediction_run(active→最新)と同一 run を対象にする(betting CLI の --race-id 依存を避け、明示 run 指定)。
- **行 key**: 画面の推奨行は recommendation_id を key にする(重複行での React key 衝突回避)。

## Requirements *(mandatory)*

### 読み出し是正(US1、read-only のまま)

- **FR-001**: 推奨の読み出しは、レースの**表示対象 prediction_run**(014 の既存 deterministic 選択 select_prediction_run と同一)に紐づく推奨のみを返さなければならない。append-only の他 run/再生成行を混在させない。
- **FR-002**: 推奨レスポンスは `stake_fraction`(Kelly)と行識別子(recommendation_id)を露出しなければならない。front は Kelly stake を表示し、行 key に識別子を用いる。
- **FR-003**: 014 は read-only を維持する(query/schema の是正のみ、書き込みエンドポイントを追加しない)。全 path GET。

### 生成(US1/US2/US3)

- **FR-004**: システムは、表示対象 prediction_run に対し**単一の一貫した推奨セット**(EV 選定 + Kelly stake)を生成・永続化できなければならない。exotic と Kelly を重複する別セットとして同時生成しない。新しい予測/確率/EV/Kelly ロジックは追加しない(既存 011/016 を使う)。
- **FR-005**: 生成の起動は ML(training/serving)を import しない経路(betting を subprocess 実行、028 と同型: cwd/VIRTUAL_ENV strip/timeout/exit-code マッピング)で行い、ops のインポート境界を保たなければならない。
- **FR-006**: 生成対象の prediction_run は API の選択則(active→最新)と同一 run を明示指定しなければならない(betting CLI の最新 computed_at 依存を使わない)。
- **FR-007**: オッズが無い/予測が無いレースは生成せず `skipped`(理由付き)として終了しなければならない(succeeded+0 行の曖昧状態を作らない)。予測・根拠表示に影響を与えない。
- **FR-008**: 生成は冪等でなければならない — 選択 run に推奨が既に存在する場合は再生成せずスキップし、append-only の重複を作らない。
- **FR-009**: レース詳細から1レースの推奨生成をオンデマンド起動でき、ジョブ状態(受付/生成中/完了/一部/失敗/対象なし)を1ラベルで表示し、完了時に推奨表示を自動 refetch する(028 予測ボタンと別ボタン・同型)。
- **FR-010**: 予測済み+オッズありレース群に一括生成する運用手段を提供し、生成・スキップ理由別件数を監査集計で返す。1レース失敗で全体を中断しない(per-race 例外隔離)。

### 横断(境界・整合性・監査)

- **FR-011**: 生成される recommendations は real 配当があれば real、無ければ推定 O_est(double-pseudo)を使い、is_estimated_odds/pseudo_odds/pseudo_roi/stake_fraction を既存契約どおり保持。front で real/推定を区別し pseudo は必ずバッジ(011/012/015/016 継承)。
- **FR-012**: 推奨の値(EV/pseudo/stake)はモデル入力特徴量に一切戻さない(リーク境界)。生成は結果(finish/払戻)を選定に読まない(選定リーク境界、betting 既存)。入力は永続化済み予測 p とオッズのみ。
- **FR-013**: ops のインポート境界(training/serving/betting/features/eval/api を import しない)を維持する。ops の job 種別/スキーマ/OpenAPI に `recommend` を追加する際、front の型 drift-check も更新する。

### Key Entities

- **Recommendation(既存)**: (recommendation_id, prediction_run_id, race_id, bet_type, selection, market_odds_used, estimated_market_odds_used, is_estimated_odds, pseudo_odds, pseudo_roi, stake_fraction)。生成・永続化するが**スキーマ変更なし**。
- **推奨生成ジョブ(新, ops)**: race_id を対象に betting CLI を subprocess 実行(028 predict job と同型)。状態・監査集計を持つ。

## Success Criteria *(mandatory)*

- **SC-001**: 予測+オッズありレースで推奨生成後、RecommendationPanel が**表示対象 run のみ**の買い目を非空・**重複なし**で表示し、EV・pseudo-ROI・**Kelly stake**・real/推定バッジが出る(実 DB 手動1レース検証)。
- **SC-002**: 同一レースを2回生成しても画面に重複が出ない(選択 run 絞り込み + 冪等生成)。
- **SC-003**: オッズ無/予測無レースで `skipped`(理由付き)になり、succeeded+0 行の曖昧状態を作らず、予測・根拠表示は byte 無変化。
- **SC-004**: 一括生成後、対象レース群の recommendations が埋まり、件数=生成+スキップ(理由別)で監査集計が一致。
- **SC-005**: ops のインポート境界テストが緑(ops が training/serving/betting/features/eval/api を import していない=subprocess 経由)。
- **SC-006**: 014 read-only 契約が不変(全 path GET・書き込みエンドポイント追加なし)。front で pseudo/推定オッズは必ずラベル(015 不変条件継承)。openapi.json/型 drift-check 緑。

## Assumptions

- betting の生成ロジックは prediction_run 明示指定で単一の一貫セット(EV+Kelly stake)を生成できる。不足があれば薄い CLI サブコマンド/引数追加で補う(新ロジックは足さない)。exotic と Kelly の統合方法(1セット化)は plan で確定。
- ops の job/worker/enqueue/router/schemas/KindT は 028 predict と同一パターンで recommend 用に拡張できる。
- front は 028 PredictButton/opsClient と同一パターンで RecommendButton を追加でき、RecommendationPanel(015)を stake_fraction/行 key 対応に拡張する。
- スキーマ変更なし(recommendations/ingestion_jobs は既存、batch id は導入しない)。migration 追加なし。
- 表示・運用の結線 feature で OOS 採否ゲート対象外。機械検証は SC-001〜006 の不変条件。
- netkeiba 非アクセス(DB の既存予測・オッズのみ)。

## Deferred（スコープ外）

- 予測完了時の推奨自動生成(まず明示ボタン/CLI。auto-trigger は append-only 増殖リスクが高く運用自動化 feature へ)
- 単勝/複勝(007)推奨の製品結線(まず exotic + Kelly stake)
- recommendations への batch/generation id 追加(冪等スキップ+run 絞りで十分な間は不要)
- real exotic odds の取得(netkeiba 依存=方針外)
- 推奨のスケジュール実行・通知(019/運用自動化 deferred)
- 買い目 UI の高度化(フォーメーション/フィルタ/ソート)
