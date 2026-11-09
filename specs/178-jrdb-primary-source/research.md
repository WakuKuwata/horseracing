# Research: 178 JRDB を主情報源にする

2026-11-08。spec の NEEDS CLARIFICATION と plan の設計判断を D1〜D15 に記録する。事実の出所は
(a) JRDB 公開仕様書 `https://jrdb.com/program/data.html` と各 `*_doc.txt`、(b) 公開サンプル 8 本を
ローカル DB(JRA-VAN 由来 2007〜2024)に突き合わせた実測(scratchpad `parity_samples.py` / `parity2.py`)、
(c) 会員規約 `https://jrdb.com/terms`(2023-03-01 改定)、(d) **会員ページ**(`/member/dataindex.html`・`/member/data/index.html`・
`/member/new_index.php`・各 `datazip/<Kind>/index.html`。2026-11-08 に利用者の Chrome(ログイン済み)で確認・D16)。

## D1. JRDB の各ファイルの中身・JRA-VAN との列対応(FR-004/005 の前提)

決定: 対応表は [contracts/files-and-ids.md §4](contracts/files-and-ids.md)。**JRDB は JRA-VAN の 48 列を全部カバーし、
不足は無い**。ただし次は同じ列に混ぜない: `first_3f`(1200m 以外は別定義=中央値 +0.3〜0.5 秒・最大 2.6 秒ずれ)、
`corner_orders`(4 枠に先頭側から詰める・値も先頭の角で ±1)、`running_style`(語彙が違う: JRDB 3 が DB の 中団/先行/差し に割れる)、
`sire_line/damsire_line`(分類体系が違う)。騎手・調教師コードは **JRDB 独自**で JRA コードと一致しない(D3)。

根拠(2008-09-13 の 324 出走行・2015-07-12 の 498 行・2024-09-29 の 24 レース):
- 結合(レースキー→race_id・血統登録番号 8→10 桁): 324/324・498/498。馬名 324/324。
- 着順・タイム・斤量・確定単勝オッズ・馬体重: 324/324 一致。人気 323/324(JRDB 0 ↔ DB NULL)。
- 後 3F: 310/324(残り 14 は DB 側の異常値 13.8 など)。前 3F: 1200m 101/101、他距離 10% 未満。
- 払戻 単勝(SED 342 桁)= 確定単勝オッズ×100: 24/24。
- KYI 枠番 498/498・性別 498/498。負担重量は見習い騎手の 5 行で JRDB 51.0 ↔ DB 54.0(どちらが JRA の公表値か**要確認**)。
- KYI 枠確定馬体重は 2015 のサンプルで 0/498(空)=前日版には入っていない。当日体重は TYB。

JRDB の 1 着賞金は BAC(レース)。SED の本賞金は馬が得た額なので `races.prize_money` に写さない。

Alternatives: 「不足が無い」ことを仕様書の読み合わせだけで判断する → サンプルの実測で前 3F と騎手コードの不一致が
見つかったので、読み合わせだけでは足りない。段 1 の parity は全期間で同じ手順を回す。

## D2. レコードレイアウトと版

決定: 固定長・cp932・CRLF、相対 1 始まり、TYPE 9/Z/X/F、空白=None、0.1 単位は 10 で割る、`XZ9` の符号つきは空白除去。
decoder は**版ごと**(KYG 2004〜/KYH 2008-09〜/KYI 2015-01〜、SEC 2002-04〜/SED 2008-09〜、SRB 2004-05〜、SKB 2001〜、
HJC 2000〜、UKC 2000〜、BAC 1998〜、KZA/CZA 2001〜、TYB 1999〜)にレイアウト表を持ち、ファイル名の版文字で選ぶ。
行長が仕様と違えばその行は書かず件数報告(fail-closed)。レイアウト表は公開仕様書の**転記**で、テストは
公開サンプルの既知の行(馬名・着順・タイム)を期待値にする(サンプルは公開物なので fixture に置ける。会員配信物は置かない=D6)。

注意点(仕様書より): KYI 第 11 版は予備を大きく取り「今後はレコード長を変えない」宣言。SED 第 4 版a(2022-08)で末尾に
発走時間が追加(それ以前のアーカイブは空=BAC の発走時間を使う)。HJC 3 連単は 9 桁。TYB は場別上書きファイル
(`TYB_E/W/L.txt`)と日次累積(`TYB+yymmdd`)の 2 種。SED 速報版(当日 18:00)は確定オッズ・馬場・通過順・前後 3F・脚質が空。

## D3. ID の写像

決定: 馬=血統登録番号の 8→10 桁(年 2 桁はレース年以下なら 2000 年代、超えれば 1900 年代。UKC 生年月日で検証)、
レース=レースキー 8 文字→12 桁(年補完・日 16 進・JRA 10 場のみ)。**騎手・調教師は JRDB 独自コード**なので、
KZA/CZA の名前+生年月日+所属+初免許年を材料に `id_mappings`(entity=jockey/trainer・source=`jrdb`)を作り、
名前だけの一致は conflict(手動)に送る(憲法 I: 推測結合しない)。DB の `jockeys`/`trainers` には名前しかないので、
**正規 ID に直接ひも付く属性**を JRA 公式サイトの騎手/調教師ページ(`pw04kmk0<5桁>` / `pw05cmk0<5桁>`=URL が正規 ID そのもの)から
取り(176 の `JraFetcher`・10 秒間隔・約 1,500 人で 1 回限り)、生年月日(必須)+名前(照合キー)で KZA/CZA と突き合わせる。
所属と初免許年は時点依存なので補助(不一致は conflict 理由に書く)。netkeiba プロフィールは正規 ID との対応証拠が無いので使わない。
未解決の人は出走行の `jockey_id`/`trainer_id` を NULL にして来歴に `unresolved_person` とし、**不足として数える**(D15 #2)。

根拠: SED 2008 の騎手コード 10446 ↔ DB 01019、KYI 2015 の 10390 ↔ 00700、調教師 10379 ↔ 01069 で**全行不一致**。
KSA 2002 サンプルの 岡部幸雄(JRDB 10076・生年月日 1948-10-31)は DB 00140 に名前で一致。

Alternatives: 名前で自動結合 → 同姓同名(例: 2 人の「横山」系)で誤結合しうるので不採用。JRA-VAN の 73 列 CSV に
JRDB コードは無いので、既存データから対応表を自動生成する道は無い。

## D4. LZH 展開

決定: 純 Python の LZH 展開(`lhafile` 系)を scrape の依存に足す。無ければ `zip` 版を優先(KYI は zip 推奨版あり)。
外部コマンド `lha`/`lhasa` は macOS に無く(実測)、運用者の環境依存になるので使わない。展開後のテキストは
`artifacts/jrdb/<yyyymmdd>/<name>.txt` に保存し、sha256 を `jrdb_files` に記録する。

## D5. 規約(FR-018)

事実(会員規約 2023-03-01): 第 3 条 個人利用が前提・営利販売禁止・法人は営利とみなす。第 5 条 情報は JRDB の著作物で
私的利用の範囲を超える複製・公表・第三者への使用許諾を禁止(ホームページ等での公開も禁止)。第 8 条 ID/パスワードの
貸与禁止・業務妨害禁止。公開ページ「レース当日のデータアクセス制限」(2025-07-28): **開催日 08:00〜19:00 は昨年以前の
過去データの DL を制限**(過去データの整備は週中に)。自動取得・DB 保存・解約後保持の**明文は無い**。

決定: 本システムはローカル・単一利用者・非公開(admin も 127.0.0.1 のみ)なので「私的利用」に収まると判断し、
(1) 配信ファイルと由来の値を**公開リポジトリ・fixture・成果物に置かない**(D6)、(2) 取得は会員 URL への
**1 ファイル 1 要求・固定間隔・同時 1**、(3) 過去データの取得は**週中のみ**(policy で開催日 08:00〜19:00 を拒否)、
(4) 解約後の保持は規約に明文が無いので**利用者が JRDB に確認**する(契約前の NEEDS CLARIFICATION として残す)。
front/admin は JRDB 由来の値を表示しうるが、ローカル専用である限り「公開」ではない。将来の公開(deploy 018)を
するなら JRDB 由来の列を外す判断が要る=spec の限界に記録。

## D6. fixture の扱い

決定: テスト fixture は (a) 公開サンプル(`/program/*/<種別>yymmdd.txt`・ログイン不要で配布)と (b) 合成行だけ。
会員配信のファイルは `artifacts/jrdb/`(gitignore)に置き、テストから読まない。176 の「fixture は raw のまま・cname は
sha だけ」と同じく、会員 URL やパスワードはコード・テスト・ログに平文で書かない。

## D7. writer の配置と規則表の拡張(FR-007)

決定: `scrape/src/horseracing_scrape/jrdb/` に fetch→decode→薄い upsert→CLI(176 の `jra/` と同形)。規則の正本は
`precedence.py` のまま、`field_transition`/`odds_decision`/`_insert_results`/払戻の集合置換に **source=jrdb の列**を足す。

| 項目群 | JRDB の立場 |
|---|---|
| 出走行・マスタ行の生成 | **JRDB が責任者**(KYI/UKC)。公式・netkeiba は既存行に書く(緊急モードは D8) |
| entry_static / race_static | JRDB は JRA-VAN と同格の「正」。stored が jra_official/jrdb のとき netkeiba は keep。公式と JRDB の不一致は `static_mismatch` で報告し**先に書いた方を保持** |
| entry_status(取消/除外) | 一方向遷移は共通。JRDB KYI 取消フラグ・SED 異常区分も入力になる |
| jockey / trainer | JRDB は写像済みの ID だけ書く(未解決は NULL+来歴) |
| body_weight | TYB(当日)は公式と同じ時刻規則。SED(事後)は結果確定後の確定値として write |
| post_time | BAC が全期間の唯一の供給元(DB は 2026 以前 NULL)。公式が来たら公式を優先 |
| win_odds / place_odds(発走前) | **JRDB は書かない**(TYB の単複は T−15 の値で、判断記録にも現在値にも使わない。基準オッズは市場値ではない) |
| 確定単勝オッズ・人気 | SED 正式版から 139 の `apply_final_odds` と同じ規則(値が変わる行だけ・公式払戻と整合) |
| results | `race_atomic`(公式と同じ)。速報版は欠けが多いので**正式版だけ**を results の入力にする(速報は着順の先出しに使わない) |
| laps | SRB → `race_laps`(netkeiba 034 と同じ表・source=jrdb) |
| win_payouts / exotic_payouts | HJC → 公式と同じ集合置換。公式が先なら同値確認で採用元は公式のまま |

順序不変を要求するのは決定論で選ぶ項目だけ(spec FR-007)。

## D8. 生成責任の移管と FR-011a・緊急モード(FR-006/008)

決定: KYI 取込時に `nk:X` の既存行があり数字が一致すれば 176 FR-011a と同じ別名規則で既存行に書く(2 行にしない)。
正規 `X` のマスタが無い場合は**作らない**(nk: 行の統合は 067 系)。JRDB の当日ファイルが締切までに来ないときの緊急モードは、
176 の公式出馬表 parser(`parse_entries`)の出力から出走行とマスタ行を正規 ID で作る `jra/emergency_entries.py` を新設し、
policy `jra_emergency_entries: true` のときだけ有効。緊急モードで作った行は来歴 `entry_static=jra_official` になり、
後から来た KYI は static を書き換えない(先着保持)。

## D9. 冪等性の単位と修正版(FR-009)

決定: `jrdb_files(file_id, kind, data_date, variant, sha256, size, published_at?, fetched_at, applied_at, status, race_status)`。
同一 sha は**製品値の変更 0**(来歴の `ingestion_job` は増える)。sha が違う同名ファイル(修正版)の新旧は **Last-Modified では決めない**
(供給元が改訂番号を出さず、旧内容の再配置・同時刻異内容・直近とバックナンバーの時刻差で崩れる=D15 #3)。決めてよい順序は
2 つだけ: (a) 同じ (kind, data_date) で **速報版 → 正式版**(配信日程で定義される。SED/SEC の木曜ファイル)、(b) **日次 → バックナンバー**
(JRDB は訂正をバックナンバーに反映する)。それ以外の異内容は `held`(自動適用せず報告・運用者が承認して適用)。
`published_at` は記録するだけ。部分成功は**レース単位**で持つ(`race_status`: race_id → applied/failed/skipped)。ファイルの `applied` は
全レース applied のときだけで、同じ sha の再投入は未 applied のレースだけ再処理する(D15 #4)。変わった行は (a) 入力差分と (b) DB 更新件数を分けて出す。

## D10. parity の定義と区分(FR-004/005)

決定: 列ごとに「意味の一致」(仕様書の確認結果を人が書く・D1 の表)と「数値の一致」(区分=年×距離帯 [〜1400/1800/2200/
それ以上]×芝ダ障 ごとの一致率)を別に報告。分母=両側非欠損の結合済み行(完走行に限る列は完走行)。片側欠損・結合不能は
件数で別掲。既定基準(spec FR-005): `first_3f` 差 ≤0.1 秒が各区分 99%・カテゴリ列は正規化後 99.5%・確定単勝オッズ完全一致 99.9%。
**サンプル実測の時点で `first_3f`(1200m 以外)・`corner_orders`・`running_style`・系統は基準に達しないことが分かっている**ので、
段 3 の修復対象候補は 確定単勝オッズ・人気・馬主/生産者・クラス・1 着賞金・発走時間・馬体重・後 3F・払戻 に絞られる。

## D7a. 現行 DB に別定義の列を流さない契約(D15 #1)

決定: decode→upsert は **書込み先プロファイル** `profile=current|jrdb_canonical` を必須引数に取る。`current`(現行 DB)は
`first_3f`・`corner_orders`・`running_style`・`sire_line/damsire_line` を**値オブジェクトから落とす**(decode 段で None・
upsert 段で列 allowlist の二重ガード)。`jrdb_canonical`(段 3b の別 DB)だけがこの 4 列を書く。通常取込・修正版・修復・再構築の
**全経路が同じ関数を通る**ので契約は 1 箇所。テストは「`profile=current` で SED を書いても 4 列が IS DISTINCT FROM で変わらない」。

## D11. 歴史の作り直し(利用者要望 2026-11-08)と修復の関係

利用者の方針: **JRA-VAN の過去データではなく JRDB で取れたデータでモデルを作る**。これは段 3「修復」より強い。

決定: 段 3 を 2 つに分ける。
- **段 3a 修復(現行 DB の穴埋め)**: D10 の基準を満たした列だけ 2025〜2026 を埋める(特徴版不変・139 と同じ波及測定)。
- **段 3b 再構築(JRDB 正本の DB)**: JRDB アーカイブから **1999(または 2007)〜現在を別 DB(または別スキーマ)に取り込み**、
  `first_3f`・`corner_orders`・`running_style`・系統も **JRDB の定義で全期間**揃える。学習・評価はこの DB に対して行い、
  特徴量は FEATURE_VERSION を上げる(値の意味が変わる=017 と同じ bump)。現行 DB と予測経路はこの段では触らない。
  現行 DB → JRDB 正本 DB への切替は**別 spec**(モデル再学習・昇格ゲート・serving の切替を伴う)。

理由: 同じ列に 2 定義を混ぜない(017/091)。2025〜2026 の穴は JRA-VAN の定義では埋められない(JRA-VAN は止まっている)ので、
穴を埋めるには定義を JRDB に統一するしかなく、それは全期間の作り直しを意味する。憲法 I の「2007 年以降のみ」は
JRA-VAN の ID 体系の理由なので、JRDB(血統登録番号・JRDB コード)なら 1999〜も使える可能性がある=**別 spec で判断**
(本 feature の再構築は 2007〜 を既定、1999〜2006 は取り込むが学習窓に入れるかは別 spec)。

「JRDB の方が必ず多い」の確認(利用者要望): 年の範囲は JRDB 1999〜 ⊃ JRA-VAN 2007〜2024。列は D1 のとおり **44 列は同義の代替あり・
4 列(前 3F・通過順・脚質・系統)は同義の代替なし=JRDB 定義で置き換える**(「不足なし」は 44 列についての主張)。証明の形は
`uncovered_reference_cells`(D15 #6): JRA-VAN 側の非欠損セルごとに、正規 ID で一意に対応する JRDB 行があり・値が非欠損で・定義が同じか
検証済み正規化で同義、を全件検査して未充足セルを 行欠落/ID 未解決/値欠損/定義不一致 に分けて数える。44 列で 0 が「対象期間・対象列で
不足なし」の必要条件。将来分は主張しない。
行数は段 1 の parity で年ごとに レース数・出走行数・結果行数 を両側で数え、JRDB < DB の年があれば**理由(欠落ファイル・
地方交流の扱い)を特定するまで段 3b に進まない**。会員ページで確認: 各ファイル種別のバックナンバーの**開始年**
(SKB 2001・SRB 2004・HJC 2000・CYB 2002 は仕様書の初版日から推定)。

## D12. 段 0・段 3.5 の計器

決定: `live/source_probe.py`。段 0(a) は 174 の `schedule_day` の窓(post−240s〜−120s)で JRA 公式 `race_odds.refresh_race_win_odds`
を叩き、`official_at` と取得時刻を記録(netkeiba は触らない)。(b) は DB の来歴 2 表・`horses.data_source`・`id_mappings` を
集計して「行の生成元/現在値/マスタ/写像」の依存を数える。(c) は JRDB 会員 URL の **HEAD/条件付き GET を 10 分おき**に
投げて各版の `Last-Modified` を記録(配信時刻の実測。規約の制限時間帯は避ける)。段 3.5 は本番 DB 複製+JRDB ファイルを
到着時刻順に再生+公式経路の実取得で、`decision_quotes` の鮮度条件を満たしたかをレースごとに残す。

## D13. netkeiba の停止方法(FR-014)

決定: `config/source-operations.json` に任意キー `netkeiba_enabled`(既定 true)を足し、netkeiba を叩く全 CLI と ops job は
起動時に読んで false なら **HTTP 0 件で SKIPPED**(172 の `collection_paused` と同じ構造・policy は CLI 起動時に読むので
再起動不要)。コードは削除しない。`jrdb_enabled`(既定 false)も同じ形。

## D14. 配信時刻と T−3(FR-019)

事実: TYB は「各レース発走 15 分前頃」(場別ファイル上書き)、KYI/BAC/UKC は金 19:00・土 20:00(翌日分)、HJC は確定後随時、
SED 速報 18:00・正式 木 16:00。**TYB は T−3 には間に合わない**ので T−3 の取得先は JRA 公式に固定(spec 既定どおり)。

**土曜の結果→日曜の特徴(D15 #5)**: netkeiba 停止後、土曜の着順・タイム・上がり・通過順・馬体重・人気は JRA 公式の結果ページ(176)が
当日書く。**確定単勝オッズの数値と前 3F は木曜の SED 正式版まで来ない**。連闘馬(土曜に走って日曜に出る馬・頻度は段 0(b) で数える)の
日曜予測では前走オッズが NULL になり、段 3b の学習 DB(木曜の値が入っている)との間に小さな train/serve スキューが残る。
これは速報版を採っても埋まらない(速報版にも確定オッズは無い)。対処は (a) 連闘頻度と影響を段 3.5 で測って限界に記録、
(b) 列ごとの「利用可能時刻」を `jrdb_files.published_at`/`applied_at` から辿れるようにして、将来の as-of 改修(別 spec)に材料を残す。
実到着時刻(ずれ)は段 0(c) で測る。会員ページで確認: 実 URL・場別 TYB の置き場・バックナンバーの置き場(利用者注記:
「直近のデータとバックナンバーが別で存在する」)。

## D16. 会員ページの事実(2026-11-08・利用者の Chrome で確認)

**置き場は 3 つ**(利用者注記「直近とバックナンバーは別」のとおり):

| 置き場 | URL の形 | 中身 |
|---|---|---|
| 最近一週間 | `https://jrdb.com/member/data/index.html` → lzh `data/<Kind>/<KIND>yymmdd.lzh`、zip `datazip/<Kind>/<YYYY>/<KIND>yymmdd.zip` | 更新日ごとに 前日情報(PACI/PACH/JRDB パック)・想定確定(KAB/BAC/KTA 木)・マスター(KZA/CZA/MZA/KSA/CSA/MSA 木)・成績速報(TYB/HJC/SEC/SED 当日)・成績詳細(SED/SEC/SKB 木) |
| バックナンバー(zip) | `https://jrdb.com/member/datazip/<Kind>/index.html` → **年度パック `<KIND>_<YYYY>.zip`** と 単体 `<YYYY>/<KIND>yymmdd.zip` | SED・KYI・UKC・HJC・BAC・TYB・SKB・KAB: **1999〜2025 の年度パック 27 本 + 1999-01-05 以降の単体 2,98x 本**。CYB: 2000〜。PACI: 単体のみ 1999〜 |
| バックナンバー(lzh・旧形式) | `https://jrdb.com/member/data/<Kind>/index.html` | SEC(1999〜2025)・KYH(1999〜2025) など旧版名。lzh は開発中止・zip は「各データ最新形式のみ」 |
| 直前情報 随時更新版 | `https://jrdb.com/member/<YYYYMMDD>/tyokuzen/TYB<yymmdd>.zip`(会員 TOP の直前情報欄・レースごとに更新。11/08 実測: 東京 2R 発走 10:25 → 10:11 更新=**T−14 分**) | 最終版は `datazip/Tyb/` |
| 騎手/調教師マスタ | `https://jrdb.com/member/datazip/Ks/<YYYY>/KZA<yymmdd>.zip`・`datazip/Cs/<YYYY>/CZA<yymmdd>.zip`(KSA/CSA も同じ場所)。index ページは無い | 全騎手/全調教師の累積マスタ(週次)。過去の時点のマスタが要るなら年フォルダを直接たどる |
| SRB(成績レース) | SED の zip に同梱(index 無し) | ハロンタイム・位置取り・トラックバイアス |

**含意**:
- zip のバックナンバーが「最新形式のみ」で 1999〜 まで並ぶ=**JRDB が旧年分を最新レイアウト(KYI/SED)で再生成している**と読める。そうなら decoder は KYI/SED の 1 版で全期間を読めて、D2 の「版ごとの decoder」は lzh 旧形式を読むときだけ要る。**1 年分(例 SED_2008.zip・KYI_2008.zip)を実際に取って行長(376/1024)を確認してから確定**する(利用者の承認後・週中)。
- 「JRDB の方が多い」の年範囲は会員ページで裏が取れた: 全主要種別が 1999〜。JRA-VAN(2007〜2024)を**上下に 8 年+2 年**超える。
- 直前情報は T−14 分前後で TYB にしか載らず、T−3 の判断記録には使わない(D14 どおり)。
- 取得 CLI の `--archive` は 年度パック 1 本(年あたり 1 要求)を既定にし、単体ファイルは直近 1 週間と修正版の差し替えにだけ使う(要求数が 1/100 以下)。アクセス制限(開催日 08:00〜19:00 は昨年以前の DL 禁止)の対象は年度パックと旧年の単体。

**到達確認(2026-11-09 月 10:00 JST・認証情報は `config/.env.jrdb`・HEAD 1 件・ダウンロードなし)**: 日曜 2026-11-08 の単体 `BAC261108.zip` が HTTP 200・1,164 bytes・
Last-Modified **2026-11-07 15:40 JST**、`KYI261108.zip` も HTTP 200・97,714 bytes・同 15:40 JST=翌開催日の BAC/KYI は前日 15:40 には出ていた(D14 の「前日 20:00 以降」は上限側の見積りで、実到着はもっと早い。
KYI/UKC の実到着は R0 の arrivals probe で取る)。

## D15. codex plan レビュー(2026-11-08・`codex exec -m gpt-6-astra`・質問 2 問)

採用 6 / 不採用 0。

| # | 指摘 | 採否 | 反映 |
|---|---|---|---|
| 1 | 別 DB を作っても、通常 writer(SED race_atomic)が現行 DB に別定義 4 列を流す契約の穴 | 採用 | D7a: 書込み先プロファイル必須・decode と upsert の二重ガード・全経路共通(precedence-jrdb §3a・FR-021a) |
| 2 | 人物写像は正規 ID に直接ひも付く属性が要る。名前検索→同名 DB 行では根拠が名前だけ。未解決を含めて「不足なし」とは言えない | 採用 | D3: JRA 公式の騎手/調教師ページ(URL=正規 ID)から生年月日を取り、KZA/CZA と突き合わせる。netkeiba は使わない。未解決は不足に数える |
| 3 | Last-Modified/fetched_at では版の単調性を保証できない | 採用 | D9: 順序を決めてよいのは 速報→正式 と 日次→バックナンバー だけ。他は `held` で運用者承認。published_at は記録のみ |
| 4 | ファイル単位の冪等性とレース単位の部分成功が整合しない | 採用 | D9/data-model: `race_status` を持ち、全レース applied でファイル applied。同 sha 再投入は未 applied レースだけ |
| 5 | 速報版不採用は妥当だが、netkeiba 停止後の土曜→日曜の供給と利用可能時刻が未解決 | 採用 | D14: 当日は JRA 公式結果で埋まる項目と木曜まで欠ける項目(確定オッズ・前 3F)を分け、連闘のスキューを段 3.5 で測り限界に記録 |
| 6 | 「不足なし」の証明は `uncovered_reference_cells`(JRA-VAN 非欠損セルごとの代替可否を全件検査)で | 採用 | parity 契約に追加・SC-009 を書き換え。主張は 44 列・固定期間に限定 |

不採用: なし。保留: 連闘のスキューを埋めるために速報版や公式結果を使った暫定値を入れるか(別 spec)。

## D17. 性能の見積り(T074・2026-11-09・公開サンプルと合成データの実測から。実測は R4/R6/R7)

plan の目標: 日次取込 1 開催日 1 分以内 / parity 2007〜2024(約 63,000 レース・約 90 万出走行)1 回 30 分以内 / 段 3b 全期間(1999〜現在)1 回 2 時間以内。
tasks T074 の中間目標: 1 日分の decode 1 秒以内・parity 1 年分(約 3,400 レース・約 48,000 行)30 分以内。

**実測(Apple Silicon・PostgreSQL は testcontainers・best-of-3)**

| 対象 | 量 | 時間 | 換算 |
|---|---|---|---|
| decode: 公開サンプル 8 種(BAC/HJC/KSA/KYI/SED/SRB/TYB/UKC・KAB は未対応種別) | 1,859 行・0.89 MB | 24 ms | 37,000〜140,000 行/秒(SED が最も遅い 37,000 行/秒=項目数が多い) |
| parity 1 開催日(合成・24 レース・SED 324 行・DB 参照込み) | 24 レース | 0.28 s | 約 12 ms/レース |
| 段 3a 修復 1 列 1 日(odds・値が変わる行だけ・冪等 2 回目込み) | 2 レース | 1.1 s | テストの固定費が大半(2 回実行+スナップショット比較) |
| 段 3b 再構築 1 開催日(4 段 job+年次比較・別 DB) | 23 レース | 2.2 s | 約 0.1 s/レース(job 行 4 本と compare の固定費込み) |

**見積り**

- 1 開催日の decode: 実運用の 1 日は BAC 36+KYI 約 500+UKC 約 500+TYB 約 500+SED 約 500+SRB 36+HJC 36 ≈ 2,100 行 → **約 30 ms**(目標 1 秒の 1/30)。
  日次取込(decode+writer)は writer が支配し、再構築の実測 0.1 s/レース から **36 レースで 4 秒前後**(目標 1 分)。
- parity 1 年(3,400 レース): 12 ms/レース × 3,400 ≈ **41 秒**+参照の読み込み(年 1 回の SQL・48,000 行)。目標 30 分に 2 桁の余裕。
  2007〜2024(63,000 レース)なら ≈ 13 分+読み込み → 目標 30 分の内側だが余裕は 2 倍程度。**参照 SQL は本番 DB(90 万行)で線形に伸びる**ので R4 で年ごとの実測を取り、超えるなら年単位の並列(年は独立)に逃がす。
- 段 3b 全期間(1999〜現在 ≈ 27 年 × 約 3,400 = 92,000 レース): 0.1 s/レース × 92,000 ≈ **2.6 時間**=目標 2 時間を**超える見込み**。
  内訳は 1 レース 1 savepoint の writer と来歴 2 表の INSERT。対策候補(R7 で決める): (a) 年を独立に並列実行(別 DB なので安全・CPU 数で割る)、(b) compare を年末 1 回に、
  (c) `race_horse_field_provenance` の行単位 INSERT を executemany に。目標を動かす判断は利用者。

**実測との差が出る要因(R4/R6/R7 で確認)**: 実アーカイブは CRLF・年度パック(1 kind 12 か月分が 1 zip)・SED の実行長 376 で合成と同じ、
parity の参照は本番 DB の 90 万行(テストは数十行)、再構築の別 DB は同一ホストでディスク競合、段 3a は年別の充足率で変わる行数が違う(JRA-VAN 期は NULL 埋めだけ)。
速報版 SED・TYB の場別上書きは同じ decode 経路なので係数は変わらない。

## D18. codex tasks レビュー(2026-11-08・`codex exec -m gpt-6-astra`・質問 2 問)

採用 11 / 不採用 0。Q1=共通コード ⏸ と rollout の順序、Q2=段 3.5 の隔離と SC-003/004 のテストが「通るが要件を満たさない」経路。

| # | 指摘 | 採否 | 反映 |
|---|---|---|---|
| 1 | ⏸ の判定は新規ファイルかでなく稼働プロセスの import 経路で行う(export・CLI 登録・再エクスポート・依存追加・import 副作用) | 採用 | tasks 冒頭「開催日の制約」に明記。登録タスクだけ ⏸・反映は R2 でまとめる |
| 2 | R2 は「反映→migration→再起動」の間に新コードのジョブが未移行 DB を読む隙間がある。親 worker/子 subprocess/実行中ジョブでコード世代が混在 | 採用 | R2 を 6 手順に(投入停止→実行中ジョブと subprocess の終了確認→migration→コード切替→起動検証→再開) |
| 3 | 0025 の実行自体が `models/__init__` 経由で壊れうる。JRDB 行を書いた後は downgrade 不可で復旧が単純でない | 採用 | R2 (1) で 0024 複製に候補 checkout から migration を先に流す。復旧手順を初回書込み前/後で分ける |
| 4 | 稼働中 `near_post.py` への公式分岐追加は ⏸。診断・隔離試験は専用の入口から公式を呼ぶ。本番分岐は公式失敗時の netkeiba fallback 禁止まで含めて検証 | 採用 | T063 を専用入口 `source_trial.capture_official` に(near_post の純関数だけ import)。新 T064a ⏸ `near_post_source` policy(既定 netkeiba・fallback 禁止)。R9 の前に 1 日運用 |
| 5 | R2 の反映と各機能の稼働を分離(既定無効・ゲート後に有効化)。停止時は policy の再読込みと常駐プロセスの fetcher 保持を確認 | 採用 | R2 で全フラグ既定・R5 で `jrdb_enabled`・R9 で worker/ops-api 停止→起動と `pgrep` |
| 6 | `nk:` 行と当日 netkeiba 行の不在だけでは非供給を証明できない(正規 ID 行内の netkeiba 由来セル・id_mappings・キャッシュ・来歴なし) | 採用 | T062/T063: 来歴 2 表・id_mappings・キャッシュ dir まで起動検査、許容する継承データを `trial-config.json` に凍結 |
| 7 | 到着時刻順でも完成済み複製 DB・事後ファイル・事前の人物写像を使えば試験は通る。KYI 遅延時も事前データが行を埋めていれば緊急モードを検証できない | 採用 | T063: 複製 DB を当日 00:00 時点に戻し、締切時点の充足で採点。T064: 緊急モードの生成処理の実行記録を要求 |
| 8 | replay の最終値を決定論項目だけで比較すると順序依存項目(race_atomic・odds 採否・来歴・held/supersede)の規則違反が抜ける。両順序で同じ誤りになる形もある | 採用 | T042: 入力履歴に応じた期待値を持つ項目は各投入直後に規則から導いた期待値と比較。古い版・異内容 held・部分失敗再投入を追加ケースに |
| 9 | `xmin` は「製品値の変更 0」と「UPDATE 0」を区別できない(同値 UPDATE は偽陽性・比較対象不足は偽陰性) | 採用 | T041/T042: 製品値は主キー付き列値比較、不要書込みは xmin、INSERT/DELETE 検出。全 applied と一部 failed 再投入を分ける |
| 10 | persons の 99% は分母次第で必要人物の欠落を隠す(取得できた 1,500 件や照合成功集合を分母にしない・出現回数で重み付けしない) | 採用 | T033/R3: 分母は対象データに出現する一意コード・騎手/調教師×年代別。投入する開催日は必要人物全件 mapped を R5 のゲート(`jrdb-audit unresolved_person=0`) |
| 11 | transport mock の「0 要求」は別クライアント・subprocess・キャッシュを保証しない。取得が空でも 0 要求は成立する | 採用 | T062/R8: プロセス全体の外向き接続ブロック(172 の `no_outbound_python_connections` と同型・netkeiba ホスト限定)+キャッシュ初期化。合格条件は 0 要求でなく全レースの充足 |

不採用: なし。保留: なし。

## D19. speckit-analyze の整合検査(2026-11-08・tasks 後)

CRITICAL 0・HIGH 4・MEDIUM 9・LOW 7。HIGH 4 件は実装前に spec/tasks へ反映した(コードで裏取り済み)。

| # | 所見 | 反映 |
|---|------|------|
| I1 | US4 受入 1 の Given「段 0 の割合が閾値以上」が FR-002(段 0 は合否に使わない)・FR-015(停止のゲートは段 3.5 全件)と矛盾 | spec US4 受入 1 の Given を「段 3.5 全件成立(SC-001a)+段 2 ゲート(SC-003/004)」に |
| I2 | 段 0 の鮮度条件の既定 [T−180, T−60] が、診断が使う 174 `near_post.schedule_day` の実窓(`TARGET_S=180`・`WINDOW_S=60` → T−240〜T−120・近接レースは 60 秒ずつ後ろ)とずれ、取得が構造的に「早すぎ」判定になる | 鮮度条件を「174 の捕捉窓そのもの」と定義し直し、独自の秒数を持たない(spec US0 受入 1・T020・T023) |
| G1 | 緊急モードは `jra/emergency_entries.py` の新設(T050)までで、本番 `jra/cli.py::cmd_entries`(`upsert_jra_entries` を直接呼ぶ)への policy 結線タスクが無く、runbook T071 の手順が成立しない | T050a ⏸ を追加(policy true なら `upsert_jra_entries` の前に呼ぶ・false はバイト不変・T043 を本番入口でも通す)。R2 (4) の反映リストと US2 依存に追加 |
| I3 | T064a/R2 が導入する policy キー `near_post_source` が T069 の一覧に無く、`scripts/source_operations_172.py` は `OPTIONAL_KEYS` 外を `policy_keys_invalid` で拒否するので R2 の wrapper 起動が落ちる | T069 を 6 キーに、`OPTIONAL_KEYS` 更新を確定事項に(wrapper テスト追加) |

**MEDIUM 9 件(同日・2 回目)**: 7 件を反映、A1/A2(FR-018/019 の NEEDS CLARIFICATION)は R0a/R0 で実測する設計なので受容。

| # | 所見 | 反映 |
|---|------|------|
| I4 | contracts §4 が 10 時オッズ・確定複勝を「段 1 の報告には出す」とするが、tasks は `target=skip` で decode せず leak-guard で禁止 | 契約側を「読まない・報告にも出さない」に統一(保存先の無い列の報告は憲法 V の迂回) |
| I5 | T049 が exotic の集合置換も `source=jrdb_result` としていたが、`exotic_odds`/`race_laps` の CHECK は `JOB_SOURCE`=`Source.ALL`。`jrdb_result` を受けるのは `official_win_payouts` のページ種別 CHECK だけ | T049/T044 で `official_win_payouts=jrdb_result`・`exotic_odds`/`race_laps=jrdb` を明記 |
| U1 | 来歴表に `published_at` 列は無く、`jrdb_files.ingestion_job_id` は上書きされるので一意に辿れない | data-model §3a「利用可能時刻の導出経路」: 来歴の job_id → `ingestion_jobs.summary.files[]`(T051 が `{jrdb_file_id, kind, data_date, variant, sha256, published_at}` を書く)→ 項目群に対応する kind の published_at。NULL は fetched_at を上限。T055 の期待を書き換え |
| I6 | US3 の動機と SC-005 がテン3F 復旧を前提にしていたが FR-005/D10 で 3a 対象外 | US3 動機を馬主/生産者/クラス/確定オッズ/発走時刻に、受入 1 を BAC 発走時刻の NULL 埋めに、SC-005 から first_3f の数値目標を外す(報告のみ) |
| I7 | SC-008「既存テストが不変」が T018/T019 の head pin 更新と矛盾 | SC-008 を「既存テストが緑・変更は head pin 11 本と 176 テストの除外表だけ」に |
| I8 | plan の既存変更範囲が tasks の ⏸ 一覧より狭い | plan の Scale/Scope と Project Structure を ⏸ 12 件(T015/T016/T022/T036/T046/T050a/T064a/T065/T068/T069 と T018/T019)に同期 |
| U2 | 段 0 の probe が `refresh_race_win_odds` を呼ぶと dry-run が無く接続先 DB に odds と `jra_odds` ジョブを commit。ops の公式取得との共有スロットの扱いも未記載 | T020: `Walker.odds_pages`+`parse_win_odds` を直接使い DB には書かない(read-only role で固定)。`JraFetcher` は `validate_startup` 経由で鍵 `jra.go.jp` の共有スロットに入るので ops と直列化され間隔を別に決めない |

**未修正(LOW 7)**: I9 性能目標 plan 30 分(全期間) vs T074 30 分(1 年)・I10 `status`→`mapping_status`・I11 contracts/cli.md と quickstart の引数/種別名(`jrdb-prerace`・`--reference-db-url` 等)・D1 policy reader 2 系統(`jrdb/policy.py` と `netkeiba_policy.read_flag`)・I13 spec の未決リストに plan で決まった項目が残る・I12 quickstart の kinds・I14 FR-017 文言と 0025 の実体。`.specify/feature.json` は 178 に更新済み。

## D20. codex 実装レビュー Phase 2(2026-11-08・`codex exec -m gpt-6-astra`・decode/ids/profile・質問 2 問)

採用 12 / 不採用 0。Q1=decode の framing と型変換に黙って誤値を作る経路、Q2=profile の二重ガードと ids の世紀規則の穴。

| # | 指摘 | 採否 | 反映 |
|---|------|------|------|
| 1 | `9`/`Z` の汎用数値パーサが書式を検証せず `1.`・指数・NaN を受理 | 採用 | 整数書式は `^-?\d+$`、小数書式は小数点必須 `^-?\d*\.\d+$`、他は `JrdbDecodeError`。`scale` は整数書式だけ |
| 2 | `XZ9` で符号のみ(`-  `)を 0 にし、符号位置も検証しない | 採用 | 全空白のみ None・符号のみはエラー・`^[+-]?[ \d]*\d[ \d]*$`(符号は先頭) |
| 3 | `9999` 時刻の秒部に上限が無く `1600` が 2 分になる | 採用 | 0.1 秒部 `< 600` を要求 |
| 4 | 日付・年・flag の桁数/値域未検証 | 採用 | YYYYMMDD は 8 桁 ASCII+暦日、YYYY は 4 桁、flag は `1`/`0`/空白だけ(他はエラー) |
| 5 | 行末の扱いが「任意個数の空行を無視」で、終端欠落・データ内 CR・混在を区別しない | 採用 | 物理行ごとに CRLF/LF/LFLF を分類し `Framing(terminator, counts, extra_blank_lines)` を返す。終端欠落は `unterminated`・データ内 CR は `cr_in_data` で reject。行番号は物理行とレコード番号を両方持つ |
| 6 | 全行 rejected だけを `JrdbLayoutMismatch` にすると部分成功が呼び出し側に見えない | 採用(一部) | `DecodedFile.partial` を追加し、呼び出し側(T030/T051)が `rows_rejected` と `race_status` に必ず記録する義務を docstring と tasks に明記。空ファイルは 0 行で正常 |
| 7 | 例外本文に外部データ(`{n!r}`・key8)が入り、`from exc` で元 ValueError の文字列も漏れる | 採用 | 本文は位置と項目名だけ・`from None`・`_combo` の ValueError も捕捉 |
| 8 | UKC builder が `include_canonical` を受けず系統コードが残る | 採用 | 全 builder に伝播し、`decode_file` の末尾で `_assert_no_canonical`(値オブジェクト属性の最終ガード)。低水準 API は `_` 付きに |
| 9 | `assert_allowed` は任意呼び出しで、呼び忘れた書込みを防げない | 採用 | `check_payload(profile, table, payload)` を書込み境界に(None も書込み扱い)。US2 の `upsert_jrdb` は全 INSERT/UPDATE でこれを通す(T048/T049 の実装条件)。接続と profile の束縛(`jrdb_canonical` は本番 DATABASE_URL を拒否)は T051 の起動検査 |
| 10 | 手製 `WriteProfile("current", 禁止列入り)` と `PROFILES` 差し替えで迂回できる | 採用 | 正規定数 2 つの同一性(`is`)だけ受理・`PROFILES` を MappingProxyType |
| 11 | 血統登録番号の世紀規則が 1999 年レース(yy 98→2098)や生年月日の使い方で破綻 | 採用 | 生年月日があればその年を採用(下 2 桁の一致を要求)、無ければレース年の世紀で未来なら −100。年齢 1〜30 の範囲外はエラー |
| 12 | `year_from_yy` の 90 境界、`day_hex="10"` の受理、ASCII 構文未検証、file_year ±1 の意味 | 採用 | `resolve_year(yy, reference_year)` で基準年に最も近い年(±1 超はエラー)、基準無しだけ契約の 90 規則。`JrdbRaceKey.__post_init__` と `key_from_text` の正規表現で両入口を同じ文法に(日は 1 桁 16 進 1〜f) |

**反映後のテスト**: scrape `tests/unit/test_jrdb_*.py` 107 件緑(codex 由来の追加 10 件を含む)。plan からの差: `split_records` の戻りは `Framing` オブジェクト(文字列ではない)・`RejectedRow` に `record_no`/`reason` を追加・`horse_id_from_blood_no` は生年月日を正とする。

## D21. 実装メモ — MVP(Phase 1〜3 + US0 + US1・2026-11-08・worktree `178-jrdb-primary-source`)

開催日のため ⏸(T015/T022/T036)は未着手。新規ファイルだけで MVP を実装し、plan/tasks からの差を記録する。

| 項目 | 事実 / 判断 |
|---|---|
| 公開サンプルの行末 | 配布形態で CR が落ちている(HJC/TYB は LF のみ)・重複している(UKC/KSA は LF LF)。decoder は CRLF/LF/LFLF を物理行ごとに分類し `Framing` で報告、データ部の長さ(レコード長−2)で桁ずれを判定。会員配信の zip が CRLF かは R1 で確認(fixture manifest に `line_terminator` を記録) |
| BAC のレコード長 | 仕様書の見出しは 176 だが末尾項目(改行 相対 183)と公開サンプルは 184。184 を採用 |
| `JrdbFileMeta.edition` | SED/SEC の 速報/正式 を配信日程(当日=速報・翌木曜 16:00 以降=正式・バックナンバーは正式)から決める `files.sed_edition`。Last-Modified は使わない(D9) |
| decode の値域検証 | codex D20 の 12 件を反映(整数/小数の書式・XZ9・9999 時刻・日付・flag・例外文字列からデータを除去・UKC の canonical 伝播・profile 正規定数・世紀規則・レースキー文法) |
| pandas を使わない | parity は純 Python(dict)で書いた。scrape の実行時依存を増やさない(ops subprocess が scrape を import する)。T028 の「pandas フレーム」は合成 `Reference` に置換 |
| 参照セルの列数 | contracts/parity.md の「44 列」は JRA-VAN CSV の列数で数えた目安。DB 列では 36 列(`REFERENCE_CELL_COLUMNS`: レースキー 5 列→race_id・コーナー 4 列→1 配列・生年月日→birth_year・18 桁 ID 不使用)。報告は列名で出すので数は問題にならない。別定義 4 列(5 物理列)は `definition_mismatch` 別掲で 0 を要求しない。騎手/調教師は `id_unresolved` |
| 段 0 probe | `refresh_race_win_odds` を使わず `Walker.odds_pages`+`parse_win_odds` を直接呼び DB には書かない(analyze U2)。鮮度窓は `near_post` の定数を import(I2)。`probe_dependencies` は SQL 8 本+連闘馬 1 本の純集計 |
| 人物ページの構造 | 176 の調査にはレースページしか保存されておらず、騎手/調教師ページの DOM は未確認。parser はラベル(生年月日/所属/見出し)で読む設計にし、生年月日が無いページは拒否。**R3 で実ページ 1 枚を見てから 1,500 件の収集に入る**(T032 の前提) |
| 人物一覧ページ | 一覧の URL/cname は運用者が R3 で公開ナビゲーションから取り `collect_profiles(index=(url, cname))` に渡す(動的 cname は保存しない=176 FR-001) |
| 書込み境界 | `profile.check_payload(profile, table, payload)` を US2 の全 INSERT/UPDATE で通す(codex D20 #9)。`jrdb_canonical` と本番 DATABASE_URL の束縛は `policy.assert_profile_binding`(CLI T051 で使う) |
| jrdb-persons | US1 は `--dry-run` のみ(報告 JSON)。id_mappings への書込みは T047 |
| 会員 URL の文字列 | `jrdb/fetch.py` だけが会員パスを持つ(URL ビルダ)。fixture manifest テストが他ファイルでの出現と認証情報の値を検査 |

**テスト(2026-11-08)**: scrape unit 全体 緑(JRDB 新規 135 件を含む)・scrape integration `test_jrdb_parity_readonly`(2)+`test_jrdb_files_table`(4) 緑・db integration `test_migration_178`(4)+`test_migration_176`(3) 緑・features head pin 9 本 32 件 緑・live `test_source_probe` 14 件 緑・ruff 緑。公開サンプル 2008-09-13 を seed した DB に対し `jrdb-parity --year 2008` が JSON を書き、着順/タイム/確定オッズ/後 3F/クラス/馬場 100%、前 3F は 1200m のみ 100%、製品表 9 表の行数と xmin は不変。

**未着手**: ⏸ T015/T022/T036(登録 1 行ずつ・R2 で反映)、US2 以降(T040〜)、Polish、Rollout。

## D22. 実装メモ — US2(第 3 の writer・2026-11-08・worktree)

**⏸ の扱い**: worktree は稼働プロセスから隔離されているので、⏸ タスク(T015 `Source.JRDB`・T016 export・T022/T036 の CLI 登録・T046 `precedence.py`・T050a `jra/cli.py`)も worktree 内で実装した。⏸ が縛るのは **main への反映(R2)** であって編集場所ではない。共通ファイルの差分は最小(precedence +37・upsert +48/−12・scrape/cli +11・jra/cli +16・live/cli +75・db enums/constraints/__init__ 数行)。一度 `ruff format src` で無関係ファイルが整形されたが `git checkout` で戻し、意図した変更だけを再適用した。

**codex unavailable**: `codex exec -m gpt-6-astra` が 2 回とも Notion MCP サーバの OAuth 認証エラー(`rmcp::transport::worker ... AuthRequired mcp.notion.com`)で起動段階で落ちた(`-c mcp_servers={}` でも同じ)。再試行上限(1 回)に達したので US2 の第三者レビューは未取得。**環境復旧(codex の MCP 設定から Notion を外す)は別タスク**。代替として下のセルフレビューを実施。

**設計判断**:
| 項目 | 判断 |
|---|---|
| 規則の置き場 | `precedence.py` に `JRDB` 定数と 3 箇所の追加分岐(静的項目の official→jrdb keep / jrdb→netkeiba keep・`odds_decision` の `source_not_allowed`・`payout_block_decision` の to_confirm 空化)+ `results_input_allowed`。netkeiba/jra_official 同士の判定は文字どおり不変(176 の unit 217・integration 166 緑) |
| 共通 writer への追加 | `apply_final_odds(horse_id_of=)`(正規 ID を持つ呼び手の注入点・既定は従来の id_mappings 解決)、`upsert_official_win_payouts` のページ種別→経路の対応表 `_WIN_PAYOUT_FEED` と採用元(official > jrdb > netkeiba)、`replace_exotic_dividends` の来歴採用元(official が stored/incoming のどちらかなら official) |
| 語彙 | JRDB が新規に作るレースの静的列は JRA-VAN 多数派の綴り(`芝/ダ/障`・`1勝/2勝/3勝/未勝利/新馬/オープン`・`G1/G2/G3/L`・`良/稍重/重/不良`・`牡/牝/セ`)。緊急モード(公式ページ)は `ダート→ダ`・`〜クラス` 除去 |
| 年代ガード | `profile=current` では `race_date >= 2025-10-11` 以外を書かない(JRA-VAN 行の保護・修復は 3a の列別 CLI)。`jrdb_canonical` は全年。**既知の穴**: `apply_final_odds` の年代ガードは共通 writer 内にあり、3b の別 DB で 1999〜2024 の確定オッズを書くには注入点が要る(T059 で対応) |
| 不要書込み | 同値の馬体重対・同値のラップは UPDATE しない(xmin 不変=codex D18 #9 の計器を守る)。来歴 observed_at は動かない(176 のオッズ「同時刻同値は新しい観測」とは別扱い・JRDB は週次ファイルなので観測時刻に意味が薄い) |
| 人物 | `load_person_map` は `id_mappings(source=jrdb, mapped)` だけ。未解決は NULL+`unresolved_person`(契約)。`write_id_mappings` は既存 mapped を触らず unmapped も行として残す |
| 部分成功 | `RaceStatus` → `jrdb_files.race_status`・ファイル status(applied/partially_applied)・同 sha 再投入は `unapplied_races` で未 applied だけ。失敗レースの savepoint は `write_entries` 内で巻き戻る |
| 緊急モード | 既存行が無いレースだけ作る・取消馬の馬番は変更情報ページから復元(`resolve_cancelled_numbers`)・復元できないレースは作らない・policy は `jra/cli.py::cmd_entries` が呼び出し時に読む(flag false なら 176 経路バイト不変) |

**セルフレビュー checklist(codex 代替)**: (1) 176 既存テスト緑 ✓ (2) 新規 unit T040 で JRDB 行の遷移表を 1 行ずつ ✓ (3) T041(a)〜(e)・T042 2 順序+再投入・T043 緊急+本番入口・T044 速報版拒否/確定オッズ/払戻整合/TYB・T045 id_mappings ✓ (4) profile ガードは `check_payload` を全 INSERT/UPDATE で通し、`_assert_no_canonical` と二重 ✓ (5) `jrdb_canonical` の DB 束縛は `assert_profile_binding`(CLI) ✓ (6) 未確認: 実会員ファイル(CRLF・年度パック)での行長・R5 の実 DB 複製再生・3b の確定オッズ年代ガード。

**テスト**: scrape unit 628 / scrape integration: jrdb 8 ファイル 28 件+176 回帰 167 件 緑 / db 0025・features pin・live probe 緑。

## D23. 実装メモ — US3(段 3a 修復・段 3b 再構築・2026-11-08・worktree)

**3a `jrdb/repair.py`**: 列ごとの修復。allowlist は 10 列ちょうど(odds/popularity・weight・last_3f・owner_name・breeder_name・race_class・prize_money・post_time・win_payouts)=段 1 の parity 規則が `same`/`normalized`/`jrdb_only` の列。別定義 5 物理列は parity 報告を読む前に `definition_mismatch` で拒否(FR-021a)。**基準の正本は `parity.COLUMN_RULES`**: 実行時に `parity_dir/*.json` を全部読み、対象列の verdict が 1 報告でも False なら `parity_below_threshold` で拒否、報告が 1 つも無ければ `no_parity_report` で拒否、比較セル 0(post_time)の None は拒否しない。CLI は job 行を作る前にこの検査を通す。

| 列 | 規則 | 理由 |
|---|---|---|
| odds(+popularity) | SED 正式版 → `apply_final_odds`(netkeiba 期のみ・値が変わる行だけ・`horse_id_of` で正規 ID) | 139 と同じ関数=同じ規則。`win_odds_observed_at`/`official_at` も 139 どおり動く(1 行でも書いたレースだけ) |
| weight | SED → body_weight 対を `field_transition`(netkeiba 期のみ・同値はスキップ) | US2 の結果 writer と同じ |
| last_3f | **NULL だけ埋める(両年代)** | 同義列だが parity 95.7%(DB 側異常値)。上書きすると来歴 `results` 群を JRDB 所有に書き換える必要があり、1 列の修復で群全体の所有を変えるのは過剰。異常値の訂正は別件 |
| owner/breeder | UKC → NULL 埋め(全年代)+ `data_source=netkeiba` のマスタは JRDB の綴りに置換(`nk:` サロゲートも対象)・jra_van/official/jrdb のマスタは keep | horses には来歴表が無いので `data_source` を所有の代わりに読む。正規化後も違う値は `netkeiba_value_differs` で件数報告(上書きはする) |
| race_class/prize_money/post_time | BAC → netkeiba 期は `field_transition(race_static|post_time)`(公式所有 keep・netkeiba 所有 write+`static_mismatch`)・JRA-VAN 期は **NULL だけ埋める**(値があれば `kept_jra_van`) | 憲法 I(JRA-VAN の値を JRDB で上書きしない)。埋めた値の来歴は `source=jrdb` |
| win_payouts | HJC → `upsert_official_win_payouts(source=jrdb_result)`(netkeiba 期のみ) | 公式所有の同値集合は採用元を動かさない(US2 と同じ) |

**利用可能時刻(FR-010)**: `jrdb/availability.py::available_at(session, race_id, group[, horse_id])` が data-model §3a の経路を実装(来歴 job_id → `ingestion_jobs.summary.files[]` → 群に対応する kind の `published_at`、無ければ `fetched_at` を上限)。アーカイブは Last-Modified を持たないので `fetched_at`=ファイル mtime(=「この時刻までには使えた」としか言えない)。同じファイルの再投入で値が変わらなければ来歴の job_id は動かない(統合テストで固定)。`cli_write._files_summary` に `fetched_at` を足した(cli_write は 178 の新規ファイル)。

**3b `jrdb/rebuild.py`**: 日ごとに entries→prerace→results→payouts を US2 の `upsert_jrdb_*`(`profile=jrdb_canonical`・別 engine)で呼ぶ。アーカイブファイルは別 DB の `jrdb_files(variant=archive)` に登録(summary.files[] の `jrdb_file_id` が実在=INV-F5)。年ごとに両 DB の races/entries/results/payout_rows/laps と別 DB の充足率を `compare-<year>.json` に出し、**races/entries/results のどれかで JRDB < 現行なら `blocking_years`・exit 1**(payout_rows/laps は現行 DB が 2025-10 以前の払戻を持たず比較にならないので外した・報告はする)。安全装置: `--db-url` が `--reference-db-url`/env `DATABASE_URL` と同じ(URL 文字列の sha)なら `same_database`、両 DB の `alembic_version` が違えば `schema_mismatch`。**共通 writer の変更 1 件**: `apply_final_odds(..., era_from=NETKEIBA_FINAL_ODDS_FROM)` を追加(既定不変)。`upsert_jrdb_results` は `prof.includes_canonical` のときだけ `era_from=None`(JRDB 正本 DB には守るべき JRA-VAN 行が無い)。現行 DB の呼び出しは全部既定。

**テストの材料**: 公開サンプルは種別ごとに日が違う(BAC/SED/SRB 2008-09-13・HJC 2024-09-29・KYI 2015-07-12・TYB 2021-09-12・UKC 2002-09-08)。`tests/_jrdb_synth.py` に (a) 生バイトの年移動 `shift_raw`(レースキー yy・race_date・血統番号の年・UKC 生年月日だけを書き換え、他はそのまま=2026 年の「アーカイブ」になる)と (b) 実レコードを雛形に SED 行から KYI/UKC/HJC を合成する `encode_field`/`patch_record`(HJC は単勝・複勝以外を空欄に)を追加。これで 2008-09-13 が BAC/KYI/UKC/SED/SRB/HJC の揃った 1 日になり、rebuild の統合テストが第 2 コンテナへ 24 レース・324 出走・結果・払戻 24・ラップ 23(サンプルの中山 4R はラップ無し)を入れ、別定義 5 列が JRDB 定義で非 NULL・現行 DB は件数不変を確認する。

**T058 `scripts/jrdb_repair_impact.py`**: 139 の `evidence/measure_repair_input_shift.py` を引数化(pre/post・`--db-url`・`--from/--to`・`--out`・`--model-root`)。読み取り専用。`decision_quotes`/`attention_picks` は触らない。**実 DB 複製での実行は R6**(artifact `artifacts/market_ev` と複製 DB が要る)=本セッションでは `--help` と lint のみ。

**未確認(R6/R7 で)**: 実アーカイブ(CRLF・年度パック)での修復件数と年別充足率・2007〜現在の再構築 2 時間以内(plan 性能目標)・`blocking_years` が出た年の理由特定(地方交流・欠落ファイル)。

**テスト**: scrape unit 641(+13 T054)/ JRDB 統合 38(+6 T055・+4 T056)/ 176 回帰 167 / db・live・features 緑 / ruff 緑。

**codex レビュー(US3・2026-11-08・`-c 'plugins."notion@openai-curated".enabled=false'` で Notion MCP を外すと起動できた=D22 の環境問題の回避策)**: 指摘 16 件。採用 10 / 限界として記録 4 / 不採用 2。
| # | 指摘 | 採否 | 反映 |
|---|---|---|---|
| Q1-1 高 | parity 未検証(None)の列が通る | 採用 | 比較セル 0・列欠落は `parity_undecided` で拒否。`jrdb_only`(post_time)だけ例外。unit 2 件追加 |
| Q1-2 高 | 1 列修復が群全体の所有を変える・JRA-VAN 期の NULL 埋めが公式所有を降格 | 一部採用 | 公式所有の群来歴は書き換えない(`provenance_kept`)・netkeiba 所有/無所有だけ jrdb に。列単位の来歴は列追加(FR-017 の外)なので限界として記録 |
| Q1-3 高 | 馬主の実質変更まで JRDB で上書き | 採用 | 正規化後も違う値は keep+`netkeiba_value_differs`(表記ゆれだけ置換) |
| Q1-4 高 | マスタの列所有・来歴・競合制御 | 一部採用 | 条件付き UPDATE(`IS NOT DISTINCT FROM 読んだ値`・`changed_concurrently`)。horses の列来歴は限界 |
| Q1-5 中 | last_3f NULL 限定は妥当 | 採用(現状維持) | 非 NULL 不一致は parity 報告で見る |
| Q1-6 中 | 冪等性主張の限定 | 記録 | 「同一入力の再実行で製品表の xmin 不変・job 行は除く」と明記 |
| Q1-7 中 | 1 列と対更新の契約 | 採用 | 報告に `columns_written`(odds→odds+popularity 等) |
| Q1-8 中 | FR-021a 全経路 | 記録 | 修復の直接 UPDATE は全部 `check_payload(CURRENT)` 経由 |
| Q1-9 中 | mtime は根拠として弱い | 採用 | `fetched_at_basis: file_mtime|http_fetch` を summary と `Availability` に |
| Q2-1 高 | URL sha 比較は弱い | 採用 | 接続して `(pg_postmaster_start_time, DB oid, datname)` を比較(`assert_distinct_databases`)。URL sha は前段に残す |
| Q2-2 高 | profile と接続先の結び付き | 一部採用 | rebuild は `target_not_jrdb_only`(他経路の行があれば拒否)+ 同一 DB 拒否。専用マーカー/権限は R6 の運用手順へ |
| Q2-3 高 | 件数は相殺される | 採用 | レース/出走(馬番)/結果/払戻/ラップのキー集合で `missing_in_jrdb`(件数・標本 20・JRDB 側の余剰)を出し、1 件でも欠けていれば blocking |
| Q2-4 高 | 払戻・laps を外す根拠不足 | 採用 | 両方 blocking に(現行に無い年は空集合=合格) |
| Q2-5 中 | 同 head だけでは target 適格性不足 | 採用 | `target_not_jrdb_only` |
| Q2-6 中 | 再開境界・未解決人物の再処理 | 記録 | 日×4 段 job+同 sha の未 applied レース再処理は実装済み。人物写像が後から解決しても同 sha では NULL が残る=再投入手順(R6)で扱う |
| Q2-7 中 | 比較の基準時刻・target の状態 | 一部採用 | `compared_at` を compare JSON に。running/failed/verified の状態表は別途 |
**テスト**: Q1-1(unit 2 件)・Q1-3(違う馬主を keep)・Q1-7(`columns_written`)・Q1-9(`fetched_at_basis`)・Q2-1(別表記 URL=`application_name` 付きの同一 DB を拒否)・Q2-3(`missing_in_jrdb` 30 件・JRDB 側余剰 24・標本 20)・Q2-5(netkeiba 行のある target を拒否)を統合テストに追加。**教訓**: 偽のレース ID `2008060401nn` は公開サンプルの実レース(中山 4 回 1 日)と衝突して欠落が 20 件に見えた=キー比較は件数より厳しいが、テストの偽データもキー空間を外す必要がある。

## D24. 実装メモ — US3.5(運用成立試験・2026-11-08・worktree)

**`live/source_trial.py`**: 複製 DB の当日分を消し(`reset_day`・replica role・**スナップショット復元ではない**=継承する前日以前のデータは `inherited_state`(JRDB 人物写像の件数+sha・horses の data_source 別件数・前日以前のレース数)として凍結設定に記録)、隔離検査(`nk:` 行・当日作成の netkeiba マスタ・当日の netkeiba 来歴/写像更新/job・HTTP キャッシュ)→ 到着時刻順のイベントループ(JRDB ファイルは `jrdb_files` に登録してから**本番の writer コマンド `cli_write.run_write`** を関数呼び・SED/SRB は `variant=archive`=正式版)→ 08:00(既定)に JRDB entries 未着なら緊急モード(公式出馬表 → `create_entries_from_official`)→ 行ができた時点で 174 `schedule_day` → 各レースの窓開始で T−3 捕捉 → 終了時にレースごと採点し JSONL に append。期待レース集合は BAC から取り、行が作られなかったレースは `missing_races`。`replay_mode`(`realtime`=R8 の実運転 / `fake_clock`=テスト)を設定と summary に記録し混ぜない。外向き接続は `block_outbound_except(www.jra.go.jp)`(Python ソケット差替え・172 と同型・証明ではなく一層)。

**T064a `near_post.py`(⏸・worktree 内で実装)**: policy 任意キー `near_post_source: netkeiba|jra_official`(欠落=netkeiba・既存テストの dict はバイト不変・不正値は拒否)。`capture_race(..., quote_source, quote_fetcher)`: official なら同じゲート順・同じ advisory lock・同じ取得後再確認で、取得だけ新モジュール **`live/official_quote.py`**(`fetch_official_quote`=176 Walker・`official_quote`=174 の quote 形へ変換・人気は None・`is_official_fetcher`)に替える。official 固有の追加: fetcher が `.post` を持たなければ `failed:fetcher_mismatch`(policy だけ切り替えて netkeiba fetcher のままの事故)、Walker の例外は `failed:fetch_failed:<型>`(netkeiba へ fallback しない)、取得後に post_time 再読と窓内再確認。CLI は policy から取得元を 1 回読み、official なら netkeiba の robots ゲートを飛ばして `validate_startup`→`JraFetcher`。試験の `capture_official` は **この本番関数を呼ぶ薄い wrapper**(fake walker を `quote_fetcher` で注入)。

**codex レビュー(US3.5・Notion plugin を外して起動・指摘 12・採用 9 / 記録 3)**:
| # | 指摘 | 採否 | 反映 |
|---|---|---|---|
| Q1-1 高 | 偽時計の再生は「移行後の全レース締切成立」を証明しない | 採用(記録) | `replay_mode` を設定/summary に明記。R8 は `realtime` で実ページ。テストは順序とゲートの検証と位置づけ |
| Q1-2 高 | `reset_day` は 00:00 復元ではない | 採用 | docstring を正し、`inherited_state`(件数+sha)と入力ファイルの sha を凍結設定に |
| Q1-3 高 | netkeiba 検査の見逃し/過剰拒否 | 採用 | 継承した前日以前の netkeiba マスタは許容し、**試験中に作られた**マスタだけ数える(`masters_created_in_trial_by_source`・隔離 SQL も created_at で限定)。ソケット差替えは「一層」と明記 |
| Q1-4 高 | 到着時刻の算定 | 採用 | `arrivals_from_probe(path, day)` は当日ファイルの URL sha で照合し、**最初の present 観測時刻**を採る(Last-Modified は情報のみ)。既定日程は `arrivals_basis=default` と記録 |
| Q1-5 高 | 締切余裕の測り方 | 採用 | `features_ready_at`・`capture_started_at`・`fetch_seconds` を記録。窓内 1 回起動は 174 と同じ(窓 120 秒・締切 18 秒) |
| Q1-6 高 | 緊急モードが部分欠落を救わない・遅着 KYI 合流未検証 | 一部採用 | 発動条件を「08:00 時点で JRDB entries 未投入」に変更(作成はレース単位=既存行なしだけ)。遅着 KYI 合流の統合テストは **未追加**(東京 2026-11-01 の JRDB 合成ファイルが要る)→ R8 で実測 |
| Q1-7 高 | 「全件」と「確定」の定義 | 採用 | 期待集合=BAC の全レース → `missing_races`・確定オッズ=出走全馬に odds かつ `win_odds_official_at IS NULL` |
| Q1-8 高 | 試験用 capture が 174 と非同一 | 採用 | 試験は本番 `capture_race(quote_source=official, quote_fetcher=)` を呼ぶ。official 固有の再確認を本番側に追加 |
| Q2-1 中 | 遅延 import の循環 | 採用 | `official_quote.py` を本番モジュールとして切り出し、near_post と trial の両方が import |
| Q2-2 高 | 人気 None の下流(175/138/141) | 記録 | 175 の採点・138 の規則(odds 条件)・141 混合は odds を使い人気は使わない見込みだが**未検証**。R8 の確認項目(公式 quote で `near-post-score` を通す) |
| Q2-3 高 | 18 秒締切と 3 往復 | 記録 | `fetch_seconds` を記録し R8 で実測。索引の事前取得は未実装 |
| Q2-4 高 | policy と quote_source の二重指定 | 採用 | `fetcher_mismatch` 検査・CLI は policy から 1 回だけ導出・official 失敗は netkeiba 0 要求で failed(統合テストで固定) |

**テスト**: live unit 105(+9・既存 1 件は main でも失敗=`test_scrape_factory_remains_at_twenty_seconds_and_three_attempts`)/ live integration 110(+5: 1 日再生 23 平地レース全件 ok・隔離違反で書込み 0・緊急モードで 12 レース作成+T−3 12 件・公式経路の `capture_race` 実 Walker 通過・公式失敗で netkeiba 0 要求)。worktree には `artifacts/chaos_bands` が無く 086 の統合テストが落ちるため main からコピーした(gitignore 配下)。

**未確認(R8 で)**: 実時間での 18 秒以内取得(3 往復)・遅着 KYI と緊急行の合流・人気 None の下流影響・2 開催日連続。

## D25. 実装メモ — US4(netkeiba を policy で止める・2026-11-08・worktree)

**読み手 `scrape/netkeiba_policy.py`**: `netkeiba_enabled`(既定 true)・`jrdb_enabled`(既定 false)を同じ `read_flag`(bool 以外は拒否)で読む。policy ファイルが**無い**ときは既定(netkeiba 有効=policy を書いていない環境を壊さない)、**読めない/オブジェクトでない**ときは例外。読み先は既定の `config/source-operations.json`、env `HORSERACING_SOURCE_POLICY_PATH`(**絶対パス必須**・複製 DB の試験用)で差し替え可。`policy_fingerprint()`(path+sha256)を ops の SKIPPED summary と `jrdb-audit` に載せ、画面の停止表示と送信側が別ファイルを見ていないか照合できる。

**ゲート(⏸・worktree 内で実装)**: netkeiba の HTTP クライアントを作る場所は src 全体で 3 箇所だけ(静的テストで固定: `scrape/cli.py::_make_fetcher`・`ops/runner.py::make_fetcher`・`live/chaos_politeness.py::make_capture_fetcher`)。3 箇所とも先頭で `assert_netkeiba_enabled()`(client 構築前)、さらに scrape/ops の fetcher は **`pre_request` に policy の再読を合成**し、構築後に policy が false になっても次の要求の前で止まる。呼び出し側: scrape CLI は `SKIPPED: netkeiba_disabled` exit 0、ops の `run_one`/`run_day` は job を `skipped`(`summary.reason=netkeiba_disabled`・`policy_disabled`・`policy{path,sha256}`・`requests_sent=0`)、live `capture-chaos` は SKIPPED exit 0、`near-post-capture` は取得元が netkeiba のときだけ `REFUSED: skipped:source_unavailable` exit 2(`near_post_source=jra_official` なら netkeiba の fetcher を作らない)。171 の robots 再取得スクリプト(`scripts/roi_explore/robots_refresh_171.py`・独自 GET)にも同じゲートを足した。

**policy 6 キー(T069)**: `config/source-operations.json` に既定値で追加し、172 wrapper の許可リストと型検査(bool / 有限数 ≥2 / 列挙)を追加。wrapper は値を env に写さない(JRDB の間隔は `jrdb_ops.sh` が写し、CLI も policy を直接読んで突き合わせる)。

**`jrdb-audit` の `stage`(T070)**: 当日の `jrdb_files`(種別ごと variant:status・欠けている種別)・`expected_races`(KYI の race_status から)・races/post_time・`entry_static` 来歴の source 別レース数・`decision_quotes` の source 別件数・results/final_odds/payouts のレース数・policy 4 値+path/sha・`stopped`(どの段で止まっているか。行 0 かつ KYI 無しは **undetermined**=休催か未取得か DB だけでは判定不能)。

**runbook `manual.md`(T071)**: policy 6 キーの表・週の流れ・段の順序・netkeiba 停止手順・JRDB 未着時(audit → 緊急モード or 再開)・緊急モードと再開を同時に選ばない・再開後の穴埋め(埋められるもの/埋められないもの=T−3 記録)・読み先の確認・held の承認・読み方・書かないもの。

**codex レビュー(US4・指摘 10・採用 6 / 一部採用 2 / 記録 2)**:
| # | 指摘 | 採否 | 反映 |
|---|---|---|---|
| Q1-1 高 | 構築時検査だけでは稼働中の停止を保証しない | 採用 | `gate_pre_request`=要求ごとに policy 再読(scrape/ops)。live の捕捉 fetcher は 1 要求で短命なので構築時検査のみ(記録) |
| Q1-2 高 | 迂回経路の網羅性 | 採用 | src の `*HttpFetcher(` 構築点が 3 箇所だけであることを静的テストで固定。robots 再取得スクリプト(171)にもゲート |
| Q1-3 高 | ファイル欠落=有効の既定が「壊し方で挙動が逆」 | 一部採用 | 読み先と sha を job/audit に記録。欠落=既定は 172 の任意キーと同じ規約として維持(本番の committed file にはキーがある)。`required` モードは未実装(記録) |
| Q1-4 高 | env で読み先が分離 | 採用 | 絶対パス必須・fingerprint を audit と job に |
| Q1-5 中 | SKIPPED の呼び出し側契約 | 一部採用 | summary に `policy_disabled` を機械可読で追加。front/worker の表示文言・再投入規則は runbook に(front 変更は FR-017 の外) |
| Q2-1 高 | 件数だけでは停止/休催/欠損を区別できない | 採用 | `expected_races`(KYI)と `undetermined` 判定。「取得許可状態(policy)」と「充足状態」は別節 |
| Q2-2 高 | final_odds の定義が確定を証明しない | 記録 | odds の取得元列は無い(V)。`win_odds_official_at IS NULL`+全馬 odds は必要条件。R8/R9 で JRDB 由来か SED 投入 job の有無で確認 |
| Q2-3 高 | 緊急モードと netkeiba 再開の同時選択 | 採用(runbook) | 生成担当は 1 つ・旧経路の job 収束を確認してから切替・衝突は隔離 |
| Q2-4 中 | env 非転記より読み手間の実効値不一致 | 記録 | CLI は policy を直接読み env と突き合わせる(176 と同じ)。audit に policy の fingerprint |
| Q2-5 高 | 再開後の穴埋め手順が無い | 採用(runbook) | 停止期間の記録→日ごとの audit→依存順の冪等再投入→T−3 記録は復元不能と明示 |

**テスト**: scrape unit +12(読み手・構築前拒否・CLI SKIPPED 0 要求・要求ごとの再読・構築点の静的検査・絶対パス/fingerprint)/ ops unit +4 / live unit +3 / scripts +8 / audit 統合 +2。既存 suite は緑だが **093 の seam テスト 2 件は assertion を変えた**(`f._pre_request == policy.pre_request` → `f._pre_request.inner == …`: 共有リミッタの hook が netkeiba スイッチのゲートの内側に入ったため。リミッタの挙動は不変)。

## D26. 実装メモ — Polish(T072〜T075・T077・2026-11-09・worktree)

- T072 `scrape/src/horseracing_scrape/jrdb/README.md`(役割・モジュール表・CLI 表・禁止事項 6・ファイル配置)。
- T073 `jrdb/held.py`+`jrdb-fetch --show-held [--date]`+`jrdb-apply-held FILE_ID --profile P [--yes --replaces ID|none]`+
  `scripts/jrdb_ops.sh show-held|apply-held`+統合テスト `test_jrdb_held_apply.py`(3 件: held → 不変 → 表示のみ → 承認で取消だけ反映・静的値不変 /
  uuid でない id と policy off の拒否 / 承認待ち版・INV-F1 違反・本文欠落の拒否)。**writer 側の修正 1 点**: `_day_files` が `save_extracted`
  の別版名 `KYI260913.<sha12>.txt` を decode できず無言で無視していた(同日に 2 版が並ぶと起きる)→ 平文名で解釈。
- T074 性能見積りは D17。T075 ruff は変更/新規 100 ファイルで緑(scripts 2 ファイルの I001 は main と同じ・放置)、`front/openapi.json`・
  `admin/openapi.json`・`front/ops-openapi.json`・`admin/ops-openapi.json` は HEAD/main とも `git diff --numstat` 0 行。
- 掃除: `live/` の 28 ファイルは ruff format だけの差分だったので HEAD に戻した(R2 の反映リストから外れる)。

**codex 実装レビュー(held 承認・`codex exec -m gpt-6-astra`・質問 2 問・10 件)**

| # | 指摘 | 採否 |
|---|---|---|
| 1 | 承認 commit と writer の分離で「承認済み・未適用」と通常 `fetched` が区別できない | 一部採用: 復旧手順を固定(writer 失敗時は承認をやり直さず同日の writer を再実行・`approval.recovery`)。永続化は列が無いので不採用(0025 不変) |
| 2 | 複数版の承認が並ぶと writer が両方読む(F1 違反) | 採用: 同じ鍵に `fetched` の承認待ちがあれば `pending_version` で拒否。F1 の部分一意制約は 0025 不変のため不採用 |
| 3 | driver 再オープンが variant をまたぐ・race_status を消す | 一部採用: 同 variant に限定。race_status の消去は維持(履歴は ingestion_jobs に残る) |
| 4 | `fetched_at` 順の先頭取得は F1 違反を隠す | 採用: active 2 行以上は `inv_f1_violation` で停止 |
| 5 | 古い held の承認で新しい有効版を無条件に失効させる | 採用: `--replaces <current id|none>` 必須・一致しなければ `current_changed`。版順の逆転(正式の後に速報)は運用者の明示判断として記録のみ |
| 6 | F4 の範囲(superseded 行の race_status) | 記録: audit の INV-F4 は `applied` 行だけを見る(現行どおり)。superseded は履歴として race_status を保持 |
| 7 | 入力差分≠製品表の変更予定 | 一部採用: `note` に明記+runbook。precedence による「更新/no-op/拒否」の予告は writer の dry-run が無いので不採用(R2 以降の候補) |
| 8 | `error and diff is None` だけでは不完全な差分で承認できる | 採用: `error` があれば常に拒否(`held_diff_incomplete`)。`decode_profile`・未解釈行数・重複キー数を出力に追加 |
| 9 | 表示後の本文差し替え | 記録: writer は読んだバイト列の sha で行を引くので F3 は writer 側で成立。承認〜writer 間の差し替えは「行が見つからず書かない」に落ちる |
| 10 | 50 件打ち切りで規模と重要性が伝わらない | 採用: 総数は全行で計算(従来どおり)+`changed_fields_count`(全変更行の項目別件数)を追加 |

採用 5・一部採用 3・記録 2。
