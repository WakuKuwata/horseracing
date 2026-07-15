# Quickstart: Evaluation Contract v2 & Historical Freeze

**Feature**: 073 | 目的: 評価契約の正しさ修正が「新データ・スキーマ変更・再学習なし」で成立し、既存 active の serving 予測がバイト不変であることを実 DB で検証する。

前提: ローカル Postgres(horseracing DB)・既存 active モデル artifact。詳細は [data-model.md](data-model.md) / [contracts/cli.md](contracts/cli.md) / [research.md](research.md)。

## 0. active model を DB で確定(D8・着手ブロッカー)— ✅ 確定済み(2026-07-15)

実 DB(`docker-postgres-1` / `horseracing` DB)で確認した結果:

| 項目 | 値 |
|---|---|
| **active model_version** | **`lgbm-063`**(`adoption_status='active'`。lgbm-062 は retired) |
| feature_version | `features-017` |
| train_through | 2026-07-05 |
| weights_uri | 絶対パス `…/artifacts/model_versions/lgbm-063/model.txt`(weights-uri 相対パス問題は解消済み) |

**parity oracle 用 artifact digest(SHA-256)** — lgbm-062 と lgbm-063 で **model/calibrator/preprocessor は byte 一致**(metadata.json のみ差、版・パス記録のため):

| ファイル | SHA-256 |
|---|---|
| model.txt | `1a85b03519a7ed78d4c1457c96c11c5652e7a9deee4a159fdb8d00d15c9cb348` |
| calibrator.pkl | `4babdda763605c1300c39d1b73ebc233e22030d46cef74c39afdc9931826d4b6` |
| preprocessor.pkl | `cf1d518dae1cc60ff3bd6f7161d21e9b2280bc5773a7a91e46ab4fecb9559711` |

→ US2 の legacy 凍結レコード(T021)と SC-005 の parity oracle は **lgbm-063** に固定。着手ブロッカー解消。

確認クエリ: `psql -U aiuma -d horseracing -c "SELECT model_version,feature_version,weights_uri FROM model_versions WHERE adoption_status='active';"`

## 1. split の recipe 化と legacy 凍結(US2)

- `race_count_v1`(既定)の recipe_hash が既存値と **byte 一致**(back-compat canonicalization)。
- `race_day_v1` を指定すると recipe_hash と model_version が **必ず変わる**。
- 同一 model_version で split を変えた再学習が **拒否**される。
- **最重要**: 確定した active の serving 予測を feature 前後で比較 → **16 頭サンプル mismatch 0**(SC-005)。

期待: 再学習・昇格・active 書換なしで legacy が `race_count_v1` として凍結される。

## 2. 採用判定の三値化(US1)

```
uv run --project training python -m horseracing_training.cli paired-eval \
  --candidate <recipe> --active <active> \
  --from <D> --to <D> --gate-config <path> --subgroups --seed 42 --num-threads 1 --json
```

期待:
- 出力が単一 enum `decision`(ADOPT/REJECT/NO_DECISION)。operator の手作業 0。
- 期間・開催日・subgroup 標本が不足するデータ → `NO_DECISION`(黙って PASS しない)。
- `--confirmatory` で gate-config hash 不一致 → 型付きエラー(fail-closed)。
- 監査 JSON に contract version・gate-config hash・source/result/race-set hash・recipe hash・checksum・started-all 集合・決定論証跡。
- `ece_by_subset` が全体 + 4 帯 + 共通 tail(≥5 サブセット)。段は raw と model-internal calibrated まで(two-gamma/stage discount は 074)。

## 3. 決定論(US1・SC-003)

同一 seed・単一 thread で paired-eval を 2 回実行 → winner NLL・paired 差・CI の絶対差が事前登録許容誤差(< 1e-9)内で一致。

## 4. started-all 統合(US1・FR-003)

harness 本体で started-all(DNF/失格=win0)評価が選択でき、paired 側と同一意味論。監査 artifact に started-all 集合と除外理由。

## 5. bootstrap 改名 + 感度(US3)

- `race_day_cluster_bootstrap_ci_v1` の数値が旧 `moving_block_bootstrap_ci` と **完全一致**(golden test)。
- v2 感度(2/3/4 日・週・開催)が diagnostic として併記され、gate の AND ではない。
- 068/069/070 の verdict が `evaluation_contract_version=v1` として**上書きされない**。

## 6. 070 凍結 + dormant 事前登録(US4)

- 070 status matrix(F03/F04/F05 rejected/unwired)が append-only supersession として固定・過去文書は不変。
- 2008–2026 が development evidence と明記。
- prospective holdout が `DORMANT` で器のみ存在・実計測は未開始。

## 受け入れ判定(Success Criteria 対応)

| 検証 | SC |
|---|---|
| 三値 enum が operator 判断 0 で得られる | SC-001 |
| 不足入力が全て NO_DECISION | SC-002 |
| 2 回実行の指標差が許容誤差内 | SC-003 |
| 監査 artifact が必須 8 項目 | SC-004 |
| active serving 予測 16 頭 mismatch 0 | SC-005 |
| split 変更で recipe_hash 変化・同 version 再学習拒否 | SC-006 |
| bootstrap 改名の数値一致 + ≥2 感度 | SC-007 |
| ECE ≥5 サブセット | SC-008 |
| 070 が昇格経路から参照 0 | SC-009 |
| 過去 verdict 上書き 0 | SC-010 |
