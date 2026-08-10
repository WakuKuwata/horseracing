# Contract: 採用ゲート(事前登録・OOS 後変更禁止)

判定は 1 bundle(10 列一括)。手順・閾値・seed は OOS 実行前に凍結し、結果を見た後の変更は禁止(spec FR-010)。既存機構のみ使用(新 eval コードゼロ)。**verdict は本番 pl_topk 構成の paired 評価で決める — binary 段に判定・打ち切りの機能は無い**(codex 2 回目レビュー論点B 採用・spec FR-013)。

## 診断段(非ゲート): binary feature-eval

- コマンド: `training feature-eval --drop-groups finish_decomp`(baseline = bundle 無し、candidate = bundle 込み。他条件は既定)
- 役割: fold パターン・効果量の**観察のみ**。どんな結果でも判定段は必ず実行される。診断結果は測定記録に併記する(判定に使った形跡と混同されないよう「診断・非ゲート」と明記)

## 凍結済み gate-config(2026-08-10 freeze・OOS 実行前)

- ファイル: [gate-config.json](../gate-config.json)
- **canonical hash: `521e6278eace90d08f6dfb60c368761d284d90c5c6ed027160a93ce1ce37cf33`**(`gate_config_hash` は `_` 始まりキーを除外)
- `assert_confirmatory(cfg, expected_hash=…, eval_window={from:2019-01-01, to:2026-08-09})` の通過を凍結時に確認済み(v2 キー・hash・窓の 3 検査すべて)
- **凍結時に確定した 2 点(いずれも OOS 実行前・結果非観察)**:
  - `bootstrap.seed = 20260713`(069/070/073 と同一値。当初 contract は CLI 既定の 20260712 と書いていたが、**同じ推定器・同じ seed で判定された近傍 bundle(069 ADOPT / 070 REJECT)と CI を直接比較できること**を優先して lineage 値に合わせた。実キー pin なので CLI 上書きは無効化される)
  - `eval_window.from = 2019-01-01`(069/070/073 と同一。当初 contract の「全期間(from=null)」から変更。fold 構造は不変=`first_valid_year=2008` の 19 fold で FR-011 と矛盾しない。採点対象レースの窓のみを lineage に合わせた)

## 判定段: 本番 pl_topk paired 評価

**アームは recipe spec で指定する**(paired-eval は保存済みモデルでなく `objective:calibration[:frac][:drop=groups]` の recipe を両アーム fold ごと再学習する — 073 C1 設計。069 F02 の確立パターン):

- candidate アーム: `pl_topk:isotonic:0.3`(本番構成=features-020 の全列。lgbm-065 系譜と同一 recipe)
- baseline アーム: `pl_topk:isotonic:0.3:drop=finish_decomp`(bundle だけを落とした同一構成=069 の drop=group 方式)
- コマンド: `training paired-eval --candidate "pl_topk:isotonic:0.3" --active "pl_topk:isotonic:0.3:drop=finish_decomp" --subgroups --confirmatory --gate-config <path> --gate-config-hash <hash> --from <eval_window.from> --to <eval_window.to> --json <report path>`
  - **gate-config.json を OOS 実行前に作成・凍結**し、canonical hash を本 contract に追記する(073 の confirmatory 契約=hash 不一致・欠落は fail-closed)。**pin は全て実キーで行う**(canonical hash は `_` 始まりキーを除外し、harness は実キーのみ読む=`_comment` に書いた数値は hash 保護外・silently ignored、069 gate-config が警告した同型の罠): **`evaluation_contract_version: "v2"`**(トップレベル・073 の `assert_confirmatory` が非 v2 を即 fail-closed する MUST キー。069 gate-config は 073 以前の作でこのキーを持たないため、**キー形式は 069 を、contract version キーは 073 gate-config を正とする**)/ `top_noninferior` / `calibration` / `subgroup_guard`(`non_inferior_margin_*` と `critical_subgroups: ["2026_only","nk","2026_nk"]`)/ `bootstrap.seed`(**20260713**=069/070/073 lineage 値)/ `bootstrap.b`(2000)/ **`eval_window`**(from=2019-01-01・to=2026-08-09)。`_comment` は注釈のみに使う。seed と B は実キー pin により CLI 上書きが無視され hash 保護下で自己強制になる(069 前例)。T018 では `--seed`/`--bootstrap-b` を渡さず、転記時にレポート JSON の実測値(`bootstrap_ci.seed`/`b`)が凍結値と一致することを照合する。T018 で `eval_window` の from/to に対応する **`--from` と `--to` を両方**渡す。**片方だけでは fail-closed で即停止する**(CLI は `{from: --from, to: --to}` を組み立てて凍結値と厳密比較するため、`--from` 省略時の None は `2019-01-01` と一致しない — 2026-08-10 の実行で実際に `ConfirmatoryContractError: eval window mismatch` が発生し、窓照合が意図どおり作動することが実証された)
  - 窓: fold 構造は 068 既定(`first_valid_year=2008` の 19 fold walk-forward)、採点対象レースの窓は `eval_window`(2019-01-01..2026-08-09)。窓の変更は OOS 前のみ可・実行後は禁止
  - **順序**: gate-config の凍結は診断段(binary feature-eval)の結果を観察する**前**に完了する(診断結果を見てから閾値を凍結する経路を塞ぐ)
  - DB の active モデルの確定(spec FR-015)は compat pin 検証と ADOPT 昇格のためであり、paired-eval はそれを消費しない
- **ゲート(spec FR-013a・verdict の正本は次の 1 本に一本化)**:
  - **verdict = PairedReport の `gate.adopted` AND `subgroup_guard`**(070 B2 と同一式)
  - `gate.adopted` は 068 契約の組込みゲート一式: winner NLL 勝ち+CI 上限<0・直近 3/5 年非劣化ガード・top2/top3 non-inferiority・ECE 非劣化(許容値は gate-config で凍結)
  - `subgroup_guard` は 069 の critical subgroup 集合 **{2026_only, nk, 2026_nk}** の intersection-union(membership=`subgroup_guard.critical_subgroups` と margin を gate-config の**実キー**で pin)。canonical は報告のみ・**coverage 帯は guard 対象外**(F02 市場観測数の定義で本 bundle と無関係・凍結コマンドは obs_count 非注入で産出されない。欠損構造の解釈は FR-018 監査が担う)
  - **レポート JSON の正確なフィールドパス**: `report["gate"]["adopted"]` と `report["subgroups"]["subgroup_guard"]`(トップレベルに subgroup_guard は無い)
  - レポートの個別数値フィールドを事後に読み替えて別の判定を構成してはならない(二重定義の禁止)
- **verdict**:
  - `gate.adopted AND subgroup_guard` → **ADOPT**(ユーザー承認の上で active 昇格)
  - 評価が実行され 上式が不成立 → **REJECT**
  - データ・環境要因で評価が実行不能 → **NO_DECISION**(ボーダーの数値を理由にしてはならない)
- **harness の `report.decision`(`DECISION=` 出力)の扱い**: これは 073 契約の組込み三値であり、underpowered 系(`stat_guard_underpowered` / `critical_subgroup_underpowered`)で NO_DECISION を返す点が本 contract の式と乖離しうる。**088 の verdict の正本は上の式であり、`report.decision` は参考値**(070 前例: CI ゼロ跨ぎ=有意でない=REJECT)。乖離した場合は spec への転記時に `report.decision` の値と cause を必ず併記する(隠さない)。この優先順位は OOS 実行前に凍結されており、結果を見てから入れ替えることを禁止する

## 判定後(spec FR-016/017)

- REJECT: FEATURE_VERSION bump と build 結線を revert(モジュール+単体テストは非結線で保全)。active serving の予測バイト不変を実 DB E2E で確認(spec SC-004)
- ADOPT: 旧版 pin(features-018 canonical hash)の compat 経路で既存モデルの予測バイト不変を確認(spec SC-005)
- どちらでも: 効果量・CI・fold パターン・subgroup 内訳・診断段の結果・カバレッジ監査(列別×年別+着順範囲異常件数)を spec の測定結果欄に転記
- **主張の範囲**: 閉じるのは「この 10 列 bundle」。軸の別構成での再提案には本測定を上回る新根拠を要求する(spec FR-017・SC-007)
