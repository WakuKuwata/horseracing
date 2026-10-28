# ROI モデルを主経路にした後の残タスク(2026-10-27 棚卸し)

前提(2026-10-27 時点で確認した事実):

- レース詳細は 154 で ROI 系(期待回収率・市場混合・注目条件・馬番順の出走表)が主領域、旧予測モデル(mix-129-nj6)と旧方策の買い目は「予測モデルとの比較」タブへ退避済み。front は tsc と vitest 405 件が緑。
- 製品の ROI 値は `mev-ens15-v1` + 141 の市場混合のまま。研究 142〜153 の候補(③完全正規化 127.35% など)はすべて `can_adopt=false / untouched_holdout=false` で製品に入っていない。
- 155〜160 で保存オッズ・精算の監査と検証済み再比較を実施。旧予測を ROI 系に足す価値は確認できず(151: +2.86pt・区間ゼロ跨ぎ / 157: +90 円)。
- 161〜172 で「発走前の同条件比較」を残す手動観測の基盤(契約・保存・実モデル接続・専用 DB role・robots・60 秒 interval)を整備し、取得は `collection_paused=true` で停止中。worker は起動しても pause で終了、ops の更新 POST は 503。
- 現在スタックは全停止(Docker デーモン停止)。142〜172 の specs・scripts・docs・artifacts と 154/156 の front/api 差分、136 系の calib_manifest 差分、172 の stack.sh/launch.json/config がすべて未コミット。CLAUDE.md と memory は 141 で止まっている。

## A. 基盤整備(判断不要・先に片付ける)

1. **未コミット差分を系統別に分けてコミット**。同じ作業ツリーに 4 系統が混在: (a) 154+156 の front/api、(b) 136 系の calib_manifest・mixture(probability/serving/training/scripts/candidate_mixture_build.py)、(c) 142〜172 の研究(specs・scripts/roi_explore・docs/roi-*・新規 scripts)、(d) 172 の stack.sh・launch.json・config/source-operations.json。`git add -A` は禁止(memory: no-git-add-all-shared-checkout)。(b) は別セッション由来なので所有者を確認してから。
2. **CLAUDE.md と memory の追随**。142〜172 の結論(ROI 主・予測は比較用・研究候補は全部不採用・取得停止中)を正本に書く。今は CLAUDE.md を読んだセッションが 141 までしか知らない。
3. **全パッケージのテストを緑に戻す**。141 時点で api/training の一部が (b) の calib_manifest 変更で落ちていた。(b) を確定させてから api/training/probability/serving を通す。
4. **スタックの再開手順を明文化**。`scripts/stack.sh` は 172 の wrapper 経由になり、`collection_paused` を外す条件(承認済み interval 60 秒・robots TTL・担当)が runbook(172 manual.md)にしかない。アプリを「使う」状態と「観測する」状態を分けた起動手順を docs に置く。

## B. アプリの ROI 主経路に欠けている機能

5. **pause 状態の可視化**(**完了 2026-10-27**: `GET /ops/v1/status` を追加し、front の 4 ボタン+購入記録、admin の範囲更新を停止中は無効化して理由を表示。ヘッダにも常設バナー。status 取得失敗時は従来どおり有効=fail-open)。front の更新ボタン・比較タブの「予測する」「買い目生成」は ops が 503 を返すだけで、画面上は壊れて見える。ops に `GET /ops/v1/status`(paused・interval・worker 有無)を足し、front/admin がボタンを無効化して理由を出す。
6. **ROI 主画面からの購入記録**(**完了 2026-10-28**: `RoiPurchaseRecord` を ROI 領域直下に置き、見送り(`presentation_unavailable`・0 点)と自由入力を同じ 106 記録に書く。提示付きの記録と訂正は比較タブのまま。買い目の提案はしない)。154 は ROI 由来の買い目・購入操作を追加しておらず、106 の購入記録ボタンは比較タブ内の折りたたみに入った。実購入の記録(ガードレール 6)が主経路から切れている。主表の近くに「この馬を買った」記録導線を置く。買い目の「推奨」にはしない(研究候補が全部 can_adopt=false なので、候補の列挙と記録に留める)。
7. **日付一覧に ROI の手掛かり**(**完了 2026-10-28**: `GET /api/v1/market-ev/day?date=` を追加し、保存行があるレースごとに per-race と同じ畳み込み(`build_market_ev`)で状態・計算時刻・120%超/市場混合 100%超の頭数を返す。front は日付一覧の各カードに「期待回収率 HH:MM 計算 · 120%超 n頭 · 混合100%超 m頭」か「未計算/再計算待ち/オッズ未取得」を中立表示し、ボード上に「計算済み X / Y レース」の 1 行。並びは会場・R 番号のまま・値での並べ替えなし)。`RaceDayBoard` は market_ev を読まない。注目条件の日次一覧(`/attention/day`)と同型の `/market-ev/day`(計算済み件数・100% 超の頭数・計算時刻)を足し、「今日どのレースを見るか」が一覧で分かるようにする。並べ替えは禁止(021 規律)、件数と有無まで。
8. **ROI 列の前向き現況ページ**(**完了 2026-10-28**: `GET /api/v1/market-ev/prospective` が 158 の抽出規則(quote token 厳密一致・結果未取込で計算・全頭同一 run/計算/観測/booster・観測≤計算<発走・出走馬一致・平地)を製品に移し、2 列(120%超・市場混合 100%超)の目印の馬に 100 円ずつ買ったとして公式単勝払戻で精算した件数と回収率・開催日ブロック bootstrap 区間(138 と同じ impl/b/seed)を返す。front は `/market-ev/prospective` ページ(各列カード・対象の絞り込み表・開催日別表)とレース詳細の注記直下の導線。採否の規則は登録せず 300 点未満は「判断保留」、到達しても判定しない。**実 DB: 保存表が最新計算しか持たないため発走後の再計算で上書きされたレースが 79/156 で、集計対象は 38 レース・8 点(混合は 10/19 以降 12 レース・4 点)**。混合列の集計開始日 `MIX_PROSPECTIVE_START_DATE=2026-10-19` は eval/market_mix.py に定義)。141 の注記は「発走前オッズでの検証はこれから記録」と書いたまま。材料は DB にある(140 の quote 来歴 `oddsobs=quote`・market_ev_predictions・official_win_payouts)。158 の抽出規則(真正 quote・発走前計算・全頭同一 snapshot)を API に移し、件数と公式払戻精算を「判断保留」の表示で出す。38 の注目条件には同種の進捗表示が既にある。
9. **荒れ度を ROI 主画面に戻す**(**完了 2026-10-28**: `GET /api/v1/races/{id}/chaos` を追加(084 の `build_race_chaos` を同じ引数で呼ぶだけ・`/predictions` の `race_chaos` とオブジェクト一致をテストで固定)。front は ROI 領域の注目条件一覧の直後に `RaceChaosPanel` を置き、比較タブからは外した(ページ内 1 箇所)。初回表示で `/predictions` は引き続き叩かない)。084/066 の荒れ度は市場 q 由来で ROI 系と相性が良いが、`/predictions` 応答に同居しているため 154 で比較タブに移った。`/races/{id}/chaos` に分離すれば主画面で出せる。
10. **モデルの用途ラベルを DB に持たせる**(**完了 2026-10-28**: 057 の CLI `uv run python -m horseracing_training set-model-label --model-version mix-129-nj6 --display-name '比較用: 6モデル平均 mix-129-nj6' --purpose '比較用(2026-10-27 の役割決定)…'` でローカル DB に記録(従来の技術説明は purpose 内に保持)。front の比較タブは `available_models` の display_name を読み、注記は表示中モデル・買い目見出しは採用モデルのラベルで組む(DB にラベルが無いときだけ従来の「旧予測モデル」)。admin は 051 で既に display_name/purpose を表示。**ROI 用 `mev-ens15-v1` は model_versions に行が無い**(別系統の artifact で、勝率モデルの registry に偽の候補行を作ると昇格/互換検査の対象になるため作らない)。ROI 側の名乗りは API 応答の `model_version` と注記の固定文言のまま)。057 の `display_name/purpose` は空のまま。「ROI 用(mev-ens15-v1)」「比較用(mix-129-nj6)」を model_versions に記録し、front/admin の見出しをハードコードから置き換える。
11. **admin の被覆率を ROI 基準に**。coverage API の「予測済み」は旧 active モデルの run の有無。ROI 主なら market_ev 計算済み・mix 可用・quote 来歴ありの列を足す。
12. **shadow-log(065)の扱い**。経路未結線で構造的に 0 のまま。ROI 列の発走前凍結に付け替えるか、ページを閉じるかを決める(8 と統合できる)。

## C. 利用者の判断が要るもの

13. **前向き比較の開始可否**。161〜172 で道具は揃い、残りは「対象レースの将来出走表が保存されていない(168)」「担当時間」「robots TTL(10/28 15:59 失効)」「`collection_paused` 解除の承認」。最初の 2 開催日は試運転扱い(161 §6)。開始しない限り、ROI 系の発走前成績は永久に 9 レースのまま。
14. **製品 ROI 値の更新規則を事前登録**。③完全正規化(127%)や予測後正規化(148)は既読期間の点推定で、採用規則が存在しない。「前向き比較で何が出たら表示値を変えるか」を 13 の開始前に決める。
15. **旧予測モデルの自動生成を止めるか**。151/157 で ROI 追加価値なし。worker 再開時は refresh→predict_auto の自動追随が走り、1 レース 24〜60 秒の CPU を比較用の値に使う。ops の既定を「expected_return は自動・predict は手動」に変えるか決める。
16. **過去データの価格修復**。155 で研究用 parquet に確定値と異なる価格が残ると判明。139 のアーカイブ無し 504 レースの修復は許可待ちのまま。研究母集団の信頼度に効くが、製品表示には効かない。

## D. 期限があるもの

17. **12 月: 2027 年用の係数と booster**。141 の混合係数(a, b)は年次凍結で 2027 年分が無い。MEV の 15 seed も 2026 年用。bundle が main から再現不能(memory: bundle-build-unreproducible-from-main)も未決着で、12 月の更新前に builder を直す必要がある。
18. **robots cache**: 10/28 15:59 JST に失効。観測を始めるなら直前に 171 の手順で再取得。

## 優先順位(提案)

1. A1〜A3(コミット・正本更新・テスト緑)。**完了(2026-10-27)**。
2. B5(pause 可視化)と B6(購入記録の導線)。**どちらも完了(2026-10-27/28)**。
3. C13・C14 の判断。前向き比較を始めないと ROI 系の正直な成績は増えない。
4. B7・B8・B9(一覧・前向き現況・荒れ度)。**B7・B8・B9・B10 完了(2026-10-28)**。
5. D17 を 11 月中に着手。
