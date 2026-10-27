# 166 実DB読み取り専用試運転・独立設計レビュー

2026-10-27。165の保存契約を凍結し、現在の実DB保存済みデータだけで接続・処理時間を検証する166について独立検討した。独立担当は実DB接続やソース変更を行っていない。

## 採用を推奨した設計

実環境のゲート診断と処理時間計測を分ける。165 `DatabaseFeatureReader.capture` は対象結果が現在DBに保存済みなら読み込み冒頭で拒否し、pure builderも対象の結果/着順/上がり/着差を拒否する。過去レースの現DBでそのまま採用まで通すことを求めず、発走済み・結果あり・古quoteで停止する動作を記録する。

処理時間は同じread-only snapshotから得た明示的なコピーで対象結果をマスクし、別のreplay originに固定して測る。マスク項目と元件数/SHAを残す。現在生成特徴、現在の予測時計、現在のDBにある静的属性は当時のas-of資料ではない。過去post・quote取得時刻を変更せず、現在の再実行を過去判断や前向き比較へ昇格させない。`accepted`や新規比較件数を作らない。

接続時から `default_transaction_read_only=on` とし、各snapshotをrepeatable-read/read-onlyにして設定値を照合する。書込の試行でreadonlyを実証する必要はない。実行SQL、snapshot開始/終了、rollback、timeoutを記録し、接続URLや秘密値は出さない。

HTTP/fetcherは使用禁止。既存162/163 `offline_only` / `connection_disabled` はDB socketも遮断するため、そのまま実DB計測に使わず、DBのみを許した境界を使う。polite fetcherも運用状態を書き得るため構築しない。

処理時間には接続、2007年からの全pool読取、既存product特徴生成、source fingerprint、全21本の初回モデルロード/推論、入力/出力canonical化・SHA・fsync/readback・ready記録を含める。pool再利用やwarmモデルの値はcold/full値と区別する。工程別時間に含まれない前処理や失敗を除外しない。OS cacheや実HTTP取得/アクセス待ちは今回制御・計測していない限界として明示する。

比較対象はEVや成否を見ずmetadataで固定する。fresh30だけでなく、165の準備窓360秒（T−15〜T−9）、判断窓60秒、最終guard5秒と比較する。TTL内というだけで実運用可能とは判定しない。

## 固定された検証対象（rootからの通知）

- full-feature benchmark: `202605040907`、15頭、全2007年以降pool。
- guard追加診断: `202604040304` / `202609040611`。
- 実DBprobeで接続既定/transaction readonlyがonであることをroot側で確認済み。
- full featureは1回、warm推論/保存計測は別記。HTTP不使用、current replayにacceptedを作らない。

## 実装後の検証

API・成果物完成後に、独立の境界テストと直接ファイル監査の結果を追記する。

## 実装レビューでの修正点

- 初回run-01は165 guardが正常終了するread-only transactionのCOMMITを166が一律拒否した。COMMITそのものはread-only transactionのデータ変更を意味しない。165を変更せず、同transaction内のreadonly/isolationを再検査したCOMMITだけを許し、台帳へ記録する設計へ修正した。書込SQLとread-write設定は引き続き禁止する。
- ready時刻のclock所属だけでは保存完了の前後関係を保証しない。feature生成→feature ready→model開始/完了→model ready→final→不採用記録を検査し、モデルの消費行列/履歴も保存入力と再結合する。
- timingやSQLの値をmanifestだけで保護せず、monotonic差分・各phaseのstart/end台帳・clock log・実SQL SHAから再計算する。
- probe済みのpandas `read_sql` がSQLAlchemy句をtable名として`has_table`へ渡す型照会は、psycopg client側のProgrammingErrorを内部処理して直後に本SELECTを行う。一般のSQLエラーを成功扱いにせず、完全なSQL SHA、型/parameter key集合、SQLSTATEなし、隣接した同phaseの本query SHA一致だけを `handled_metadata_probe` として分類する。DBserverエラーや別query/別connection/別phaseでは失敗のままとする。
- current replayの採用不可flag、固定target、1800秒TTL、準備窓360秒などをoffline verifyでも照合する。guardの5秒診断は記録されたquery開始→確認時刻から再計算する。

run-02では全履歴・入力・実21本・保存の一式が約60秒で完了した。最終run-03へ向けて上記の独立攻撃を追加した。プロセス初回のモデルロードを含むが、OS/filesystem cacheは制御しておらず、HTTPやアクセス待ち、真の発走前状態、実レースでの時点証拠を検証したものではない。

## 最終run-03の独立検証

独立攻撃テストは **33 passed (3.40s)**、Ruff PASS。DBへ接続せず、完成packetのコピーを使って内部整合性を攻撃した。

- 原quoteの元時計とHTTP/取得区間不足の明示、全頭/明示ID map/原digestを検査。
- 当日全結果だけをcopy上でマスクし、元poolと過去結果・価格を変更しないこと、未来/2007年以前poolを拒否することを検査。
- 1800秒境界・未来quote・過去post・過去判断後の現在予測は採用不可。
- manifestまで更新した攻撃でも、model readyの前倒し、feature ready前の開始、model消費行列、timing、clock行欠落、SQL SHA、maskのas-of昇格、採用flag、TTL緩和を拒否。
- metadata例外の本query SHA・隣接index・型・SQLSTATE・phase・完全parameter key集合を差替えても拒否。

`independent_runtime_audit.py` は166/165本体をimportせず、全ファイル/SHA・code/model pin・実時計/ready順序・quote元取得時計・全出走集合・readonly証拠・SQL SHA・phase時間を直接検算した。既知pandas metadata probeは厳密一致の5件のみ、未処理SQL失敗は0件。15本と6本のnative LightGBM、旧校正isotonicと履歴補正を別計算して保存予測と一致した。公式HTML9レース129頭も別会計で再parse一致、現在replayへ結合していない。

最終値は処理内部全体 **60.4049055秒**、主工程 **59.068990084秒**、peak process RSS **4,313,186,304 bytes**。race68,500 / race_horses970,677 / results965,618 / horses177,726 / MEV935,057行を読み、2007年以降の履歴を縮めていない。元quoteは古いまま、raw HTTP不足も補っていない。比較追加0件・採用不可・新規収集0・DBデータ変更0を確認した。

監査自身の修正2点は本体問題と区別する。preweightの固定3列は `weight / weight_diff / carried_weight_ratio` であり、過去既知の`prev_weight`を欠損にしない。また凍結163監査helperのpandas3文字列欠損比較を、166監査内のframe復元だけobject/None表現へ正規化した。証拠bytes・category語彙・model行列・モデル/sourceは変更していない。この補正後に未知血統を含む実15頭の全native再計算が通過した。

成果物: [独立audit](independent-audit-run-03.json)、[テスト出力](independent-tests-run-03.txt)。実DB/HTTPへ新しく接続したのはroot側のreadonly benchmarkのみで、独立担当は保存された結果だけを検算した。
