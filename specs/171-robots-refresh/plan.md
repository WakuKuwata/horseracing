# 171 robots限定更新と運用interval・競合確認

2026-10-27。ユーザーはレースデータの新規収集を禁じたまま、robotsだけの更新、source interval運用設定/競合確認、preflight再実行を許可した。

169で対象とするhttps://race.netkeiba.com/robots.txtだけにGETを1回送る。170の専用private profile/DBを用い、対象identityを再照合してから、既存RequestPolitenessのcanonical netkeiba.com bucketで1枠確保する。今回のHTTPに必要なthrottle予約/拒否cooldown更新は実施範囲。レースデータ・予測・購入・roleは変更しない。

robots cacheと同一のflockを確実に獲得し、取得はredirect/retry/proxy環境利用なし、固定URLだけを許可する。200/404/410だけを保存し原本とcacheをreadbackする。400/403/429は既存cooldownを保存して停止し、その他status/transport/oversize/保存失敗も停止、無断再試行なし。fresh Disallowは更新成功でも観測NO_GO。

API/ops等の初期process環境からallowlist設定だけを抽出し、保存設定とコードdefault、同一DBのqueue/session/lockとthrottleをread-only照合する。raw環境、秘密設定本体/DSN/hash、SQL query本文は保存しない。設定値の観測と運用者の承認を分ける。手動60秒に対し他processの既定1秒が残れば全体統一とは扱わず、今回勝手に再起動/停止/設定変更しない。現在の無競合は将来の無競合の証明にしない。

更新後は169 check/verifyを専用roleで再実行する。独立担当へ境界と証跡の監査を依頼する。取得30分以内・判断前の両予測durable保存・三段階確認・固定判断窓・公式払戻精算は維持する。live plan/observeやモデル推論は実行しない。
