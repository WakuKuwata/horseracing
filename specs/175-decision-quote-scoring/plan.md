# 175 plan — 判断記録(decision_quotes)の採点

**Branch**: `175-decision-quote-scoring`(main 上で作業・push なし) | **Date**: 2026-10-29 | **Spec**: [spec.md](spec.md)
**Input**: 利用者決定 3 件(朝の特徴行 parquet 保存を 10/31 前に入れる・第 1 候補=148 の全頭正規化を登録・混合列の候補なし)。codex unavailable(400 継続)→ 各節にセルフレビューを併記し、[research.md](research.md) D13 に checklist。

## Summary

174 が残す発走 3 分前の判断記録(1 レース 1 行・全馬 quote+3 系統の p̂/EV)を、**結果を読む前に固定した手順**で採点する読み取り専用の経路。中核は eval の純関数(funnel → 100 円精算 → 開催日ブロック bootstrap → 凍結規則 `display-update-rule-v1` の三値、診断=T−3 較正と価格ずれ)。DB ロードと候補の再現(朝の特徴行 parquet+保存 quote で現行を再予測し保存 `p_ens` とビット一致したレースだけを対にする)は live の CLI `near-post-score`。174 の捕捉 CLI には「朝の特徴行を parquet に保存する」小変更を 10/31 前に入れる。300 点未満のあいだ三値は `insufficient` で、年内に答えが出るのは較正と価格ずれ(spec §2)。

## Technical Context

- **Language/Version**: Python 3.12(uv・各パッケージ独立 venv)
- **Primary Dependencies**: eval=numpy のみ(既存 `bootstrap.race_block_ratio_bootstrap_ci_v1`・`attention_rules.race_payout_consistent`・`display_update_rule`・`market_mix.exceeds_mix_threshold`)。live=db(ORM `DecisionQuote`/`Race`/`RaceHorse`/`RaceResult`/`OfficialWinPayout`)・training(`market_ev` の ensemble/booster ロードと `market_structure`)・eval・pandas/pyarrow(parquet)
- **Storage**: PostgreSQL 16(読み取りのみ・書き込みゼロ)。ファイル: `artifacts/near_post/<day>/features-<sha12>.parquet`(174 CLI が書く)・`artifacts/near_post/score/<from>_<to>_<run_id>.json`(本 CLI が書く・既存は上書きしない)
- **Testing**: pytest。eval=単体(合成 fixture・決定論)、live=単体(fake predictor)+統合(testcontainers postgres・migration 0022 以降)、api=B8 との funnel parity(api は eval と自身の両方を import できる)
- **Target Platform**: ローカル macOS(開催日の夜に人が実行)
- **Project Type**: library(eval)+ CLI(live)
- **Performance Goals**: 1 開催日 36 レース・候補 2 本で 2 分以内(朝の特徴行は parquet から読む=再構築しない。parquet が無い日だけ `build_day_features` を回す=174 実測 14 秒/日)
- **Constraints**: 読み取り専用 role `hr_manual_observer` で実行可・決定論(同 seed → 同 JSON)・append-only の出力・結果を読む前に固定した帯/閾値/候補
- **Scale/Scope**: 2026 年内 19 開催日 ≈680 レース ≈ 120% 列 140 点・混合列 230 点(spec §2)。2027 以降も同じ CLI

## Constitution Check(v1.0.1)

- [x] **I. データ契約**: race_id 12 桁・2007 年以降・ID は既存表の結合のみ。新規 ID 結合なし。PASS
- [x] **II. リーク防止**: 採点出力・候補の p̂ を特徴に戻さない(features の leak-guard に `decision_quotes` 参照禁止は 174 で既存。本 spec は出力ディレクトリを features が読まないことをテストに追加)。候補の再現は「保存 quote を差し替えた朝の特徴行」=174 と同じ as-of 境界で、結果後情報は精算にしか使わない。PASS
- [x] **III. 評価先行**: 帯・閾値・bootstrap・三値規則は凍結 JSON(sha 照合)。候補は `candidates.json` に結果を読む前に登録し、三値は登録日以降の点だけ(機械強制)。結果を見て動かせる自由度を残さない。PASS
- [x] **IV. 確率整合性**: 候補 `postprocess`(148 正規化)は p/Σp で Σ=1 を保証。現行 ens15 の p̂ は市場連動の二値モデルで Σ≠1 が仕様(137)=そのまま。PASS
- [x] **V. 再現性・監査**: 出力に rule_version・凍結 JSON sha・bootstrap impl/b/seed・artifact sha・candidates.json の sha・code sha・run_id・計算時刻。精算は保存しない(読み取り時計算・憲法 1.0.1 注記の範囲内)。PASS
- [x] **VI. feature 分割規律**: UI なし。API 後段は eval の同じ関数を呼ぶ(B8 と同型)。migration なし。PASS
- [x] **品質ゲート**: codex unavailable(400・141 以降継続)→ research D13 のセルフレビュー checklist で代替し、環境復旧は別タスク(CLAUDE.md の規約)。

## Project Structure

### Documentation (this feature)

```text
specs/175-decision-quote-scoring/
├── spec.md
├── plan.md              # 本ファイル
├── research.md          # D1〜D13
├── data-model.md        # 入力レコード・分類・集計・候補・出力 JSON
├── contracts/
│   ├── cli.md           # near-post-score / near-post-capture の parquet 保存
│   └── report.md        # 出力 JSON の形と不変条件
├── quickstart.md
├── candidates.json      # 候補の事前登録(実装時に第 1 候補を書く・sha を出力に記録)
└── tasks.md             # /speckit-tasks(未作成)
```

### Source Code (repository root)

```text
eval/src/horseracing_eval/
├── decision_quote_scoring.py     # 新規: 純関数(分類・精算・列集計・同期 bootstrap 差・三値・診断)
└── tests/unit/test_decision_quote_scoring.py

live/src/horseracing_live/
├── near_post.py                  # 変更: DayFeatures の parquet 保存/読込(sha 照合)
├── near_post_score.py            # 新規: DB ロード→QuoteRecord・候補の再現ゲート・候補予測・JSON 出力
└── cli.py                        # 変更: near-post-score サブコマンド・near-post-capture に --features-dir
live/tests/unit/test_near_post_score.py
live/tests/integration/test_near_post_score_db.py

api/tests/unit/test_prospective_funnel_parity_175.py   # B8 classify_race と 175 の共有段の一致(同一 fixture)
features/tests/unit/test_decision_quotes_leak_guard.py # 変更: artifacts/near_post の非参照を追加
scripts/near_post_ops.sh                               # 変更: capture に --features-dir、score サブコマンド
specs/174-near-post-decision-quotes/manual.md          # 変更: 夜の手順に score を追加
```

**Structure Decision**: 純関数は eval(predictor-agnostic・DB 非依存=後段 API が import できる)、DB と training artifact に触る部分は live(174 の再現部品 `build_day_features`/`rows_with_quote`/`ProductionPredictor` と同居)。api・ops・scrape・db のスキーマは変更しない。

## 設計決定(詳細は research.md)

- **D1 配置**: eval 純関数+live CLI(上記)。
- **D2 入力の正規化**: live が DB から `QuoteRecord`(plain dataclass: 保存行の列+現在の started 集合+結果+公式払戻+確定単勝の来歴)を組み立て、eval はそれだけを読む。
- **D3 分類の順序**: spec FR-002 の 10 段。B8 と共有する段(jump/field/値域/結果/払戻)の述語は名前と意味を B8 に合わせ、api 側の parity テストで同一 fixture の判定一致を固定(実装は別=eval は api を import できない)。
- **D4 精算と区間**: 100 円×目印・公式払戻・`race_block_ratio_bootstrap_ci_v1`(impl/b/seed/block は `display_update_rule.BOOTSTRAP` を読む)。**[現行, 候補] を 2 行で 1 回渡し**、差の区間は `replicates[1] − replicates[0]` の percentile。どちらかの分母が 0 の replicate は差からも除く。
- **D5 三値**: `display_update_rule.decide(n_points=登録日以降の対の目印本数, diff_ci_low)`。候補なし実行は `n_points` のみで `insufficient`/(300 以上なら)`keep` ではなく **`no_candidate`** を別に返す(規則は候補の有無を区別しないので、CLI の出力層で区別)。
- **D6 候補の再現ゲート**: 朝の特徴行(parquet・sha 照合)→ `rows_with_quote`(保存 `field` の win_odds/popularity)→ `ProductionPredictor.predict` → 保存 `pred.p_ens` と全馬 `==`。不一致は `not_reproducible`(件数・最大 |Δ|)。parquet が無い日は `build_day_features` で再構築し、`features_sha256` の一致/不一致を併記(条件にしない)。
- **D7 候補の種類**: v1 は `postprocess`(148 正規化: `p_norm = p_ens / Σ_started p_ens`・`EV_norm = p_norm × odds`・閾値 1.2)と `ensemble_dir`(別 manifest の 15 seed・sha=manifest sha)。混合列の候補なし(決定 3)。
- **D8 登録**: `candidates.json`(spec 配下・JSON・`registered_on`・`artifact_sha256`・`train_through`)。CLI は実行時に sha を照合し、ファイル自体の sha を出力に記録。三値は `captured_at(JST 日付) ≥ registered_on` の対のみ。
- **D9 診断**: 較正=系統別 EV 帯×オッズ帯の Σwon/Σp̂(同じ bootstrap)。価格ずれ=log(O_F/O_J)。保持率=p̂ 固定近似。全て `diagnostic: true`。
- **D10 parquet 保存(174 の変更)**: `near-post-capture` が `build_day_features` 直後に `artifacts/near_post/<day>/features-<sha12>.parquet` を書く(存在すれば上書きしない)。読込時に `frame_sha256(read) == sha` を要求(T0 で実 DB の dtype 往復を確認。崩れるなら pickle に切替=research D10)。捕捉の判定順・fetch・保存は変えない。
- **D11 出力**: JSON 1 ファイル(contracts/report.md)+標準出力の要約。語彙は FR-020。
- **D12 テスト**: eval 単体(funnel 合計・精算・同着・同期差・決定論・帯境界)/ live 単体(fake predictor で再現ゲートの一致/不一致・sha 不一致で終了 2)/ live 統合(DB から集計・読み取り role)/ api parity / features leak-guard。
- **D13 運用**: `scripts/near_post_ops.sh results <day>` の後に `score <from> <to>` を 1 行追加。manual.md に「夜の 4 番目」として記載。

## Complexity Tracking

違反なし。
