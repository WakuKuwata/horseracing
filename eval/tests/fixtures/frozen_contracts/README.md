# 凍結済み評価契約の golden fixture(feature 100 / T001)

094〜099 の**実行前に凍結された gate-config** と、それが実際に生んだ verdict の
契約上重要なキーを、テストから読める形で固定したもの。

## なぜ必要か

feature 100 は評価契約に手を入れる。**契約に触る変更で最も怖いのは、過去の凍結成果物を
静かに壊すこと**である。特に:

- `gate_config_hash` の計算に既定値を注入すると、凍結 hash が全部変わる(FR-002a)
- `EVALUATION_CONTRACT_VERSION` を上げると、`assert_confirmatory` の**等値比較**により
  これら全ての config が即座に `ConfirmatoryContractError` になる(analyze C1)

どちらも「テストは通るが本番が静かに壊れる」型なので、**過去の実物**を固定して守る。

## 出所

`index.json` の `source_gate_config` / `source_verdict` が正本のパスを指す。
このディレクトリのファイルは**コピーであって正本ではない**。正本(spec ディレクトリ)を
編集したらここも作り直すこと。

`expected_gate_config_hash` は各 spec の `gate-config.hash.txt`(099 のみ verdict.json の
`gate_config_hash`)から取った。**2026-08-25 時点のコードで 6 件すべて再現することを
確認済み**。

## verdict-keys.json に入れたもの

verdict.json 全体は大きく、かつ本 feature と無関係な差分で壊れやすい。契約の後方互換に
効くキーだけを抜いてある: `artifact_kind` / `eligible_for_verdict` /
`evaluation_contract_version` / `gate_config_hash` / `verdict` と、
`primary` の `point` / `sample_ci` / `total_ci` / `gate`。

094/095/096 は verdict.json を持たない(それぞれ別形式の成果物)ので gate-config のみ。
