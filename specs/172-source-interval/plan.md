# 172 取得を停止したままsource intervalを統一

2026-10-27。API/ops/手動CLIを60秒以上へ統一する。レースデータ新規取得は禁止、robots再更新も今回行わない。

共通public設定はsource=ops fetch=60、shared limiter有効、collection_paused=true。未知/不正/非有限/<60/不一致/既存のより長い間隔の短縮を拒否する。手動private profileは既存の60秒を変更せず、profile_environment下で実RequestPolitenessの間隔を確認する。

stack.shを専用wrapper経由へ変更し、API/ops-apiは共通設定をimport前に適用、実source/ops config/policy値を記録して起動する。opsはouter ASGIで非GET/HEAD/OPTIONSをアプリ・DB dependency前に503拒否する。外向きPython socket connectも停止する。workerはmain import前にPAUSED終了し、recovery/claim/lane/予測子processを起動せず、PIDも残さない。worker concurrency=0やauto capture flagのみを停止手段にしない。

停止直前にqueue/running=0・throttle状態をread-only確認する。旧workerと子孫の停止を確認し、旧ops/APIを停止して新API/ops-apiだけ起動する。既存DB接続/基本artifact設定は維持する。起動後の新PID/runtime記録/環境、実local GETとops POST拒否、DB queue/throttle不変を確認する。native DBのみread-only検査を行い、source slotは予約しない。

169 preflightを共通設定と専用private profileで再実行し、独立担当に公開証拠を監査してもらう。取得30分以内・判断前の両予測durable保存・三段階guard・固定判断窓・公式払戻精算は維持する。165〜171の凍結source/artifactを変更しない。

手動観測の正式入口は別module `scripts/manual_observer_172.py` とする。共通設定/専用profile/実policyを検査し、observeは旧165 CLIのimport・DB・HTTP・予測前にPAUSED_NOT_STARTED/exit3で停止する。preflightだけは169のread-only検査へ接続する。API/opsが稼働中のwrapper sourceをこの追加で変更しない。旧165の直接--enable-collection実行は今回の正式運用経路にしない。
