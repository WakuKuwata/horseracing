# 既存ロジック修正の記録

2026-09-13。調査で確認した不具合に対する修正。新しい予測特徴量やモデルは追加していない。
作業ブランチは `codex/existing-logic-fixes`、元のコードは `a275e85fd2268d4d985bbc94f7040f75837a4fbb`。
元mainで進行する135の再現性検証と分離するため、`horseracing-logic-fixes` worktreeで実装した。

## 修正した挙動

| 箇所 | 修正前の問題 | 修正後 |
| --- | --- | --- |
| 採用ゲート | 探索的なADOPT、明示的な不採用フラグ、別の候補向けレポートを通常昇格へ流用できた | 確認評価・厳密なboolean・全サブグループの保証を必須にし、候補・比較対象それぞれの評価時と登録時の実学習契約を照合する |
| モデル登録・昇格 | ACTIVEの重複や、登録後の本体差替えを防げない | 既存ACTIVEがあれば新規登録は候補に留める。通常昇格で登録時のモデル・校正器・前処理・metadataのSHAを再検査し、切替を直列化する |
| 取消後の予測 | 保存確率から取消馬だけを除くため、win/top2/top3が現在の出走集合と不整合になる | 選択済みrunの馬ID集合が現在のSTARTED集合と完全一致しなければ失効。API・推薦は既存の空応答を返し、再推論で復旧する |
| 自動再計算 | 取消後も「予測済み」と判定し、再計算をskipする | 同じモデル・校正・入力条件の最新runについて出走集合を検査し、不一致ならforceなしでも再計算する |
| 校正必須設定 | manifest未設定時にlegacy処理へ戻る | 予測・推薦・期間更新の起動前に設定エラーにする。設定欠落は無駄なretryをせずFAILEDとして記録する |
| HPO | CVの学習行が自分の正解を含むTEで符号化され、最終学習のOOF処理と異なる | HPOも共通OOF処理を使用。検証行のencoderはそのCVの学習部分だけでfitする |
| 結果完全性 | 取消馬の古い結果が、出走馬の結果欠損を件数上相殺できた | race IDとhorse IDの両方でSTARTED馬の結果だけを数える。中止・失格は結果ありとして維持する |

取消後の復旧では、各確率を単純な倍率で補正しない。同じ頭数でも馬IDが入れ替われば再推論が必要。
古い一致runへの自動fallbackも行わない。通常のAPIは200のtyped empty、明示モデル指定は既存404、推薦は`no_run`となる。

## 採用証拠の扱い

確認評価の新しいJSON出力に、実際の学習契約と確認条件の来歴を追加した。数値ゲート、旧recipe hash、凍結レポートは変更していない。
walk-forward各期と最終全期間学習は学習データが異なるため、booster本体の同一SHAは要求しない。学習手順の一致と、登録した最終本体が配布時に変わっていないことを別々に検査する。

旧モデルにはこの来歴がないため、通常昇格の証拠を後付けで捏造しない。既存の理由付きoverrideを残し、その理由を記録する。
対応外の派生学習器についても、通常学習器と同じ契約だと仮定して承認しない。
HPOでは評価期と最終学習で最良パラメータが変わり得るため、探索した候補設定と選択手順を照合する。データに依存する最終選択値の一致は要求しない。

## 検証記録

各package専用のPython 3.12環境をoffline lockfileから構築した。DB検証はSQLiteの小さいfixtureまたは一時PostgreSQL containerで行った。
稼働DBの書換え、本番モデルの切替、大規模再学習は実施していない。

- 学習・結果完全性: 対象training 58件、eval 7件成功。HPO/finalのOOF一致、結果IDの交差、DNF/失格、日付範囲を確認。
- Ops: unit全体と校正引数・worker recovery/livenessの対象integration、合計56件成功。capture前の設定検証を追加した後、関連unit・predict capture/flow・校正引数の47件も成功。通常の一時障害retryは維持。
- API: 取消回帰と既存対象22件成功。追加unit/integrationで既存fixture不足が判明し、固定digestのchaos JSON 2点だけを原本とSHA照合してコピー。関連26件再実行が成功し、未解消失敗なし。
- 自動再計算: 最終コードで取消回帰・既存backfill・入力条件別skipのintegration全19件成功。実特徴量生成・モデル推論・保存まで実行し、出走復元時も古いrunへ戻らず再計算することを確認。
- 採用ゲート: 対象unit 126件、実PostgreSQL integration 2件成功。小さい実LightGBMで確認評価→最終再学習→登録→通常昇格まで確認。保存中のDBロック保有、別接続がACTIVEへ変更した後の古いキャッシュによる上書き拒否、昇格計画後のbinding変更拒否も確認。

同一テストを含む検証があるため、上記件数は単純合算しない。APIの固定JSONコピーはテスト用で、配布するsource差分に含めない。
対象source・testのruff、および`git diff --check`も通過した。独立レビューの指摘を反映し、未解消の対象テスト失敗はない。

再実行に使った主なコマンド（各packageディレクトリ内で実行）:

```sh
# serving/
.venv/bin/python -m pytest tests/integration/test_backfill_population.py tests/integration/test_predict_backfill.py tests/integration/test_weight_regime_idempotency.py -q
# ops/ 初回
.venv/bin/python -m pytest tests/unit tests/integration/test_ops_calib_argv.py tests/integration/test_worker_recover.py tests/integration/test_worker_orphan_liveness.py -q
# ops/ capture前検証追加後
.venv/bin/python -m pytest tests/unit/test_calibration_required.py tests/integration/test_predict_capture.py tests/integration/test_predict_flow.py tests/integration/test_ops_calib_argv.py -q
# api/
.venv/bin/python -m pytest tests/integration/test_prediction_population.py tests/unit/test_selection.py tests/integration/test_predictions_api.py tests/integration/test_predictions_market_q.py tests/integration/test_recommendation_scope.py -q
# training/ 実fit・保存・昇格・キャッシュ競合
.venv/bin/python -m pytest tests/integration/test_promotion_binding.py -q
```

## 精度検証が残る項目

脚質の`後方`と`差し/追込`の意味の統一、馬主マスターの時点整合、現行top-k校正の材料選択は、既存6モデルの入力・校正を変える問題として残る。
当時の馬主データを現在値から復元したり、定義の異なる脚質を単純に同一視したりはしない。
Unknown脚質だけの修正は132の最新年で悪化しているため、その変更を今回の不具合修正へ混ぜていない。

これらは定義と校正材料の来歴を固定し、再学習と未使用期間の比較で判断する。今回のテストは不具合と正常経路の回帰検証であり、予測精度の向上幅を実測したものではない。
HPOは現行6モデルでは未使用で、取消結果による件数相殺も固定111素材では0件だった。この2点は潜在不具合の修正であり、現行モデルの誤予測の原因と断定していない。
