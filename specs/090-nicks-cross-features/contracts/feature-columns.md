# Contract: ニックス特徴列と不変条件

**Feature**: 090-nicks-cross-features

## 列契約

group `nick_cross` の 2 列。列名・意味・型は本契約が正本(data-model.md と同期)。

| 列名 | 型 | 定義 |
|---|---|---|
| `nick_lift_log` | float64 | `log(入れ子縮約後の交差率) − log(独立性期待率)` |
| `nick_obs_count` | float64 | L0 セル(父×母父)の as-of 有効観測数。0.0 = 前例なし |

## 不変条件

- **INV-N1(リーク境界・時間)**: 算出に用いる観測は対象レースより**厳密に前**に確定した
  ものに限る。同一開催日の観測を含めてはならない。対象レースのオッズ・結果・未来のレースを
  変更しても出力は 1 ビットも変わらない。
- **INV-N2(リーク境界・自馬除外)**: すべての集計(交差セル・父 marginal・母父 marginal・
  全体 marginal)から**対象馬自身の過去実績を控除**する。対象馬の過去成績を変更しても、
  その馬に与えられる `nick_lift_log` / `nick_obs_count` は変わらない。
- **INV-N3(as-of の再計算)**: 期待値を構成する 3 つの marginal と各階層の推定は、
  対象時点ごとに as-of で計算する。**縮約済みの値をキャッシュして使い回してはならない**
  (070 の規律の趣旨)。ただし階層モデルとして親の推定値へ寄せること自体は正当であり、
  禁止されるのは stale な値の再利用である(codex #2 でこの取り違えを是正)。
- **INV-N4(決定性)**: 同一入力から同一出力(浮動小数点まで一致)。集計順序に依存しない
  実装であること。
- **INV-N5(定数の凍結)**: `LAMBDA_L0` / `LAMBDA_L1` / `EPS_LO` / `EPS_HI` はモジュール
  定数とし、実行時引数にしない。**評価結果を見た変更を禁止**する(憲法 III)。λ は
  既存 feature からの流用ではなく、計画段階で測定した分散分解から導出した値を用いる
  (data-model §4)。硬い閾値定数(旧 `MIN_CELL`)は存在しない。
- **INV-N6(materialize parity)**: 事前生成した特徴と逐次計算した特徴が**ビット一致**する
  (`check_exact=True` かつ `check_dtype=True`)。定数を実行時引数にしないことがこの条件の
  前提である(025 規約)。
- **INV-N7(ソース不変)**: 新規のソース列を読まない。`source_fingerprint` は変化しない
  (031/059/061 同型)。したがって保存済み特徴の再生成手順は変わらない。
- **INV-N8(既存特徴の不変)**: 026 / 032 / 056 の血統特徴は削除も変更もしない。本 feature の
  列追加は**純加算**であり、既存列の値を 1 ビットも変えない(左結合・キー一意・列名重複なし
  を機械検証する。058 の additive-merge 検証と同型)。
- **INV-N9(欠損の意味)**: キーが作れない行(父名または母父名が不明)のみ NaN。観測が薄い
  行は親へ縮約した値と `nick_obs_count = 0.0` で表す。**0 埋めによる欠損の隠蔽を禁止**する。
- **INV-N10(親の leave-child-out)**: 粗い階層(L1)の推定に、当該の細かいセル(L0)の
  観測を含めてはならない(同じ情報の二重利用になるため)。L0 セルの実績だけを変えても
  L1 の推定値が動かないことをテストで固定する。

## 版と互換

- FEATURE_VERSION を features-018 → **features-021** に更新する。
  **features-019 は焼却済み(070 で使用後 revert)につき再利用禁止**、features-020 は 088 が
  予約済み。
- 運用モデル(features-018 で学習)の serving は compat pin(058/061 方式)で維持し、
  予測がバイト一致することを実 DB で確認する。

## 採用判定契約(**本節が判定の唯一の正本**。spec / plan / tasks / quickstart は本節を参照する)

- 判定は**実装前に凍結**した設定で行い、本番相当構成(pl_topk)で新旧を**同一条件比較**する。
- 簡易目的関数(binary)での打ち切り判定は**採否に用いない**(088 の教訓: 過小評価により
  逆方向の効果を排除できない)。診断としてのみ実行可。

### 判定式(3 分岐・088 と同一)

| 判定 | 条件 |
|---|---|
| **ADOPT** | 評価が完走し、`gate.adopted`(勝者 NLL 勝ち + 信頼区間上限 < 0 + 直近 3/5 年ガード + top2/top3 非劣化 + 校正非悪化)**かつ** `subgroup_guard` が**ともに成立** |
| **REJECT** | **評価が完走した上で**上記が不成立(点推定が良くても信頼区間がゼロを跨げば REJECT。070 の前例) |
| **NO_DECISION** | **評価そのものが実行不能な場合に限る** — 下記のいずれか |

**NO_DECISION が成立する条件(これ以外では宣言してはならない)**:

1. 評価が完走しない(実行時エラー・fail-closed による中断)
2. 対象期間に評価対象レースが存在せず信頼区間を算出できない
3. 特徴のカバレッジがゼロ(全行が欠損 or 全行が親フォールバック)で、候補と基準の入力が
   実質的に同一になり比較が成立しない

**ボーダーの数値を理由に NO_DECISION と宣言することを禁止する**。カバレッジが低い(ただし
ゼロでない)場合は REJECT または ADOPT のいずれかであり、「薄いから判定不能」とはしない。

**harness が返す `report.decision` は参考値**であり判定の正本ではない(073 の規約。
underpowered 系で NO_DECISION を返すことがあるため、上表の式を正本とする)。

### 判定コマンド(正本・全ドキュメントはこれを参照する)

```bash
uv run --project training python -m horseracing_training paired-eval \
  --candidate "pl_topk:isotonic:0.3" \
  --active    "pl_topk:isotonic:0.3:drop=nick_cross" \
  --from <gate-config の eval_window.from> --to <gate-config の eval_window.to> \
  --seed <gate-config の bootstrap.seed> --bootstrap-b <gate-config の bootstrap.b> \
  --subgroups --confirmatory \
  --gate-config specs/090-nicks-cross-features/gate-config.json \
  --gate-config-hash <凍結ハッシュ> \
  --json artifacts/090-paired.json
```

**必須引数の理由(実装の実査で確認・省略すると凍結が無効化される)**:

- `--from/--to` **必須**: `assert_confirmatory` は CLI 窓が `None` のとき `eval_window` の
  照合を**丸ごとスキップ**する(`eval/decision.py` の `if eval_window is not None:`)。
  渡さないと凍結した評価窓が適用も検証もされず、CLI 既定(全期間)で実行される。
- `--seed` / `--bootstrap-b` **必須**: gate-config からは読まれず CLI 既定
  (20260712 / 2000)が使われる。凍結した値を明示的に渡さないと飾りになる。
- `--gate-config` **必須**: `--gate-config-hash` だけでは
  「confirmatory mode requires a gate-config (missing)」で必ず失敗する。
- `--subgroups` **必須**: これが無いと判定式の片翼 `subgroup_guard` がレポートに出ない。

### gate-config の必須キー(欠けると `--confirmatory` が即 fail-closed)

`evaluation_contract_version`(現行契約版)/ `primary` / `top_noninferior` / `calibration` /
`subgroup_guard` / `eval_window` / `bootstrap`。
**`top_noninferior` と `calibration` はトップレベルに置く**(069 の注記)。

## 不採用時の後始末契約

- FEATURE_VERSION の更新と build 結線のみを revert する。
- 算出モジュールと単体テストは**非結線で保全**する(062/070 同型・負の結果の記録)。
- 後始末後、運用モデルの予測が判定前と**バイト一致**することを実 DB で確認する。
