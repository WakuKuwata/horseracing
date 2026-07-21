# Contract: CLI / entry-path flags (076)

全 entry path に同一の 2 flag を足す(FR-017)。既定は現行挙動(FR-004/SC-007)。

## 共通 flag

| flag | 既定 | 意味 |
|---|---|---|
| `--calib-manifest <ABS_PATH>` | なし | 絶対パス必須(相対=エラー・D5)。指定=activation 対象 |
| `--calib-mode {legacy-runtime,manifest-required}` | `legacy-runtime` | `manifest-required` は無効/欠如 manifest で非 0 終了 |

**flag 組合せ**:
- `--calib-manifest` 指定 かつ `--calib-mode legacy-runtime` は矛盾 → typed エラー(fail-closed)。
- `--calib-mode manifest-required` かつ `--calib-manifest` 未指定 → typed エラー・非 0 終了(manifest 必須
  なのに path が無い=fail-closed。runtime fit に fallback しない)。

## 対象コマンド

| パッケージ | コマンド | 注入先 |
|---|---|---|
| betting | `recommend`(recommend-serve) / `recommend-backfill` | `_fit_product_p_calibrator` を manifest 経路に |
| serving | `predict` / `predict-backfill` | `run_serving` / `run_serving_backfill` の stage_discount |
| live | `refresh` | `orchestrate.refresh_range`(`p_calibrator` two_gamma + `stage_discount` 両方) |
| live | `collect-prospective`(065) | prospective-collect 経路(**`p_calibrator` two_gamma のみ**=推薦 win 中心・stage_discount は表示 top2/top3 専用で無関係) |
| api(read-only) | (predictions 応答) | dispersion が manifest **直読**(US3・D10)。CLI ではなく API が読む |
| training | `dispersion-pcal` | verify/inspect 用途に縮退(artifact 生成廃止・直読は API 側) |
| ops | (subprocess) | `runner.py` が serving/recommend argv に `--calib-manifest`/`--calib-mode` を伝播 |

## ops subprocess 伝播(境界維持)

`ops/runner.py` は ML/betting を import せず、`uv run --project {serving,betting} … --calib-manifest
<abs> --calib-mode <mode>` を argv に足すのみ(028/043/053 の subprocess 境界を維持)。ops job payload に
manifest path/mode を持たせ、fixture-first 期は未設定=現行挙動。

## logic_version / 冪等

manifest 由来出力は `;calib=<manifest_digest[:12]>;calibmode=manifest` を付す(data-model §4)。
backfill/recommend の冪等キーに `;calib=<digest>` を含め、別 digest=別 run(silent skip 禁止・FR-010)。

## 検証(parity/leak/fail-closed)

- 各コマンドで `--calib-manifest` 無し = 現行とバイト同等(SC-007)。
- `manifest-required` + 無効 manifest = 非 0 終了・0 行(SC-005・FR-022)。
- backfill: 無効 manifest はループ前に検出(per-day error に飲まれない)。
- 全経路(CLI/live/ops)が同一 digest を解決(SC-011)。
