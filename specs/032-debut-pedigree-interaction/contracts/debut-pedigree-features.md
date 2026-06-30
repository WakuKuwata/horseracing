# Contract: debut_pedigree features (032)

## モジュール契約

`features/src/horseracing_features/debut_pedigree_features.py`

```python
DEBUT_PEDIGREE_COLUMNS = [
    "sire_debut_win_rate",
    "debut_x_sire_win_rate", "debut_x_sire_dist_band_win_rate",
    "lowhist_x_sire_win_rate", "lowhist_x_sire_dist_band_win_rate",
]

def build_debut_pedigree_features(
    frames: Frames, *, history: pd.DataFrame | None = None,
    pedigree: pd.DataFrame | None = None, min_starts: int = 10,
) -> pd.DataFrame:
    """Per (race_id, horse_id) の debut_pedigree 5 列。
    sire_debut_win_rate は 026 _other_offspring を debut-runs サブセットに適用(新情報)。
    ゲーティングは history(is_debut/is_low_history)× pedigree(sire_*) の積(既存 as-of 列のみ)。
    history/pedigree が None なら内部で build_history_features/build_pedigree_features を呼ぶ
    (materialize からは既算出を渡す)。返り値 columns=[race_id,horse_id,*DEBUT_PEDIGREE_COLUMNS]、
    全特徴 float64。
    """
```

## 集計契約

### sire_debut_win_rate（新情報）
- debut run = 各 horse の race_date 最小の STARTED 出走 1 行。
- debut-runs サブセットに 026 `_other_offspring(targets, debut_runs, "sire_name")`(sire 累積−自馬累積、daily cumsum−当日)を適用 → o_wins(デビュー戦勝利数)・o_cnt(デビュー戦数, 自馬除外・strictly-before・同日除外)。
- `sire_debut_win_rate = where(o_cnt >= min_starts, o_wins / o_cnt, NaN)`。

### ゲーティング交互作用（既存列の積）
- `debut_x_sire_win_rate = is_debut * sire_win_rate`
- `debut_x_sire_dist_band_win_rate = is_debut * sire_dist_band_win_rate`
- `lowhist_x_sire_win_rate = is_low_history * sire_win_rate`
- `lowhist_x_sire_dist_band_win_rate = is_low_history * sire_dist_band_win_rate`
- 片側 NaN → NaN(numpy 既定)。0 埋め禁止。出力は `.astype("float64")`。

## 不変条件（テストで担保）

### 正しさ（test_debut_pedigree_features.py）
- INV-C1: 種牡馬 S の他産駒 2 頭がデビュー戦 1 勝/1 敗 → S 産駒対象馬の sire_debut_win_rate==0.5(min_starts を小さく設定したテスト)。
- INV-C2: 自馬のデビュー戦は集計に入らない(自馬除外)。
- INV-C3: is_debut=1・sire_win_rate=r → debut_x_sire_win_rate==r、is_debut=0 → 0.0。
- INV-C4: sire_win_rate=NaN → ゲーティング積 NaN。
- INV-C5: 他産駒デビュー戦母数 < min_starts → sire_debut_win_rate NaN(0埋めしない)。
- INV-C6: 全列 dtype float64。

### リーク（test_debut_pedigree_leak.py）
- INV-L1: 自馬の今走 finish_order/result_status を変えても全列不変。
- INV-L2: 同日に走る同種牡馬他産駒の結果を変えても sire_debut_win_rate 不変(同日除外)。
- INV-L3: 未来の同種牡馬産駒デビュー戦を変えても不変(strictly-before)。
- INV-L4: ソース grep: `debut_pedigree_features.py` が今走 result/finish_order/odds 列を生参照しない。

### パリティ/カバレッジ（test_materialize_core.py 拡張）
- INV-P1: materialize==in-memory が `assert_frame_equal(check_exact=True, check_dtype=True)`。
- INV-P2: `materialized_columns()` に 5 列が含まれ odds/payout/dividend トークンを含まない。
- INV-P3: FEATURE_VERSION == "features-010"。

## 採用ゲート契約（training feature-eval）

- baseline = candidate − `--drop-groups`(既定 `debut_pedigree`)= features-009。
- candidate = full features-010。
- primary_pass = 平均 win LogLoss 改善 かつ 平均 ECE 非悪化(tol 1e-3)。
- fold ガード = strict majority(n_win*2 > n_folds) かつ worst-fold dECE ≤ 2e-3 かつ worst-fold dLogLoss ≤ 5e-3。
- adopted = primary_pass かつ fold ガード。ablation/セグメント診断は SECONDARY(採否に使わない)。
