# 163 独立設計・実装レビュー

2026-10-27。対象は新規収集を始めない、実モデル接続・停止中取得アダプタ・実時計/ローカル保存先の検証。

## 事前の独立設計意見

- 162は凍結した合成予測・疑似時計の試運転として保持し、163は`offline_model_connection`等の別契約とする。実時計でも`eligible_for_prospective=false`を維持する。
- 保存済みquoteの当時取得時刻をロード時刻へ更新してはならない。新しいローカル合成quoteを使う場合はlocal fixtureとして明示し、実配信の取得と区別する。
- MEV15seedと旧mix129の6memberを実行し、その計算に渡した全特徴行列、全頭出力、モデルSHA、計算開始/終了時計を残す。旧preweightの体重関連null、市場非入力を維持する。
- 予測計算完了と耐久保存完了を分ける。両予測/quoteのfsync・readback後のreceiptが判断以前であること、decision/sealの保存確認でもquote ageが1800秒以内であることを検査する。
- 実時計はUTC wall timeとmonotonicの組で観測し、逆行・極端なジャンプを拒否する。疑似post時刻を現在の仮レースに置く場合、実レースの前向き観測としない。
- 実時計の仮未来postセッションへ過去公式結果を付け替えてはならない。公式精算は別の過去decision/raw HTML replayで当時の時計を保存するか、合成公式fixtureとして明確に分ける。未来postを待つ必要はない。
- 公式HTMLのhorseIDとcanonical IDは推測結合しない。`nk:`除去は同一性の証明にならない。必要なら当時の明示mappingを保存し、ない場合は未精算にする。
- 取得アダプタは実transportを起動できない構成にし、`enabled=True`等の値でも新規通信を許可しない。CLIへcollect/live/schedulerを足さず、ローカル再生のみを許す。
- 1レース1判断の使用入力証跡に範囲を限定し、一般的なオッズ時系列やlatest DBの変更に広げない。

## 最終検証

実装完成後の独立テスト・成果物再計算で追記する。

## 最終独立検証結果

**PASS（新規収集を行わない実モデル/実時計接続の検証範囲）**。

対象: `artifacts/163-offline-model-connection/run-03`。manifest SHA256:
`189af7554d60117efeee84214e0fb0e8d8aceb0bdfe3485fb58cd2fb0d67579c`。

独立攻撃テスト `scripts/tests/test_model_connection_review_163.py` は **14件通過**。主実装のcontract/store/selector/settler/model adapterをimportせず成果物を読む `independent_connection_audit.py` もPASSした。親の統合検証では新規90件＋既存168件、計258件通過と報告されている。

### 成果物の別計算

- source・モデル本体・全保存物SHAと、入力→ready→decision→seal→admission参照を確認した。
- 実UTC時計とmonotonic観測の非逆行・経過差1秒以内、各取得/予測/保存時計が観測ログに存在することを確認した。
- 両予測の入力利用・計算開始/終了・全行created/updatedがready以前、readyが判断以前である。判断/封印はいずれも仮発走前の固定窓内で、quoteから封印まで1.617920秒。全頭集合は3頭で完全一致した。
- MEVは77列（66数値＋11カテゴリ）の入力を独立再encodeし、15本の保存Boosterをnative LightGBMで実行した。各memberと15平均が保存出力に一致した。
- 旧6memberは保存行列とプリプロセッサからカテゴリ/target encodingを別計算した。native Booster出力を独立にrace-softmaxし、保存isotonic閾値を補間、clip/正規化、厳密過去historyの補正項と残差補正、6平均を別計算して一致した。main model adapterの出力をそのまま正解にはしていない。
- 両腕の固定係数による確率、EV、購入順位・買い目を独立計算して一致した。
- 現時計の仮未来レースは公式資料がないためpendingで、両腕の払戻/利益はnull。過去資料を現在sessionへ結び付けていない。
- 過去9レース/129頭の公式精算は別モードで検算した。凍結した当時のdecision/settlementの対応を使い、保存HTMLの単勝払戻を独立に読み直した。両腕とも8点・800円・公式払戻2,590円・利益1,790円に一致した。元の観測時刻を変えず、架空の過去sealは追加していない。

### 攻撃的検証と解消を確認した問題

- 停止adapterの属性を後からenabledへ書き換えてもfetchは拒否し、socketを作らない。
- 保存quote原文のCRLFを含むbytes SHA、元取得時計、saved_response originを保持する。読み直し時刻による鮮度更新はしない。
- エラーstatusに全頭データが残る応答や、明示されたbody race_idがURL/予定と異なる応答を拒否する。
- real clockの保存観測でprocess epoch変更・時計逆行を拒否する。
- 実モデル推論後のp改変、encoded matrixを再hashした改変、消費入力と異なるraw feature、旧予測1行のみ未来更新、体重を後付けする操作を拒否する。
- 入力dictのkey順がcanonical JSON保存で変わる問題を確認し、固定列順によるDataFrame構築後、保存前後のconsumed matrix一致が通ることを検証した。
- 仮未来postの現時計sessionに未来のobserved_atを付けた公式形式fixtureを今保存できる穴を独立テストで再現した。store入口の`observed_at <= clock.now()`検査追加後、証跡公開前に拒否することを確認した。
- actual model読込後のnative状態reset追加でのexit139診断では、その新規resetを除去し、既存product経路とpredict時1threadを使う案を支持した。除去後のsmoke・保存既存入力parity・本独立native再推論は成功した。resetが唯一のC側原因と断定はしない。

### 限界

これは**実モデル本体を実時計・実ローカル保存先へ接続したオフライン検証**である。現在の価格/非市場入力は明示的に合成したlocal fixture、postも仮時刻であり、新しい実レースから取得した資料ではない。`eligible_for_prospective=false / comparison_data_added=0`が適切に固定されている。

過去精算のHTML原文は保存source horse IDを持つが、本replayでは原文単勝額と凍結canonical decision/settlementを照合した範囲に限定する。後からsource↔canonicalのID対応を作った、または過去の欠けた全入力/commit時計を再現したとは主張しない。

保存原文/時計の自己申告真正性、sourceの外部認証、実レース入力の利用可能性、実配信の動作・約定、敵対的な全証跡再生成の防止、物理ディスク障害はこのオフライン検証の証明範囲外。実時計でもローカルfixtureを実取得へ昇格させない点が重要である。
