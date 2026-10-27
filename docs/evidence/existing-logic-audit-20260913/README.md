# 既存ロジック監査の証拠

[報告本文](../../existing-logic-audit-20260913.md)。2026-09-13の未修正コードに対する調査。以下は「不具合が再現すること」も確認する診断スクリプトであり、将来の修正後にそのまま成功すべき回帰テストではない。

| 証拠 | 内容 |
|---|---|
| [feature-input-reproduction.py](feature-input-reproduction.py) / [結果](feature-input-results.json) | 馬主masterの時点違反、脚質Unknownの0化、SHA固定111素材の脚質語彙・同じ位置取りへの現行ルール適用・結果完全性の集計 |
| [serving-reproduction.py](serving-reproduction.py) / [結果](serving-results.json) | 実router＋DB戻り値fixtureによる取消後確率、実関数ASTによるmanifest必須設定の抜け |
| [training-reproduction.py](training-reproduction.py) / [結果](training-results.json) | 独立PL有限差分、5木模型の行順復元、HPOのTE不一致、結果件数判定の穴 |
| [gate-reproduction.py](gate-reproduction.py) / [結果](gate-results.txt) | 探索ADOPTの昇格、別recipe／採用不可report、Session代替で旧active残留、135の独立CI計算 |
| [serving-tests.txt](serving-tests.txt) | serving101件＋probability17件の成功したtool出力の記録。記録作成のための再実行はしていない。 |
| [gate-training-tests.txt](gate-training-tests.txt) / [gate-eval-tests.txt](gate-eval-tests.txt) / [repro135-tests.txt](repro135-tests.txt) | 各34／101／65件の実行stdout。 |
| [gate-test-commands.py](gate-test-commands.py) | 上記3グループのテストコマンド。実行すると自身のディレクトリへ新たなlogを書く。 |
| [audit-manifest.json](audit-manifest.json) | 調査時HEAD、検査したsource、保存した証拠のSHA256 |

リポジトリのルートからの再現コマンド:

```sh
training/.venv/bin/python docs/evidence/existing-logic-audit-20260913/feature-input-reproduction.py
api/.venv/bin/python docs/evidence/existing-logic-audit-20260913/serving-reproduction.py
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 training/.venv/bin/python docs/evidence/existing-logic-audit-20260913/training-reproduction.py
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 training/.venv/bin/python docs/evidence/existing-logic-audit-20260913/gate-reproduction.py
```

スクリプトは今回のworkspace絶対パスを含む。別の場所で再現する場合はROOT／REPO定数を移動先へ合わせる。JSON等は実行時の結果を保存したもので、再現コマンドはstdoutへ出す。比較の前提となる既存111／135の素材・補助コードは元リポジトリに必要。

本番DBへの接続、ネットワーク要求、全体の再学習は行わない。gateの保存検証は一時ディレクトリとSession代替だけを使う。training再現には合成24行・5木のLightGBM学習が1回含まれる。今回の調査全体では初回と証拠再生の計2回・10木を実施した。本文の60件の学習unit成功は担当エージェントの完了済みtool結果に基づき、ここに保存した34件のgate-trainingログとは別の実行である。

固定111素材は `source-frames.pkl` SHA256 `7dc539c4224fe4132c3892099f2c62790b47d1f3136fe8b79bbaeadd5c02ef11`、実期間末日2026-08-23。snapshotは `58397ba94dc685c6e49b509619fb447106702d869adebc38889b021f3be6859a`。上限後の実レースや新しい成績を読み込んだ集計ではない。
