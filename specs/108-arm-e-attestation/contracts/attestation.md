# Contract: attestation の方式別スキーマと再構成 (108)

## payload 構築

入力 = モデルディレクトリの `metadata.json`(+ 任意の freeze ファイル)+ 引数の `code_sha`。

1. 校正方式を `metadata.calibration` から決定する
2. **legacy 方式**: 現行と完全に同一の payload を組む(キーの追加・削除・型変更を一切しない)
3. **arm E 方式**: `internal_calibration` を arm E 形(`calib_frac=0.0` / `split_unit=null` /
   `n_oof_blocks`)で組み、mask が設定されている場合のみ `weight_mask` キーを挿入する
4. `n_oof_blocks` は `metadata.calibration_protocol.n_oof_blocks` から読む
   (**top-level の `calibrator_degenerate` は読まない** — INV-A6)
5. mask は `metadata.weight_mask` の `rate`/`seed` から読む(`columns`/`unit` は features 層で
   固定のため payload に持たない)

## 検証(fail-closed)

- 共通項目は現行の検証をそのまま適用
- **`method` の値域を既知集合に限定する**(セルフレビュー観点 2 で検出した fail-open の封鎖):
  未知の method 文字列は型付きエラーで拒否する。「OOF でなければ legacy」という 2 分岐にすると、
  将来の第 3 の方式や typo が legacy 検証を素通りする(085 と同型の欠陥)
- `internal_calibration` は**方式で 3 分岐**:
  - 既知 legacy: `0 < calib_frac < 1` かつ `calibration_split_unit` 非空(現行どおり)
  - arm E(OOF): `calib_frac == 0.0` かつ `calibration_split_unit is None` かつ
    `n_oof_blocks` が正整数
  - **未知: 拒否**
- `weight_mask` があるとき: `rate ∈ [0,1]` かつ `seed` が整数。**片方だけは拒否**。
  **metadata に `weight_mask` キーがあるのに rate/seed が読めない場合も拒否**
  (「設定されていない」と「記録し忘れた」を区別する・観点 1)
- 未知のキーは拒否(現行の「unexpected fields」規律を維持)
- 上記いずれの違反も**型付きエラー**。既定値へのフォールバックを作らない

## 再構成

- legacy 方式 → 現行と同じ単純構成の予測器を組む(挙動不変)
- arm E 方式 → OOF 校正つきの合成予測器を組む。`n_oof_blocks` と mask rate/seed を
  attestation の値で設定する(**コード既定に落とさない**)
- 再構成後、記録された構成との一致を検査し、不一致は型付きエラー(INV-A5)

## 保守方針

省略軸は「arm E か否か」の **1 軸に限定**する。将来さらに構成が増えても新しい省略軸を作らず、
同じ方式分岐に相乗りさせる(省略ルールが増えるほど「省略」と「欠落」の区別が困難になるため)。

## 禁止

- 旧世代 payload へのキー追加(digest が変わる)
- digest 計算から新キーを除外すること(記録が守られなくなる)
- 検証の緩和による arm E 受理(方式別の厳格化で受け入れる)
- 既存 manifest / 過去 verdict の書き換え
