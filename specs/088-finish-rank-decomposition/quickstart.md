# Quickstart: 着順の頭数正規化+ラグ分解 bundle (088)

検証の実行手順(implement 後)。前提: ローカルスタックの postgres が起動済み(`scripts/stack.sh`)。

## 1. 単体テスト(ネットワーク・DB 不要)

```bash
cd features && uv run pytest tests/test_finish_decomposition_features.py -q
```

期待: 手計算 fixture([contracts/feature-columns.md](contracts/feature-columns.md) の例 — 8頭出走5着=4/7・15完走最下位=14/17・最下位同着 max<1・trend5 符号・従属等式 INV-C11)・リーク不変(INV-C5)・純加算(INV-C7)・dtype(INV-C8)・範囲異常 NaN(INV-C2a)・072 投影 parity(INV-C10)が全緑。

## 2. 実 DB バイトパリティ(SC-002)

bundle 追加後の build で既存共有列が旧 build(features-018 baseline)と全行一致することの一度きり実測(058/061 方式):

```bash
cd features && uv run python -m horseracing_features materialize --out <絶対パス>/artifacts/features.parquet
```

その後 `scripts/parity_088.py` で features-018 baseline parquet との共有列 check_exact + check_dtype 比較。

## 3. gate-config の凍結 → 診断段(非ゲート)

**先に** gate-config.json を作成・凍結する(閾値・seed・margin・`eval_window`。canonical hash を contracts/adoption-gate.md に追記)。凍結は診断結果を観察する前に完了する(事前登録の汚染防止)。その後:

```bash
cd training && uv run python -m horseracing_training feature-eval --drop-groups finish_decomp
```

観察のみ(fold パターン・効果量)。**この結果がどうであれ手順 4 は必ず実行する**([contracts/adoption-gate.md](contracts/adoption-gate.md))。

## 4. 判定段: pl_topk paired 評価(常時実行)

```bash
cd training && uv run python -m horseracing_training paired-eval --candidate "pl_topk:isotonic:0.3" --active "pl_topk:isotonic:0.3:drop=finish_decomp" --subgroups --confirmatory --gate-config <gate-config.json 絶対パス> --gate-config-hash <hash> --from <eval_window.from> --to <eval_window.to> --json <レポート絶対パス>
```

アームは recipe spec(両側 fold ごと再学習・保存モデル非消費)。`--from`/`--to` は両方必須(片方だけだと窓照合が fail-closed で即停止)。長時間ジョブ=nohup+監視(056 運用ノート)。**verdict = レポートの `gate.adopted` AND `subgroup_guard`**(FR-013a・個別数値の事後読み替え禁止。harness の `DECISION=` 出力は 073 参考値であり乖離時は本式が正)。

## 5. カバレッジ監査(FR-018 / SC-006)

`scripts/finish_decomp_coverage.py` で列別×年別の非欠損率・着順範囲異常の件数・退化(出走1頭)の実頻度を開示。

## 6. 判定後

- **REJECT**: FEATURE_VERSION bump と build 結線を revert → `cd features && uv run pytest -q`(全緑)→ features-018 で materialize 再生成 → 実 DB E2E で active モデル予測バイト一致(SC-004)→ 測定結果(判定段+診断段・カバレッジ)を spec に転記
- **ADOPT**: 本番モデルを学習(`cd training && uv run python -m horseracing_training train-evaluate --objective pl_topk --calibration isotonic --artifacts-dir <絶対パス>`・nohup・相対パス禁止=[[weights-uri-relative-path-ops-bug]])→ compat pin 経路で旧モデル予測バイト一致(SC-005)→ ユーザー承認の上で active 昇格 → 測定結果を spec に転記
