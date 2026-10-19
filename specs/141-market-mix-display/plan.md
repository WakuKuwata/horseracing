# Implementation Plan: 141 期待回収率の市場混合版を並走表示する

**Branch**: `141-market-mix-display` | **Spec**: `spec.md`

## 0. 契約

### 0.1 eval `horseracing_eval/market_mix.py`(新規・純関数のみ・numpy 以外に依存しない)
- `MIX_VERSION = "mix-ens15-v1"`・`MIX_EV_THRESHOLD = 1.0`(厳密 `>`)・`MIX_DISPLAY_ENABLED`(held-out の結果で決める・既定 True は確認後に確定)。
- `MixCoefficients(year, a, b, fit_from_year, fit_through_year, n_races, n_rows, source_sha256)` の凍結表 `MIX_COEFFICIENTS: dict[int, MixCoefficients]`(2025・2026)。
- `coefficients_for(year) -> MixCoefficients | None`。
- `mix_probabilities(win_probs, odds, coef) -> np.ndarray`: FR-001 の式。入力検証(長さ一致・p̄>0・odds≥1.0・2 頭以上)に失敗したら `ValueError`。log-sum-exp で安定化。
- `MIX_SOURCE_MODEL_VERSION = DISPLAYED_MARKET_EV_MODEL_VERSION`(attention_rules から import)=係数は ens15 の p̄ 専用。

### 0.2 研究スクリプト `scripts/roi_explore/market_mix_20261019/`
- `fit_mix.py`: 15 seed OOF(`artifacts/roi_explore/results/armC_binary*_drop-sameday+weightlive_from2007_ens15/predictions.parquet`)と `rows_2007.parquet` から、10/10 の measure2.py と同じ母集団(race_ok ∧ ¬dead_heat ∧ race_date ≤ 2026-06-26)で年 Y=2013..2026 の係数を 2010..Y−1 で fit(scipy L-BFGS-B・境界 [0,5]²・初期値 (1,0))。評価は共有定義の `mix_probabilities` で行う(fit の目的関数も同じ式)。出力 `coefficients.json`(年ごとの a・b・n・入力 sha)と `walkforward.json`(正規化 winner NLL・EV_mix 帯別 n/won/Σp/Σwon÷Σp/ROI・現 S3 との重なり・2013+ と年別)。
- `heldout.py`: 2026-08-10〜2026-10-18 の結果確定レースで、勝ち馬の保存単勝×100 が公式払戻と ±10 円で一致するレースだけ(=確定値)を対象に、本番 `market_ev.load_rows → build_features` と ens15 の 2026 booster で p̄ を再計算(DB 読み取り専用)。生 p̄ と混合後(2026 係数)で正規化 winner NLL・EV 帯別較正・EV>1.0/EV_mix>1.0 の件数と公式払戻 ROI を出し、paired 差は開催日ブロック bootstrap(B=4,000・seed 20261019)。`protocol.json`+sha を実行前に凍結、結果は append-only。
- 2025 年用係数(2010..2024 で fit)は 2025 年に計算された保存行(137/138 の埋め戻し)の表示用。

### 0.3 API(純追加)
- `StoredMarketEv` に `win_prob` を追加(読むだけ)。`build_market_ev` が `available` のときだけ混合を計算。
- 応答: `HorseMarketEv.expected_return_mix: float | None`・`exceeds_mix_threshold: bool | None`。`MarketEvAvailable.mix: MarketMixAvailable | MarketMixUnavailable`(`status`・`version`・`coef_year`・`a`・`b`・`threshold`・`source_model_version` / `status="unavailable"`・`reason: Literal["no_coefficients","invalid_inputs","disabled"]`)。`MarketEvUnavailable` は変更しない。
- 年はレースの `race_date`(router が races から読む)。`api` は eval を import 済み(attention_rules)=境界は変えない。

### 0.4 front
- `HorseEntriesTable`: 「期待回収率」列の右に「市場混合」列(%表示・推定バッジ・並び替え不可)。`exceeds_mix_threshold` は中立の目印(記号のみ・色/塗り/太字なし)。`mix.status="unavailable"` は「—」。
- `ExpectedReturnNote`: 混合版の段落(固定文言・数値は fit/held-out の凍結値の転記)。禁止語の不変テストに混合版の文言を追加。
- OpenAPI snapshot・生成型(front/admin)を再生成。

## 1. 実装順
1. eval 定義+単体テスト(式・数値安定・入力検証・年引き)→ 2. fit_mix(SC-001 再現)→ 係数を凍結 → 3. held-out protocol 凍結 → 実行(FR-006)→ `MIX_DISPLAY_ENABLED` 確定 → 4. API+テスト → 5. front+テスト → 6. 独立レビュー → 7. 記録。

## 2. 決定

| # | 決定 | 理由 |
|---|---|---|
| D1 | 読み取り時計算(保存しない・migration なし) | 混合は 1 レースの保存 `win_prob`・`odds_used` だけの純関数。保存すると埋め戻しが要り、埋め戻しの再計算は 140 の評価母集団(結果確定前の最後の計算行)を上書きする危険がある。049/106 と同じく派生値は読み時 |
| D2 | 定義は eval に置く | API と研究スクリプトの両方が使う。attention_rules と同じ場所。api は eval を既に import |
| D3 | 閾値 1.0・目印は中立 | 混合後 >1.2 は 0.77 で過大が残る(極端側の再選択)。>1.0 は 0.95。主表示の 120%超の目印と混同しないよう色を付けない |
| D4 | 係数はレース年で引き、無ければ unavailable | ブースターの年次 manifest と同じ規約。2027 は 12 月に更新 |
| D5 | held-out は確定オッズで | 係数は確定オッズで fit した。判断時オッズの検証は 140 の harness に腕を足す形で後日(本 feature では記録の限界として注記) |
| D6 | 2026-08-10 以降に限定 | 06-27〜08-09 は保存単勝が発走前値のまま(139 で修復できなかった 504 レース)。勝ち馬オッズ×100=公式払戻で確定値を確認できるレースだけを使う |

## 3. レビュー

**codex unavailable**: `codex exec` が 2 回とも 400(`gpt-5.5` と既定の `gpt-6.1-sol` がどちらも「ChatGPT アカウントでは未対応」)。同一タスクの再試行上限に達したので、代わりに**結論を渡さずコードだけを読む独立レビュー**(サブエージェント・テスト実行つき)を 1 本走らせた。codex 環境の復旧は別タスク(モデル指定の見直し)。

独立レビュー(high 0・medium 2・low 7)の採否:

| id | 指摘 | 採否 | 対応 |
|---|---|---|---|
| M1 | 注記の「100% 超の回収率 106.1%(95% 区間 98〜114%)」は境界 1.0 を同じ結果で決めた標本内の値で、利益の示唆に読める。区間は fit_mix.py の出力に無い | 採用 | 回収率と区間を注記から外し、推定/実際の比 0.95 と「境界はこの検証の結果を見て決めた」を明記 |
| M2 | held-out の事前登録・一度だけは自己申告で検証できない(protocol・script・結果が同じ分に作られ未コミット・script は sha 固定外・結果を消せば再実行できる) | 採用(開示) | spec に「事前登録は自己申告」と開示。script の sha256 を本節に記録(heldout.py `c4f60dd4…`・fit_mix.py `f51dd2bd…`)。コミットは利用者の指示を待つ |
| L1 | `races.race_date` は nullable で、NULL なら新コードが 500 | 採用 | `race_date=None` は `mix: unavailable(no_coefficients)`+テスト |
| L2 | 保存行の booster(年次 manifest)と係数の年を照合していない(loader は前年 manifest に落ちうる) | 採用 | `booster == ensemble_{coef.year}.json` でなければ `model_mismatch`+テスト(実 DB は全行一致) |
| L3 | mix の状態と各馬の値の整合が schema で強制されていない | 採用 | `MarketEvAvailable` に model_validator(available ⇔ 全馬非 null)+テスト |
| L4 | 禁止語・損益色の不変テストが available の混合表示を通っていない | 採用 | 表と注記で available の fixture・理由 4 種の文言のテストを追加 |
| L5 | 「同等以上」は言い過ぎ(区間 [−0.0042, +0.0014])・判定規則は検出力が低くほぼ必ず通る | 採用 | 文言を「悪化は見られなかった(区間は改善と悪化の両方を含む)」に。spec に検出力の低さを開示 |
| L6 | heldout.py の `jump` カウンタは常に 0(load_rows が障害を除外済み)・`not_in_results` 32 は結果未確定の平地 | 記録のみ | 実行済み結果を変えない(一度だけ)。本記述で説明 |
| L7 | 列名「市場混合」は市場の確率と読める・CSS 不足・invalid_inputs の文言・「7 対 3」は指数で重みでない | 採用 | 列名「期待回収率(市場混合)」・`.ev-mix-cell` に nowrap/tabular-nums・文言修正・「7 対 3」を削除 |

## 4. 実装記録(2026-10-19)

### 4.1 再現(SC-001・`walkforward.json`)
共有定義の純関数で 10/10 報告を完全に再現(係数 6 桁一致・共有定義と fit 式の差 3.3e-16)。2026 年用 a=0.27854・b=0.73207(2010〜2025 で fit)、2025 年用 a=0.28335・b=0.72781。

| 2013〜2026-06(確定オッズ) | 生 p̄ | 市場混合 |
|---|---|---|
| 正規化 winner NLL | 1.951682 | 1.951013 |
| EV>1.0 の頭数 / Σwon÷Σp | 62,100 / 0.875 | 24,737 / 0.953 |
| EV>1.2 の頭数 / Σwon÷Σp | 14,946 / 0.751 | 2,330 / 0.765 |
| 混合後 EV 帯 (1.0,1.2] / (1.2,1.5] / >1.5 の Σwon÷Σp | — | 0.970 / 0.754 / 0.911(130 頭) |

帯別回収率は 1.2 を超えると単調でない(0.94・0.95)=目印は 1.0 で止める根拠。

### 4.2 held-out(一度だけ・`results/heldout_2026-08-10_2026-10-18.json`)
593 レース・7969 頭・23 開催日(勝ち馬の保存単勝×100=公式払戻のレースのみ)。

| 指標 | 生 | 混合 | 差(混合−生)[95%] |
|---|---|---|---|
| 正規化 winner NLL | 1.93564 | 1.93430 | -0.00135 [-0.00423, +0.00141] |

規則(区間下限 >0 なら非表示)に当たらず `MIX_DISPLAY_ENABLED=True`。集合の記録(判定に使わない): 生 EV>1.0 は 444 頭・Σwon÷Σp 1.01、生 EV>1.2(S3)は 45 頭、混合 EV>1.0 は 149 頭・1.23 [0.54, 2.00]、混合 EV>1.2 は 2 頭。どれも少数で何も言えない。

### 4.3 実装とテスト
- eval `market_mix.py`(14 テスト)・API(読み取り時計算・応答純追加・booster/年照合・整合 validator・attention 2 経路に race_date)・front(列「期待回収率(市場混合)」・中立の「100%超」・注記)。
- テスト: eval 14+attention 72 / api 単体+統合 233 中 227 緑(失敗 6 件は他セッションの未コミット変更 `probability/calib_manifest.py` の `build_manifest(base_model_version=)` 由来で 141 と無関係。全体では他に joint parity 1 件=同原因・chaos p95 1 件=既知の性能テスト)/ front 391 / admin 43 / OpenAPI drift 緑・front と admin の snapshot はバイト一致。
- 実画面(10-17 の 202604040111): 生 137.8%→混合 114.1%、124.6%→105.9%、114.5%→99.4%。目印は灰色・通常の太さ・背景なし。API は再起動済み(運用中の画面に反映)。

### 4.4 残る作業と限界
- 表示値は**発走前のオッズ**で計算される一方、係数は確定オッズで fit した。140 の判断時採点(`judged_20261011/score.py`)に混合の腕を足すと、発走前の較正を前向きに記録できる(未実施)。
- 2027 年用の係数は 12 月に年次 fit(ブースターの年次更新と同時)。無ければ列は「—」。
- held-out の判定規則は検出力が低く(593 レース)、表示可否の歯止めとしては弱い。
