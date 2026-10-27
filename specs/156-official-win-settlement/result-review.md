# 156 単勝の公式払戻精算への統一

2026-10-26実装・検証完了。

## 変更した挙動

購入履歴と比較用政策線の単勝支払額を、`OfficialWinPayout.payout_yen × 購入額 / 100`に統一した。政策線は提示時の買い目・金額を引き続き使い、提示オッズや現在オッズを精算に使わない。共通のsettle_betを呼び、支払額の実装を分岐させない。

| 状況 | 扱い |
|---|---|
| 完全結果と勝馬/公式払戻集合が一致 | settled_real。勝馬は個別公式額、敗馬は0円 |
| 同着 | 勝馬全頭の払戻が揃うことを確認し、各馬の個別額で精算 |
| 公式払戻100円 | 正当な的中・元返しとして精算 |
| 無結果／started馬の結果不足 | pending。的中・払戻はnull |
| 公式払戻未保存／一部未保存／勝馬と不一致 | レース単位でunsettleable。敗馬だけを0円で集計しない |
| 馬番重複／不正な着順・状態／出馬にない孤立結果 | unsettleable。辞書化で異常を隠さない |
| 明示取消・除外 | 返還。本人に結果行もある矛盾状態は未精算 |
| 未知番号／結果行のないstarted馬 | 返還しない |
| 中止・失格 | 結果存在として扱う。正常な全結果・払戻があれば単勝は不的中 |

未精算の実購入レースは累積系列から除き、pending件数・金額に別掲する。GET時の再計算を維持しており、後から結果や払戻が保存・訂正されると次の取得で反映される。append-onlyの購入記録は変更しない。

出馬と結果を別クエリで取得して全ID集合を検証する。旧RaceHorse起点outerjoinでは見えなかった孤立結果も検出する。連系券の公式配当/推定配当の方式は継続し、取消と結果欠落を区別する返還判定を共通化した。

任意のsettlement_reasonをAPI応答に追加し、画面に「結果不足」「公式払戻未保存」「公式払戻と結果が不整合」等を表示する。front/adminのOpenAPI snapshotと生成型も同期した。

## 確認した具体例

保存オッズ9.0・提示オッズ5.8・公式払戻600円を分けてseedし、500円購入の払戻が3,000円になることを確認。政策線の200円購入は払戻1,200円・差引1,000円で、旧提示オッズ精算の960円にはならない。

同着の5番1,340円・15番1,960円をAPIテストでも確認し、保存オッズ×100の2,650円・3,960円を使わない。結果後着・払戻後着・公式額訂正が同一の購入記録IDに対して次GETで反映されることも検証した。

## 検証結果

- 精算/購入fold単体、購入API、OpenAPI、read-only制約、race-chaos契約の選択テスト: **89 passed**。
- フロント全体: **405 passed**。typecheck / lint / OpenAPI drift / production buildすべて成功。
- admin: typecheck / OpenAPI drift成功。
- 変更Pythonのruffとgit diff --check成功。
- 独立設計・最終差分レビュー: PASS。独立担当による単体32件も通過。
- PostgreSQLテストは専用testcontainerを使用。運用DBへの書込み、新規データ収集、保存資料の修復、モデル学習は行っていない。

初回実装時点の広いAPI検査では254 passed / 6 failed。失敗はすべて既存`test_dispersion_calib_activation.py`のfixtureが、別の未コミット変更で必須化された`build_manifest(base_model_version=...)`を渡していないことによるTypeErrorだった。この時点では校正fixtureを変更せず、後続のユーザー依頼で下記のとおり解消した。

## 後続対応: 校正fixture修正とAPI全件確認

2026-10-26、校正テストの修正とAPI全体確認の依頼に対応した。

- `test_dispersion_calib_activation.py`と`test_joint_legacy_parity.py`のfixtureに`base_model_version=_MODEL`を明示し、対象モデルと校正manifestの世代を一致させた。該当8テスト成功。
- API全体の初回実行では407成功・1失敗となり、性能テストの順序依存を追加で発見した。性能テストが独自の`_migrated` fixtureで別DBへ環境変数を切り替える一方、seed用の共有engineは先行テストのDBへ接続したままで、APIの読取先と分かれ404になっていた。
- 性能テストも共通のPostgreSQL testcontainer fixtureを使用するようにして、書込先と読取先を統一。性能閾値と計測回数は維持した。
- 最終のAPI全体実行は**408 passed / 0 failed / 0 errors / 0 skipped（32.56秒）**。既存のStarlette/httpxに関する非推奨警告1件のみ。
- 変更したテスト3ファイルのruff、git diff --check、独立差分レビューはいずれもPASS。

APIディレクトリで実行:

```sh
.venv/bin/python -m pytest -q --junitxml=../artifacts/156-official-win-settlement/api-full-suite.xml
```

[最終API全件JUnit記録](../../artifacts/156-official-win-settlement/api-full-suite.xml)。最初の全件実行記録は同ディレクトリの`api-full-suite-initial.xml`に保存した。

## 再現コマンド

apiディレクトリ:

```sh
.venv/bin/python -m pytest tests/unit/test_purchase_view.py tests/unit/test_settlement.py tests/unit/test_purchase_fold.py tests/integration/test_purchase_api.py tests/integration/test_openapi_contract.py tests/integration/test_readonly_invariant.py tests/integration/test_race_chaos_api.py -q
```

frontディレクトリ:

```sh
pnpm test && pnpm typecheck && pnpm lint && pnpm check:openapi && pnpm build
```

旧監査155の仕様・protocol・成果物は上書きしていない。今回変更したpurchase_viewの旧bytesは155のsource-at-auditに保存し、過去の監査時点と区別した。155 protocolの現行ツリーsource検査は今回の正当なsource更新を検知するため、旧sourceでの再現には保存bytesを使用する。
