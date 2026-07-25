結論として、PRIMARY の winner NLL や開催日 bootstrap が壊れている証拠はありません。問題は主に「不確実な悪化」を REJECT に落とす判定の非対称性と、副指標を点推定だけで hard fail にする規則です。

一方、superiority を「非劣性＋方向一致」に置き換えて直接 ADOPT することは、同じデータ量・同じ偽陽性率のままではできません。小効果は `NO_DECISION/CARRY_FORWARD` として扱い、active 昇格とは分離するのが妥当です。

## (a) 現判定ルールの穴

1. 主指標の REJECT/NO_DECISION が非対称です。

現行は、点推定が少しでも悪ければ CI に関係なく REJECT、少し良くて CI が 0 を跨げば NO_DECISION です。[decision.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/decision.py:99)

つまり、

- `Δ=+0.00001, CI=[-0.004,+0.004]` → REJECT
- `Δ=-0.00001, CI=[-0.004,+0.004]` → NO_DECISION

となります。「証拠なし」と「悪化の証拠あり」が混同されています。

2. top2/top3・ECE・直近窓は「confident hard fail」ではありません。

top2/top3 は 0.0005、ECE は 0.001 の点推定境界を少しでも超えると REJECT ですが、それぞれの CI は計算されていません。[paired.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/paired.py:165) [gate-config.json](/Users/kuwatawaku/workspace/horseracing/specs/073-eval-contract-correctness/gate-config.json:5)

直近3年・5年もゼロ幅の点推定 AND で、微小な正値を悪化としています。[paired.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/paired.py:351)

したがって、閾値が数値的に厳しすぎると断定する以前に、「閾値超過の不確実性を扱わず REJECT している」ことが定義上の問題です。calib-split B が top3 で落ちた一例だけでは、0.0005 自体の妥当性は判定できません。

3. subgroup IUT は保守的ですが、意図どおりなら誤りではありません。

各 subgroup の `PASS/FAIL/NO_DECISION` は CI と margin を使っており、ここだけは統計的に対称です。[subgroups.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/subgroups.py:65)

ただし `nk` と `2026_nk` は包含関係があり、さらに `2026_only` も要求するため、全てを「採用を積極的に証明すべき条件」にすると Type II error は大きくなります。全群が本当に安全必須なら IUT は正当です。「通らないから」緩めるべきではありません。

4. 定義以前の fail-open もあります。

- critical subgroup が設定されていても、subgroup 計算自体が省略されると ADOPT 可能です。[decision.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/decision.py:80)
- CLI の subgroup は任意フラグです。[cli.py](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/cli.py:755)
- `no_decision_min_days=10` は全体の開催日数には使われますが、各 critical subgroup の開催日数には適用されていません。[decision.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/decision.py:71)
- confirmatory 呼出しは固定 `from/to` を照合していません。[cli.py](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/cli.py:1125)

これは閾値調整とは別の契約整合性問題です。

## (b) シンプルな代替判定定義

### 提案1：ADOPT集合を増やさない「証拠対称三値化」

`Δ = candidate − active`、CI を `[L,U]` とします。

| 対象 | PASS | FAIL | NO_DECISION |
|---|---|---|---|
| PRIMARY | `U < 0` | `L > 0` | それ以外 |
| top2/top3/ECE/直近、margin=`m` | 点推定 `Δ ≤ m` | `L > m` | 点推定は超過したが `L ≤ m` |
| critical subgroup | `U < m` かつ最低開催日数充足 | `L > m` | CI跨ぎ・不足・欠損 |

全体は次のままです。

- ADOPT：PRIMARY PASS かつ全必須ガード PASS
- REJECT：PRIMARY FAIL、統計的に確かな guard FAIL、または ECE 非常停止
- NO_DECISION：それ以外

これなら現在 ADOPT になる候補を新たに増やさず、境界付近の top3/ECE/直近悪化を REJECT ではなく NO_DECISION にできます。現行コードの「confident guard breach」という説明とも一致します。

なお正式な「非劣性 PASS」は本来 `U<m` です。それを top2/top3/ECE 全てへ要求すると、現在よりさらに採用しにくくなるため、ここでは後方互換的な最小変更を提案しています。

### 提案2：NO_DECISION に `CARRY_FORWARD` disposition を付ける

三値 verdict 自体は変更せず、次を満たす NO_DECISION だけを候補として保持します。

- 全期間の `Δ̂ < 0`
- PRIMARY の `U < 0.005`
- 直近3年・5年とも `Δ̂ ≤ 0`
- PRIMARY・top2/top3・ECE・critical subgroup に統計的 FAIL がない
- recipe/hash を固定し、active へは自動昇格しない

`0.005` は現在すでに winner NLL subgroup margin として固定されている値です。[gate-config.json](/Users/kuwatawaku/workspace/horseracing/specs/073-eval-contract-correctness/gate-config.json:10) ただしこれは「改善を証明した幅」ではなく、候補保留用の screening ceiling に限定すべきです。

名称例は `NO_DECISION / CARRY_FORWARD` で十分で、第四の採用 verdict は不要です。

## (c) 偽陽性リスク

提案1は ADOPT 条件を緩めないため、active 誤採用率は増えません。増えるのは REJECT から NO_DECISION へ移る候補数と再評価コストです。

一方、非劣性＋方向一致を直接 ADOPT に使うのは危険です。SE≈0.0022 なら、正規近似で95% CI上限は概ね `Δ̂+0.0043` です。margin=0.005 の場合、帰無仮説 `Δ=0` でも `U<0.005` はほぼ方向条件 `Δ̂<0` に包含され、約半数が通り得ます。これは superiority gate の代替にはなりません。

CARRY_FORWARD では候補偽陽性が増えます。また、同じ2019–2026窓を見ながら多数候補を蓄積すると winner’s curse が生じます。複数候補を組み合わせても、同じ窓を再利用する限り独立な確認にはなりません。

## (d) 憲法III・057との整合条件

- 073 の gate-config、固定窓、`2026_only`、過去 verdict は変更・再分類しない。[decision.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/decision.py:112)
- 新定義は successor contract として事前登録し、既に見た結果への適用は `exploratory disposition` に限定する。
- `CARRY_FORWARD` は評価上の候補資格だけであり、active/default 昇格ではない。これは「eval合格≠active昇格」という057 FR-009と整合します。[057 spec.md](/Users/kuwatawaku/workspace/horseracing/specs/057-model-switching/spec.md:88)
- 再確認には、2026-07-12以後の別名・別固定 cohort、固定recipe、候補数、確認回数、停止規則、alpha配分を事前登録する。同じ073窓の再実行は証拠を増やしません。
- 未使用データがない間は CARRY_FORWARD のまま据え置く。2019–2026は既に development evidence と明記されています。[development-evidence.md](/Users/kuwatawaku/workspace/horseracing/docs/plan/development-evidence.md:14)

したがって、推奨は「PRIMARYを変える」ではなく、まず提案1で REJECT の意味を「悪化の証拠あり」に限定し、提案2で小効果を active とは分離した候補台帳へ残すことです。
