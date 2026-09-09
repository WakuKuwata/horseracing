# Feature Specification: 6 モデル平均の本番 serving 結線と採用

**Feature Branch**: `main`(直接)
**Created**: 2026-09-09
**Status**: 実装中
**Input**: ユーザー「131 の spec から着手して採用まで進めて」。130 の発走前 walk-forward(−0.0090・total CI 上限 −0.004・7 年すべて負)を根拠に、129 の候補 bundle を通常の serving 経路で提供し active に昇格する。

## 背景と判断

- 129 の bundle は shadow 専用で、既存 loader(単一 booster)からは読めない。front / ops / live / backfill はすべて `load_serving_model` → `run_serving` を通るので、そこに 6 モデル平均を載せる。
- 採用根拠は 130(発走前条件・22,990 レース)であり、112 の将来窓確認は取っていない。これは **override 昇格**として `promote-model --override-reason` に記録し、隠さない。112 の将来窓は事後確認として shadow 記録を続ける。
- 4 日 rehearsal(+0.0015)は 130 と矛盾しないが支持もしない。残余リスクとして記録する。

## User Stories

### US1 — 通常経路で 6 モデル平均を予測する(P1)

`load_serving_model` が mixture 用の model_version 行を認識し、`run_serving` / `run_serving_backfill` がそのレースを 6 member 推論+補正+平均で予測して既存テーブルに永続化する。front からは他のモデルと同じに見える。

受入: (1) 登録した model_version を `serving predict --race-id` で指定すると prediction_run が作られ、win は shadow の serving 条件 rehearsal と 1e-10 で一致する。(2) 既存モデル(lgbm-094-cap900)の予測経路はバイト不変(既存テスト緑)。(3) 説明(040)は「未提供」として NULL、API/front が落ちない。

### US2 — 登録と昇格(P1)

bundle を `artifacts/model_versions/<name>/` に自己完結コピーして model_versions 行を candidate で作り、`promote-model --override-reason … --apply` で active にする。昇格前確認(実在・絶対パス・feature schema)を既存 `plan_promotion` に通す。

受入: active が 1 件だけ新モデルになり、旧 active は candidate に戻る。両行の metrics_summary に根拠が残る。rollback コマンドが記録される。

### US3 — 2026 年分の埋め戻しと運用条件(P2)

昇格後、2026 年の既存レースを新モデルで backfill し、front の既定表示が新モデルになる。係数は 2026 用なので、2027 の予測は「1 年の猶予」で許し、それを超えたら fail-closed。

## Functional Requirements

- **FR-001** mixture の model_version 行は `weights_uri`/`calibrator_uri` とも `<dir>/bundle.json` の絶対パスを指し、`<dir>/metadata.json` は `artifact_kind: mixture_model_version`・`bundle_sha256`・`feature_version: features-021`・`feature_hash: <138 列 hash>`・`objective: mixture` を持つ。loader は metadata の artifact_kind で分岐し、bundle SHA を照合してから member を読む。
- **FR-002** 予測は `prepare_race_inputs(regime='serving')`(091 のレース単位体重正規化と同一関数)→ 6 member 推論 → 年別係数の補正 → 等重み平均で **win** を得る。top2/top3 は本番の表示規約に従い、平均 win から `assemble_predictions(stage_discount=λ)` で導出する(現行モデルと同じ後処理・win は不変)。研究の「平均後の top2/top3」は本番では使わない(表示層の校正規約を優先)。
- **FR-003** prior_gap の履歴は race_horses の started 行を対象日より厳密前に絞って読む(129 shadow と同一関数)。履歴の取得は 1 実行/1 日あたり 1 クエリ。
- **FR-004** 特徴行列に無い `race_date` は pipeline が対象日で付与する。既に列がある場合は一致を要求する。
- **FR-005** logic_version に `;mix=<bundle sha 12 桁>;coef=<係数年>` を付ける。係数年と対象年が異なる(猶予適用)場合は `;coefstale=1`。猶予は 1 年まで、超過は例外。
- **FR-006** 説明(explanation)は全馬 NULL。feature_snapshots は full 138 列の入力を保存し `_raw_win`/`_calibrated_win` は平均 win を入れる。
- **FR-007** 既存 `ServingModel` 経路(booster モデル)は 1 バイトも変えない。mixture の分岐は `isinstance` で行い、既存テストは無改修で緑。
- **FR-008** 登録は `scripts/register_mixture_model_version.py` が行う。bundle の全 member ファイルを SHA 照合しながらコピーし、コピー先で `load_mixture_bundle` が通ることを確認してから行を作る。既存行があれば拒否。
- **FR-009** 昇格は既存 `promote-model` を使う(新規の昇格経路を作らない)。override 理由に 130 の数値と evidence パスを書く。
- **FR-010** DB への書込は登録行 1 件と、既存経路による prediction_run の追加のみ。migration なし。

## Success Criteria

- **SC-001** `serving predict --model-version <mix>` と `mixture_shadow capture --regime serving` の同一レースで win の最大差 ≤ 1e-10。
- **SC-002** 既存 serving/training/api テスト緑、lgbm-094-cap900 の予測経路に差分なし(既存 unit テストが担保)。
- **SC-003** 昇格後 `select adoption_status='active'` が新モデル 1 行。
- **SC-004** 2026 年 backfill が error_days 0 で完走し、API `/predictions` の既定が新モデルの run を返す。

## 残余リスク(明示)

- 将来レースでの実測はゼロ。129 の prospective capture を続け、112 の事後確認に使う。
- 係数は 2025 末までの fit。2026-12 に係数を更新した bundle v2 が必要。
- 6 member の推論は単一モデルの約 6 倍だが、rehearsal 実測は 1 レース 0.05 秒で無視できる。
