# Feature Specification: 騎手の時変切片 — confirmatory 測定と採否

**Feature Branch**: `107-jockey-tv-intercept`

**Created**: 2026-09-02

**Status**: 完了・REJECT(2026-09-02)

**Input**: User description: "騎手の時変切片(年次更新 730 日窓の部分プーリング b)の confirmatory 測定と採否 — 騎手軸 screening 生存(2026-09-02)の一度きりの決着。"

## 背景と性格づけ

production モデルの騎手情報は OOF target encoding が識別子を勝率スカラーに置き換えており、
騎手個体の自由度が 1 次元しかない。この残差構造を 3 段の screening で絞り込んだ:

| 段 | 測定 | 結果 |
|---|---|---|
| 分割オラクル(2026-08-27) | held-out OOF 予測に加法的騎手切片 | −0.0039(6 分割で頑健) |
| 生カテゴリ列 spike | 識別子を真のカテゴリ列として追加 | **+0.0135 有意悪化 = 閉鎖** |
| 静的部分プーリング spike | 全史 OOF 平均の b を外部加算 | −0.0020 CI 跨ぎ = 曖昧 |
| **時変 b spike(2026-09-02)** | b の推定行を訓練末尾 730 日窓に限定 | **C−A = −0.003281 CI[−0.00602, −0.00029] = 生存**(騎手軸初の CI 上限<0) |

本 feature は、この生き残った唯一の形 —— **全史 booster を offset に固定し、訓練末尾
730 日の内側 OOF 行だけから経験ベイズ縮約の騎手切片 b を推定して raw score へ外部加算
(GBM 入力列にしない・split 予算を食わない・FEATURE_VERSION 不変)、isotonic は調整後
スコアで再 fit** —— を、選択に使っていない別窓で**一度だけ** confirmatory 測定し、
ADOPT / REJECT を決着させる。

**null も成功**である。screening の生存マージンは +0.00028 で再学習ノイズ SD(fold 水準
0.001816)より小さい。さらに **screening の点推定 −0.00328 は confirmatory の δ=0.00352 に
届いていない** — スパイクの数値がそのまま再現しても primary は FAIL であり、採用には
screening を上回る効果が別窓で出る必要がある。confirmatory が際どいどころか不利寄りで
あることを実行前から明示する。REJECT の
場合、騎手軸は「加法切片・730 日窓・30 騎乗ゲート・年次更新・isotonic 再 fit という
**この設計族**」について閉じる(騎手情報の統計的全否定ではない — 時変効果の別表現・
交互作用は kill されないが、優先度は screening 3 段の履歴に基づき大きく下がる)。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 別窓 confirmatory 測定 (Priority: P1)

運用者として、時変騎手切片の効果を**選択に使っていない評価窓**で、事前登録した採用ゲート
(凍結 gate-config・再学習 seed 分散込み CI・δ provenance)の下で一度だけ測定し、
ADOPT / REJECT の verdict を得たい。screening(2025-2026 窓)は選択に使用済みなので、
その数値を採否の根拠に再利用してはならない。

**Why this priority**: この feature の存在理由そのもの。US2/US3 は verdict の分岐先であり、
US1 が完了しなければどちらにも進めない。

**Independent Test**: 凍結済み gate-config の hash 照合下で confirmatory driver を実行し、
verdict(ADOPT/REJECT/NO_DECISION)と証拠 artifact(per-race 差の生値から点推定・CI が
ビット一致で再計算できる形式・100 US1 契約)が得られること。

**Acceptance Scenarios**:

1. **Given** gate-config が OOS 実行前に凍結され hash 照合が有効、**When** confirmatory を
   実行、**Then** 事前登録した単一の verdict 式で三値判定が出て、証拠 artifact だけから
   点推定・CI が再計算できる
2. **Given** 評価窓が screening 窓(2025-01-01 以降)と重なる設定、**When** driver に渡す、
   **Then** fail-closed で拒否される(選択済み窓の再利用を構造的に禁止)
3. **Given** 両アームの予測が全レースで一致(b が効いていない縮退)、**When** 判定前検査、
   **Then** 実行を abort し効果数値を出力しない(097 型の縮退防止)

---

### User Story 2 - ADOPT 時の serving 統合 (Priority: P2)

verdict が ADOPT の場合、利用者に届く予測が実際にこの機構を通るように、騎手切片を
本番の予測経路に組み込みたい。b と縮約強度は学習時に凍結して監査可能にし、
「b は年 1 回の再学習サイクルで更新される」という運用規約を記録に残す。

**Why this priority**: ADOPT 分岐でのみ必要。測定(US1)が先。

**Independent Test**: 候補モデルとして登録した artifact から serving が b を読み、
同一レースの予測が confirmatory 時の候補アームの手続きと一致すること。切片なしの
既存 active モデルの予測はバイト不変であること。

**Acceptance Scenarios**:

1. **Given** ADOPT verdict と候補モデル artifact、**When** serving が予測を生成、
   **Then** b の外部加算が適用され、logic_version に機構マーカーが記録される
2. **Given** 既存 active モデル、**When** 本 feature のコードが結線された状態で予測、
   **Then** 予測はバイト不変(opt-in 構造・既定は現行経路)
3. **Given** b に無い騎手(新規・window 内騎乗不足)、**When** 予測、**Then** 切片 0
   (prior へ完全縮約)で予測が正常に出る

---

### User Story 3 - REJECT 時の閉鎖と保全 (Priority: P3)

verdict が REJECT の場合、結線だけを revert し、測定モジュールとテストを非結線で保全して
(062/070/090 同型)、騎手軸をこの設計族について閉じた記録を残したい。将来同じ軸を
測り直そうとしたとき、何をどの数値で閉じたかが一目で分かること。

**Why this priority**: REJECT 分岐でのみ必要。負の結果の保全はこのリポジトリの規律。

**Independent Test**: revert 後に既存全スイートが緑・active モデルの予測バイト不変・
保全モジュールの単体テストが非結線のまま緑。

**Acceptance Scenarios**:

1. **Given** REJECT verdict、**When** 後始末完了、**Then** serving/training の結線差分は
   ゼロで、測定モジュール+テストは直接呼び出しで緑
2. **Given** 閉鎖記録、**When** spec を読む、**Then** 閉鎖の主張範囲(設計族限定)と
   実測値が転記されている

---

### Edge Cases

- **騎手 ID の分裂(067 nk: サロゲート)**: 2025 年以降の騎乗の一部は `nk:` 代理 ID で
  記録されうる。同一騎手が 2 つの ID に分裂すると window 内騎乗数が分割され b が
  過小推定される。confirmatory 実行前に window 内の nk: 騎手 ID 件数と分裂疑いを監査し
  開示する(修復は 067 の領分でありスコープ外)
- **window 内騎乗ゼロの評価レース**: 評価窓に現れる騎手が window に無い場合は切片 0。
  カバレッジ(評価レースの騎乗のうち b を持つ割合)を fold 別に開示する
- **経験ベイズの暴走**: 短窓で τ̂²→0(λ→∞)または少数支配で λ 過小。screening で
  事前固定したクランプ [10, 500] を凍結流用し、クランプ発動の有無を fold 別に記録する
- **pre-2007 残存レースの混入**: DB にはデータ量実験の残存レース 71,549 件(1986 年〜)が
  あり、評価レースのロードを特徴プール開始日で絞らないと OOF 分割が特徴行ゼロで落ちる
  (2026-09-02 実測)。全 eval 経路で開始日の明示を必須とする
- **両アーム同一予測**: b が全ゼロに縮退した場合は判定を出さず abort(数値の出力自体を
  抑止し「差ゼロ=同等」の誤読を防ぐ)

## Requirements *(mandatory)*

### Functional Requirements

**測定(US1)**

- **FR-001**: confirmatory の候補手続きは screening spike と同一の設計族に凍結する:
  年 1 回更新の 730 日窓・30 騎乗ゲート・経験ベイズ縮約(クランプ [10, 500])・
  raw score への外部加算・調整後スコアでの isotonic 再 fit。**W・MIN_RIDES・クランプの
  グリッド探索や事後変更は禁止**(変更するなら別 feature で新規事前登録)
- **FR-002**: 評価窓は**選択に使っていない期間**のみで構成する(screening が使った
  2025-01-01 以降を採点窓に含めない)。窓は gate-config に凍結し、driver は凍結窓との
  不一致を fail-closed で拒否する
- **FR-003**: 採用判定は評価契約 v4 の既定ゲート一式に従う: paired winner NLL・
  開催日クラスタ bootstrap・**再学習 seed 分散込み total CI**・δ(導出 provenance 必須・
  測定ノイズからの導出は fail-closed)。実効バーは **点推定 < −δ(δ=0.00352・凍結
  gate-config が正本)かつ total CI 上限 < 0**(−0.0031 は CI 条件が課す下限であって
  δ の方が厳しい — analyze H1 で是正)
- **FR-004**: verdict の正本は事前登録した**単一の式**とし、gate-config 凍結後の
  読み替え・個別数値の事後選別を禁止する。三値(ADOPT/REJECT/NO_DECISION)で記録する
- **FR-005**: 証拠 artifact は 100 US1 契約に従う: per-race 差の生値を保存し、
  artifact だけから点推定・CI がビット一致で再計算できる。append-only
- **FR-006**: 両アームの予測一致(縮退)を判定前に検査し、縮退時は効果数値を出力せず
  abort する
- **FR-007**: 実行前監査として (a) window 内の騎手 ID 分裂(nk:)件数 (b) 評価レースの
  b カバレッジ (c) fold 別 λ・騎手数・クランプ発動 を開示する
- **FR-008**: 全 eval 経路で評価レースのロード開始日を特徴プール開始日に固定する
  (pre-2007 残存レースの混入防止)

**採用時(US2)**

- **FR-009**: b・縮約強度・window 定義・推定に使った行の範囲を model artifact に凍結し、
  読み込み時に整合検証する。serving は artifact の値のみを使う(実行時再推定しない)
- **FR-010**: 切片の適用は opt-in 構造とし、既定(現行 active モデル)の予測はバイト
  不変。適用時は logic_version に機構マーカーを記録する
- **FR-011**: 「b は再学習サイクル(年 1 回目安)で更新する」運用規約と、更新を怠った
  場合の劣化リスク(estimand は年次更新手続きの性能であり、放置された b の性能ではない)
  を運用文書に記録する
- **FR-012**: 昇格は既存の昇格ゲート(標準窓非劣化+本 verdict+subgroup guard)を
  すべて通過した場合のみ。confirmatory verdict 単独で active を書き換えない

**閉鎖時(US3)**

- **FR-013**: REJECT/NO_DECISION の場合、結線差分を revert し、測定モジュール+単体
  テストを非結線で保全する。active モデルの予測バイト不変を実 DB E2E で確認する
- **FR-014**: 閉鎖の主張範囲を「この設計族」に限定して spec に転記する。screening の
  生値(オラクル −0.0039・静的 −0.0020・時変 −0.00328)と confirmatory の実測値を併記する

**共通**

- **FR-015**: 測定段階では モデル・特徴・FEATURE_VERSION・スキーマ・API・OpenAPI・
  買い目生成・確率導出(009)を一切変更しない。migration なし
- **FR-016**: b と isotonic が同じ内側 OOF ラベルを見るラベル二重使用は既知の限界として
  文書化する(対照アームと同一手続きなので paired 比較の妥当性は保たれ、評価窓は非汚染)

### Key Entities

- **騎手切片 b**: 騎手 ID → 実数の写像。訓練末尾 730 日の内側 OOF 行から経験ベイズ縮約で
  推定。window 内 30 騎乗未満は係数を持たない(0 = prior へ完全縮約)
- **gate-config**: 評価窓・アーム定義・判定式・δ・seed・bootstrap 設定の凍結記録。
  OOS 実行前に hash を確定し driver が照合する
- **証拠 artifact**: per-race paired 差の生値+判定値。append-only・ビット一致再計算可能
- **verdict**: ADOPT / REJECT / NO_DECISION の三値+根拠 artifact への参照

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: confirmatory が凍結 gate-config の hash 照合下で完走し、三値 verdict と
  証拠 artifact が得られる(artifact からの再計算が判定値とビット一致)
- **SC-002**: 評価窓に 2025-01-01 以降のレースが 1 件も含まれない(構造検査で確認)
- **SC-003**: 実行前監査(ID 分裂・カバレッジ・λ 診断)が artifact に記録されている
- **SC-004**: どちらの verdict でも、active モデルの予測が実 DB E2E でバイト不変
  (ADOPT の場合は候補モデルの登録のみで active は昇格ゲート通過まで不変)
- **SC-005**: REJECT の場合、revert 後に全パッケージのテストが緑で、保全モジュールの
  単体テストが非結線のまま緑
- **SC-006**: spec に実測結果(点推定・CI・verdict・閉鎖範囲または採用範囲)が転記され、
  screening の履歴と一続きで読める

## Assumptions

- confirmatory の評価窓は 2025 年より前の walk-forward 窓(疑似 cutoff・097/098 型)で
  構成する。具体的な cutoff 年は plan で決め gate-config に凍結する(選択リークを避ける
  ため、窓の選定にラベルや効果数値を使わない)
- 過去窓での測定は「この機構が時代を超えて可搬か」を測る。serving 時代(2026・nk: ID 混在)
  への外挿には限界があり、subgroup guard(069)の適用可否は plan で確定する
- screening spike のハーネス(`scripts/jockey_timevarying_spike.py`)は confirmatory の
  実装土台として流用できる。ただし判定・凍結・証拠保存は v4 契約の正規機構
  (paired-eval driver・gate-config hash 照合)に載せ替える
- 再学習 seed 分散は実測済みの sd_fold(2026-08-18・6 seed の probe)の再掲で織り込む
  (v4 標準・単一 seed 実行)。新規バンドル実測はしない — seed を増やして CI を縮める
  操作は 100 R9 の型であり、やるなら 100 US3 の再事前登録が筋(plan D7)
- 騎手 ID の分裂修復(067 の残余)はスコープ外。分裂は b を過小推定する方向に働くので、
  本測定の結果は保守側に倒れる

## 実測結果 (2026-09-02・confirmatory 完了・REJECT)

**判定 = REJECT(gate_hard_fail・`final_decision` の三値・事前登録どおり)**

| 測定 | 値 |
|---|---|
| 窓 | 2022-01-05..2024-12-28(採点 10,366 レース・適格 10,353・**321 開催日**) |
| **diff(候補−arm E)** | **−0.001902** |
| 標本 CI | [−0.004260, +0.000486] |
| **total CI(seed 膨張込み)** | **[−0.005029, +0.001248] — ゼロ跨ぎ** |
| gate | primary FAIL(点推定が −δ=−0.00352 に未達)・stat_guard FAIL・top2/top3 非劣性 FAIL・recent PASS・calibration PASS |
| 実行時間 | 2,692 秒(見積 60 分に対し 45 分) |

**screening との一続きの表(FR-014)**:

| 段 | 窓 | 効果 |
|---|---|---|
| 分割オラクル | 2025-26(選択) | −0.0039 |
| 静的 b spike | 2025-26(選択) | −0.0020 CI 跨ぎ |
| 時変 b spike | 2025-26(選択) | −0.00328 CI[−0.00602, −0.00029] 生存 |
| **confirmatory** | **2022-24(独立)** | **−0.0019 CI 跨ぎ → REJECT** |

**機構の読み**: screening の −0.00328 は独立窓に**輸送されず**、静的 b の screening 値
(−0.0020)と同水準に回帰した。鮮度増分(screening C−B=−0.0009)は窓固有のゆらぎだった
公算が高い。スパイク実行前に明示した「生存マージン +0.00028 < 再学習ノイズ SD 0.0018」の
懸念がそのまま実現した形であり、**margin-teacher(099)の「spike GO ≠ 本番 GO」の再演**。

**実行妥当性(T007・preflight)**: 3 fold とも λ=31〜43 でクランプ非発動・騎手 153〜161 人・
window 行 ~95k/fold・評価騎乗の b カバレッジ 93.75%・nk: 騎手 0(2022-24 窓の想定どおり)。
NO_DECISION に相当する実行異常はなく、REJECT は実力での判定。

**証拠(SC-001/002)**: `evidence/paired-evidence.json`(10,353 行・append-only)からの
公式 `evidence.recompute` が点推定・標本 CI・total CI を**ビット一致**で再現。全レース日が
2022-01-05..2024-12-28 に収まることを確認(2025+ 混入ゼロ)。active モデルの予測は
測定前後で md5 一致(SC-004)。

**閉鎖の範囲(FR-014)**: 騎手軸は「**加法切片・730 日窓・30 騎乗ゲート・年次更新・
isotonic 再 fit という設計族**」について閉じた。騎手情報の統計的全否定ではない —
時変効果の別表現・交互作用は kill されていないが、screening 4 段+confirmatory の履歴に
基づき優先度は大きく下がる。再開には新規の事前登録が必要。

**既知の限界(FR-016)**: b と isotonic は同じ内側 OOF ラベルを見る(cross-fit しない)。
両アーム同一手続きなので paired 比較の妥当性は保たれ、評価窓は非汚染。2026/nk 時代への
可搬性は本測定の死角だったが、REJECT により moot。

**保全**: `scripts/jockey_timevarying_spike.py`(screening)・`scripts/jockey_tv_confirmatory.py`
(driver・公式 paired_eval 直結)・`eval/tests/unit/test_jockey_tv_confirmatory.py`(9 テスト)・
gate-config(凍結 hash `c872172a…`)・evidence 一式を保全。結線差分ゼロ
(training/serving/eval/features のソース不変・eval 503+31 / training 487 緑)。
