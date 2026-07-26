# horseracing-live

## `capture-chaos` operator procedure (Feature 084)

Run the capture manually for each race at approximately T-30 minutes, or target all currently
pending races on a race day:

```bash
uv run --project live live capture-chaos --race-id 202602011206
uv run --project live live capture-chaos --date 2026-07-26
```

`--race-id` and `--date` are mutually exclusive. `--min-seconds-to-post` defaults to `0`; set it
to an operational safety margin such as `600` to skip captures made with less than ten minutes
remaining. A re-capture voids the prior active snapshot and appends its replacement, so it does not
create a within-race odds history.

The coverage target is the preregistered US6 coverage gate across eligible pending races. Review
the command's rejection reasons, capture-strength counts, numeric `seconds_to_post` coverage, and
post-time coverage; if the US6 threshold is missed, the full audited display must not be enabled.
Only `confirmatory` rows enter the prospective confirmation cohort. This requires a fresh
cache-free fetch, pending checks before and after it, and a known future `post_time`. The measured
`post_time` coverage is 0% for 2024, 22.9% for 2025, and 100% for 2026, so `weak` is expected for
older races.

Initial operation is entirely manual. This feature introduces no automatic scheduler; scheduling
remains future scope.
