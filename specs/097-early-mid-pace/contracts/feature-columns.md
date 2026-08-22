# Contract: early_mid_pace 列の不変条件

- **INV-EM1(一貫性)**: `rel_early_mid` の定義は全履歴・全距離で単一(`em = finish_time_s −
  last_3f` のレース内相対)。距離により物理的意味が変わることは仕様であり、値の定義は変えない。
- **INV-EM2(独立性)**: `first_3f` 列・`asof_rel_first3f_*`・`asof_pace_balance_avg` の値を
  一切変更しない。新列はそれらを読まない(rel_em は finish_time/last_3f のみから作る)。
- **INV-EM3(リーク境界)**: 対象レースの結果・同日他レース・未来レースの変更で対象行の新列は
  不変(挙動テスト 3 方向)。
- **INV-EM4(欠損)**: em ≤ 0・入力欠損・過去完走ゼロは NaN。0 埋め・平均埋め禁止。
- **INV-EM5(パリティ)**: features-022 ビルドで既存共有 138 列は features-021 ビルドと全量
  バイト一致(check_exact + check_dtype)。
- **INV-EM6(materialize)**: 新規ソース列ゼロ = source_fingerprint 不変。materialize 経路と
  in-memory 経路はバイト一致。
- **INV-EM7(1200m 恒等)**: 1200m の行では走単位 rel_em が rel_first3f(実測由来)と一致する
  (両方非欠損の行で厳密一致 — 導出 backfill と同じ恒等式なので成立しなければ実装バグ)。
- **INV-EM8(手計算 fixture)**: 単体テストは手計算の厳密値で固定し、λ/窓に対して意味のある
  規模の値を使う(090 の教訓: 有限性だけ見るテストはバグを捕まえない)。
