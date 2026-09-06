# Quickstart: 買い目パターン採否ゲート(109)

## 前提
- Docker postgres 稼働(`scripts/stack.sh` または `docker start docker-postgres-1`)、`DATABASE_URL=postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing`
- OOF 束 `artifacts/oof/8bdde268…/bundle.json` が存在(108 生成物・digest 照合)
- clean tree(`freeze` は dirty を拒否。コードと spec 成果物を先にコミットする=tasks T019)

## 手順(順序固定)

```bash
cd training && uv run pytest ../eval/tests/unit/test_buy_patterns.py ../eval/tests/unit/test_buy_pattern_gate.py ../eval/tests/unit/test_bootstrap_block.py ../scripts/tests/test_buy_pattern_gate_cli.py -q
```
期待: 全緑(列挙 393・重複 9 本不在・p 値/Holm/降格/状態機械・結果並べ替え不変・第二集計・ブロック=1 日で既存関数と 1e-12 一致・注入のレース内排他・CLI の fail-closed 8 点)。

```bash
cd training && uv run python ../scripts/buy_pattern_gate.py freeze
```
期待: `patterns.json`(393 本)と転記済み gate-config、`gate_config_hash` 表示。以後この hash を全段に渡す。

```bash
cd training && uv run python ../scripts/buy_pattern_gate.py selftest --gate-config-hash <H>
```
期待(SC-001): `evidence/selftest.json` に 境界帰無(代表 5 本・全体境界と部分帰無の 2 構成)の誤採用率(点推定と二項片側 95% 下側限界 ≤ 2.5%)、代表 5 パターンの検出力曲線・80% MDE(概算 0.075〜0.08 帯)・降格率(KL 最小傾き / 高オッズ集中 / 開催日集中 + オッズ中立感度)、ρ=0.796 陰性対照、実測所要時間(見込み 4 時間級。外側 20 反復の外挿: ＿＿分 / 実測: ＿＿分)。**ここで exit 2 なら screen に進まない**。

```bash
cd training && uv run python ../scripts/buy_pattern_gate.py screen --gate-config-hash <H>
```
期待(SC-002/003/004): `population.json`(年別件数・流れ図)、対照の確認窓外参考値(発見期/資格期)、393 本の集計と証拠、`survivors.json`(0〜5 本)。

```bash
cd training && uv run python ../scripts/buy_pattern_gate.py confirm --gate-config-hash <H> --survivors-hash <S>
```
期待(SC-002/005/008): 生存者数に関わらず対照が既知帯(cap21_all 0.80〜0.83 / favorite 0.76〜0.80 / no_bet sentinel 1.00)、生存者ごとの状態と感度と `available_at`、`evidence_refs`、`verdict.json`。

```bash
cd training && uv run python ../scripts/buy_pattern_gate.py recompute --window confirmatory
```
期待(SC-004): 証拠だけからの再計算が summary/verdict とビット一致。

## 差分ゼロの確認(SC-007)
```bash
git diff --stat -- db api front betting probability serving features ops admin training/src
```
期待: 出力なし。
