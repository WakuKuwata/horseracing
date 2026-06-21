# Feature Specification: JRA-VAN 過去データ取込 (2007+)

**Feature Branch**: `002-jra-van-ingest`

**Created**: 2026-06-21

**Status**: Draft

**Input**: User description: "JRA-VAN Historical Ingest (2007+) — 年別 CSV をコアテーブルへ冪等取込"

## 概要

ローカルの JRA-VAN 年別 CSV ファイル (`raw_data/jra-van/<year>`) を読み取り、Feature 001 で確定した
コアテーブル (races / horses / jockeys / trainers / race_horses / race_results) へ冪等に upsert する
取込パイプラインを提供する。取込ジョブは `ingestion_jobs` で監査する。

本フィーチャーの範囲は「取込ロジック (パース + 正規化 + upsert + 監査)」。コアテーブル
(races/horses/jockeys/trainers/race_horses/race_results) のスキーマは変更しない。ただし監査
(憲法 V) のため、`ingestion_jobs` に取込件数を保持する列を**非破壊で追加**する (憲法 VI の非破壊拡張)。
特徴量計算・学習・予測・netkeiba スクレイピングは別フィーチャー。

「利用者」は人間エンドユーザーではなくオペレーター (手動で取込を実行する) と、取込結果に依存する
下流コンポーネント (評価ハーネス・特徴量生成)。

### 入力データの実像 (調査済み)

- `raw_data/jra-van/` 配下に 1986〜2025 の年別ファイル (拡張子なし)。1 ファイル = 1 年。
- 形式: **Shift_JIS** エンコードの CSV、**73 列固定**、1 行 = 1 レース 1 頭の成績。2007 年は約 49,009 行。
- レースレベル項目 (年月日・開催場名・回・日目・レース番号・クラス・芝/ダート・距離・馬場・天候) が
  各馬行に繰り返され、馬レベル項目 (馬名・性・齢・騎手名・斤量・着順・着差・人気・オッズ・上がり・
  タイム・馬体重・増減・血統登録番号・騎手コード・調教師コード・父母名等) が続く。
- 18 桁 ID 例 `200708110101010101` が存在。これを 12 桁 raceId (`YYYYVVKKDDRR`) + 馬番に分解する。
- 73 列の意味を定義した data dictionary はリポジトリに無い (列レイアウト確定が本フィーチャー最大の
  不確実性)。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 1 年分のファイルをコアテーブルに取り込める (Priority: P1) 🎯 MVP

オペレーターが 2007 年ファイルを end-to-end で取り込み、races / horses / jockeys / trainers /
race_horses / race_results に冪等 upsert できる。

**Why this priority**: これが取込の最小成立単位。1 年分が正しく入れば、下流の評価ハーネス・特徴量が
着手できる。プロジェクトで初めて「実データがコアテーブルに乗る」価値を提供する。

**Independent Test**: 2007 年の小さな golden CSV fixture (数レース分) を投入し、期待されるレース数・
出走数・結果数が制約違反なく入り、同一ファイル再投入で重複行が増えないことを検証する。

**Acceptance Scenarios**:

1. **Given** 妥当な 2007 年 fixture、**When** 取込を実行する、**Then** races / race_horses /
   race_results が期待件数で作成され、`race_id` は `^[0-9]{12}$` を満たし、出走馬・騎手・調教師が
   horses / jockeys / trainers に upsert される。
2. **Given** 取込済みの同一ファイル、**When** もう一度取込を実行する、**Then** 行数は増えず、対象項目が
   最新値に更新される (冪等)。
3. **Given** 出走表に現れる未登録の馬・騎手・調教師、**When** 取込する、**Then** 発見的に upsert され
   FK 整合が保たれる (upsert 順序: races/horses/jockeys/trainers → race_horses → race_results)。
4. **Given** 1 回の取込実行、**When** 完了する、**Then** `ingestion_jobs` に source=`jra_van`・対象年・
   状態・処理行数・エラーが記録される。

---

### User Story 2 - 状態コードを正しく正規化しラベルを汚染しない (Priority: P1)

出走取消・競走除外・競走中止・失格・同着を JRA-VAN の値から判別し、`enums` の
`entry_status` / `result_status` に正しくマップする。

**Why this priority**: 憲法 NON-NEGOTIABLE 原則 II (リーク防止) と、学習ラベルの正しさを担保する。
状態の誤分類は `labels.derive_labels` (finished のみ教師化) を直接汚染するため、取込の正確性の核心。

**Independent Test**: 取消/除外/中止/失格/同着を含む golden fixture を取込み、`labels.derive_labels`
が finished のみを返し、各状態が `entry_status` / `result_status` に正しく区別されることを検証する。

**Acceptance Scenarios**:

1. **Given** 出走取消・競走除外の行、**When** 取込する、**Then** `entry_status` = `cancelled` /
   `excluded` で記録され、`race_results` 行は作られない (非出走、INV-1)。
2. **Given** 競走中止・失格の行、**When** 取込する、**Then** `race_results` 行は作られるが
   `result_status` = `stopped` / `disqualified` で、完走前提集計から除外される。疑似着順 (最下位等) に
   変換されない。
3. **Given** 同着 (複数馬が同一着順) の行、**When** 取込する、**Then** 同一 `finish_order` を共有する
   形で記録され、`labels.derive_labels` で複数の勝ち馬を許容する。
4. **Given** JRA-VAN の状態元値、**When** マップする、**Then** 元値 → 状態コードの対応が明示された
   対応表に従い、未知の元値はエラーとして記録され黙って finished 扱いされない。

---

### User Story 3 - 複数年を一括取込し、境界・再開・監査を担保する (Priority: P2)

オペレーターが 2007〜2025 を一括取込でき、2007 境界の強制・途中失敗からの再開・年単位の監査が
できる。

**Why this priority**: 全学習データを揃えるために必要だが、MVP (US1/US2) が 1 年で成立した後に拡張
できるため P2。

**Independent Test**: 2006 と 2007 の fixture を渡し、2006 がスキップ記録され 2007 のみ取込まれること、
途中失敗後に checkpoint から再開して重複が出ないことを検証する。

**Acceptance Scenarios**:

1. **Given** 1986〜2025 のファイル群、**When** 一括取込を実行する、**Then** `race_date` が 2007-01-01
   以降のデータのみ取込まれ、2006 以前ファイルはスキップとして記録される。
2. **Given** 年ファイルの取込が途中で失敗する、**When** 再開する、**Then** `ingestion_jobs` の
   checkpoint (処理済み行番号) から再開し、既に取込んだ行は重複しない。
3. **Given** 全年取込の完了、**When** 監査する、**Then** 年ごとの取込件数・スキップ・エラーが
   `ingestion_jobs` で確認できる。

---

### Edge Cases

- 同着 (複数馬が同一着順)。
- 出走取消・競走除外・競走中止・失格の各状態。
- 同一馬が複数年・複数レースに登場 (horses は upsert で 1 行)。
- 列数が 73 でない行・Shift_JIS デコード不能行・空フィールド。
- 同一年ファイルの再取込 (冪等)。
- 2006 以前ファイルの混入 (スキップ)。
- 海外馬・地方馬・血統登録番号欠損。
- 馬体重・増減が欠損 (未計量) の行。
- 個別列から導出した `race_id` が `^[0-9]{12}$` を満たさない行 (18桁 ID は cross-check のみ)。

## Requirements *(mandatory)*

### Functional Requirements

**取込・パース (US1)**

- **FR-001**: システムは Shift_JIS エンコードの年別 CSV を、73 列固定のレコードとしてストリーミング
  解析しなければならない。
- **FR-002**: システムは各行から、現行コアスキーマが必要とする列を抽出してコアテーブルへマップ
  しなければならない。スキーマ外の列は読み飛ばし、後続フィーチャーで非破壊拡張できるようにする。
- **FR-003**: システムは 18 桁 ID から 12 桁 `race_id` (`YYYYVVKKDDRR`) と馬番を導出し、`race_id` が
  `^[0-9]{12}$` を満たすことを保証しなければならない。
- **FR-004**: システムは開催場名を `venue_code` に変換しなければならない (対応表は research R3 で確定済み: 標準 JRA 10 コース)。
- **FR-005**: システムは races / horses / jockeys / trainers → race_horses → race_results の順で
  upsert し、FK 整合を保たなければならない。出走馬・騎手・調教師は発見的に upsert する。
- **FR-006**: システムは同一年ファイルの再取込を冪等に扱い、重複行を作らず最新値に更新しなければ
  ならない。
- **FR-007**: システムは取込実行ごとに `ingestion_jobs` に source=`jra_van`・対象年・状態・処理行数
  (processed/skipped/error 件数)・テーブル別件数 (races/race_horses/race_results)・エラーを記録
  しなければならない。これらの件数列は `ingestion_jobs` への非破壊な列追加で保持する (ジョブ時点の
  件数を後から再現できるようにするため。core テーブル集計では再取込後に復元できないため不十分)。

**状態正規化 (US2)**

- **FR-008**: システムは JRA-VAN の状態元値を `entry_status` (`started`/`cancelled`/`excluded`) と
  `result_status` (`finished`/`stopped`/`disqualified`) に明示対応表でマップしなければならない。
  finished / DNF / DNS の 3 区分は保証する。取消 vs 除外、競走中止 vs 失格 の 4 分類細分は、異常区分
  指標を golden fixture で特定できた範囲で精緻化する (best-effort、未特定なら DNS→cancelled /
  DNF→stopped を既定とし、ラベル整合は 3 区分で担保)。
- **FR-009**: システムは出走取消・競走除外を非出走として扱い、`race_results` 行を作ってはならない。
- **FR-010**: システムは競走中止・失格を出走済みとして `race_results` 行を作るが、`result_status` を
  非 finished とし、疑似着順に変換してはならない。
- **FR-011**: システムは同着を同一 `finish_order` の共有で表現しなければならない。
- **FR-012**: システムは未知の状態元値を黙って finished 扱いせず、エラーとして記録しなければならない。

**境界・一括・再開・監査 (US3)**

- **FR-013**: システムは `race_date` が 2007-01-01 以降のデータのみ取込み、境界判定に
  `validation.is_in_ingest_scope` を使用しなければならない (独自の日付比較を書かない)。
- **FR-014**: システムは 2006 以前ファイルをスキップし、`ingestion_jobs` に `status='skipped'` の
  ジョブ行として記録しなければならない (`skipped` は migration 0004 で `JobStatus` に非破壊追加)。
- **FR-015**: システムは年ファイル単位の `ingestion_jobs` と checkpoint (処理済み行番号) を持ち、
  途中失敗から再開でき、再開時に重複を生まないようにしなければならない。
- **FR-016**: システムは 2007〜2025 を一括取込でき、年ごとの取込件数・スキップ・エラーを監査可能に
  しなければならない。

**データ品質・provenance**

- **FR-017**: システムは列数が 73 でない行、Shift_JIS デコード不能行、`race_id` 形式不正行を黙って
  捨てず、行番号付きで `ingestion_jobs` のエラーとして記録しなければならない。
- **FR-018**: システムは JRA-VAN の単勝オッズ・人気を「結果確定時」値として `race_horses` に保存し、
  その provenance (発走前特徴量に使用不可) を **data-model/research にドキュメント記載**しなければ
  ならない。行レベルの provenance 列やスキーマ変更は本フィーチャーでは行わない (JRA-VAN はソース
  単位で一様に結果確定時値であり、行フラグは冗長。リーク防止は憲法通り特徴量フィーチャーで強制し、
  本フィーチャーの「スキーマ変更なし」方針とも整合する)。
- **FR-019**: システムは欠損項目 (馬体重・増減の未計量、血統登録番号欠損等) を `null` (Unknown) として
  保存し、`0` と区別しなければならない。

### Key Entities *(include if feature involves data)*

本フィーチャーは新エンティティを作らない。Feature 001 のコアテーブルへ書き込み、`ingestion_jobs` で
監査する。取込時に確定する論理対象:

- **取込ジョブ (ingestion_jobs)**: source=`jra_van`、対象年 (scope/scope_value)、状態、checkpoint、
  処理行数、スキップ、エラー理由。
- **状態対応表 (logical)**: JRA-VAN 状態元値 → `entry_status` / `result_status` の対応。
- **開催場対応表 (logical)**: 開催場名 → `venue_code` の対応。
- **列レイアウト (logical)**: 73 列のうちコアスキーマが必要とする列の位置と意味。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 2007 年 golden fixture から、期待されるレース・出走・結果件数が正確に取り込まれる
  (件数が期待値と一致)。
- **SC-002**: 取消/除外/中止/失格/同着が `entry_status` / `result_status` に正しくマップされ、
  `labels.derive_labels` が finished のみを返す (各状態の分類が 100% 期待通り)。
- **SC-003**: 同一年ファイルの再取込で行数が増えない (冪等)。
- **SC-004**: 2006 以前ファイルがスキップされ、コアデータに 1 行も混入しない。
- **SC-005**: 不正行 (列数・エンコード・`race_id` 形式・未知状態) が黙って捨てられず、すべて
  `ingestion_jobs` に行番号付きで記録される。
- **SC-006**: 2007〜2025 全年を取込でき、年ごとの取込件数 (processed/skipped/error・テーブル別) が
  `ingestion_jobs` の件数列で確認できる (実データでのスモーク)。

## Assumptions

- 列マッピングは現行スキーマが必要とする列のみを確定する。確定できない/不要な列はスキーマ外として
  読み飛ばし、後続で非破壊拡張する。73 列レイアウトの確定は research.md の必須成果物。
- JRA-VAN を ID の canonical source として扱い、血統登録番号・騎手コード・調教師コードをそのまま
  `horse_id` / `jockey_id` / `trainer_id` とする。`id_mappings` の本格運用は netkeiba 合流フィーチャー
  まで deferred (本フィーチャーでは未対応 ID を作らない)。
- 取込は手動実行 (オペレーター起動)。自動スケジュールは将来スコープ。
- 実データ `raw_data/jra-van/` は gitignore 済み (ローカルのみ)。テストは小さな golden fixture を
  repo に含める。
- 開催場名 → `venue_code` 対応、JRA-VAN 状態元値 → 状態コード対応は research で確定し、テストで固定
  する。
- 18 桁 ID から `race_id` を導出する正確な分解規則は research の成果物 (例で検証する)。
