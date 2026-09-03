# Quickstart: arm E 系モデルの OOF attestation 対応 (108)

## 前提

- postgres 稼働(`scripts/stack.sh status`)
- 現行 active = arm E 系(`model_versions` の active 行・モデルディレクトリに `metadata.json`)
- 作業ツリーが clean(production スコープの manifest 生成に必要)

## 1. attestation の確認(US1・秒オーダー)

現行 active のディレクトリから attestation を生成し、arm E 構成
(`n_oof_blocks` / mask / `calib_frac=0.0` / `split_unit=null`)が含まれることを確認する。
同時に**旧世代(lgbm-063)の digest が golden 値と一致**することを確認する。

## 2. コスト実測の中断点(US2 前半・FR-018)

OOF 再生成を **1 fold だけ**実行して所要時間を測る。
**ETA が見積(3〜5 時間)の 2 倍を超えたら本実行に進まない** — 実行方式を見直してから再判断する。
実測値は `evidence/oof-eta.json` に記録する。

## 3. OOF 再生成 + manifest 生成(US2 本体・数時間)

全史(2008-2026)で OOF 束を生成し、074 の凍結 gate-config で校正 verdict を測って
production スコープの manifest を作る。nohup でバックグラウンド実行し、完了後に
`evidence/` へ bundle digest・verdict・manifest digest を記録する。

**verdict は測定結果**。two_gamma / stage 割引のどちらが非採用でも次に進む。

## 4. 活性化の検証(US3)

| 確認 | 期待 |
|---|---|
| 既定設定での予測 | 本 feature 導入前とバイト一致(SC-005) |
| 明示有効化 + 有効 manifest | 監査記録に manifest 識別子が現れる |
| 明示有効化 + 旧世代 manifest | 実行前に拒否・1 行も書かれない |
| 明示有効化 + 学習終端以前の対象日 | 拒否される |

## 5. 運用手順の記録(US3・FR-014)

有効化の設定箇所・確認方法・元に戻す方法を記録する。**既定の切り替えは行わない**(FR-015)。

## 検証(SC 対応)

| SC | 確認 |
|---|---|
| SC-001 | 現行 active の attestation → 再構成が記録と一致 |
| SC-002 | 旧世代の payload/digest が完全一致(golden test) |
| SC-003 | 改竄・世代取り違え・構成矛盾の 3 種すべてで拒否 |
| SC-004 | production manifest 1 つ生成・検証通過・digest 再現 |
| SC-005 | 既定設定の予測がバイト一致 |
| SC-006 | 監査記録に識別子・不整合 3 種が実行前に拒否 |
| SC-007 | 旧世代 manifest と過去 verdict が 1 件も書き換わっていない |
| SC-008 | 運用手順どおりに実行して動作する |
