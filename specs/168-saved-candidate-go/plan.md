# 168 保存済み資料からの候補・GO確認

2026-10-27。167の選定条件を変更せず、実DBとローカル原本の保存済みメタデータだけで初回候補とGO項目を調べる。新規収集、live plan/observe/settle、予約、モデル推論、定期処理は起動しない。

- DBは166の接続既定/transaction read-only、repeatable-read、SQL allowlist、HTTP/Python socket遮断を再利用する。開催/出走/結果の有無を読み、オッズ・予測値・勝敗内容は選定に読まない。今日以降の日付または未来postの全行を対象にする。
- メタデータ候補はpost UTC/race_id順。2026年JRA平地、T−45分以上、全頭ID/馬番/status、対象頭数、結果なしを確認する。これだけで全公式カード/IDmap/担当者対応を証明したとはしない。
- 独立担当はDB再接続なしでarchive URL index→raceごとの元取得時刻で最新shutuba原本→titleの本人開催日を読む。将来日付へのナビリンクとsynthetic予定は実候補へ使わない。
- robotsは既存JSONだけを直読みし、cache更新/lock取得をしない。source throttleはcanonical key netkeiba.comをread-onlyで見る。現在の非blockは将来slotや競合なしの保証ではない。
- liveのDATABASE_URL、担当者の時間、候補原本5分/全頭対応などは確認できる範囲を明示する。quote30分・両ready前D・3guard・公式結果は未到来と不足を分け、現在の読取時計や再推論で補わない。
- 候補なし/未確認ありならselected=null、NO_GO_SKIP。live planや予約を作らず、今回の比較データ追加は0。

コードは `scripts/roi_explore/candidate_go_168/{snapshot,access,assessment}.py`、出力は `artifacts/168-saved-candidate-go/run-01`。165〜167のsource/保存成果物は凍結維持する。検証は候補順/UTC/45分境界/除外/robots-Allow/backlogの新規テストと既存DB境界、独立の原本/SQL/台帳/SHA監査を行う。

この確認はスナップショット時点の調査であり、将来の自動GO判定や監視ではない。metadata上の候補が将来生じても、担当者/全原本/起動直前GOが未確認なら自動で選定・登録・収集を開始しない。
