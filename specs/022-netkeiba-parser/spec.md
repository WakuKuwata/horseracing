# Feature Specification: 実 netkeiba パーサ (real netkeiba HTML parsing)

**Feature Branch**: `022-netkeiba-parser`

**Created**: 2026-06-28

**Status**: Draft

**Input**: User description: 実netkeibaパーサ — 実際のnetkeiba HTML(出馬表/単勝オッズ/結果ページ)を解析する本物のパーサを実装し、既存008の合成HTMLスキーマ前提スタブを置換する。

## 背景 *(context)*

feature 008 (netkeiba-scraping) の取得層 (`scrape/fetch.py` の `HttpFetcher`: httpx + robots.txt + per-domain rate-limit 1秒 + exponential backoff + file cache)、ID 解決 (`idmap.py`: netkeiba→JRA-VAN は `id_mappings` 経由、未マップは surrogate `nk:` + UNMAPPED キュー)、race_id 構築 (`venues.py`: `build_race_id`、JRA-VAN race_id=`YYYYVVKKDDRR`、会場コードは netkeiba と恒等)、DB 書き込み (`upsert.py`: races/race_horses/race_results、JRA-VAN 結果は INSERT-only 保護、pre-race odds は result-pending のみ上書き) はいずれも本物として動作する。

一方、**parse 層 (`parse/entries.py` / `odds.py` / `results.py` / `exotic_odds.py`) はテスト用に発明した合成 HTML スキーマ (`div.race[data-year/data-track/...]`, `tr.horse[data-horse-id/...]`) 前提のスタブ**であり、実 netkeiba の HTML を一度も解析できない。008 spec は FR-013 / SC-007 で「保存済み HTML フィクスチャでネットワーク非依存にテストできること」しか要求しておらず、合成フィクスチャでその弱い要件を満たしていた。結果として scrape (008) およびそれに依存する live serving (019) は実 netkeiba では機能しない。

本 feature はこの唯一の実害スタブ (parse 層) を、実 netkeiba HTML を正しく解析する本物のパーサに置換し、既存の取得・ID 解決・書き込みの本物の配管へ接続する。これにより live serving (019) と将来の RaceFront からの「1日分実データ更新」が実際に動作する土台を完成させる。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 出走表を実 netkeiba から取り込む (Priority: P1)

オペレーターが、これから行われる (結果未確定の) レースの出走表 netkeiba ページを指定すると、実ページの HTML が解析され、未来 race (race_id / 開催日 / 開催場 / 距離 / 馬場種別) と出走馬 (枠 / 馬番 / 馬 ID / 騎手 / 調教師 / 性齢 / 斤量 / 出走状態) が既存テーブル (`races` / `horses` / `jockeys` / `trainers` / `race_horses`) に取り込まれる。

**Why this priority**: 出走表は予測の入力 (特徴量生成の母集団) であり、これ無しには予測も推奨も生成できない。実 netkeiba 連携の最小の価値はここにある。これ単独で「未来レースの予測を回せる」状態になる。

**Independent Test**: 保存した**実 netkeiba 出走表 HTML フィクスチャ**を `FixtureFetcher` 経由で取り込み (ネットワーク非依存)、未来 race と出走馬が正しいフィールドで DB に入り、マッピング済み馬は canonical_id、未マッピングは surrogate `nk:` で記録され、UNMAPPED キューに積まれることを検証する。

**Acceptance Scenarios**:

1. **Given** 実 netkeiba 出走表ページの保存 HTML, **When** 取り込みを実行, **Then** 有効な JRA-VAN 12桁 race_id を持つ未来 race と全出走馬が `races`/`race_horses` 等に取り込まれる。
2. **Given** 出走取消・除外を含む出走表, **When** 取り込み, **Then** 各馬の出走状態 (entry_status) が正しく区別されて記録される (取消馬を出走として誤記録しない)。
3. **Given** 2007 年より前 / JRA 以外の開催で有効な race_id を構築できないページ, **When** 取り込み, **Then** 行を書き込まず skip し、`ingestion_jobs` に記録する (偽 ID を作らない)。

---

### User Story 2 - 結果を実 netkeiba から取り込む (Priority: P2)

オペレーターが、確定した (結果が出た) レースの結果 netkeiba ページを指定すると、実ページが解析され、各馬の着順・競走状態・タイムが `race_results` に INSERT-only で取り込まれる (既存の JRA-VAN 結果は上書きしない)。

**Why this priority**: 結果はバックテスト・評価 (007/011/016) と予測の答え合わせに必要。出走表 (P1) で予測を回せるようになった後、実績で評価するために要る。

**Independent Test**: 保存した実 netkeiba 結果 HTML フィクスチャを取り込み、`race_results` に着順・状態・タイムが入り、同一レースに既存結果がある場合は上書きしないことを検証する。

**Acceptance Scenarios**:

1. **Given** 実 netkeiba 結果ページの保存 HTML, **When** 取り込み, **Then** 出走各馬の着順・競走状態 (完走/中止/失格) が `race_results` に記録される。
2. **Given** 既に JRA-VAN 由来の結果がある race, **When** netkeiba 結果取り込み, **Then** 既存行は上書きされない (INSERT-only)。

---

### User Story 3 - 単勝オッズを実 netkeiba から取り込む (Priority: P3)

オペレーターが、結果未確定レースの単勝オッズ netkeiba ページを指定すると、実ページが解析され、各馬の単勝オッズ・人気が `race_horses.odds` に最新値で上書きされる (結果のある race は JRA-VAN 最終オッズ保護のため更新しない)。

**Why this priority**: 推奨 (EV/Kelly) は市場オッズを使うが、予測自体はオッズ無しで回る。出走表・結果より優先度は下。netkeiba オッズページは動的描画の懸念が最も大きい (下記前提参照)。

**Independent Test**: 保存した実 netkeiba オッズ HTML/データのフィクスチャを取り込み、result-pending race の `race_horses.odds` が更新され、結果のある race は更新されないことを検証する。

**Acceptance Scenarios**:

1. **Given** result-pending race の実 netkeiba 単勝オッズデータ, **When** 取り込み, **Then** `race_horses.odds` が最新値で更新される (スナップショット履歴は保存しない)。
2. **Given** 結果が確定済みの race, **When** オッズ取り込み, **Then** odds は更新されない (JRA-VAN 最終オッズ保護)。

---

### User Story 4 - 開催日からレース一覧を取得する (Priority: P4)

オペレーターが開催日 (YYYYMMDD) を指定すると、その日の全 race_id が netkeiba のレース一覧から列挙され、各レースの出馬表/結果/オッズ URL を生成して後続の取り込み (US1–US3) に渡せる。これにより「1日分まとめて取り込む」運用が、URL の手入力なしに成立する。

**Why this priority**: US1–US3 は個別 URL 指定前提で、日単位運用には race_id の事前列挙が要る (019 で「race_id→URL 自動逆引き」として deferred されていた)。予測・取り込みの母集団を作る前段だが、個別 URL 指定でも回るため P4。

**Independent Test**: 保存した実 netkeiba レース一覧フラグメント HTML フィクスチャを `FixtureFetcher` 経由で解析し、その日の全 race_id が重複なく出現順に列挙され、12桁として無効な ID が除外されることを検証する (ネットワーク非依存)。

**Acceptance Scenarios**:

1. **Given** 開催日のレース一覧フラグメント HTML, **When** 列挙を実行, **Then** その日の全 race_id が重複排除・出現順で返り、各 race の出馬表/結果/オッズ URL を構築できる。
2. **Given** JRA 開催の無い日付のフラグメント, **When** 列挙, **Then** 空のリストが返る (エラーにしない)。一方、ペイロード自体が空/取得失敗なら fail-close する。

---

### User Story 5 - 馬のプロフィール (識別・血統) を補完する (Priority: P5)

オペレーターが明示的に補完を起動すると、surrogate (`nk:`) で取り込まれた馬の db.netkeiba.com プロフィールページが取得され、**識別・血統属性のみ** (性別 / 生年 / 父・母・母父の ID と名) が `horses` の **NULL 列だけ** に補完される (既存 JRA-VAN 値は上書きしない)。通算成績・賞金等の競走成績は **一切読み込まない** (リーク境界)。

**Why this priority**: 血統/性別/生年は予測特徴量 (036 系) の入力になり得るが、出走表取り込み (US1) だけで予測は回る。補完は任意・後追いで足り、既定では起動しないため最も低い P5。

**Independent Test**: surrogate 馬 1 頭を DB に置き、保存した実 horse プロフィール HTML フィクスチャから 性別/生年/血統が NULL 列に補完され、既存の非 NULL 値が保持され、競走成績フィールドが解析結果に存在しないことを検証する。

**Acceptance Scenarios**:

1. **Given** 識別・血統が NULL の surrogate 馬と保存プロフィール HTML, **When** 補完を起動, **Then** 性別/生年/父・母・母父が `horses` に書かれ、`ingestion_jobs` に job_type='horse_profile' で記録される。
2. **Given** 性別等が既に入っている馬, **When** 補完, **Then** 既存値は上書きされず、NULL 列のみ埋まる。
3. **Given** プロフィールページ, **When** 解析, **Then** 通算成績・賞金・近走着順は解析結果に含まれない (leak-guard)。

---

### Edge Cases

- netkeiba の HTML 構造が変化し必須要素が取得できない場合は **fail-close** (誤データを作らない) し、`ingestion_jobs` に errors を記録する。部分的に取得できた場合も、必須要素を欠く行は書かず errors に計上する。
- 同一馬名でも netkeiba ID が JRA-VAN にマッピングされていない場合は surrogate `nk:` を用い、推測結合しない (デビュー馬・新規エンティティ対応)。
- 出走表ページにオッズ列が含まれていても、それを結果・特徴量として扱わない (リーク境界・責務分離)。
- ページが想定と異なる種別 (例: 地方競馬・海外) で有効な JRA-VAN race_id を構築できない場合は skip。
- 文字エンコーディング・全角/半角・タイム表記 (例: `1:34.5`) の正規化を行う。
- レース一覧 (US4) は JS 描画の top ページではなく、同等のサーバ描画フラグメント (`top/race_list_sub.html?kaisai_date=`) を静的取得して race_id を抽出する (FR-013 のハイブリッド方針と整合)。過去日のフラグメントは `result.html`、未来日は `shutuba.html` へリンクするが、抽出は `race_id=` を対象とするためどちらでも成立する (実 markup で確認)。
- プロフィール補完 (US5): 馬本体ページ (`/horse/{id}/`) は 馬名/性別/生年は静的取得できるが**血統ブロックは JS 描画 (空 `#horse_pedigree_box`)** のため、血統は専用のサーバ描画ページ `/horse/ped/{id}/` (`table.blood_table`, 父系=`td.b_ml`/母系=`td.b_fml`) から取得する (実 markup で確認、2 ページ取得)。母父が版面差で取れない場合は当該項目のみ None (Unknown) とし推測しない。
- db.netkeiba.com は **EUC-JP かつ Content-Type に charset ヘッダ無し**のため、取得層は本文 `<meta charset>` を見て明示デコードする (UTF-8 の race.netkeiba.com は無影響)。これを怠ると文字化けする (実 markup で確認)。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: システムは**実 netkeiba の出走表ページ HTML** を解析し、race と出走馬の各フィールド (race_id 構築に必要な 開催年/会場/回/日次/レース番号、開催日、距離、馬場種別、および 枠/馬番/馬ID/騎手ID/調教師ID/性齢/斤量/出走状態) を抽出できなければならない (MUST)。
- **FR-002**: システムは**実 netkeiba の結果ページ HTML** を解析し、各馬の着順・競走状態・タイムを抽出できなければならない (MUST)。
- **FR-003**: システムは**実 netkeiba の単勝オッズ**データを解析し、各馬の単勝オッズと人気を抽出できなければならない (MUST)。
- **FR-004**: 抽出結果は既存の取得層 (`HttpFetcher`)・ID 解決 (`id_mappings` 経由、未マップは surrogate `nk:` + UNMAPPED キュー)・race_id 構築 (`build_race_id`)・DB 書き込み (`upsert.py`) に接続しなければならない (MUST)。新たな DB スキーマ変更は行わない (MUST NOT)。
- **FR-005**: netkeiba の 馬/騎手/調教師 ID は `id_mappings` 経由でのみ JRA-VAN と対応付け、推測結合してはならない (MUST NOT)。未来 race は有効な JRA-VAN 12桁 race_id を構築できる場合のみ書き込み、構築不能なら行を書かない (MUST)。
- **FR-006**: 必須要素を取得できない場合、システムは fail-close し誤データを作らず、`ingestion_jobs` に job 種別・scope・件数・status・時刻・parser/logic バージョン・errors を記録しなければならない (MUST)。
- **FR-007**: 単勝オッズ取り込みの書き込みモードは race の状態で分岐する。result-pending な race は最新値で**上書き**する (MUST、スナップショット履歴は保存せず `updated_at` のみ)。result-finalized な race は **odds が NULL の馬にのみ補完**し、既存 (JRA-VAN 由来含む) の odds 値は上書きしてはならない (MUST NOT)。これにより netkeiba 単独の確定済みレースでも確定単勝オッズ・人気を取得でき (netkeiba 確定オッズ JSON は upcoming/finished 双方を返す)、かつ JRA-VAN 最終オッズは保護される (旧「結果ありレースは一律スキップ」を 2026-06-28 に改訂)。
- **FR-008**: 結果の取り込みは INSERT-only とし、既存 (JRA-VAN 由来含む) の結果行を上書きしてはならない (MUST NOT)。
- **FR-009**: netkeiba から取得した odds・結果は、モデルの入力特徴量に再投入してはならない (リーク境界、MUST NOT)。この不変条件は leak-guard テストで担保する (MUST)。
- **FR-010**: パーサは**保存済みの実 netkeiba HTML フィクスチャ**に対する単体テストでネットワーク非依存に検証できなければならない (MUST)。フィクスチャは実ページ由来とし、合成 data-* スキーマを置換する (MUST)。
- **FR-011**: 取得は netkeiba の robots.txt とレート制限 (既存 1秒/ドメイン間隔) を遵守し、個人利用の範囲で礼儀正しく行わなければならない (MUST)。利用規約上スクレイプが許容されない場合は取得しない方針を明記する。
- **FR-012**: 既存スタブパーサ (合成スキーマ前提) は実パーサへ**置換**し、合成フィクスチャに依存した既存テストは実フィクスチャベースへ更新しなければならない (MUST)。実パーサは単一経路とし、移行期間の並存は行わない (決定: 置換)。**対象は entries / results / 単勝 odds の 3 パーサに限る**。exotic odds パーサ (`parse/exotic_odds.py`) は本 feature 対象外で合成のまま残置する (次段)。
- **FR-013**: システムは netkeiba の動的描画ページに対して必要なデータを確実に取得できなければならない (MUST)。取得方式は**ハイブリッド**とする (決定): 出走表 (entries) と結果 (results) は**サーバ描画 HTML を静的取得して解析**し、単勝オッズ (odds) は **netkeiba 内部の JSON データ (埋め込み JSON ないし JSON エンドポイント) を利用**する。headless ブラウザ (Playwright 等) は導入しない。実 netkeiba ページの構造を実サンプルで確認したうえで、この方式の妥当性を plan で検証する (構造が想定と異なる場合は plan 段で方式を見直す)。
- **FR-014** (US4, 追補): システムは開催日 (YYYYMMDD) を指定して、その日の全 race_id を列挙できなければならない (MUST)。取得は JS 描画の top ページではなく**サーバ描画フラグメントを静的取得**して race_id を抽出する (FR-013 整合)。列挙は重複排除・出現順とし、12桁として無効な ID を除外する (MUST)。列挙自体は読み取り専用で、コア表を変更しない (MUST NOT)。JRA 開催の無い日は空リストを返し (エラーにしない)、ペイロードが空/取得失敗なら fail-close する (MUST)。
- **FR-015** (US5, 追補): システムは馬の db.netkeiba.com から**識別・血統属性のみ** (性別 / 生年 / 父・母・母父の ID・名) を抽出し、`horses` の **NULL 列のみ**に補完できなければならない (MUST)。識別 (馬名/性別/生年) は本体ページ `/horse/{id}/` から、血統は専用ページ `/horse/ped/{id}/` (サーバ描画 `blood_table`) から取得する (本体ページの血統は JS 描画のため、FR-013 整合で 2 ページ静的取得)。既存値 (JRA-VAN 由来含む) を上書きしてはならない (MUST NOT)。血統馬 ID も `id_mappings` 経由で解決し推測結合しない (MUST NOT)。補完は既定で起動せず、オペレーターの明示起動でのみ実行する (opt-in, MUST)。`ingestion_jobs` に job_type='horse_profile' で記録する (MUST)。騎手・調教師は補完すべき列が無く対象外とする。
- **FR-017** (US5, 追補): 取得層は対象ページの文字エンコーディングを正しく扱わなければならない (MUST)。Content-Type に charset が無い場合は本文の `<meta charset>` を見て明示デコードする (db.netkeiba.com は EUC-JP・charset ヘッダ無し)。これにより取り込み文字列の文字化けを防ぐ (MUST)。
- **FR-018** (追補): 出馬表から 斤量 (jockey_weight)・馬体重増減 (weight_diff)、結果から 上がり3F (last_3f)・コーナー通過順 (corner_orders) を抽出して既存列に永続化しなければならない (MUST、スキーマ変更なし)。着差 (finish_time_diff, interval) は netkeiba の馬身差表記でなく**各馬の絶対タイムから勝ち馬との差を算出**して埋める (JRA-VAN の秒数 interval と整合)。脚質 (running_style) は netkeiba に専用列が無いため**コーナー通過順の初角位置×頭数から JRA-VAN 語彙 (逃げ/先行/中団/差し/追込) に導出**し、NULL の場合のみ補完する (既存 JRA-VAN 値は上書きしない、ヒューリスティック明示)。これらは 023 のペース/時計特徴 (features-006) の入力であり、netkeiba 取り込みレースの予測品質を JRA-VAN と揃える。
- **FR-019** (追補): 出馬表からレース名 (race_name)・グレード (grade=G1/G2/G3)・発走時刻 (post_time, JST) を抽出して既存列に永続化しなければならない (MUST)。grade アイコンは**当該レースの `.RaceName` 要素内に限定**して読む (ページ全体検索は nav/sidebar の他レースのグレードアイコンを誤検出する — 実レースで未勝利が G3 と誤判定される回帰を確認済み)。
- **FR-016** (US5, 追補): プロフィール補完は**競走成績 (通算成績・勝率・賞金・近走着順等) を一切読み込んでも保存してもならない** (リーク境界、MUST NOT)。解析結果の型に成績フィールドが存在しないことで構造的に担保し、leak-guard テストで検証する (MUST)。

### Key Entities *(include if feature involves data)*

- **出走表 (entries) の解析結果**: 未来 race のメタ (開催年/会場/回/日次/レース番号 → race_id、開催日、距離、馬場種別) と出走馬の集合 (枠/馬番/netkeiba馬ID/馬名/騎手ID・名/調教師ID・名/性別/年齢/斤量/出走状態)。既存 `races`/`race_horses`/`horses`/`jockeys`/`trainers` に対応。
- **結果 (results) の解析結果**: race_id と各馬 (netkeiba馬ID) の 着順・競走状態 (完走/中止/失格)・タイム。既存 `race_results` に対応。
- **単勝オッズ (win odds) の解析結果**: race_id と各馬 (netkeiba馬ID) の 単勝オッズ・人気。既存 `race_horses.odds` に対応。
- **実 HTML フィクスチャ**: 実 netkeiba の出走表/結果/オッズページを保存したテスト用 HTML。ネットワーク非依存テストの基盤。
- **レース一覧の解析結果 (US4)**: 開催日 (YYYYMMDD) と、その日の race_id 列 (重複排除・出現順)。新テーブルなし (読み取り専用の列挙)。
- **馬プロフィールの解析結果 (US5)**: netkeiba 馬 ID・馬名・性別・生年・父/母/母父の (ID, 名)。既存 `horses` の識別・血統列に対応。**競走成績フィールドを持たない** (リーク境界)。
- **取り込みジョブ監査 (ingestion_jobs)**: 既存テーブル。job 種別 (entries/results/odds/exotic_odds/horse_profile)・scope・件数・status・parser/logic バージョン・errors。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 保存済みの実 netkeiba 出走表 HTML フィクスチャから、未来 race と全出走馬が正しいフィールドで既存テーブルに取り込まれ、マッピング済みは canonical_id・未マッピングは surrogate で記録される (出走馬の取りこぼし 0、誤フィールド 0)。
- **SC-002**: 保存済みの実 netkeiba 結果 HTML フィクスチャから、出走各馬の着順・状態・タイムが `race_results` に取り込まれ、既存結果がある場合は上書きされない。
- **SC-003**: 保存済みの実 netkeiba 単勝オッズフィクスチャから、result-pending race の odds が更新され、結果のある race は更新されない。
- **SC-004**: 必須要素を欠く (構造変化を模した) フィクスチャに対し、システムは行を書かず fail-close し、`ingestion_jobs` に errors を記録する。
- **SC-005**: netkeiba 由来の odds・結果がモデル特徴量に現れないことを leak-guard テストで確認できる。
- **SC-006**: 全パーサテストがネットワーク非依存で完結する (テスト実行時に外部 HTTP を行わない)。
- **SC-007**: 実 netkeiba から取得したデータで予測 serving (006/019) がエラーなく予測を生成できる (出走表→特徴量→予測のエンドツーエンドが実データで成立)。
- **SC-008** (US4, 追補): 保存済みレース一覧フラグメントから、その日の全 race_id が重複なく出現順で列挙され、無効な ID が除外される。JRA 開催の無い日は空リスト、空ペイロードは fail-close する。
- **SC-009** (US5, 追補): 保存済み horse プロフィールから、surrogate 馬の 性別/生年/血統が NULL 列のみに補完され、既存の非 NULL 値は保持される。解析結果に競走成績フィールドが存在しないことを leak-guard テストで確認できる。

## Assumptions

- 取得層 (`HttpFetcher`)・ID 解決 (`idmap.py`)・race_id 構築 (`venues.py`)・DB 書き込み (`upsert.py`) は本物として再利用でき、本 feature では変更しない (取得方式の決定 FR-013 によっては取得層に追補が入りうる)。
- DB スキーマ変更は行わない。既存テーブルに取り込む (憲法 VI / 008 系踏襲)。
- exotic odds (複勝/馬連/馬単/ワイド/三連複/三連単) の実パーサは本 feature の対象外 (次段 deferred)。
- RaceFront 側の「更新」ボタン・write API は本 feature の対象外 (別 feature)。本 feature は CLI / 既存 pipeline 関数経由で取り込みを実行する。
- 自動スケジューリング・複数ソース・ログイン必須ページは対象外。個人利用・手動実行前提 (憲法 技術制約)。
- 実 netkeiba HTML サンプルは、本 feature 内で **polite 設定 (robots/rate-limit) のもと 1 回限りの取得を許容**して保存し、テストフィクスチャ化する (決定)。以後のテストはこの保存フィクスチャに対してネットワーク非依存で実行する。取得は entries/results/odds 各ページ種別につき必要最小限の件数に限る。

## Out of Scope

- DB スキーマ変更。
- exotic odds の実パーサ (次段)。
- RaceFront の write UI / write API (別 feature)。
- 自動スケジューリング、定期再取得、複数データソース、odds スナップショット履歴。
- ログイン/有料会員限定ページの取得。
- 着差 (`race_results.finish_time_diff`) の取り込み（finish_time のみ対象。着差は対象外）。
- 騎手・調教師のプロフィールページ補完 (補完すべき列が無いため対象外。US5 は馬の識別・血統のみ)。
- レース一覧 (US4) の起点となる開催日カレンダー自体の自動探索・期間一括巡回 (オペレーターが日付を与える前提)。
- プロフィール由来データを含む実ライブ取得の本番稼働 (US4/US5 の実 markup は本番前に `capture-fixture` で実ページ検証が必要)。

## 追補メモ (2026-06-28)

US4 (開催日レース一覧取得) と US5 (馬プロフィール識別・血統補完) は、本 feature の当初スコープ (entries/results/odds の実パーサ置換) の **後追い追補**として追加した。きっかけは外部設計ドキュメント (Obsidian `data-sources.md` / `scraping-netkeiba.md`) と実装の整合確認で、ドキュメントが挙げる「開催日一覧ページ」「未登録エンティティのプロフィール補完」が未実装と判明したため。Playwright / オッズ URL に関するドキュメント記述は本 spec の FR-013 (静的取得ハイブリッド、Playwright 不採用) と相違するが、これは**外部ドキュメント側が古い**ものであり、repo spec・実装の修正は不要 (外部ドキュメントは本リポジトリの管理対象外)。設計方針は codex の second opinion を取得済み (特に US5 のリーク境界 = 競走成績を読まない方針)。

**データ網羅性パス (2026-06-28)**: 実レース 1 件 (202505040301、東京 未勝利、10頭) を end-to-end 取り込み中に未取り込み列を洗い出し、entries/results パーサを完全化した (全て既存列・スキーマ変更なし)。実レースで全列が埋まることを確認:
- 斤量 (jockey_weight)・馬体重増減 (weight_diff) — entries パーサで抽出 (FR-018)。斤量は FR-001 が既に要求していた取りこぼしの解消。
- 単勝オッズ・人気 — odds ルールを FR-007 改訂で確定済みレースも fill-if-null 補完 (確定オッズ JSON が finished も返す)。
- 着差・上がり3F・通過順 (race_results) + 脚質 (race_horses, 導出) — results パーサで抽出/算出 (FR-018)。**023 ペース/時計特徴 (features-006) の入力**で、これが無いと netkeiba レースは識別力が落ちる。着差は絶対タイム差で算出 (JRA-VAN 秒 interval 整合)、脚質は通過順初角位置から JRA 語彙へ導出 (fill-if-null)。
- レース名・グレード・発走時刻 (races) — entries パーサで抽出 (FR-019)。grade は `.RaceName` 内限定 (ページ全体検索で未勝利が G3 と誤判定される回帰を発見・修正)。
- 残: real exotic odds (D, 合成スタブのまま=別段)、race_name_short / race_status (軽微)。

**実 netkeiba selector 検証 (2026-06-28 実施)**: polite fetcher で実ページを最小限取得し US4/US5 のパーサを実 markup で検証。結果フィクスチャを `scrape/tests/fixtures/real/` に保存 (race_list_20241228 / horse_profile_2022103995 / pedigree_2022103995) し、ネットワーク非依存テストに組み込んだ。検証で判明した実装修正 = (a) **db.netkeiba.com は EUC-JP・charset ヘッダ無し**で文字化け → 取得層に meta charset デコードを追加 (FR-017)、(b) 馬本体ページの**血統は JS 描画** (空 `#horse_pedigree_box`) → 血統は `/horse/ped/{id}/` のサーバ描画 `blood_table` から取得する 2 ページ方式へ、(c) 実 blood_table の class は `b_ml`(父系)/`b_fml`(母系) で母父は母セル後の最初の `b_ml` (当初の `b_fl` 仮定は誤り)。識別 (馬名/性別/生年) と一覧抽出は当初 selector のまま実 markup で成立。
