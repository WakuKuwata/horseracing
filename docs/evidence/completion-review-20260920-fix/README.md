# 133 完了監査指摘の修正確認（2026-09-20）

登録期間、正式判定の主条件、単一最終判定の保存を修正した。事前登録、必要改善幅0、既存の途中集計と入力記録は維持している。135の学習や本番モデル切替は行っていない。

## 修正と検証

| 指摘 | 修正 | 確認 |
|---|---|---|
| 期間外レースで必要開催日数を満たせる | 記録選択・純粋採点の両方で登録窓を強制。両端を含み、不正日付も除外 | 実登録の「期間外1日＋期間内23日」は23日・NO_DECISIONに変化 |
| serving副条件でも正式判定が出る | 登録primary以外はSECONDARY_SUMMARY_NOT_A_VERDICT、verdict=null | 実登録の期間内24日でも副条件は判定なし |
| 出力先を変えると再度FINALを作れる | 登録SHAごとの固定最終記録、計算前からのプロセスロック、保存済み結果の再出力 | 並行2プロセスで計算1回、別出力も同一結果。出力失敗後もDBを再読せず復旧 |

判定不能のNO_DECISION・interim・副条件は最終枠を消費しない。正式結果をユーザー指定先より先に保存し、最終記録の破損時は停止する。診断rehearsalは純粋関数でもinterimだけに限定する。`final_verdict`のregime引数は必須で、CLIからの重複判定も解消した。

- [合成データの修正後再現](paired-boundaries-after.json)。修正前の[再現コード](../completion-review-20260920/reproduce_paired_boundaries.py)をそのまま実行した。
- [既存途中集計の不変確認](interim-parity.json)。修正前と同じDB読み取り専用snapshotで選択72件、全体・条件別・head指標・レース別値・除外・保留・判定状態が完全一致。集計時刻だけを除いた。
- [結果・属性のsnapshot](interim-input-snapshot.json)と[DB不要の再検証コード](verify_interim_parity.py)を保存した。過去の保存interimとも完全一致する。結果ラベルの正しさを独立監査したという意味ではない。

修正後は対象25テスト、serving全223テストPASS。ruff checkもPASS。独立担当は実装を読み、期間・primary・保存制御のblocking指摘なし。追加の並行プロセステストとCLIの再出力時DB不読テストは主担当が確認した。

リポジトリ直下で再実行するコマンド:

```bash
serving/.venv/bin/python -m pytest -q serving/tests/unit
serving/.venv/bin/ruff check serving/src/horseracing_serving/paired_prospective.py serving/tests/unit/test_paired_prospective.py
serving/.venv/bin/python docs/evidence/completion-review-20260920/reproduce_paired_boundaries.py
serving/.venv/bin/python docs/evidence/completion-review-20260920-fix/verify_interim_parity.py
```

事前登録SHA256は `b7352356d031582239a2596039fb48a77514fa7315832a940356bd1bb537b11b` のまま。修正前の監査結果は元のディレクトリに残している。135のquickstartには9/20専用実行と保存隔離環境のpreflightへの入口を追記した。

正式な将来比較は引き続き窓終了（12/28 JST）と24開催日等の条件を待つ。この修正で比較実験そのものが完了したという意味ではない。最終記録の単一性は同じ保存ディレクトリを使うCLI実行で保証し、ディレクトリ自体は継続利用・保全する。
