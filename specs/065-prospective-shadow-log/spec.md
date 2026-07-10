# Feature Specification: prospective shadow-betting log(前向き影buy記録)

**Feature Branch**: `065-prospective-shadow-log`

**Created**: 2026-07-10

**Status**: Draft

**Input**: User description: "Feature 065: prospective shadow-betting log — 発走前オッズを決定時点で凍結して『買ったつもり』を記録し、結果確定後に closing でなく約定可能だったオッズで精算する、回収率を正直に測る計器。過去データからは作れない前向きの装置。"

## 背景・動機 *(この feature の存在理由)*

これまでの検証で、以下が確定した:

- 勝率モデル lgbm-061 は市場 q に LogLoss で全セグメント負け(047)。買い目 policy は odds<21 cap で出血を ×0.721→×0.818 に減らせたが天井は <1.0(feature 064)。p×q 合成 spike も α≈0(純市場が最良)= 合成でも市場を超えられない。
- **決定的な計測上の欠陥**: 過去の realized ROI は全て closing/final オッズ由来で楽観バイアス(closing-oracle)を含む。DB のオッズは全て一括ロード(updated_at ほぼ 2026-07-01)で、**発走前オッズは1件も存在しない**。「発走前に実際に約定できたオッズで利益が出るのか」は過去データからは**構造的に測定不能**。
- したがって唯一の正直な計測法は **prospective(前向き)**: 発走前オッズを決定時点で凍結し、その凍結オッズで結果確定後に精算する。これが closing-oracle を回避する唯一の道。

本 feature はモデルや買い目ロジックを変えない。**「約定可能だったオッズでの真の回収率」を今後貯めて正直に測る計器**を作る。計器は空で始まり、これから発走前オッズを捕捉して初めて埋まる。

## Clarifications

### Session 2026-07-10

- Q: prospective 識別(前向き・凍結オッズで出した推奨 vs closing backfill)をどう表現するか → A: **logic_version マーカー**(生成時に `;prospective=1;odds_asof=<ts>` を付与。スキーマ変更ゼロ=migration なし、064 の oddscap と同方式で一貫。凍結オッズは既存 `market_odds_used` が担う。非混同[SC-002]は marker の文字列マッチで担保=064 実績あり)
- Q: shadow-log 集計の永続化をどうするか → A: **recommendations から read-time 集計**(凍結オッズ付き prospective 推奨は既に永続化済み。集計は読み取り時に計算=049 win backtest 同型。新テーブル/新列なし・migration なし。前向き推奨は少量のため read-time で十分。集計スナップショットの時系列保存は deferred)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 前向きに出した買い目を凍結オッズごと記録する (Priority: P1)

運用者として、まだ結果の出ていない(発走前の)レースに対し、その時点のオッズで買い目を生成し、**使用したオッズと決定時刻を凍結して**「前向きに出したもの」と明示的に記録したい。後で closing オッズに上書きされても、記録した買い目の評価は決定時点のオッズで固定される。

**Why this priority**: 計器の心臓部。前向き記録(=約定可能だったオッズの凍結)が無ければ、closing-oracle を排除できず、この feature の目的が達成できない。

**Independent Test**: 結果ペンディングのレースに前向き買い目生成を実行 → 推奨が「prospective(発走前・凍結オッズ)」と識別可能に記録され、後で race_horses.odds を closing 値に更新しても、その推奨の評価用オッズは決定時点の値のまま。

**Acceptance Scenarios**:

1. **Given** 結果ペンディング(result なし)かつ発走前オッズのある started 馬がいるレース, **When** 前向き買い目を生成, **Then** 各推奨に決定時点オッズ・決定時刻・prospective 識別が記録される。
2. **Given** 記録済みの前向き推奨, **When** 後刻そのレースのオッズが closing 値に更新される, **Then** 既存推奨の評価用オッズ(凍結値)は変わらない。
3. **Given** 既に結果が出ているレース, **When** 前向き記録を試みる, **Then** 拒否される(前向き=結果ペンディングのみ・fail-closed)。
4. **Given** closing 一括データに後ろ向きに backfill された推奨(feature 044/064), **When** shadow-log から見る, **Then** 前向き記録と**混ざらず区別**される。

---

### User Story 2 - 前向き実績を正直に集計するビュー (Priority: P2)

利用者として、前向きに出した買い目が結果確定後に**約定可能だったオッズで**どれだけ回収したか(回収率・的中率・確定 bet 数・未確定数・void 数)を時系列で見たい。closing backtest とは別セクションで、「real 約定可能オッズ・prospective・将来利益を約束しない」と正直にラベルされる。(見送り率は行が残らず算出不能=FR-004)

**Why this priority**: 計器の読み出し。前向き記録があっても、それを retrospective closing backtest と分離して正直に集計できなければ、また closing-oracle と混同される。

**Independent Test**: 前向き記録のうち確定済みのものを集計 → prospective 専用の回収率/的中率/確定数/未確定数/void 数が、closing backtest とは別セクション・正直ラベル付きで表示される。未確定は「集計待ち」と区別。

**Acceptance Scenarios**:

1. **Given** 確定済みの前向き推奨群, **When** shadow-log ビューを見る, **Then** prospective の回収率(凍結オッズ×公式結果)・的中率・確定 bet 数・未確定数・void 数が算出される(見送り率は算出しない=行が残らないため)。
2. **Given** 前向き記録に確定済みと未確定が混在, **When** 集計を見る, **Then** 確定済みのみ集計し未確定は「settle 待ち」と別扱い(未確定を回収に混ぜない)。
3. **Given** shadow-log と既存 closing backtest(049/064), **When** 両者を見る, **Then** セクションが分離され、prospective は「real 約定可能オッズ・前向き・closing でない・利益を約束しない」と常時ラベル。
4. **Given** 前向き記録がまだゼロ(計器が空), **When** ビューを見る, **Then** 空状態を正直に表示(「まだ前向きデータが無い/収集はこれから」)。

---

### User Story 3 - 前向き収集を回す運用ワンショット (Priority: P3)

運用者として、「未来レースのオッズ取得 → 前向き買い目生成 → 記録」を1コマンド相当で回し、結果確定後に「精算 → shadow-log 反映」も回したい。既存の scrape(008)/live refresh(019/050)/settle(049)を薄く束ねるだけで、新しい予測・精算ロジックは足さない。

**Why this priority**: 計器を継続的に埋める運用ループ。P1/P2 が箱、P3 が「箱に流し込む蛇口」。手動でも回せるが、束ねると運用が現実的になる。

**Independent Test**: 発走前オッズのあるペンディングレース群に対し前向き収集を実行 → prospective 推奨が生成され、後で結果を入れて精算を実行 → shadow-log に realized が反映される。冪等(二重投入で重複しない)。

**Acceptance Scenarios**:

1. **Given** 発走前オッズを持つペンディングレース群, **When** 前向き収集を実行, **Then** 各レースに prospective 推奨が生成される(オッズ欠損レースは prediction のみ・記録スキップ理由付き)。
2. **Given** 前向き収集を同一レースに再実行, **When** 生成, **Then** 冪等に skip(重複記録なし)。
3. **Given** 前向き記録済みレースに結果が入る, **When** 精算を実行, **Then** 凍結オッズで realized が算出され shadow-log に反映される(結果を特徴に戻さない)。

---

### Edge Cases

- 発走前オッズが無いペンディングレース → prediction は出すが prospective 買い目記録はスキップ(理由付き)。
- 決定後にそのレースのオッズが動く(発走前でも刻々変わる) → 記録した推奨は決定時点の凍結値で固定(後刻値では再評価しない)。
- レース取消・出走取消(記録後に scratch) → 既存 void 規則(049)に従い回収非算入。
- 前向き記録と backfill 記録が同一レースに併存 → shadow-log は prospective 印のあるものだけを集計(混同禁止)。
- 計器が空(前向きデータゼロ) → 空状態を正直に表示、偽の集計をしない。
- 発走前オッズフィードが停止/未整備 → 計器は埋まらない(運用前提の不足として明示、エラーにしない)。
- **レースは終わっているが結果が未 ingest**(result-pending 判定は「結果行の不在」であり wall-clock の未走ではない=codex 指摘) → この状態で scrape すると **closing オッズを prospective として凍結しうる**(closing-oracle の裏口)。→ capture 規律(FR-011: fresh scrape + 捕捉時刻記録 + post_time 前要求)で防ぎ、post_time 未知なら「発走前保証が弱い」とラベルして区別。
- post_time が null のレース → 発走前保証が弱く、集計で「弱保証」として区別(強保証と混ぜない or 別掲)。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 前向き買い目生成は **結果ペンディング(race_results 無し)** のレースにのみ行い、結果が既にあるレースでは拒否する(fail-closed)。
- **FR-002**: 前向き推奨には **決定時点で使用したオッズ(凍結=既存 market_odds_used)・決定時刻(computed_at)・オッズ捕捉時刻(odds_asof=収集フローの scrape/capture 時刻)・prospective 識別** を記録する。後でオッズが closing に更新されても、その推奨の評価用オッズは不変。odds_asof は「オッズをいつ観測したか」であり、RaceHorse.updated_at(汎用行鮮度)ではなく捕捉イベントの時刻を用いる(codex 指摘)。
- **FR-003**: 前向き記録は closing 一括データに後ろ向きに backfill した推奨(044/064)と**機械的に区別**できる(混同不可)。識別は **logic_version マーカー**(`;prospective=1;odds_asof=<ts>`)で表現し、**発走前の prospective 収集経路のみ**がこれを付与する(clarify 2026-07-10)。マーカー解析は厳密トークン(`;` split、loose 部分一致禁止)で行う。
- **FR-011**: prospective 収集は **capture 規律**を守る: (a) 同一フローで発走前オッズを fresh に取得し、その捕捉時刻を odds_asof に記録、(b) 生成直前に result-pending を再確認(fail-closed・下記の限界も参照)、(c) レースの post_time が既知ならオッズ捕捉が post_time より前であることを要求、post_time が未知(null)なら**発走前保証が弱い旨をラベル**して集計で区別する。1レース1policy=one-shot(締切直前の late-market を cherry-pick させない)。
- **FR-004**: shadow-log 集計は **prospective 印のある確定済み推奨のみ**を対象に、凍結オッズ×公式結果で realized 回収率・的中率・確定 bet 数・未確定数・void 数を算出する。未確定は集計(回収/的中)に含めず別計上。**見送り率(skip rate)は算出しない** — 買い目ゼロのレースは行が残らず、recommendations だけからは分母を作れないため(codex 指摘・attempt log 永続化は schema 変更で不可)。ROI/的中の分母は `hit is not None`(void 除外)。集計は recommendations を **run 跨ぎで直接クエリ**し、active-run scoped 表示クエリは使わない。favorite_realized や race_horses.odds の現在値を ROI に一切入れない(凍結 market_odds_used のみ)。
- **FR-005**: shadow-log ビューは既存 retrospective closing backtest(049/064)と**別セクション**で、「real 約定可能オッズ・prospective・closing でない・将来利益を約束しない」を**常時ラベル**表示する。
- **FR-006**: 前向きデータがゼロのとき、偽の集計を出さず**空状態を正直に表示**する。
- **FR-007**: 前向き収集の運用(オッズ取得→生成→記録、結果後の精算→反映)は既存 CLI(008/019/050/049)を束ねるのみで、**新しい予測・精算ロジックを追加しない**。冪等は **(race, model, prospective policy) で run 跨ぎ**に効かせる(run 単位では live の append-only 新 run 生成で重複しうる=codex 指摘)。check-then-insert は advisory-lock で競合防止。
- **FR-008**: オッズ・結果・prospective 識別は**モデル特徴に流入しない**(リーク境界)。selection は結果を読まない。
- **FR-009**: 疑似値(推定オッズ由来)は既存規律どおり必ずラベルし、prospective の realized(real 単勝オッズ)とは別扱い。exotic prospective は対象外(win のみ)。
- **FR-010**: **スキーマ変更なし**(clarify 確定): prospective 識別は logic_version マーカー、shadow-log 集計は recommendations からの read-time 計算で、新列/新テーブル/migration を一切足さない。表示に新 API フィールドが要る場合のみ OpenAPI 純追加で契約先行・front snapshot/drift-check 同期。

### Key Entities *(include if data involved)*

- **前向き推奨(prospective recommendation)**: 発走前・結果ペンディング時に、凍結した決定時点オッズで出した買い目。決定時刻・使用モデル/policy・prospective 識別を伴う。既存 recommendations の意味論を踏襲し、backfill 記録と区別可能。
- **shadow-log 集計**: prospective 確定済み推奨の realized(凍結オッズ×公式結果)を時系列・全体で集計した読み取り専用ビュー。**recommendations から read-time 集計**(新テーブル不要・clarify 確定)。集計スナップショットの永続化は deferred。
- **凍結オッズ(frozen bet-time odds)**: 記録時点で確定させた、その推奨の評価に使う唯一のオッズ。後刻の closing 更新で変化しない。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 前向きに記録した推奨は、記録後にそのレースのオッズを closing 値へ更新しても、評価用の凍結オッズ・realized 結果が**バイト単位で不変**(closing-oracle を構造的に排除できている)。
- **SC-002**: shadow-log 集計は prospective 印のある確定済み推奨のみを対象とし、backfill(closing)推奨や未確定推奨を**1件も混入させない**。
- **SC-003**: 利用者は shadow-log ビューから、(a) それが real 約定可能オッズの前向き実績であること、(b) closing backtest とは別物であること、(c) 将来利益を約束しないこと、(d) 現在のデータ量(まだ収集中か)を、**画面の常時表示から**判別できる。
- **SC-004**: 前向き収集の運用ループ(生成・精算)は再実行で**重複記録ゼロ**(冪等)であり、結果ペンディングでないレースへの前向き記録は**必ず拒否**される。
- **SC-005**: prospective 識別・凍結オッズ・結果のいずれも**モデル特徴に流入しない**(leak-guard test 緑)、かつ selection が race_results を読まない。

## Assumptions

- **計器は going-forward**: 過去データからは埋まらない。今すぐ数字は出ず、信頼できる回収率は**数か月の前向き収集後**に初めて得られる。この前提を spec/表示に明記する。
- **「発走前」は運用規律に依存する保証**(codex 指摘): result-pending は「結果行の不在」で、レース後・結果未 ingest を含みうる。真に発走前であることは capture 規律(fresh scrape + 捕捉時刻 + post_time 前・one-shot)でしか担保できず、post_time 未知のレースでは弱保証。締切直前や結果 ingest 前に回すと late-market/closing 的になり計器が汚れる。**operator が規律を守ることが計器の正しさの前提**であり、これを spec/表示で正直に開示する。
- **データ依存(運用前提)**: 計器を埋めるには (1) 未来レースの ingest(現在 DB は 2026-07-05 で停止)と (2) 発走前オッズのフィード(netkeiba scrape 008・以前ブロックされたが polite+backoff で本来可能)が要る。これは本 feature の実装ではなく**運用の前提条件**であり、フィードが無ければ計器は空のまま(エラーにしない)。
- **win のみ**: real 単勝オッズを凍結して評価。exotic prospective は real exotic 配当(012)取得が前提で対象外。
- 既存の予測(006/019)・推奨(045)・精算(049)・scrape(008)経路をそのまま再利用し、新しい予測/精算ロジックは足さない。
- 利益は約束しない。計器が「約定可能オッズでも回収 <1.0(利益不能)」を示す可能性が高く、それも誠実な結論として受け入れる。

## Out of Scope (Deferred)

- 発走前オッズの自動スケジュール取得(cron/ops ジョブ化)。
- exotic の前向き記録(real exotic 配当 012 取得が前提)。
- real-money 運用・アラート/通知。
- 複数ソースオッズ・締切 N 分前など時点別オッズ時系列。
- 発走前オッズフィードの新規構築(netkeiba ブロック解除の技術対応は別作業)。

## Constitution Alignment

- **II リーク防止 (NON-NEGOTIABLE)**: オッズ・結果・prospective 識別は表示/評価にのみ使い、モデル特徴に戻さない(leak-guard)。selection は race_results を読まない。p≠q 分離(この計器は市場を特徴化しない)。
- **III 評価先行 (NON-NEGOTIABLE)**: これは「利益を主張する」機能でなく「利益が出るか出ないかを closing-oracle 無しで正直に測る計器」。ROI>1 を前提にしない。
- **IV 確率整合性**: 予測 p は既存経路のまま不変。前向き記録は確率導出を変えない。
- **V 再現性・監査**: 凍結オッズ・決定時刻・prospective 識別・モデル/policy を記録し、prospective 実績を再現可能に。pseudo は必ずラベル。
- **VI feature 分割規律**: スキーマ変更を極力回避。必要時のみ小 migration を正当化し、API/OpenAPI 契約先行・front snapshot/drift-check 同期。UI は契約確定後。
