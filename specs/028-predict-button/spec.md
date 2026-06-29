# Feature Specification: レース詳細の予測生成ボタン (Predict Button via ops path)

**Feature Branch**: `028-predict-button`

**Created**: 2026-06-29

**Status**: Draft

**Input**: レース詳細画面に「予測する」ボタンを追加し、見ているレースのモデル予測をその場で生成・表示できるようにする。予測生成は write 操作なので read-only の 014 API には置かず、024 で確立した `ops/` 書き込み経路（POST → ジョブ enqueue → worker 実行 → front がジョブ状態をポーリング → 成功で 014 予測クエリを invalidate）に新しい job_type `predict` として載せる。スキーマ変更なし。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 見ているレースの予測をその場で生成 (Priority: P1)

レース詳細画面を開いたユーザーが、そのレースにまだモデル予測が無い（または最新でない）場合に「予測する」ボタンを押すと、現行採用モデルでそのレースの予測が生成され、完了後に画面の予測セクション（勝率・p/q・校正・監査）に反映される。

**Why this priority**: 予測表示の画面・API・モデルは実装済みだが、予測データが存在するレースが少なく、多くのレースで「予測なし」になる。本ボタンが「見ているレースをその場で予測」してこのギャップを埋め、製品目的（人間が予測・確率・EV を見て判断する意思決定支援）に直結する。これ単体で価値が成立する MVP。

**Independent Test**: 予測の無い実レースを開き「予測する」を押す → ジョブが受付され、完了後に予測セクションが表示される（再読み込み不要）。

**Acceptance Scenarios**:

1. **Given** 予測の無いレースの詳細画面, **When** 「予測する」を押す, **Then** ボタンが受付状態になりジョブ ID が払い出される。
2. **Given** 予測ジョブ実行中, **When** ユーザーが待つ, **Then** 状態が「受付→生成中→完了」と進み、完了時に予測セクションが自動更新される（クエリ invalidate）。
3. **Given** 予測ジョブ完了, **When** 予測セクションを見る, **Then** 当該レースの勝率（1着/2着以内/3着以内）と監査（model_version/computed_at）が表示される。
4. **Given** ボタン押下直後（受付中）, **When** 連打しようとする, **Then** 二重起動しない（受付中はボタン無効・debounce）。

---

### User Story 2 - ジョブ状態と失敗の明示 (Priority: P1)

予測生成は数十秒かかるため、ユーザーにジョブの進行（受付中／生成中／完了／失敗／対象なし）が常に分かり、失敗時は理由が分かるように表示する。

**Why this priority**: 非同期処理は状態が見えないと不安・誤操作を招く。024 のデータ更新ボタンと同じ「3状態（ローディング／成功／typed エラー）」規律を踏襲し、信頼できる UX にする。

**Independent Test**: 予測ジョブの各状態（queued/running/succeeded/failed/skipped）でボタンの表示が対応するラベルに変わり、失敗時はエラー内容が出る。

**Acceptance Scenarios**:

1. **Given** ジョブが running, **When** ポーリング, **Then** 「生成中」を表示。
2. **Given** ジョブが failed, **When** 完了, **Then** 「生成失敗」＋理由（typed エラー）を表示し、再実行可能。
3. **Given** 存在しない/対象外レース, **When** 予測を要求, **Then** typed な 404/422（不正な race_id）でユーザーに分かる形で表示（未処理 500 を出さない）。

---

### User Story 3 - read-only 境界と監査の保持 (Priority: P1)

予測生成（write）は ops 経路のみで行い、表示用の 014 API は read-only のまま不変であること、ジョブと予測に監査情報が残ることを保証する。

**Why this priority**: 憲法 VI（read-only の表示 API）と V（再現性・監査）の遵守は非交渉。書き込み経路の分離を崩すと将来の手戻り・誤用リスク。

**Independent Test**: 014 の全エンドポイントが GET のみで write しないこと（既存テスト）が維持され、予測ジョブ実行後に ingestion_jobs と prediction_runs に監査行が残る。

**Acceptance Scenarios**:

1. **Given** 予測ボタン, **When** front が呼ぶ, **Then** 呼び先は ops エンドポイントのみ（014 api は呼ばない・write しない）。
2. **Given** 予測ジョブ完了, **When** 監査を確認, **Then** ingestion_jobs に job_type=predict の行（status/summary/trace_id）、prediction_runs に model_version/logic_version/computed_at が残る。
3. **Given** 014 api, **When** 予測ボタン機能追加後, **Then** api は依然 read-only（write エンドポイントを足さない）。

### Edge Cases

- **過去レース（結果確定済み）**: 予測表示目的のため許可（live(019) の result-pending ガードは掛けない）。最新採用モデルの予測を表示。
- **未来レース（結果未確定）**: 許可。odds 無しでも予測は生成可（推奨は別、本機能は予測のみ）。
- **未来レースで出走馬が未確定（entries 不完全）（codex Q2）**: 予測を実行せず skipped として明示（中途半端な prediction_run を残さない＝entries 確定後に再実行）。
- **採用モデルが 0 本 / 複数（codex risk）**: run_serving が一意の active モデルを要求するため、ジョブを failed としエラー理由を summary に残し front に表示（サイレント失敗にしない）。
- **既に予測あり・連続クリック（dedup, codex Q3）**: 進行中（queued/running）の predict ジョブがあれば二重 enqueue しない。完了済みは明示クリックで再生成（モデル/エントリ更新後に最新で作り直せる）。ingestion_jobs に汎用 payload 列が無いため、model_version は dedup キーに埋め込まず prediction_runs の監査に残す（in-flight 限定 dedup でスキーマ拡張を回避）。
- **存在しない race_id / 出走馬なし**: typed エラー（404/422）で、ジョブは skipped/failed として明示。
- **連打・同時押し**: front debounce + 受付中ボタン無効 + サーバ側 dedup で二重起動しない。
- **predict と refresh の同時実行**: 別 job_type で競合しない（同一レースに両方 enqueue されても破綻しない）。
- **生成中の離脱**: ジョブはサーバ側で継続、再訪時に最新予測が表示される。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: ops に予測生成を起動する write エンドポイント（race 単位）を追加し、受付時にジョブを enqueue してジョブ識別子を返す MUST。予測生成（run_serving）は ops 経路でのみ行う MUST。
- **FR-002**: ops worker が予測ジョブを実行し、現行採用モデルでそのレースの予測を生成・永続化（prediction_runs / race_predictions）する MUST。実行は既存の serving 予測経路（as-of・リーク安全）を再利用し、新たな予測ロジック・特徴計算を足さない MUST NOT。
- **FR-003**: 予測ジョブは過去レース・未来レースのいずれも対象にできる MUST（result-pending 制限は掛けない）。ただし**出走馬が未確定（entries 不完全）の未来レースは実行せず skipped とする** MUST（中途半端な prediction_run を残さない、codex Q2）。
- **FR-004**: 既定の使用モデルは現行採用モデルとする MUST。採用モデルが一意でない（0 本/複数）場合はジョブを failed としエラー理由を summary に残す MUST（codex risk）。dedup は**進行中（queued/running）の同一レース predict ジョブの二重 enqueue を防ぐ**ことを MUST とし、完了済みジョブは明示クリックでの再生成を許す（model_version は prediction_runs 監査に残し dedup キーに埋め込まない＝ingestion_jobs スキーマ拡張を回避、codex Q3）。
- **FR-005**: front のレース詳細画面に「予測する」ボタンを追加し、押下→ジョブ受付→状態ポーリング→完了で予測表示を自動更新（再取得）する MUST。受付中は二重起動を防ぐ（無効化／debounce）MUST。
- **FR-006**: ジョブ状態（受付/生成中/完了/失敗/対象なし）と失敗理由をユーザーに明示する MUST。ローディング／成功／typed エラーの3状態を区別する MUST（未処理 500 を出さない）。
- **FR-007**: 表示用 014 API は read-only のまま不変とする MUST（予測生成の write エンドポイントを api に足さない MUST NOT）。front が write するのは ops のみ MUST。
- **FR-008**: 予測ジョブの監査（job_type=predict/scope/status/summary/trace_id）を ingestion_jobs に、予測の監査（model_version/logic_version/computed_at）を prediction_runs に残す MUST。
- **FR-009**: DB スキーマ変更なし（migration head 不変、既存 ingestion_jobs/prediction_runs/race_predictions を再利用）MUST。
- **FR-010**: ops/front の契約（予測エンドポイントの形・ジョブ応答）を契約ファイルに定義し、front の生成型と同期する MUST（契約先行、憲法 VI）。
- **FR-011**: 確率整合性は既存の win→joint(009)・整合性チェックを通る MUST（本機能は経路追加のみで確率ロジック不変）。
- **FR-012**: リーク境界不変（予測は as-of・対象レースより前のみ・結果/オッズを入力にしない）MUST。本機能は新しいリーク面を作らない MUST NOT。

### Key Entities *(include if feature involves data)*

- **Predict job（予測ジョブ・既存 ingestion_jobs 再利用）**: job_type=predict / scope=race / scope_value=race_id / status / summary / trace_id。予測生成の起動・進行・結果の監査単位。
- **Prediction run（既存 prediction_runs）**: 生成された予測ランの監査（model_version/logic_version/computed_at）。
- **Race predictions（既存 race_predictions）**: 馬別の勝率（1着/2着以内/3着以内）。014 が表示に読む。
- **Predict button（front UI）**: レース詳細のアクション。ジョブ起動・状態表示・成功時に予測再取得。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 予測の無い実レースで「予測する」を押すと、特別な操作なしに（再読み込み不要で）予測セクションが表示されるまで完結する。
- **SC-002**: 予測ジョブの状態が常に受付/生成中/完了/失敗/対象なしのいずれかで明示され、失敗時は理由が分かる（未処理 500 ゼロ）。
- **SC-003**: 予測生成後、ingestion_jobs に job_type=predict の監査行、prediction_runs に model_version/computed_at が残る（100% 監査可能）。
- **SC-004**: 014 api は機能追加後も read-only（全エンドポイント GET、write エンドポイント数ゼロ）。
- **SC-005**: 同一レース×同一モデルの連続要求で重複生成が起きない（dedup）。連打で二重ジョブが発生しない。
- **SC-006**: DB migration head 不変、ops/front とも契約と生成型が一致（drift なし）。

## Assumptions

- 024 の ops 基盤（POST→enqueue→worker claim→runner→ジョブ状態ポーリング、ingestion_jobs、front の RefreshButton/opsClient、read-only api との分離）は本機能の土台として利用可能（main にマージ済み）。
- serving の予測経路（run_serving, build_feature_matrix の as-of）は過去・未来いずれのレースにも実行可能で、result-pending ガードは live(019) 側にのみ存在する（serving 本体には無い）。本機能はそのガードを掛けない。
- 鮮度（dedup の有効期間）は 024 の既定（約1時間）に倣う。モデル更新時は dedup キーにモデルを含めることで再生成される。
- 予測 1 レースの生成は数十秒規模（特徴行列構築のため）であり、非同期ジョブ＋ポーリングで UX を吸収する（同期応答にしない）。
- live(019) との役割分担: live は result-pending 限定＋odds 必須＋Kelly recommendation までの「運用予測」、本 predict ボタンは「任意レースの予測生成・表示」（odds 任意・予測のみ、recommendation は別）。
- 過去レースの予測は最新採用モデルで上書きされる（最新モデルの予測を見せる目的、限界として開示）。

## Deferred

- 1 日分一括の予測バッチ（refresh_day 同型の `POST /ops/v1/days/{date}/predict`）— 一覧から「今日の全レース予測」UX に自然（codex Q6、worker は race ループのみ）。MVP は race 単位、バッチは follow-up。
- ジョブ summary の `source='manual'` タグ（live(019) 自動実行とバックテスト時に分離）— codex Q6、監査の付加価値。
- materialized parquet による予測生成の高速化。
- worker キュー容量の監視・throttle、優先度制御。
- UI でのモデルバージョン選択。
- 予測スナップショット履歴（過去モデルでの再現）。
- 予測と同時に買い目推奨（betting 連携）を生成すること。
