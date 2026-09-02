# Contract: confirmatory driver (107)

## 入力
- `--gate-config specs/107-jockey-tv-intercept/gate-config.json`(必須)
- `--gate-config-hash <64hex>`(必須・凍結時に記録した値)
- `--json evidence/verdict.json` / `--evidence evidence/paired-evidence.json`(append-only・
  既存パスは拒否)

## fail-closed(すべて実行前)
1. `assert_confirmatory(cfg, expected_hash, eval_window)` — 契約版 v4 等値・hash 一致・
   窓一致・seed_noise.sd_fold 存在
2. `assert_delta_provenance(cfg, root=<リポジトリルート>)` — δ の導出参照が解決でき、
   測定ノイズ由来でない(ref はルート相対なので root 明示が必須・analyze M1)
3. `eval_window.to < 2025-01-01` — screening 窓(選択済み)の再利用を構造的に禁止
4. `load_eval_races(start_date=FEATURE_POOL_START, end_date=eval_window.to)` — pre-2007 除外
5. 定数一致: driver の W/MIN_RIDES/λ クランプ == gate-config の凍結値
   (== spike モジュールの値。unit テストでも固定)

## 実行
- `paired_eval(candidate_factory, active_factory, races, gate_config=cfg,
  first_valid_year=2022, bootstrap_seed=cfg.bootstrap.seed, bootstrap_b=cfg.bootstrap.b,
  subgroups=True)` — subgroups は開示のみ(critical=[])
- 縮退検査: 両アームの予測差分頭数 ≥1 でなければ abort・効果数値を出力しない

## 出力
- `verdict.json`: {decision: ADOPT|REJECT|NO_DECISION, reason, gate: {...v4 標準...},
  point, sample_ci, total_ci, n_races, n_days, gate_config_hash, contract_version,
  preflight_ref, evidence_ref, computed_at}
- `paired-evidence.json`: 公式 evidence artifact(再計算ビット一致が受入)
- `preflight.json`: fold 別診断(λ raw/clamped・騎手数・window 行数・nk: 件数・カバレッジ)

## smoke モード(analyze H3)
- `--smoke` 指定時: 縮小 config を受け、verdict.json から point/sample_ci/total_ci を
  redact(構造フィールドのみ)。smoke の採点窓は凍結窓 2022-2024 と互いに素であること
  を driver が assert する

## 禁止
- 判定式のオーバーレイ(final_decision の三値が正本)
- 実行後の gate-config・凍結定数の変更(再測定は新規事前登録)
- 効果数値を見てからの窓・アーム・subgroup の変更
