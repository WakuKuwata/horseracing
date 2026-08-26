# Contract: paired 判定の per-race 証拠(US1)

## 目的

判定 1 回につき、その判定を**再現できる生の証拠**を残す。現状は `PairedReport` の内側で
per-race の差を計算しておきながら、driver が verdict を書くときに落としている
(097 の verdict.json に差の生値は 1 件も無い)。判定 1 回は 2〜4 時間かかるので、
「一度回して捨てる」構成は事後解析を構造的に不可能にしている。

## 産出物

`PairedEvidenceArtifact`(形は [data-model.md](../data-model.md) §1-2)。

## 呼び出し側の契約

- `paired_eval` は per-race 行を **必ず**返す。オプションにしない。
  - 理由: オプションにすると「重い判定を回したのに証拠が無い」が再発する。
- 複数窓を束ねる driver(`regime_paired` 系・097 型の per-window driver)は、
  **束ねる前の各窓の証拠をすべて**書き出す。要約だけを書いてはならない(INV-A3)。
- artifact は append-only。再実行は新しいファイルを作る(INV-A2)。

## 再現の契約(この契約の中核)

証拠 artifact **だけ**を入力に、以下を再計算して verdict と**ビット一致**しなければならない
(INV-A1):

1. 点推定(全レースの平均 paired 差)
2. sampling CI(開催日クラスタ bootstrap・`bootstrap.{b, seed, alpha, block}` を使用)
3. total CI(`seed_noise` ブロックがあれば合成、無ければ恒等)

**再現に必要なものはすべて artifact に載っていること**が要件である。載っていない依存が
見つかったら、それは artifact 側に足す(実装時に発見される可能性が高い)。

## 符号規約

`diff = candidate − active`。**明示検査する**(INV-E4)。

理由: アームの向きが逆でも CI の幅だけはもっともらしく見える。再計算の一致だけでは
取り違えを検出できない。

## 共変量

- 事前登録した量のみを載せる。
- **結果(着順)を読まない**。勝ち馬の特定以外の形で結果を持ち込まない(INV-E5)。
- **判定式には一切入らない**。US2 を測定で棄却したため、共変量は**記録のみ**である。
  gate-config に `control_variate` ブロックを追加してはならない(FR-014)。
- モデル特徴に還流させない(INV-A4・憲法 II)。leak-guard テストで機械固定。

## 後方互換

- v4 で凍結済みの gate-config(094〜099)を v5 のコードで実行したとき、
  **verdict の既存キーの値はすべてビット一致**する(FR-002)。
- 証拠 artifact は**純粋な追加**であり、既存 verdict の形を変えない。

## テスト

| 種別 | 内容 |
|---|---|
| 必須 | 証拠だけからの再計算が verdict とビット一致(INV-A1) |
| 必須 | 行数 == `n_races`、不一致で fail-closed(INV-E1) |
| 必須 | 符号規約の検査。向きを反転させた mutation が落ちる(INV-E4) |
| 必須 | 行順シャッフルで再計算結果が不変(INV-E6) |
| 必須 | 094〜099 の golden fixture で verdict がビット一致(FR-002) |
| 必須 | 共変量に結果由来の量を混ぜた mutation が leak-guard で落ちる(INV-E5) |
| 必須 | 複数窓 driver が各窓の証拠を落とす mutation が落ちる(INV-A3) |
