# Feature Specification: オッズ上限つき買い目 policy + 正直な意思決定支援表示

**Feature Branch**: `064-odds-cap-betting-policy`

**Created**: 2026-07-09

**Status**: Draft

**Input**: User description: "Feature 064: オッズ上限つき買い目 policy + 正直な意思決定支援表示。現行 EV≥1.0 全馬買いは真OOS(2008–2026 walk-forward・902,710頭)で回収 ×0.721。唯一頑健なレバーは odds-cap(odds<21 で ×0.818・odds[6,21) で ×0.822・19/19年安定)。EV閾値引き上げ/haircut は逆効果(×0.703)。利益化は不能(天井 ~0.82<1.0、no-bet=×1.00 が最適)。改善の意味は出血低減 + 限界の正直な提示。"

## 背景と検証済み事実 *(この feature の設計根拠)*

本セッションで、backfill 済みモデル予測と walk-forward OOS(学習起点2007に整合させた 2008–2026 全19 fold・902,710頭・障害誤ラベル除外・binary+isotonic+TE proxy)を用いて、現行買い目ロジックの realized win 回収率を実測した。確立した事実:

- 現行 `EV = model_p × odds ≥ 1.0` の全馬買いは realized 回収 **×0.721**。bet の約半分が 51 倍超の大穴帯に集中し、そこが **×0.634** で全体を押し下げる(047/048 のモデル tail 過信が偽 value を生む)。
- **唯一頑健なレバーは odds 上限フィルタ**: `EV≥1.0 & odds<21` で **×0.818**、`odds[6,21)` で **×0.822**、**19/19 年で現行を上回り安定**、leave-one-winner-out で高オッズ1発依存なし。
- **EV 閾値引き上げ・edge haircut は逆効果**(`EV≥1.3` 全体 ×0.703 < 現行 ×0.721)。高 EV は「高オッズ × 膨れた tail 確率」由来のため、閾値を上げるほどモデルが最も過信する大穴を厚く買う。→ 既存提案(`docs/market-aware-betting-policy-proposal.md`)の EV閾値/haircut 案は棄却し、primary lever を odds-cap に差し替える。
- p−q 乖離・逆張り(モデル本命≠市場本命=本命ベタ超えは 11/19 年のみ=不安定)・モデル本命集中・人気帯・頭数 — ~10 レバー族すべてが odds-cap を超えず、**×0.82 の天井を破れない**。
- **利益化は構造的に不能**。天井 ~0.82 < 1.0。**「賭けない(no-bet)= ×1.00」が損しない唯一の方策**。よって本 feature の価値は「市場超過の収益」ではなく「現行ルールの構造的な出血を安定的に減らす」+「限界(利益不能・楽観バイアス)を利用者に正直に見せる」ことにある。製品目的([[product-goal-decision-support]])と整合する。

## Clarifications

### Session 2026-07-09

- Q: odds-cap の cap 値をどれで事前登録するか(<21 / [6,21) / <11) → A: **上限のみ odds<21**(単一閾値=最もシンプルで防御的。下限追加の +0.4pt はノイズの可能性がありパラメータを増やさない。[6,21) は採用ゲートで別途評価するに留める)
- Q: odds-cap を製品の既定 policy にするか opt-in に留めるか → A: **採用ゲート合格後に既定 ON**(実装直後は opt-in=既定は現行 EV≥1.0 全体でバイト同等、US3 の production 構成 walk-forward ゲート合格後に既定を cap ON へ切替。憲法 III=proxy 検証だけで本番既定を変えない、と整合)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 出血を減らす odds-cap policy の既定適用 (Priority: P1)

買い目を生成する運用者/利用者として、モデルが過信する大穴帯を構造的に除外した推奨を得たい。現行の「EV≥1.0 なら全部買う」は大穴 tail を大量に拾って余計に損をしており、事前登録・walk-forward 検証済みの odds 上限を既定で適用することで、同じモデル予測から**より出血の少ない**買い目集合を得る。

**Why this priority**: 本 feature の中核。唯一検証で頑健に効くレバー(odds-cap、0.721→~0.82、19/19年安定)を製品の買い目生成に載せる。これ無しでは feature の実利がない。

**Independent Test**: 既存の永続化済み予測 run に対し odds-cap policy 有効/無効で買い目を生成し、(a) cap 超オッズの馬が推奨から除外される、(b) cap 内の推奨は現行と同一(EV 閾値・Kelly は不変)、(c) logic_version に cap パラメータが記録される、ことを実 DB で確認できれば単独で価値がある。

**Acceptance Scenarios**:

1. **Given** モデル予測済みのレースで started 馬にオッズがある, **When** odds-cap policy(上限=事前登録値)で win 買い目を生成, **Then** オッズが上限以上の馬は EV≥1.0 でも推奨に含まれず、上限未満の馬は現行と同一条件(EV≥threshold・renorm・Kelly)で選定される。
2. **Given** odds-cap policy 無効(既定互換モード), **When** 買い目を生成, **Then** 現行(Feature 045/046)とバイト同等の結果になる(後方互換)。
3. **Given** 生成した推奨行, **When** logic_version を読む, **Then** cap 種別・cap 値・policy 名が記録され、stake=fraction×bankroll と併せて再現可能。
4. **Given** 全馬が cap 超オッズのレース(全馬大穴), **When** 買い目を生成, **Then** 推奨ゼロ(= no-bet)で正常終了し、エラーにならない。

---

### User Story 2 - 正直な意思決定支援としての買い目表示 (Priority: P2)

利用者として、買い目が「儲かる」ものではなく「損失を抑える判断材料」であることを、画面上で誤解なく理解したい。現状の表示は疑似 ROI と単勝の realized backtest を出すが、「利益は出ない(回収<1)」「賭けないのが最も損しない」「モデルは市場に対する再現可能な優位を持たない」という核心事実が前面に出ていない。

**Why this priority**: 製品目的(正直な意思決定支援)の実装。P1 のロジックが「損を減らす」に留まる以上、それを利益と誤読させない表示が不可欠。ロジック無しでも表示単独で価値がある(現行データで実装可能)。

**Independent Test**: rec panel に、表示中の単勝推奨から算出した realized 回収率(<1)と、同一母集団での no-bet(×1.00)・本命ベタ買いのベースラインを併置し、「市場超過の再現優位なし」の中立注記が常時表示されることを、MSW スタブと実 DB の両方で確認できる。

**Acceptance Scenarios**:

1. **Given** 単勝推奨に確定結果がある, **When** rec panel を見る, **Then** realized 回収率(平均回収倍率)が no-bet 基準(×1.00)と本命ベタ買い基準と並べて表示され、回収<1 が損益色や利益語なしの中立な事実として提示される。
2. **Given** 表示中の推奨集合, **When** odds 帯別の realized 回収を見る, **Then** 帯ごとの回収率と bet 数が(gap ソート・損益色なしで)表示され、大穴帯が最大の出血であることを利用者が自分で確認できる。
3. **Given** どのレースでも, **When** 買い目セクションを見る, **Then**「このモデルは市場に対する再現可能な優位を持たず、買い目は損失を抑える判断材料であって利益を示すものではない」旨の中立注記が常時表示される。
4. **Given** あるレースで policy が推奨ゼロ(全馬 cap 超), **When** rec panel を見る, **Then** 空欄でなく「見送り(全馬が上限オッズ超)」等の skip 理由が明示される。
5. **Given** 疑似オッズ/疑似 ROI/double-pseudo の値, **When** 表示する, **Then** 既存規律どおり必ずラベル付きで、realized(real)値とは別グループ・別バッジで提示される(疑似を実績と誤読させない不変)。

---

### User Story 3 - production 構成での採用ゲート最終確認 (Priority: P3)

開発者として、odds-cap policy を既定化する前に、検証時の高速 proxy(binary+isotonic+TE)ではなく production 構成(pl_topk + features-016 = lgbm-061 系)の walk-forward OOS で、cap レバーが現行 policy の出血を fold 安定的に減らすことを事前登録ゲートで最終確認したい。

**Why this priority**: 憲法 III(評価先行)。既定化は検証済みだが、忠実性(proxy→production)を採用ゲートで閉じる。ゲート不合格なら cap 値を再検討 or 既定化見送り(表示 US2 は独立に価値が残る)。

**Independent Test**: production 構成の walk-forward OOS で、現行 `EV≥1.0` policy と odds-cap policy を同一母集団・同一 fold で比較し、事前登録した cap 値・指標・採否バーに対する合否レポートが出る。

**Acceptance Scenarios**:

1. **Given** production 構成の walk-forward OOS, **When** 現行 policy と odds-cap policy を比較, **Then** 回収率・的中率・bet 数・見送り率・maxDD・最大連敗・log growth・odds帯別/fold別安定性が両 policy について算出され、cap policy が現行の出血を**fold 安定的に**下回る(=回収率が上回る)かを事前登録バーで判定する。
2. **Given** 採否判定, **When** ゲートを評価, **Then** 「ROI>1」は採否バーにせず、「現行 policy 比で回収率が改善 かつ fold 安定(過半 fold で改善・最悪 fold 非悪化)」を合格条件とする。
3. **Given** cap 候補が複数(例 <21 と [6,21)), **When** 選択, **Then** cap 値の選択は評価期間の外(事前登録 or nested)で固定され、評価結果を見てから cap を動かさない。

---

### Edge Cases

- 全馬が cap 超オッズ → 推奨ゼロ(no-bet)を正常状態として扱い、skip 理由を表示(エラーにしない)。
- オッズ欠損の started 馬 → 現行どおり確率分母には残すが bet はしない(cap 判定はオッズがある馬のみ)。
- 既定 cap は上限のみ(odds<21)で下限を設けない。採用ゲートで [6,21) 型(下限併用)を評価する場合は、下限も事前登録値として logic_version に記録する。
- odds-cap policy と Kelly sizing の相互作用 → cap で母集団を絞った後に既存 Kelly allocation(相互排他群)を適用(cap は選定段、Kelly は sizing 段の分離を保つ)。
- exotic 券種 → real 配当オッズ非取得のため本 feature の対象外(cap は win のみ)。exotic は従来どおり pseudo 表示。
- realized backtest 表示は既存どおり単勝(real 単勝オッズ)のみ。cap 適用後も的中/回収の意味論(Feature 049)は不変。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: win 買い目生成に **odds 上限フィルタ(事前登録値 odds<21、上限のみ)** を追加し、上限以上のオッズの馬を推奨から除外する。EV 閾値・renormalization・Kelly sizing の既存挙動は変更しない(cap は選定母集団を絞るのみ)。下限は設けない([6,21) 型は採用ゲートで別途評価するに留める)。
- **FR-002**: cap の種別・上限値(=21)・policy 名を**事前登録**し、生成される推奨の `logic_version` に記録する(再現・監査)。
- **FR-003**: odds-cap policy は **opt-in/既定切替可能**とし、無効時は現行(Feature 045/046)とバイト同等の結果を返す(後方互換)。**実装直後の既定は現行(cap 無効)**とし、**US3 の production 構成 walk-forward 採用ゲート合格後に既定を cap 有効へ切り替える**(proxy 検証だけで本番既定を変えない=憲法 III)。
- **FR-004**: cap 値・policy パラメータは **race_results を読まず**、**モデル特徴に流入しない**(リーク境界)。オッズ/市場 q は買い目 policy の入力にのみ使う(p≠q 分離)。
- **FR-005**: 買い目表示に、表示中の単勝推奨から算出した **realized 回収率**を、同一母集団の **no-bet(×1.00)** と **本命ベタ買い**のベースラインと**併置**する。損益色・利益語・回収率ソートは用いない。
- **FR-006**: 買い目表示に **odds 帯別の realized 回収率と bet 数**を(中立・非ソートで)提示し、大穴帯の出血が可視化される。
- **FR-007**: 買い目セクションに「モデルは市場に対する再現可能な優位を持たず、買い目は損失を抑える判断材料であって利益を示さない」旨の**中立注記を常時表示**する。
- **FR-008**: policy が推奨ゼロのレースでは、空欄でなく **skip 理由(例: 全馬が上限オッズ超)** を表示する。
- **FR-009**: 疑似値(estimated odds / pseudo_odds / pseudo_roi / double_pseudo)は既存規律どおり**必ずラベル付き**で、realized(real)値とは**別グループ・別バッジ**で提示する(疑似を実績と誤読させない不変を維持)。
- **FR-010**: 採用ゲート評価は **production 構成(pl_topk + features-016)の walk-forward OOS** で、現行 `EV≥1.0` policy と odds-cap policy を**同一母集団・同一 fold**で比較し、回収率・的中率・bet 数・見送り率・maxDD・最大連敗・log growth・odds帯別/fold別安定性を算出する。
- **FR-011**: 採否バーは **「ROI>1」ではなく「現行 policy 比で回収率が改善 かつ fold 安定(過半 fold 改善・最悪 fold 非悪化)」** とし、cap 値の選択は評価期間の外で固定する(selection leak 回避)。
- **FR-012**: スキーマ変更は避ける(既存 `recommendations` 列で cap 情報を logic_version に収める)。表示に新 API フィールドが要る場合は OpenAPI を純追加で契約先行し、front 型を committed snapshot + drift-check で同期する。

### Key Entities *(include if feature involves data)*

- **買い目 policy**: 「どの馬を買うか」を決める選定方策。入力=モデル p(必要なら校正済)・オッズ・EV。odds-cap は選定母集団に対するオッズ窓フィルタ。出力=推奨集合(+ Kelly stake)。**モデル特徴には一切流入しない**。
- **推奨(recommendation)**: 既存テーブル。cap 情報は `logic_version` に記録(新列なし)。realized 事後値(的中/回収)は既存 Feature 049 の意味論を踏襲。
- **policy 評価レポート**: walk-forward OOS で現行 policy と cap policy を比較した採用ゲート成果物(回収率/的中率/bet数/見送り率/maxDD/最大連敗/log growth/odds帯別・fold別)。永続化は本 feature ではスコープ外(deferred)。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: production 構成 walk-forward OOS(2008–2026 相当)で、odds-cap policy の realized win 回収率が現行 `EV≥1.0` policy を上回り、かつ**過半 fold で改善・最悪 fold で非悪化**する(事前登録バー合格)。
- **SC-002**: 既定互換モード(cap 無効)で生成した買い目が、現行(Feature 045/046)と**バイト同等**である(後方互換の回帰ゼロ)。
- **SC-003**: 生成された全推奨の `logic_version` から cap 種別・cap 値・policy を読み取り、stake=fraction×bankroll と併せて**推奨を完全再現**できる。
- **SC-004**: 買い目表示を見た利用者が、(a) 回収率が 1 未満であること、(b) 賭けないのが最も損しないこと、(c) モデルに市場超過の再現優位が無いこと、を**画面上の常時表示から**確認できる(疑似値は必ずラベル付き・realized と分離)。
- **SC-005**: cap 情報・realized ベースライン・odds帯別回収・中立注記のいずれも**モデル特徴に流入しない**(leak-guard test 緑)、かつ selection が race_results を読まない。

## Assumptions

- odds-cap の cap 値は **上限のみ odds<21** を事前登録する(clarify 2026-07-09 で確定)。[6,21) 型の下限併用は採用ゲートで別途評価するに留め、本 feature の既定候補は odds<21。cap 値は結果を見てからの調整はしない(憲法 III)。既定 ON への切替は US3 の production 構成ゲート合格後(clarify 確定)。
- realized 評価は **単勝(real 単勝オッズ)のみ**。exotic は real 配当オッズ非取得のため pseudo のまま対象外。
- 過去オッズは closing 寄り(発走前オッズは履歴非保持=Feature 019)であり、realized 回収は**楽観バイアスを含む**。真の実運用検証は今後 live refresh で購入時点オッズを貯める **prospective のみ**である旨を spec/表示に明記する。
- 検証は高速化のため binary+isotonic+TE proxy を用いた。cap レバーの**方向**は objective に依らず頑健だが、回収の**絶対値**は production(pl_topk)と多少ずれるため、既定化前に production 構成で最終確認する(US3)。
- 本 feature は betting(選定/表示ロジック)+ eval(採用ゲート)+ api/front(表示)に触れる。ops job/admin からの policy 切替は対象外。
- codex second opinion をレビューに用いる(betting+eval+採用ゲートに触る高リスク領域=CLAUDE.md 方針の MUST)。

## Out of Scope (Deferred)

- real exotic 配当オッズの取得(netkeiba ブロック中)と exotic の real 回収評価。
- 発走前オッズの時点固定・購入時点オッズの履歴保持(prospective policy 評価の基盤)。
- policy 評価レポートの永続化・admin/ops からの policy 起動や切替 UI。
- 複数券種同時 Kelly・cross-type 相関・オンライン再フィット。
- cap 以外のレバー(p−q・逆張り・本命集中・人気帯・頭数): 本セッションで OOS 不採用と判明済み。再探索は独立 feature で事前登録が必要。

## Constitution Alignment

- **II リーク防止 (NON-NEGOTIABLE)**: cap 値・オッズ・q・realized ベースラインは買い目 policy と表示にのみ使い、モデル特徴に戻さない(leak-guard test)。selection は race_results を読まない。p≠q 分離を維持。
- **III 評価先行 (NON-NEGOTIABLE)**: cap 値・policy 族を事前登録し walk-forward で採否。ROI>1 単独で採用しない。cap 選択を評価期間から分離(nested)。
- **IV 確率整合性**: 買い目は win p から算出、cap は選定段のフィルタで確率導出(009)を変えない。
- **V 再現性と監査**: cap/policy を logic_version に記録、pseudo/real 分離、realized は ResultBadge。
- **VI feature 分割規律**: スキーマ変更回避を第一に(既存列 + logic_version)。表示に必要なら OpenAPI 純追加で契約先行・front snapshot/drift-check 同期。
