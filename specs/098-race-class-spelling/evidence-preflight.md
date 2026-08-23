# evidence-preflight (T001) — 2026-08-23

- active = lgbm-094-cap900 / feature_version = `features-021` / feature_hash = `663fe86c756428fca7411f23bb5f0a4eaa91926b067a0e0acc4a11d581da0f7a`
- active の pandas_categorical[race_class] = ['1000万', '1600万', '1勝', '2勝', '3勝', '500万', 'OP(L)', 'オープン', '新馬', '未勝利', '重賞', '１勝', '２勝', '３勝', 'Ｇ１', 'Ｇ２', 'Ｇ３', 'ＪＧ１', 'ＪＧ２', 'ＪＧ３', 'ｵｰﾌﾟﾝ']
  → 両綴り共存: True
- registry FEATURE_VERSION = `features-021`
- features-022 焼却の根拠(git log main):
  - `8122f64 feat(097): REJECT — 代替列の採否ゲート run 2 と後始末(features-021 へ revert・モジュール保全)`
  - `a1a0381 fix(training): arm E + drop_features が無音で drop を無視する潜在バグ(097 run 1 を無効化)`
  - `808f92c feat(097): early-mid pace 2 列を features-022 として構築(US1 完了)+ 判定 driver(US2 配線)`
  - `5d2fc29 docs(097): analyze 3 周目で収束 — LOW 8 件を文書で閉じ hash は据え置き(6f76bf15…)`
  - `c45fb48 docs(097): analyze 2 周目 — HIGH 3/MEDIUM 6/LOW 4 を全件修正し hash を再凍結(6f76bf15…)`
  - `341c139 docs(097): analyze 1 周目 — HIGH 5/MEDIUM 11/LOW 5 を全件修正し hash を再凍結(6dd6a013…)`
  - `da1b12e docs(097): codex tasks レビューを反映 — 昇格ゲートの穴を塞ぎ、マスク provenance を契約化`

## race_class 綴り別件数

```
race_class    n         d0         d1
        1勝 5936 2019-06-01 2025-10-05
        １勝  824 2025-10-11 2026-08-23
        2勝 2972 2019-06-01 2025-10-05
        ２勝  411 2025-10-11 2026-08-23
        3勝 1313 2019-06-02 2025-10-05
        ３勝  194 2025-10-11 2026-08-23
     OP(L)  425 2019-01-06 2025-10-04
      オープン  164 2025-10-11 2026-08-23
     ｵｰﾌﾟﾝ 2365 2007-01-07 2025-10-05
        重賞   12 2009-08-23 2021-05-29
```

→ `１勝/２勝/３勝/オープン` は 2025-10-11 以降のみ、`1勝/2勝/3勝/ｵｰﾌﾟﾝ/OP(L)` は 2025-10-05 以前のみ(quickstart §0 と一致)。

## T016 serving E2E(features-023 registry・active lgbm-094 = compat 経路)

- race 202601020101(12 頭)。worktree(features-023 registry)の `serving predict` は compat 経路でロードされ logic_version に `reg=features-023;rcr=raw` が付く(run `2166859c-…`)。
- **同一 DB 状態**で main tree(features-021 registry)から同レースを予測(run `f3eabc4c-…`)し突き合わせ: **win/top2/top3 の mismatch 0・最大絶対差 0.0**(SC-004 成立)。
- 注記: 08-22 の旧 run(`b81d8c85-…`)との比較は 12/12 で不一致だったが、これは 08-22→08-23 の間に馬主/生産者・血統系統・通過順の修復で as-of 特徴が動いたため(表現とは無関係)。比較は必ず同一スナップショットで行う(088 の教訓)。
- 監査 dict(語彙外値 n_unknown)は predict の run summary(CLI 出力)に載る設計。今回のレースは語彙内のみ。

## T017 materialize(features-023)

- manifest: feature_version=features-023・n_rows 963,019・materialized_columns 113 = 021 と同一集合(`race_class` は静的列で非 materialize)。source_fingerprint は 021 manifest(00:08 時点)と異なるが data_through は同日で、差は日次取込で DB が動いた分(表現は parquet に載らない)。
