# Contract: finish_decomp 列定義(事前登録・凍結)

この 10 列の定義は OOS 実行前に凍結される。OOS 結果を見た後の列の追加・削除・定義変更は禁止(spec FR-010)。定義の正本は [data-model.md](../data-model.md) の表であり、本 contract は不変条件を列挙する。

## 不変条件(単体テストで機械固定)

- **INV-C1(値域と意味)**: `*_finish_pct` 系は NaN でなければ [0, 1]。`finish_pct = 0 ⇔ finish_order = 1`(勝ち)。値は「自分より先に完走した出走馬の割合」であり、非完走馬がいるレースでは最大値が 1 に届かないことを許容する(「1=最下位」は保証しない)
- **INV-C2(退化)**: `n_started == 1` の走の finish_pct は NaN(0/0 を作らない)
- **INV-C2a(範囲検証)**: `finish_order < 1` または `finish_order > n_started` の走の finish_pct は NaN(黙って値を作らない)+件数をカバレッジ監査に出す
- **INV-C3(同着)**: 同 `finish_order` の複数頭は同じ finish_pct(手計算 fixture で固定)。最下位同着で max pct < 1 になるケースを fixture に含める
- **INV-C4(系列)**: ラグ・rolling・expanding・trend は全て完走走のみの系列上で定義され、既存 `prev_finish` の系列規約(finished-only merge_asof)と一致する
- **INV-C5(as-of)**: 対象レース自身・同日・未来の結果を変更しても 10 列は不変(リーク不変テスト、spec FR-005)
- **INV-C6(欠損)**: 観測不足・窓内 NaN は NaN(伝播)。0 埋め禁止(憲法 IV: Unknown と 0 の区別)
- **INV-C7(純加算)**: bundle 追加前後で既存共有列は全行 check_exact + check_dtype 一致(spec FR-007)
- **INV-C8(dtype)**: 10 列とも float64 固定(プール依存 dtype ドリフト防止・026 前例)
- **INV-C9(trend 符号と独立性)**: 着順が改善(finish_pct 減少)している 5 走系列で `finish_trend5 < 0`(手計算 fixture)。x は等間隔 {1..5}・OLS 傾き。4・5 走前の個別ラグは列に採録しない(trend5 が採録列の線形結合にならないための構成条件 — 列を増やす変更はこの条件を壊すため禁止)
- **INV-C10(072 投影)**: `target_race_ids` 指定時の出力 == full build の対象行(check_exact + check_dtype)。n_started(race-level primitive)は投影時も全過去レースで計算する
- **INV-C11(従属の明示)**: `avg_last3_finish_pct` は #1/#4/#5 の算術平均と完全従属(spec FR-002a)。テストで等式を固定し、帰属解釈の注記が消えないようにする

## 手計算 fixture の例(単体テスト)

- 8 頭出走・全馬完走レースの 5 着: finish_pct = 4/7
- 18 頭出走・3 頭中止(15 完走)の最下位完走(15 着): finish_pct = 14/17(1.0 に届かない=仕様)
- 3 頭出走で着順 1,2,2(同着): finish_pct = 0, 0.5, 0.5(最下位同着で max < 1)
- 完走系列 [pct=0.8, 0.65, 0.5, 0.35, 0.2](古→新)の trend5: OLS 傾き = −0.15(改善)
- 2 頭同着 3 着(10 頭出走): 両頭とも finish_pct = 2/9
- avg_last3_finish_pct == mean(prev, prev2, prev3 の各 finish_pct)(INV-C11 の等式)
