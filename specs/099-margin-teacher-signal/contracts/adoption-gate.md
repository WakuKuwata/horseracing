# Contract: 採用ゲートと verdict 分岐(099)

## 凍結

- 正本は [`gate-config.json`](../gate-config.json)。OOS 実行前に凍結し、confirmatory 実行は
  `--gate-config-hash` 照合+`--from/--to` の窓照合で fail-closed(073 契約)
- **凍結 hash(完全値・照合はこの値との完全比較)**:
  `d8c479dea834a22e4b27030d4558e9b1cc2e120639fbb29c32a8955331d098b7`
- **config キーの消費者**(非消費キーの罠 = 070 前例 の回避): `arms` = paired-eval
  confirmatory の注入+実効 recipe 照合(T016a)。`arm_identity` = 実行前検査(T017)+
  実行後 assert(T017a)。`determinism` = 運用者が本実行 §4 で
  `--num-threads 1` を手渡し(smoke は配線確認のみで不要)。`smoke` = T016a の注入機構が消費
  (非 confirmatory + `--gate-config` 時に低容量 n_estimators=50 を適用。redact =
  効果数値を読まない・転記しない、は運用者の規律)
- evaluation contract **v4**(seed_noise 込みの総 CI をゲートが読む)。凍結後の変更は
  再事前登録のみ(値を見てからの変更禁止)

## アーム(差 = 教師信号のみ)

| | candidate | active |
|---|---|---|
| spec | `pl_topk:oof_isotonic:mteach=v1` | `pl_topk:oof_isotonic` |
| 校正 | strict-past OOF isotonic(arm E・n_oof_blocks 8) | 同左 |
| 容量 | n_estimators 900 | 同左 |
| weight mask | 0.5 / 20260810 | 同左 |
| seed | 42 | 同左 |
| 教師信号 | **margin-aware V1** | 現行 PL top-3 |

- 両アームとも fold ごとに再学習(保存 booster 不使用・068 C1)・materialized snapshot を
  pin(091 D16)・num_threads=1(determinism 凍結)
- **アーム同一性 fail-closed**: paired-eval CLI は candidate と active の recipe_hash が
  同一なら実行前にエラー(`mteach=v1` の綴りミスによるセグメント黙殺 = 両アーム同一化を
  実行前に捕まえる。085/097 の実害形)

## 判定

- **verdict の正本は harness の組込み三値**(ADOPT / REJECT / NO_DECISION)。
  `final_decision`(v3/v4 実装)がそのまま出すものを転記し、個別 fold・個別 subgroup の
  数値による事後の読み替えを禁止する(068 C2・088 前例)
- primary: pooled winner NLL 差の点推定 < −0.002 AND 総 CI(seed_noise 込み)上限 < 0
- guards: recent 3y/5y 非劣性(margin 0.005)・top2/top3 非劣性(0.0005)・ECE 非劣性
  (0.001・緊急 0.05)・critical subgroups(`recent_year_only` / `nk` / `recent_year_nk`)
  は v3 意味論(FAIL のみ veto・NOT_PROVEN は開示)

## verdict 分岐の後始末

- **ADOPT**: candidate 登録のみ(`register_as_candidate` 経路)。active 昇格は本 verdict +
  標準窓非劣化 + prospective の別段判断(085 §7 と同じ規律)。fit_info の margin_teacher
  統計が metadata に載っていることを登録時に確認
- **REJECT**: 結線(dataset の aux 列生成・recipe field・CLI セグメント)を revert。
  `pl_topk_objective` の `stage_scales` 引数+単体テストは**保全**(None 既定は現行と
  ビット一致なので残しても挙動不変 — 呼び出し側の結線だけを剥がす)。測定数値を spec 末尾に
  転記し軸を閉じる
- **NO_DECISION**: 実行不能・検出力不足の場合のみ。ボーダーの数値を理由にしない。原因を
  記録し、再実行は新規の事前登録として扱う
- いずれの分岐でも: 既存 active モデルの serving 予測はバイト不変(INV-MT6 / SC-004)
