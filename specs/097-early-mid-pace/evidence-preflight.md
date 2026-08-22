# 097 preflight (T002)

- 日付: 2026-08-22
- active: `lgbm-094-cap900` / metadata.feature_version=`features-021` / metadata.feature_hash=`663fe86c756428fca7411f23bb5f0a4eaa91926b067a0e0acc4a11d581da0f7a`
- registry: FEATURE_VERSION=`features-021` / model-input 列数=138 / hash=`663fe86c756428fca7411f23bb5f0a4eaa91926b067a0e0acc4a11d581da0f7a`
- 一致: YES (compat pin に使う値は metadata.feature_hash)
- race_results(2026, n=30,829): finish_time 99.5% / last_3f 99.4% (いずれも ≥99 を満たす)

## T016 materialize(features-022)

- `artifacts/features.manifest.json`: feature_version=features-022 / n_rows=962,553 / cols=115 / source_fingerprint=`58b614e0…`
- 021 時点の manifest は fingerprint `23fb65d2…` / n_rows=962,076。**差は DB が動いたため**(8/22 開催分の取込で +477 行)であって 097 由来ではない:
  `loader.py` の diff 0 行・`materialize.py` の diff に fingerprint 射影の変更なし(コメント 1 行のみ)= INV-EM6(新規ソース列ゼロ)はコードで成立。

## T018 serving E2E smoke(SC-005)

同一 DB 状態で、021 コード(registry/materialize を HEAD に一時退避)→ 022 コードの順に `predict --race-id 202601020109`(14 頭・lgbm-094-cap900)。

- run A(021): `c7515808…` logic_version `feat=features-021;…`
- run B(022): `2c4d91e5…` logic_version `feat=features-021;serve=…;reg=features-022;…` = **compat 経路でロード**
- **win/top2/top3 の mismatch = 0 / 14 頭**。Σwin = 1.0 両方。今朝の ops run(`a48acd9b…`)とも mismatch 0。
