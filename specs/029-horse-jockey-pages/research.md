# Phase 0 Research: 馬・騎手プロフィールページ

設計の非自明点について codex second opinion（spec 前のアーキ方針、plan 前の実装論点）を取得し、現状コード（api routers/queries/schemas、db models、front HorseEntriesTable/router/queries）を確認して確定した判断を記録する。

## D1. endpoint 形（プロフィール vs 履歴の分割）

- **Decision**: `GET /api/v1/horses/{horse_id}`（識別＋血統＋通算成績）と `GET /api/v1/horses/{horse_id}/history`（レース別履歴・ページング）を分割。騎手も同型（`/jockeys/{id}`、`/jockeys/{id}/history`）。履歴は既存の `Page[T]`（`schemas.py`）＋`page`/`page_size`（上限あり、`routers/races.py` の `_MAX_PAGE_SIZE` 同型）を踏襲。
- **Rationale**: 通算プロフィールは race 文脈不要なので unscoped でよい。履歴を 1 レスポンスに同梱すると長期馬で肥大するため分割＋ページング。既存パターン再利用で実装/契約コスト最小。
- **Alternatives considered**: 単一 endpoint に全履歴同梱（肥大・ページング不可＝却下）。race-scoped（as-of 特徴を出す段階で必要だが、本 feature は事実集計なので不要＝却下）。

## D2. 成績集計（母数・取消/除外の扱い）

- **Decision**: 単発の集約クエリ（N+1 なし）で算出。
  - **出走数** = `race_horses.entry_status='started'` の件数（馬は horse_id、騎手は jockey_id 起点）。
  - **勝率/連対率/複勝率** = それぞれ `finish_order==1 / <=2 / <=3`（`result_status='finished'` かつ `finish_order` 非 null）の件数 ÷ **出走数**。
  - **平均着順** = `finished` かつ `finish_order` 非 null の平均。
  - 取消・競走除外（`entry_status!='started'`）は出走数に含めない。中止・失格（`result_status!='finished'` / `finish_order` null）は出走数に含むが着順率・平均着順の対象から除外。
- **Rationale**: 日本競馬慣行（勝率/連対率/複勝率は出走数基準）に合わせると直感的。完走基準にすると取消で母数が動く。`result_status`/`finish_order` の null/区分で機械的に分離できる。
- **集計経路**: 馬＝`race_results`(horse_id) と `race_horses`(horse_id) を結合し `GROUP BY horse_id`。騎手＝`race_horses`(jockey_id) を起点に対応する `race_results` を結合し `GROUP BY jockey_id`。
- **インデックス**: `(race_id, horse_id)` PK はレース起点に効くが horse_id/jockey_id 起点スキャンには弱い。**スキーマは変えず**、履歴のページング上限と `ORDER BY race_date DESC` 安定順で read 負荷を抑える（インデックス追加は別途・本 feature 対象外）。
- **Alternatives considered**: per-row ループ集計（N+1＝却下）。完走基準の母数（取消で母数が揺れる＝却下）。

## D3. 契約の additive 変更（jockey_id 露出）

- **Decision**: `HorseEntry` に `jockey_id: str | None` と `trainer_id: str | None` を additive 追加（列は `race_horses` に既存、schema 未露出）。`race_horses()` クエリの select に両 ID を追加。`openapi.json` と型を再生成し drift-check。
- **Rationale**: front が出走表から騎手プロフィールへリンクするには ID が必要（現状は名前のみ返す）。additive な追加で既存利用を壊さない。
- **read-only 不変**: 追加は read フィールドのみ。`test_no_write_boundary` / `test_readonly_invariant`（全 GET・行数不変）は不変。
- **Alternatives considered**: 名前文字列で遷移（同名衝突・guess-join＝却下）。

## D4. front 構成

- **Decision**: 新ルート `/horses/:horseId`・`/jockeys/:jockeyId`。`HorseDetailPage`/`JockeyDetailPage` は既存 `QueryStateView`（loading/empty/typed-error の 3 状態）と `formatNum`/`formatPct` を再利用。`queries.ts` に `useHorseProfile`/`useHorseHistory`/`useJockeyProfile`/`useJockeyHistory` を既存 `useQuery<T, ErrorInfo>`＋`unwrap` パターンで追加。`HorseEntriesTable` の馬名/騎手名を `<Link>` 化。
- **リンク化規則**: 解決済み canonical ID のみリンク。**surrogate（`nk:` プレフィックス）や null は非リンク（平文）**。馬/騎手 ID は JRA-VAN canonical（非 `nk:`）かで判定。
- **数値表示**: nullable な率・平均は `formatNum` で `null → '--'`（NaN を出さない）。
- **Alternatives considered**: 全行リンク化（surrogate はリンク切れ＝却下）。

## D5. 事実 vs モデル特徴量の UI/経路分離（憲法 II/V）

- **Decision**: プロフィールの成績は「確定実績」「過去成績」と明記し、モデル出力（p/q）の `PredictionTable`/`PQCompare` とは型・hook・コンポーネントを完全分離。api 側は read-only transaction＋`features`/`training` import 禁止（既存 `test_no_write_boundary`）で境界を機械強制。本 feature の表示値はモデル特徴に流れない（read 専用・予測入力に渡さない）。
- **Rationale**: 「事実の集計」と「モデルの予測/特徴量」をユーザーが混同しないことが製品目的（正直な意思決定支援）に直結。憲法 II のリーク境界も維持。
- **Alternatives considered**: 成績と予測を同一表に混在（混同・誤読＝却下）。

## D6. リスクと留意点

- `finish_order` 欠損（取消・中止）は着順率・平均着順から除外（D2）。
- `nk:` surrogate の馬/騎手はリンク不可、canonical 数値 ID と混同しない（D4）。
- 大量履歴はページング＋安定順＋上限で対処（D1/D2）。
- 未存在 ID＝typed 404、未処理 500 を出さない（既存 `_err` パターン）。馬/騎手 ID は固定フォーマットを持たない（race_id の 12 桁固定とは異なる）ため形式 422 は設けず 404 に一本化。
- 表示値（率・履歴）はモデル特徴量に流用しない（leak-guard test、II）。

## スキーマ変更の要否（結論）

**変更なし。** 既存テーブルの read 集約のみ。契約変更は `HorseEntry` への nullable フィールド追加（additive）に留まり、migration head は不変。
