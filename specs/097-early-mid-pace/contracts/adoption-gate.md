# Contract: 097 採否ゲート(実行前凍結)

## 事前登録(gate-config.json に凍結・hash fail-closed)

- **カットオフ**: 2019-01-01 / 2021-01-01 / 2023-01-01 の 3 本(固定・追加/削除禁止)
- **マスク定義**: `race_results.first_3f = NULL WHERE race_date >= cutoff AND distance <> 1200`
  (1200m 導出は本番で生きているのでマスクしない = 定常状態の忠実な再現)。セッション内・
  未 commit・終了時必ず rollback。マスクは first_3f のみに作用し行の適格性を変えない
- **採点窓**: [cutoff+1y, cutoff+2y) = 2020 / 2022 / 2024(互いに素)。washout 1 年 =
  実測で直近 5 完走の 98.1% が cutoff 後 → **estimand は定常状態**と宣言
- **アーム**: 候補 = features-022 全列 / 基準 = 同一マスク環境で drop=early_mid_pace(069)。
  レシピは両方 arm E(OOF isotonic 8 blocks)+ rounds 900 + 091 体重マスク + seed 42・
  fold 内でマスク済みデータから再学習(codex (iii): 学習非マスク+採点マスクは禁止)
- **primary**: 3 窓 pooled の paired race-level winner NLL 差。判定式は v4 標準:
  `point < −0.002 AND seed 分散込み CI 上限 < 0`(sd_fold=0.001816・n_folds=3・
  race-day cluster bootstrap b=2000)
- **guard 1(full-info)**: マスク無し同窓の paired 差で evidence-of-harm(three_way・
  margin +0.003)が出たら FAIL
- **guard 2(実レジーム方向一致)**: 2025-10-11 以降の実劣化窓の paired 差で ci_low > +0.005
  なら FAIL(検出力不足のため成立は要求しない)

## verdict(正本は単一式)

    verdict = primary(pooled) AND guard1 AND guard2
    ADOPT / REJECT。NO_DECISION は評価実行不能の場合のみ

- 個別カットオフの結果による事後選別禁止(068 C2 同型)
- 結果は「反実仮想頑健性」として報告する(過去予測の再構成と読ませない — codex (iv))
- ADOPT は**列の採用**であってモデル昇格ではない(昇格 = 標準窓非劣化 + 本 verdict +
  prospective の 3 点セットで別途)

## 対称性の契約(実行時 assert)

- マスク適用前後で: 採点対象レース集合が同一・各レースの started 集合が同一・winner が同一
- 両アームが同一ビルド(同一 matrix)から列選択のみで分岐している
