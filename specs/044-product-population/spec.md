# Feature Specification: 製品をデータで満たす — 予測 backfill と一括 populate (Product Data Population)

**Feature Branch**: `044-product-population`

**Created**: 2026-07-02

**Status**: Draft（codex CLI 利用不可のため single-opinion。設計はコードベース根拠に基づく）

**Input**: 021/040/043 で表示・生成の仕組みは完成したが、予測(prediction_runs/race_predictions)と推奨(recommendations)が数レース分しか無く、大半のレースは空で製品が実際に使えない。予測を日付範囲で一括生成する手段(予測 backfill)を追加し、予測+推奨を規模で流して製品を実データで満たす。

## 背景と目的

製品目的は「人間が予測・確率・信頼性を見て自分で判断する意思決定支援」(021)。021(p/q・校正)+ 040(予測根拠・重要度・乖離)+ 043(買い目生成)で**配管は通った**が、DB には私が検証で作った数レース分の予測/推奨しか無く、RaceFront の一覧・詳細を横断すると**大半のレースが空**。「作ったが使えない」状態を解消し、製品を実データで動く状態にする。

**既存資産**: serving CLI `predict --date`(1 日分 run_serving)/ `predict --race-id`。043 betting CLI `recommend-backfill --from --to`(推奨の日付範囲 backfill)。**欠けているのは予測の日付範囲 backfill**(現状 1 日ずつしか予測できない)。

本 feature は新しい予測/確率ロジックを足さない — run_serving(006, as-of リーク安全)を範囲で回すオーケストレーションのみ。

## 制約・設計方針(コードベース根拠)

- **p-parity(最重要)**: backfill の予測は per-day で `run_serving(date=D)` と同一の `build_feature_matrix(end_date=D)` 経路を通し、日ごとの run_serving とバイト一致する予測を出す(範囲一括ビルドは 026 で static 列の pool 依存 dtype ドリフトが報告されているため採らない。019 の p-parity 不変条件を維持)。
- **冪等ポリシー**: 「**現 active モデルの prediction_run が無いレースだけ生成**」を既定とする。単純な any-run skip でなく model_version 単位。理由: read API(014 select_prediction_run)は active→最新 run を選ぶため、旧モデル(例 lgbm-039)でしか予測されていないレースは active モデル(例 lgbm-042)の run を持たない → 生成対象にすることで gap 補完と旧モデルレースの更新を両立。`--force` で active-run が既にあっても再生成。
- **リーク境界不変(憲法 II)**: run_serving は結果を読まず as-of features を使う(build_feature_matrix, end_date=race_date, 同日除外)。backfill は新予測ロジックを足さない=新リーク面ゼロ。オッズ/結果は特徴に戻さない(既存 leak-guard)。
- **スキーマ変更なし**(prediction_runs/race_predictions/recommendations は既存)。migration 追加なし。netkeiba 非アクセス(DB の既存 races/odds のみ)。
- **read-only 014 不変**。予測の書き込みは serving、推奨は betting(043)。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 予測の日付範囲 backfill (Priority: P1)

運用者が日付範囲を指定して、その範囲の全レースに対し現 active モデルの予測を一括生成できる。冪等(active モデルの run が既にあるレースはスキップ)で、`--force` で強制再生成。per-day の例外隔離(1 日の失敗で全体を止めない)と、生成/スキップ理由別件数の reconciliation を出力する。

**Why this priority**: 本 feature の中核。推奨(043 backfill)は既にあるが、その前提となる予測が範囲で作れないため製品が埋まらない。

**Independent Test**: 予測のあるレースを含む日付範囲で predict-backfill 実行 → active モデルの run が無かったレースに run が生成され、既にあったレースはスキップ、件数=生成+スキップ+エラーで一致。予測値は同条件の run_serving(date=D) とバイト一致。

**Acceptance Scenarios**:

1. **Given** active モデルの run が無いレースを含む範囲, **When** predict-backfill 実行, **Then** 各対象レースに active モデルの prediction_run + race_predictions が生成され、生成/スキップ件数が集計される
2. **Given** active モデルの run が既にあるレース, **When** predict-backfill(既定), **Then** そのレースはスキップされ重複 run を作らない
3. **Given** `--force`, **When** predict-backfill, **Then** active-run があっても再生成する(append-only、新 run)
4. **Given** 1 日分の処理が失敗, **When** 範囲処理中, **Then** その日はエラー計上して残りの日を継続(全体中断しない)
5. **Given** 同一レースを per-day run_serving と backfill 双方で予測, **When** 比較, **Then** win_prob がバイト一致(p-parity)

---

### User Story 2 - 実データで製品を満たす(populate & 検証) (Priority: P1)

運用者が最近の一定範囲について predict-backfill → recommend-backfill(043)を実行し、RaceFront の一覧・詳細が横断的に予測・根拠・買い目を実データで表示する状態にする。

**Why this priority**: 「製品をデータで満たす」の実現そのもの。US1 の仕組みを実データに適用して検証する。

**Independent Test**: 最近 N 日を populate → RaceListPage が予測ありレースを、RaceDetailPage が p/q・根拠・推奨を実データで表示することを確認(1 レース以上で end-to-end)。

**Acceptance Scenarios**:

1. **Given** predict-backfill + recommend-backfill を最近範囲で実行, **When** RaceDetailPage を開く, **Then** 予測・スコア寄与・買い目(EV/Kelly)が実データで表示される
2. **Given** オッズが無いレース, **When** populate, **Then** 予測は生成され、推奨は 043 の skip 規律で対象外(エラーにしない)

---

### Edge Cases

- **取消のみ/出走馬なしレース**: run_serving は started 馬が無いレースをスキップ(既存挙動)。backfill も同様にスキップ計上。
- **オッズ無レース**: 予測は生成(オッズ不要)、推奨は 043 が skip。
- **結果未確定(未来)レース**: run_serving は result-pending-safe(019)。backfill 対象に含めてよい。
- **active モデル変更後**: 旧モデルのみの run を持つレースは active-run 無し扱いで生成対象(既定ポリシー)。
- **大量範囲の負荷**: per-day で feature matrix を構築(1 日 1 回)。範囲は運用者が区切る(全期間一括は非推奨、件数を出して可視化)。
- **重複 run**: 既定は active-run ありをスキップ=重複を作らない。`--force` 時のみ append-only で新 run(read は最新を選ぶ)。

## Requirements *(mandatory)*

- **FR-001**: システムは日付範囲 [from, to] の全レースに対し、現 active モデル(または明示指定モデル)で予測を生成・永続化できなければならない。予測は run_serving(006)を用い、新しい予測/確率ロジックを追加しない。
- **FR-002**: backfill の予測は、同一レースを per-day run_serving(date=D)で予測した場合と win_prob がバイト一致しなければならない(p-parity、per-day の build_feature_matrix(end_date=D)経路を用いる)。
- **FR-003**: backfill は既定で冪等でなければならない — 対象モデルの prediction_run が既にあるレースは再生成せずスキップする。`--force` 指定時のみ active-run があっても再生成する。
- **FR-004**: backfill は per-day の例外隔離を行い、1 日の失敗で全体を中断してはならない。生成/スキップ(理由別: 対象モデル run 既存 / 出走馬なし)/エラーの件数を reconciliation として出力しなければならない。
- **FR-005**: 予測 backfill はリーク境界を保つ(結果を読まない・as-of features のみ・オッズや結果を特徴に戻さない)。スキーマ変更を行わない。
- **FR-006**: 運用者は予測 backfill と推奨 backfill(043)を組み合わせて日付範囲を populate でき、その結果 read-only 製品(014/015)が該当レースの予測・根拠・推奨を実データで表示する(014 は不変)。

### Key Entities

- **prediction_run / race_predictions(既存)**: backfill が active モデルで生成・永続化。スキーマ変更なし。
- **recommendations(既存, 043)**: recommend-backfill が生成。本 feature は予測 backfill を提供し populate フローを完成させる。

## Success Criteria *(mandatory)*

- **SC-001**: predict-backfill 実行後、対象範囲で active モデルの run が無かったレースに run が生成され、件数=生成+スキップ(理由別)+エラーで一致する(reconciliation)。
- **SC-002**: backfill で生成した予測の win_prob が、同一レースの per-day run_serving 予測とバイト一致する(p-parity テスト)。
- **SC-003**: 既定実行が冪等(2 回目は active-run 既存で全スキップ、新 run を作らない)。`--force` でのみ再生成。
- **SC-004**: 実 DB で最近範囲を populate 後、RaceDetailPage が予測・スコア寄与・買い目を実データで表示する(手動 1 レース以上)。
- **SC-005**: リーク境界テスト・read-only 014 テストが緑のまま。スキーマ変更なし(migration head 不変)。

## Assumptions

- run_serving は date/race_id 指定で as-of リーク安全に予測・永続化する(006、既存)。backfill は日ごとに run_serving と同一経路を回すことで p-parity を担保する。予測ロジックは追加しない。
- 043 recommend-backfill が推奨側の範囲生成を担う。本 feature は予測側を補い populate を完成させる。
- backfill は運用 CLI(serving)。ops job / 自動スケジュールは deferred(既存の予測/推奨ボタンで on-demand は足りる)。
- 表示・運用の feature で OOS 採否ゲート対象外。機械検証は SC-001〜005 の不変条件。
- netkeiba 非アクセス(DB の既存 races/odds のみ)。

## Deferred（スコープ外）

- ops job / front からの範囲 backfill 起動(CLI で足りる。on-demand は 028/043 ボタン)
- 自動スケジュール(予測→推奨を新規レースで自動、019/運用自動化)
- 予測+推奨を1コマンドで束ねる convenience(まず 2 CLI の組合せ)
- range 一括 feature matrix ビルドによる高速化(dtype 安全性の検証後。まず per-day で p-parity 優先)
- 予測完了時の推奨自動生成
