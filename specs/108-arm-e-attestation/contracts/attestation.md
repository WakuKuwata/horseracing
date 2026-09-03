# Contract: attestation の方式別スキーマと再構成 (108)

## payload 構築

入力 = モデルディレクトリの `metadata.json`(+ 任意の freeze ファイル)+ 引数の `code_sha`。

1. 校正方式を `metadata.calibration` から決定する
2. **legacy 方式**: 現行と完全に同一の payload を組む(キーの追加・削除・型変更を一切しない)
3. **arm E 方式**: **出荷ビューと構成ブロックを分離**する
   - 出荷ビュー: metadata の値を**そのまま**記録する。**欠落を既定値で補完しない**
     (現状の `calib_frac=0.3` / `split_unit=race_count_v1` の捏造を封鎖)
   - 構成ブロック: `protocol_version`・`n_oof_blocks`・mask rate/seed・
     **容量を含む解決済みパラメータ**
   - `calib_frac` は `{requested, effective}` に分けて記録する。`requested` は recipe 宣言値、
     `effective` は booster が実際に使った値(0.0)。**この protocol version では `requested` は
     非挙動項目**であり、挙動一致の比較から除く
4. `n_oof_blocks` / `protocol_version` は `metadata.calibration_protocol` から読む
   (**top-level の `calibrator_degenerate` は読まない** — INV-A6)
5. mask は `metadata.weight_mask` の `rate`/`seed` から読む(`columns`/`unit` は features 層で
   固定のため payload に持たない)

## 検証(fail-closed)

- 共通項目は現行の検証をそのまま適用
- **`method` の値域を既知集合に限定する**(セルフレビュー観点 2 で検出した fail-open の封鎖):
  未知の method 文字列は型付きエラーで拒否する。「OOF でなければ legacy」という 2 分岐にすると、
  将来の第 3 の方式や typo が legacy 検証を素通りする(085 と同型の欠陥)
- `internal_calibration` は**方式で 3 分岐し、経路を完全に分離する**(codex 指摘 6):
  - 既知 legacy(`isotonic`): `0 < calib_frac < 1` かつ `calibration_split_unit` 非空(現行どおり・
    **一切変更しない**)
  - arm E(`isotonic_strict_past_oof`): `protocol_version` が既知・`n_oof_blocks` が正整数・
    `calib_frac.effective == 0.0`・出荷ビューの欠落が既定で埋められていないこと
  - **未知の method / 未知の protocol_version: 拒否**
- **相互整合**(codex 指摘 4): 出荷ビューが OOF を名乗るのに構成ブロックが無い・
  両者の主張が矛盾する・未知の組合せ は**すべて拒否**
- `weight_mask` があるとき: `rate ∈ [0,1]` かつ `seed` が整数。**片方だけは拒否**。
  **metadata に `weight_mask` キーがあるのに rate/seed が読めない場合も拒否**
  (「設定されていない」と「記録し忘れた」を区別する・観点 1)
- 未知のキーは拒否(現行の「unexpected fields」規律を維持)
- 上記いずれの違反も**型付きエラー**。既定値へのフォールバックを作らない

## 再構成

- legacy 方式 → 現行と同じ単純構成の予測器を組む(挙動不変)
- arm E 方式 → **構成ブロックから**組む:
  `ModelRecipe(calibration="isotonic", calib_frac=<既定・非挙動>, split_unit=<既定>,
  params=<記録された容量>, weight_mask_rate/seed=<記録値>)` に
  `CalibSplitFactory(method="isotonic", n_oof_blocks=<記録値>)` を被せる。
  **出荷ビューの値から recipe を組んではならない**(`split_unit=None` は構築不能)。
  `n_oof_blocks`(既定 3)と**容量**(既定 ≠ 900)は**コード既定に落とさない**
- 再構成後、記録された構成との**挙動的一致**を検査し、不一致は型付きエラー(INV-A5)

## 保証境界(codex 指摘 3・明記が要件)

- digest が保証するのは **payload の改竄検出**であって、**記録内容が登録時の真実だったことの
  遡及証明ではない**(登録時の構成識別子が保存されていないため)
- 決定論の担保範囲: OOF 分割規則・行順・mask アルゴリズムはコード側で固定されており、
  payload の `code_sha` と `protocol_version` の組で部分的に縛られる。
  **完全な固定ではない**(codex 部分採用 7)
- **`num_threads` は決定論を担保しない**(T011c で確定した事実): このコードベースのどの経路も
  スレッド数を LightGBM に伝播していない。legacy factory も**要求値と証明書の値の合意チェック**
  として使うだけで、実際のスレッド数は LightGBM の既定に委ねられる。arm E も同じ意味論に
  揃えた(以前は引数を受け取って黙って捨てていた)。したがって
  **一致項目としての `num_threads` は「食い違いを拒否する」保証であって「同じ結果になる」保証ではない**
- 再生成予測と出荷済みモデルの予測照合による強い保証は**採らない**(コストが目的に見合わず、
  比較可能性も確定できない・codex 不採用 8)

## 保守方針

省略軸は「arm E か否か」の **1 軸に限定**する。将来さらに構成が増えても新しい省略軸を作らず、
同じ方式分岐に相乗りさせる(省略ルールが増えるほど「省略」と「欠落」の区別が困難になるため)。

## 禁止

- 旧世代 payload へのキー追加(digest が変わる)
- digest 計算から新キーを除外すること(記録が守られなくなる)
- 検証の緩和による arm E 受理(方式別の厳格化で受け入れる)
- 既存 manifest / 過去 verdict の書き換え
