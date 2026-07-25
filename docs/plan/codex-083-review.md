並走はユーザー指定どおり実施していません。実装変更も行っていません。

結論は「条件付き GO」です。1 GET・migration なし・最新 run の read-only 転記は妥当ですが、`payload: dict`＋手書き TS 型の現案のまま進めるのは推奨しません。

## 設計の穴（優先度順）

1. **P0: 型弱化は 075 の罠を解消せず、admin 側へ移している**

現案は API で `payload: dict`、admin で `as SaPayload` とキャストし、ほぼ全 field を optional にしています。[schemas.py](/Users/kuwatawaku/workspace/horseracing/api/src/horseracing_api/schemas.py:550) [DiagnosticsPage.tsx](/Users/kuwatawaku/workspace/horseracing/admin/src/pages/DiagnosticsPage.tsx:106)

これは key 改名・欠落時に `undefined → —` や空配列となり、075 と同じ silent-null を再現します。version field は自己記述であって自己検証ではありません。また `run.payload or {}` により空 payload も 200 になり得ます。[diagnostics.py](/Users/kuwatawaku/workspace/horseracing/api/src/horseracing_api/routers/diagnostics.py:63)

推奨は versioned v1 schema です。

- `instrument_contract/provenance/population/CI/calibration/year row` は全型付け
- axis は `grain` による race/horse discriminated union
- bucket「名」は動的 `dict[str, RaceBucketV1 | HorseBucketV1]`
- `definition` の内部だけ JSON object を許容
- required field に default/optional を置かず、各 model は `extra="forbid"`
- `metric_contract_version` 不明時は typed `diagnostic_contract_unsupported`
- malformed latest run は古い run にフォールバックせず fail-closed

075 の原因は「型付け」ではなく、異なる key の `**dict` と `extra=ignore/default None` です。`model_validate(raw_payload)`＋`extra=forbid` なら逆に防止策になります。

2. **P0: `buckets` の「payload 順」は保持できない**

`axes` は配列なので固定順を保持できますが、`buckets` は JSON object です。生成側で名前順に挿入していても、JSONB object の key 順は契約になりません。[segment_accuracy.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/segment_accuracy.py:297)

したがって `Object.entries(axis.buckets)` は anti-fishing の固定順保証になりません。長期的には各 axis に `bucket_order` を持たせるか、bucket を ordered array にすべきです。既存 run を表示する当面策は、`mask_library_version × axis_id` ごとの固定・結果非依存 order を viewer に持たせることです。

3. **P0: 082 producer 側に、viewer が露出する前に扱うべき契約差分がある**

- `excess_nll_market` は model NLL を全 race、market NLL を market-complete race で計算して差を取っています。欠損があると同一母集団比較ではありません。[segment_accuracy.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/segment_accuracy.py:310)
- `load_eval_races` は finished label がゼロの race を ledger より前に落とします。したがって「全 race＝scored＋exclusions」を満たしません。[dataset.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/dataset.py:178)
- spec は ECE/NLL/excess の cluster-bootstrap CI を要求しますが、payload の ECE/CITL は point のみです。[spec.md](/Users/kuwatawaku/workspace/horseracing/specs/082-segment-accuracy-readout/spec.md:70)
- race-grain CITL は、選択した各 race の全馬について `Σp=Σy=1` なので構造的にほぼ常時 0 です。実 run でも race axes は 0 です。[evidence-first-run.json](/Users/kuwatawaku/workspace/horseracing/specs/082-segment-accuracy-readout/evidence-first-run.json:196) 「良好な較正」と誤読させず、race grain では N/A/恒等的と表示すべきです。
- surface の frozen definition は芝/ダですが、実 payload には障害が存在します。[segment_accuracy.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/segment_accuracy.py:167) mask domain/orderの再確認が必要です。

viewer が再計算して補正するのは禁止です。producer を直すか、該当表示を一時的に抑制・限定表示してください。

4. **P0: 2 セクションを独立状態にする必要がある**

現在の未コミット実装では、segment-edge の loading/404/error がページ全体を early return するため、segment-accuracy run が存在しても表示されません。[DiagnosticsPage.tsx](/Users/kuwatawaku/workspace/horseracing/admin/src/pages/DiagnosticsPage.tsx:27)

2 セクションは取得・loading・404・error を独立させるべきです。

5. **P1: envelope に `diagnostic_run_id` が必要**

082 は発見後の事前登録に `discovery_run_id` を要求します。[spec.md](/Users/kuwatawaku/workspace/horseracing/specs/082-segment-accuracy-readout/spec.md:87) しかし payload は DB 採番前に作られるため ID を持てず、現 envelope にもありません。UUID を envelope に追加してください。監査、ETag、将来の run 固定 axis endpoint にも使えます。

6. **P1: “typed 404” が OpenAPI 上は typed ではない**

現 OpenAPI は endpoint の 200 しか宣言せず、404 ErrorBody は生成型に現れません。runtime body が定型なだけです。404 を OpenAPI response として明示し、`kind` も `str` ではなく `Literal["segment_accuracy"]` にすべきです。

7. **P1: UI の監査情報が不足する**

最低限、常時または監査パネルで以下を表示すべきです。

- 「run 生成時の active recipe による historical OOF」であり、現在の active artifact ではない
- computed_at と eval window 終端を別々に表示
- `secondary=true`、`can_adopt=false`、discovery rule、CI 未調整注記
- probability stage、logic/version、mask hash、code SHA、bundle/attestation digest
- n_races/n_horses/n_days、exclusion ledger
- axis definition、grain、origin。post-081 origin は折りたたみ外にも明示
- market-complete の分子/総 race 数
- 年別値と reliability bins は中立な詳細表示としてアクセス可能にする

## 代替案

第一推奨は「versioned typed payload」です。mask v2 の axis 追加は、axis ID と definition を動的にすれば schema 変更不要です。metric 構造が変わる v2 は viewer 更新を要求するのが正しい fail-closed です。

次善案として envelope＋verbatim dict を採るなら、admin に Zod 等のruntime decoderを必須とし、対応 version だけ描画してください。手書き TypeScript interface と `as` だけでは不可です。

「axis metadata のみ型付け、buckets は dict」は、admin が読む主要数値が全て未検証のままなので、最も避けたい中間案です。

## 440KB と endpoint 分割

ページング・axis 別 endpoint は不要です。実 evidence は pretty JSON で438,670 bytesですが、compact JSON は約208,799 bytes、gzip後は約33,532 bytesです。18 axes・96 buckets・960 reliability binsで、localhost admin の1 GETとして十分小さいです。

むしろ分割すると、途中で latest run が更新される整合性問題が生じます。最適化するなら先に以下です。

- `diagnostic_run_id` ベースの ETag/Last-Modified
- gzip
- React Query の長めの stale time／focus refetch抑制

数MB級に増えた時点で、run IDを固定した axis endpointを検討すれば十分です。

## UI加工の許容範囲

- バケット並び替え: 固定・versioned・値非依存なら可。metric順、ユーザーsort、locale依存sortは不可。
- 数値format: 固定桁・単位付与・符号表示は可。値に応じた桁数、色、強調は不可。nullと0を必ず区別。
- 折りたたみ: 全 axis 同一defaultなら可。worstだけ自動展開は禁止。SECONDARY、estimand、CI注記、交絡は折りたたまない。
- 年別・reliability詳細: chronological/probability-bin順の中立表示は可。
- 禁止: rank、top/worst、PASS/FAIL、CIが0を跨ぐかによる色・badge、派生score、再集約、値filter。

## 必要なテスト

- persist helper → JSONB → API の完全経路で canonical JSON deep equality。byte equalityではなく意味的同一性を検証。
- race/horse両 grain、market unavailable、null reliability bin、CI、by_yearを含むfixtureでnested値を直接assert。
- key typo・余剰key・欠落key・未知version・空payloadが200にならない。
- envelope/payloadのkind、date window、metric/mask version/hash一致。
- `diagnostic_run_id`、最新run選択、typed 404/OpenAPI 404。
- segment-edge 404＋segment-accuracy 200、および逆方向の独立表示。
- axes/buckets/year/binsの固定順。効果量を逆順にしたfixtureでも並びが変わらない。
- sort control・rank/worst・値依存class/styleがDOMに存在しない。
- SECONDARY、can_adopt、discovery rule、未調整CI、confounds、originが常時表示。
- null→ダッシュ、0→0、nested値がNaN/undefinedにならない。
- producer側でmarket同一母集団、exclusion Σ reconciliation、ECE CI、surface domainを回帰。
- OpenAPI純追加、front/admin snapshot byte一致、生成型drift、全path GET、betting/training非import。

最終判断は、**一括GET・read-only・migrationなしはそのまま採用、契約型と順序保証を修正してから進める**です。現状の `dict + optional TS cast` のままは NO-GO です。
