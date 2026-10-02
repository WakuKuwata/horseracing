# 解析レビュー(138・2026-10-01・ワークフロー 2 体)

数値の照合: 凍結表の全 70 値が `rules_S1_S5_freeze.json` と一致(確認済)。以下は構造の指摘と採否(rev2 に反映)。

| 重大度 | 指摘 | 採否 | 反映 |
|---|---|---|---|
| CRITICAL(code) | 既存 `GET /market-ev` は「最新 computed_at の 1 版」を返す(`queries.market_ev_rows`・複数版混在は ValueError)。ens15 を列・単 seed を内訳に並べる FR-007 は成立せず、手動再計算で列が黙って単 seed に戻る | 採用 | FR-004: 表示版を単一定数 `DISPLAYED_MARKET_EV_MODEL_VERSION` で明示・FR-001: 2 版を 1 回の実行で同一 run_id/トランザクションに書く・SC-006 でテスト固定 |
| HIGH(内部) | 包含関係(S2⊂S1⊂S4・S1⊂S3)と受入シナリオ・パネル例が矛盾 | 採用 | 包含関係を明記、例と受入 1.1/1.2 を自己整合に修正、包含 assert をテストに |
| HIGH(内部) | 同じ馬・同じ rule の複数 pick 保存はオッズ履歴(憲法 V・086 で「生涯 1 件」に縮めた型) | 採用 | pick は 1 レース 1 馬 1 rule 生涯 1 件(UNIQUE)・void は追記・集計方針 v1=「発走前の最初の判断」 |
| HIGH(内部) | 段階規則が未定義(「ゼロを跨ぐ」誤記・600 点再判定の意味・非単調・復活可否) | 採用 | チェックポイント方式(300/600 点時点の tally・ラチェット・600 でも跨げば判定保留で終了・復活なし・新 ID) |
| HIGH(内部) | 価格感度「強(20% ずれても 100% 超)」は本文が「優位の大半が消える」と評価した同じ実験の逆向きラベル。σ=0.2 の集合は別物(n 1.9 倍・重なり 30%) | 採用 | 軸名を「価格ずれ試験(代理)」に、語を使わず数値(回収率・n・重なり)で表示。レベル 3 の基準を「優位の半分保持」に引き上げ(該当なし)。凍結表に n・重なり列を追加 |
| HIGH(code) | pick は結果未確定レースにしか書かれず、gap は training の特徴なので API は該当を再判定できない=SC-009(確定済み 2026-09 の再計算)が構造的に不可能 | 採用 | 計算した全レースの該当馬を pick に書き `result_pending_at_compute` で集計から除外・`days_since_last` を行に保存・API は再判定しない |
| HIGH(code) | seed 2〜15 は booster 無し・threads 4〜8 の非決定 fit・凍結 JSON の生成スクリプト不在=本番 ens15 は測った ens15 と別物で SC-001 の再現経路が無い | 採用 | `freeze_rules_S1_S5_20261001.py` をコミット(入力 sha256・seed 同梱)。本番 15 本を `num_threads=1, deterministic=true` で学習し、その予測で表を再凍結してから出荷(暫定値と明記) |
| HIGH(code) | FR-011 の「買い方」が ATTENTION_SCOPE の「買い」と既存 `ExpectedReturnNote` テストの裸 `/買/` に当たる | 採用 | 文言を「条件」に、ATTENTION_SCOPE から裸の「買い」を外し正規表現を凍結、注記は別コンポーネント `AttentionNote` |
| MEDIUM | 禁止語集合の割当(PROFIT_LANGUAGE は回収率を含む)・鮮度注記の「通常」が `UNMEASURED_ODDS_DRIFT` に当たる | 採用 | 「通常」を使わない文言に。集合 × コンポーネントを FR-008 に明記 |
| MEDIUM | FR-010「137 テスト緑」と列切替・白枠・「120%超」チップが両立しない | 採用 | 「120%超」チップは廃止し統合、変わる挙動を列挙してテストを新仕様に更新(FR-010) |
| MEDIUM | 入れ子 rule のチップ/レベル/不通過時の扱いが未定義 | 採用 | チップ=不通過でない最上位・レベル=その rule の軸・(130% 超)は S2 が不通過でないときのみ・ID 列挙に不通過タグ |
| MEDIUM | 一覧・日付ページ・診断の FR/SC/エンティティ欠落 | 採用 | FR-013・SC-010・US3 受入 2 本・Tally に次チェックポイント/開始日/void 件数/鮮度帯内訳 |
| MEDIUM | 取消・field_changed の pick 精算が未定義 | 採用 | void(`scratched`/`field_changed`)、pick 馬が出走していれば自身の結果で精算、US2 受入 5 |
| MEDIUM | migration 0019 で head 固定テスト 10 本が赤になる | 採用 | 憲法 VI 行に明記(features 9・live 1)、トリガ名と 2 本構成も明記 |
| MEDIUM | 「約 10 分」は timeout 600 秒ちょうど。1 回の実行で 2 版を同一特徴量から計算すれば秒単位、別 job にすると本当に 15 倍 | 採用 | FR-001 で同一実行・同一トランザクション、Assumptions を実測前提に、SC-011 で実測 |
| MEDIUM | `booster`/`booster_sha256` は 1 本の契約、`MarketEvModel` は 1 dir 1 本 | 採用 | artifact レイアウト `seed_NN/model_YYYY.txt` + `ensemble_YYYY.json`、行には manifest 名と sha(Merkle 型) |
| MEDIUM | `evaluate.day_bootstrap` は scripts 配下で api から import 不可。再実装は二重実装 | 採用 | eval パッケージへ移し研究スクリプトもそれを使う(FR-005) |
| MEDIUM | 列切替で `exceeds_threshold` は S3 の意味になり「120%超」と二重・閾値が front/api/training の三重定義 | 採用 | 「120%超」廃止、軸判定は API(レジストリ 1 箇所)、front は価格鮮度のみ |
| LOW | 確認窓の的中数が表に無い/軸の表示語が不統一/S4 の価格感度は毛差/ens15 の仕様が artifact から検証できない/鮮度の再描画方針/「印」「●●●」の語 | 採用 | 表に的中列、FR-006 の表示語表、価格ずれ軸の再定義で S4 の毛差問題は消滅、FR-001 で spec.json に seed/rounds/threads、描画時評価・自動更新なし、aria-label 規約 |
| LOW | 「booster 1 本約 100 秒」は arm 全体の時間/field_changed の文言は「再計算待ち」 | 採用 | Assumptions と Edge Cases を修正 |

## rev3(2026-10-02): `/speckit-analyze`(spec/plan/tasks 横断・2026-10-01)の解消

指摘 25 件(CRITICAL 1・HIGH 5・MEDIUM 9・LOW 10)をすべて解消した。ID ごとの対応表は `plan.md` §5 末尾、決定は D15〜D23。要点:

- **C1(CRITICAL・憲法 III)**: 表示モデルの切替(単 seed → ens15)を採用とみなし、ベースライン比較+ECE の採否条件を事前登録(D15・FR-015)。freeze スクリプトに較正を追加して研究 fit で実行した(既存の凍結値は差 0 件)。暫定値: ens15 LogLoss 0.201204 / ECE(等質量)0.00066、単 seed 0.201584 / 0.00111 → 条件を満たす。選ばれた馬の過大評価(S1 期待回収率の平均 140.6% → 実際 121.1%・S3 138.2% → 106.4%・S5 140.2% → 100.8%)を期待回収率の単位で開示する。`evidence/rules_S1_S5_freeze_research_provisional.json`。
- **H1**: 「最初の計算」を append-only の `attention_race_scans` への `ON CONFLICT DO NOTHING RETURNING` で判定(D16)。rev2 の「pick 行が無いとき」は初回 0 頭のレースで破れていた。
- **H2**: チップは判断時点の表示。価格鮮度は表示版の最新行で決め、現在値でチップの条件を確かめて(`chip_now`)外れていれば最弱+「判断時点のみ該当」(D17・FR-016・US1 受入 8〜11)。
- **H3**: チェックポイント判定を append-only の `attention_checkpoints` に記録して正とする。材料は発走 3 日経過分、書き手は training(D18)。
- **G1/G2**: 本番経路の parity タスク(T020a)、定義をレジストリに先に置き freeze がそれを使う+`definitions_sha256`(T004a・D20)。
- MEDIUM/LOW: 文言・用語・タスク順序・依存関係を修正(I1〜I6・O1〜O3・G3・G4・A1〜A8)。`.specify/feature.json` を 138 に更新。

migration 0019 は 1 表 → 3 表(`attention_picks`・`attention_race_scans`・`attention_checkpoints`・トリガ関数は共有)。tasks は 47 → 52 本。続く検証ワークフロー(codex+3 体+反証・反証に耐えた 34 件=25 論点)も反映し 53 本(plan §5「rev3 の検証」・D24〜D26)。特に、強調レベル 3 は凍結値で到達しない=137 の白枠・太い左帯は出荷後に表示されなくなる点は利用者の判断事項として残した。
