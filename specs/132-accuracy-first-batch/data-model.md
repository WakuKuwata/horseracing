# Data model

- InputFreeze: cutoff=2026-09-06,actual_end=2026-08-23,snapshot/Frames/cache/config/source SHA,元recipe/member/年。
- MixedRace: 日付,race ID,ordered started IDs,EvalRace,corrected member heads,mixed heads,regime。
- StyleOverlay: semantics ID,許可8列,original/candidate matrix identity,変更セル/token監査。未変更列/label/順序一致。
- Comparison: arm/year/regime/baseline,NLL/top2/top3/Brier/ECE/count,paired CIと固定モデル条件付き範囲。
- Output: completion receiptと参照hash、追記。未完了を成功扱いにしない。can_adopt=false,eligible_for_verdict=false。
