# 証跡 — feature 100

この feature は **spec を書いている最中に自分の中心仮説を測定で殺した**。何がどの測定で
確定したかを、再現コマンドつきで残す。

| ファイル | 何を確定させたか | 再現 |
|---|---|---|
| `cv-rho-probe.txt` | **US2 の棄却**。汎用共変量では paired 差の分散は削れない(多変量 R²=0.029・CI 幅削減 1.5%)。paired 化そのものが共変量調整の完成形 | `cd training && uv run python ../scripts/cv_rho_probe.py` |
| `screen-power-probe.txt` | **screening ハーネスは盲目ではない**。効果量既知の合成信号を注入した MDE は winner NLL で **0.001〜0.002**。採用ゲートの δ=0.002 はこの帯の上 | `cd training && uv run python ../scripts/screen_power_probe.py` |
| `screen-track-bias.txt` | **当日馬場バイアス軸の閉鎖**。同日除外が構造的に隠していた唯一の合法軸。自馬の枠/先行度の上に増分なし。対照のレース定数が厳密に `+0.000000` で消えることも実証 | `cd training && uv run python ../scripts/screen_track_bias.py` |

いずれも DB の状態に依存する(2026-08-25 時点・予測は `lgbm-058-acc` の永続化分 8,703 レース)。
再実行時に数値が動くのは正常で、**結論の向き**が変わったときだけ問題になる。
