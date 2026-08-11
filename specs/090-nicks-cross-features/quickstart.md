# Quickstart: ニックス特徴の検証手順

**Feature**: 090-nicks-cross-features

> **本 feature は REJECT で決着済み(2026-08-11)**。特徴定義版の更新と結線は revert 済み
> なので、**下記のうち手順 3〜6(materialize / カバレッジ監査 / 採用判定 / 後始末)は
> 現在の repo 状態では実行できない**(registry に nick 列が無く、CLI サブコマンドも
> revert 済み)。これらは判定時に実施した記録として残す。
>
> **いま実行できるのは手順 1(単体テスト)と手順 2(leak-guard)だけ**で、これらは
> 非結線で保全したモジュールを直接呼ぶため緑のまま。再測定したい場合は、結線
> (registry への 2 列登録 + FEATURE_VERSION bump + materialize への 1 行)を戻してから
> 手順 3 以降を実行すること。

前提: ローカル DB 起動済み(`scripts/stack.sh`・localhost:15432)。作業は worktree
`.claude/worktrees/090-nicks-inbreeding` 内。

## 1. 単体テスト(定義とリーク境界)

```bash
uv run --project features pytest features/tests/unit/test_nick_cross_features.py \
  features/tests/unit/test_projection_blocks.py -q
```

(REJECT 後も **49 件緑**。保全したモジュールを直接呼ぶため結線に依存しない)

期待(すべて手計算 fixture で厳密値を検証):
- 交差セルの残差・縮約・`nick_obs_count` が定義どおり(SC-003)
- 入れ子の部分プーリングが**閾値ではなく連続的**に働く(L0 の観測が増えるほど L1 推定から
  離れて生の交差率へ近づく)
- **leave-child-out**: L1 の推定に当該 L0 セルの観測が入っていない(INV-N10)
- L0/L1 とも前例ゼロなら独立性期待値へ落ち、残差 0・`obs_count=0`
- 期待値ちょうどのセルで残差 ≈ 0
- **自馬除外**: 対象馬の過去実績を変えても出力不変(INV-N2 / SC-002)
- **同日除外・strictly-before**: 同日の他馬・未来の観測を足しても出力不変(INV-N1)
- 父名/母父名が欠損 → NaN、観測が薄い → 親縮約値 + `obs_count=0`(INV-N9)
- 決定性(同一入力 → 同一出力・INV-N4)

## 2. leak-guard(結果・オッズの非流入)

```bash
uv run --project features pytest features/tests/unit -k "leak" -q
```

期待: 対象レースの結果・オッズ・未来レースを変更しても本 feature の出力が不変(SC-001)。

## 3. 実データでのカバレッジ監査(SC-004)

```bash
uv run --project features python -m horseracing_features nick-coverage-audit --json artifacts/090-nick-coverage.json
```

期待の出力: **年別** × **`nick_obs_count` の帯別**(0 / 1-19 / 20-99 / 100+)の件数と割合、
および欠損率(連続的に寄せる方式では「どの段に落ちたか」という離散区分が存在しないため)。
全期間累計の参考値は L0 が 75.6%(n≥20)だが、as-of では初期年ほど下がる。この乖離を
そのまま開示する(過大評価を避けるため)。

> **注**: training 側の既存 `coverage-audit` は 069 の過去市場カバレッジ専用で `--group` を
> 取らない(実査確認済み)。本 feature の監査は features CLI に**新規サブコマンドとして
> 追加**する(タスク T012)。既存 `coverage-audit` は変更しない。

## 4. materialize parity(INV-N6)

```bash
uv run --project features python -m horseracing_features materialize
uv run --project features pytest features/tests -k "parity" -q
```

期待: 事前生成と逐次計算がビット一致(`check_exact` / `check_dtype`)。既存列が
1 ビットも変わらないこと(INV-N8 の純加算検証)も同時に確認する。

## 5. 採用判定(事前登録ゲート)

判定設定を凍結してから 1 回だけ実行する。**実行コマンドの正本は
[contracts/feature-columns.md](./contracts/feature-columns.md) の「判定コマンド」節**であり、
本書はそれを参照する(重複記載はドリフトの原因になるため置かない)。

必須引数の要点: `--from/--to`(これが無いと凍結した評価窓の照合が丸ごとスキップされる)・
`--seed`/`--bootstrap-b`(gate-config からは読まれない)・`--gate-config`(無いと
confirmatory が必ず失敗)・`--subgroups`(無いと判定式の片翼が出ない)。

判定は「採用」「不採用」「判定不能」の 3 値(contracts/feature-columns.md の採用判定契約)。

## 6. 判定後

### 不採用の場合

FEATURE_VERSION の更新と build 結線のみ revert し、モジュールと単体テストは非結線で残す。
その後:

```bash
uv run --project serving pytest serving/tests -q
```

さらに実 DB で運用モデルの予測が判定前とバイト一致することを確認する(SC-006)。

### 採用の場合

運用モデルを更新し、判定に用いた全指標が非悪化であることを記録する(SC-007)。

## 成功条件の対応

| SC | 検証手段 |
|---|---|
| SC-001 | 手順 2(leak-guard) |
| SC-002 | 手順 1(自馬除外テスト) |
| SC-003 | 手順 1(手計算 fixture) |
| SC-004 | 手順 3(年別 × `nick_obs_count` 帯別) |
| SC-005 | 手順 5(3 値判定) |
| SC-006 | 手順 6(不採用時のバイト一致) |
| SC-007 | 手順 6(採用時の指標記録) |
| SC-008 | 実装中の新規取得件数が 0(本 feature はネットワークを一切使わない) |
