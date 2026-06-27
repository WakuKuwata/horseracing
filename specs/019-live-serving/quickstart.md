# Quickstart: ライブ serving (019)

end-to-end 検証ガイド。詳細は [contracts/live_serve.md](contracts/live_serve.md) と
[data-model.md](data-model.md) 参照。

## 前提

- ローカル DB（[[local-db-setup]]）: `DATABASE_URL=postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing`
- active な serving model（[[local-db-setup]] のパイプラインで adopted）。
- 008 netkeiba スクレイピングが動作。**スキーマ変更なし**（migration 追加しない）。

## 検証シナリオ（合成データ中心、ネットワーク不要のテスト）

### 1. fail-closed ガード（US1 / SC-001）

```
live live-serve 999999999999     # 不正 id → 拒否
live live-serve <已走 race_id>    # result-pending でない → 拒否（retrospective を案内）
```

### 2. 未開催レースの live 予測（US1 / SC-005,006）

result-pending の race に対し entries+pre-race odds を投入（テストは合成データ）→ live-serve：

```
live live-serve <pending_race_id> --model-version <mv>
```

期待: prediction_run/race_predictions 生成、新馬/unmapped が出走頭数に含まれ Σ 整合、`race_results` を変更しても
予測不変（リーク境界）。

### 3. live 推奨（pre-race odds, US2 / SC-002,003）

```
live live-serve <pending_race_id>        # 推奨まで
```

期待: 009→010(pre-race)→011/016 推奨が使用オッズ値 + computed_at 付きで append-only 保存、is_estimated_odds=
true（double-pseudo）、live Kelly は shadow 明示。pre-race odds 欠損時はオッズ依存推奨が 0 件（予測は保持）。

### 4. p パリティ + prospective（US3 / SC-004,008）

```
# 過去レースで live 経路と retrospective(run_serving) の予測 p 一致をテストで検証
cd live && uv run pytest -k "parity or leak"
```

期待: live 経路と retrospective の予測 p 一致、race_results 変更で予測不変、生成物が computed_at + 使用オッズ値で
後日 backtest 投入可能。

## 実 DB スモーク

```
live list-pending --date <date>          # result-pending レース列挙（無ければ空）
live live-serve <id>                      # scrape→predict→recommend（ネットワーク到達時）
```

## 受け入れ

- pytest（合成データ）: fail-closed ガード・予測（as-of/Unknown/Σ整合）・推奨（estimated/使用オッズ保存/shadow）・
  p パリティ・リーク境界・決定論。
- 実 DB/ネットワーク到達時のみ live スモーク（不可なら合成テストで代替）。
