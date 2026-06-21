# Research: JRA-VAN 過去データ取込 (2007+)

Phase 0 調査。実データ (`raw_data/jra-van/2007`, Shift_JIS, 73列, ~49,009行) を解析して列レイアウトと
正規化規則を確定する。確定不能・スキーマ外の列は「読み飛ばし」とし、後続で非破壊拡張する。

## R1. 73列レイアウト (1-indexed)

`2007` の実データから確定。**[使用]** = 本 feature でコアテーブルにマップ、**[skip]** = スキーマ外で
読み飛ばし (将来拡張)、**[要検証]** = golden fixture で値域・意味を確定。

| # | 内容 | 例 | 区分 → マップ先 |
|---|---|---|---|
| 1 | 年(2桁) | 07 | [使用] raceId 導出補助 |
| 2 | 月 | 08 | [skip] |
| 3 | 日 | 11 | [skip] |
| 4 | レース日 (YYYY.M.D) | 2007.8.11 | [使用] races.race_date |
| 5 | 開催回 (kai) | 1 | [使用] raceId 導出 |
| 6 | 開催場名 | 札幌 | [使用] races.venue_code (R3 の表) |
| 7 | 日目 (nichime) | 1 | [使用] raceId 導出 |
| 8 | レース番号 | 1 | [使用] races.race_number / raceId 導出 |
| 9 | レース名(短)+記号 | 未勝利* | [使用] races.race_name_short |
| 10 | レース名(全, 全角パディング) | (空白) | [使用] races.race_name (trim) |
| 11 | クラス | 未勝利 | [使用] races.race_class |
| 12 | 競走条件コード | 7 | [skip] |
| 13 | グレード | (空) | [使用] races.grade (空=null) |
| 14 | 芝/ダート | 芝 | [使用] races.track_type |
| 15 | 内外回りコード | 0 | [skip] |
| 16 | (未確定) | 17 | [skip] |
| 17 | (未確定) | 3 | [skip] |
| 18 | 距離(m) | 1500 | [使用] races.distance |
| 19 | コース区分 | A | [skip] (馬場詳細) |
| 20 | 馬場状態 | 稍 | [使用] races.going |
| 21 | 天候 | 曇 | [使用] races.weather |
| 22 | (未確定) | 12 | [skip] |
| 23 | 頭数 | 14 | [skip] (出走数は集計で導出可) |
| 24 | (賞金等) | 500 | [skip] |
| 25-30 | タイム指数/記録系・競走種別 | 48.8.. / 3 | [skip] |
| 31 | 18桁レース馬ID | 200708110101010101 | [要検証] raceId cross-check |
| 32 | 枠番 | 1 | [使用] race_horses.frame |
| 33 | 馬番 | 1 | [使用] race_horses.horse_number |
| 34 | 馬名 | テーオーブラック | [使用] horses.horse_name |
| 35 | 性 | 牡/牝/セ | [使用] horses.sex, race_horses.sex |
| 36 | 齢 | 2 | [使用] race_horses.age |
| 37 | 騎手名 | 北村友一 | [使用] jockeys.jockey_name |
| 38 | 斤量 | 53.0 | [使用] race_horses.jockey_weight |
| 39 | ブリンカー等フラグ | (空)/B | [skip] (将来) |
| 40 | 着順 (0=非完走) | 3 / 0 | [使用] race_results.finish_order + 状態判定 (R4) |
| 41 | 着差 (----=非完走) | 0.3 / ---- | [使用] race_results.finish_time_diff |
| 42 | 人気 | 5 | [使用] race_horses.popularity (結果確定時) |
| 43 | 単勝オッズ | 16.2 | [使用] race_horses.odds (結果確定時, R5) |
| 44 | (指数) | 89.9 | [skip] |
| 45 | 走破タイム (M.SS.s) | 1.29.9 | [使用] race_results.finish_time |
| 46-47 | (未確定) | (空) | [skip] |
| 48-51 | 通過順 (各コーナー) | 0,2,3,3 | [使用] race_results.corner_orders |
| 52 | 脚質 | 先行 | [使用] race_horses.running_style |
| 53 | 上がり3F | 36.1 | [使用] race_results.last_3f |
| 54-56 | (指数/補正) | 3 / 35.87 / 49.4 | [skip] |
| 57 | 馬体重 | 442 | [使用] race_horses.weight |
| 58 | 馬体重増減 | -14 | [使用] race_horses.weight_diff |
| 59 | 調教師名 | 梅田智之 | [使用] trainers.trainer_name |
| 60 | 所属 (栗/美/地) | 栗 | [skip] (将来) |
| 61 | (コード) | 130 | [skip] |
| 62 | 血統登録番号 | 2005109144 | [使用] horses.horse_id |
| 63 | 騎手コード | 01102 | [使用] jockeys.jockey_id, race_horses.jockey_id |
| 64 | 調教師コード | 01084 | [使用] trainers.trainer_id, race_horses.trainer_id |
| 65 | 馬主名 | 小笹公也 | [skip] (将来 owners) |
| 66 | 生産者/牧場 | 一山牧場 | [skip] |
| 67 | 父名 | アラムシャー | [使用] horses.sire_name |
| 68 | 母名 | タイムレスジェム | [使用] horses.dam_name |
| 69 | 母父名 | Woodman | [使用] horses.damsire_name |
| 70 | 父系 | ニアークティック系 | [skip] |
| 71 | 母父系 | ネイティヴダンサー系 | [skip] |
| 72 | 毛色 | 黒鹿 | [skip] (将来) |
| 73 | 生年月日 (YYYYMMDD) | 20050419 | [使用] horses.birth_year (先頭4桁) |

- **Decision**: 上表の [使用] 列のみをマップ。[skip]/[要検証] は読み飛ばし or cross-check のみ。
- **Rationale**: 現行スキーマが必要とする列に限定し MVP を早く回す。血統 ID 列は存在せず名前のみ →
  `sire_id/dam_id/damsire_id` は null、名前を保存。
- **Alternatives considered**: 全73列を別テーブルに保存 → スキーマ変更が必要で本 feature 範囲外。

## R2. raceId 導出 (12桁 YYYYVVKKDDRR)

- **Decision**: `venue_to_code` は 2 文字ゼロ詰め文字列 (例 `"05"`) を返し、`races.venue_code` (Text) に
  そのまま格納する。`race_id = f"{year:04d}{venue_code}{kai:02d}{nichime:02d}{race_no:02d}"` (venue_code は
  既に 2 桁文字列、他は int を `:02d`)。year=col4 の YYYY、venue=col6→R3、kai=col5、nichime=col7、
  race_no=col8。導出後 `^[0-9]{12}$` を `validation.is_valid_race_id` で検証。col31 の 18桁 ID を
  cross-check に使う。
- **Rationale**: 個別列から決定論的に組める。18桁 ID 直接パースより堅牢 (桁割りの曖昧さを回避)。
- **A–F 拡張 (実データで判明)**: meeting-position フィールド (kai/nichime/race_no) は値 >=10 を 1 文字
  `A`..`F` (A=10..F=15) で符号化する。2007 では nichime に 319 件の `A` (=開催10日目) が出現し、18桁 ID
  `...10...` と一致を確認。`_to_meeting_int` で A–F を解釈する (数字のみだと該当行を取りこぼす)。
- **Alternatives considered**: 18桁 ID を切り出す → 内部桁構成が未文書で誤りやすい。cross-check に留める。

## R3. 開催場名 → venue_code (標準 JRA 10 コース)

- **Decision**: 札幌=01, 函館=02, 福島=03, 新潟=04, 東京=05, 中山=06, 中京=07, 京都=08, 阪神=09,
  小倉=10。実データの distinct = {中京,中山,京都,函館,小倉,新潟,札幌,東京,福島,阪神} と一致 (10場)。
- **Rationale**: JRA 標準コードと完全一致。未知の場名はエラー化 (地方・海外混入の検知)。
- **Alternatives considered**: なし (標準確定)。

## R4. 状態正規化 (最大リスク — golden fixture でロック)

- **Decision**:
  - `finish_order (col40) >= 1` → 完走: `result_status='finished'`, `entry_status='started'`。
  - `finish_order == 0` → 非完走。走行データ (通過順 col48-51 が非0 / 走破タイム / 単勝オッズ) の有無で:
    - **走行あり (DNF)**: `entry_status='started'` + `race_results` 行を作成、`result_status='stopped'`
      (既定) または失格指標があれば `'disqualified'`。`finish_order` は NULL とする (0 を入れない)。
    - **走行なし (DNS)**: `entry_status='cancelled'` (既定) または除外指標があれば `'excluded'`、
      `race_results` 行は作らない (INV-1)。
  - **同着**: 同一 `finish_order` を共有 (col40 が同値)。
  - **未知/曖昧**: 黙って finished にせず、行をエラーとして `ingestion_jobs` に記録 (FR-012)。
- **Rationale**: 実データで finish_order=0 が非完走を表すこと、走行データ有無で DNS(325)/DNF(137) が
  分離できることを確認済み。学習ラベルは finished のみ (labels.derive_labels) なので、最低限
  finished/非finished の境界が正しければラベル汚染は起きない。
- **未確定 (golden fixture でロック)**: 取消 vs 除外、競走中止 vs 失格 を一意に決める「異常区分」列は
  単一列として未特定。当面 DNS→cancelled / DNF→stopped を既定とし、失格・除外の判別指標が確定したら
  4分類に精緻化する。確定するまで 4分類の細分は限定的だが、finished/DNF/DNS の 3区分は保証する。
  実装時に取消/除外/中止/失格/同着を含む golden fixture で期待値を固定する。
- **Alternatives considered**: finish_order=0 を疑似最下位に変換 → 憲法・spec で禁止。

## R5. オッズ/人気の provenance (リーク防止)

- **Decision**: col43 単勝オッズ・col42 人気は「結果確定時」値。`race_horses.odds`/`popularity` に保存
  するが、data-model/research に「発走前特徴量に使用不可」と明記。行レベルの provenance 列は作らない
  (FR-018: スキーマ変更なし、ソース単位で一様)。
- **Rationale**: JRA-VAN 過去データのオッズは確定時値で一様。リーク防止の強制は特徴量 feature の責務
  (憲法 II)。本 feature はソース由来の意味論をドキュメントで固定する。
- **Alternatives considered**: 専用フラグ列追加 → Feature 001 契約へのスキーマ変更が必要で範囲外。

## R6. パース戦略

- **Decision**: 標準ライブラリ `csv` + `open(path, encoding='cp932', errors='strict')` で行ストリーム。
  各行は 73 フィールド固定を検証し、不一致・デコード不能は行番号付きでエラー記録。バッチ (例 1000 行)
  で upsert。pandas 不使用。
- **Rationale**: 49k×19年 を低メモリでストリーム。cp932 は Shift_JIS の上位互換で機種依存文字も扱える。
  errors='strict' で破損を検知 (黙って欠落させない)。
- **Alternatives considered**: pandas read_csv → 型推論・欠損変換が暗黙化しリスク。手書き split →
  クォート処理を誤る。

## R7. upsert と冪等性

- **Decision**: SQLAlchemy の PostgreSQL `INSERT ... ON CONFLICT DO UPDATE` を使い、PK 上で upsert。
  順序: races → horses/jockeys/trainers → race_horses → race_results。同一年再取込で重複なし。
- **Rationale**: PK (race_id / horse_id / 複合) が冪等性を保証。FK 順で整合。`updated_at` は Feature 001
  のトリガが更新。
- **Alternatives considered**: 事前 SELECT して分岐 → 競合と往復増。ON CONFLICT が簡潔・堅牢。

## R8. CLI と監査・再開

- **Decision**: `argparse` で `ingest-year <path>` と `ingest-all <dir>`。年ファイル単位に
  `ingestion_jobs` 行を作り、status (running→succeeded/failed/partial)、processed 行数、checkpoint
  (処理済み行番号)、error を記録。失敗後の再開は checkpoint 以降のみ処理 (upsert なので重複無害)。
- **Rationale**: 手動オペレーション。年単位の監査が自然。upsert 冪等なので checkpoint は最適化であり
  正しさは upsert が担保。
- **Alternatives considered**: Typer → 依存追加。argparse で十分。

## R9. テスト戦略

- **Decision**: golden fixture (小さな SJIS CSV、数レース、取消/除外/中止/失格/同着を含む) を repo に
  含め、parser/mapping/status をユニット、取込→testcontainers PG を統合。実データ全年取込はスモーク
  (任意・ローカル)。
- **Rationale**: 実データは gitignore で CI 不可。golden fixture で決定論的に検証。状態正規化は fixture
  で期待値固定 (最大リスクの封じ込め)。
- **Alternatives considered**: 実データ依存テスト → 再現性なし。
