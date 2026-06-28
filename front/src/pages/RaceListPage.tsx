import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";

import { useRaces } from "../api/queries";
import { RaceDayBoard } from "../components/RaceDayBoard";
import { EmptyView, QueryStateView } from "../components/StateView";

// A JRA race day has at most ~36 races (3 venues × 12); 200 comfortably covers a single day
// and a window of recent days for the day selector.
const DAY_PAGE_SIZE = 200;
const RECENT_PAGE_SIZE = 200;

export function RaceListPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const urlDate = searchParams.get("date") ?? "";

  // Recent races → distinct race days (desc) for the day selector. No date filter, so this is the
  // latest window regardless of which day is selected.
  const recent = useRaces({ page_size: RECENT_PAGE_SIZE });
  const recentDays = useMemo(() => {
    const set = new Set<string>();
    for (const r of recent.data?.items ?? []) if (r.race_date) set.add(r.race_date);
    return [...set].sort((a, b) => (a < b ? 1 : -1)); // most recent first
  }, [recent.data]);

  // Default to the latest available day until the user picks one.
  const effectiveDate = urlDate || recentDays[0] || "";

  const day = useRaces(
    { date: effectiveDate, page_size: DAY_PAGE_SIZE },
    { enabled: !!effectiveDate },
  );

  function selectDate(value: string) {
    const sp = new URLSearchParams(searchParams);
    if (value) sp.set("date", value);
    else sp.delete("date");
    setSearchParams(sp);
  }

  // Truly empty DB (no races at all): recent finished, no days, no explicit pick.
  const noData =
    !urlDate && !recent.isLoading && !recent.isError && recentDays.length === 0;

  const isLoading =
    (!urlDate && recent.isLoading) || (!!effectiveDate && day.isLoading);
  const error = day.error ?? (!urlDate ? recent.error : null) ?? null;

  return (
    <section>
      <div className="toolbar">
        <label htmlFor="day-select">開催日</label>
        <select
          id="day-select"
          value={recentDays.includes(effectiveDate) ? effectiveDate : ""}
          onChange={(e) => selectDate(e.target.value)}
        >
          {!recentDays.includes(effectiveDate) && effectiveDate && (
            <option value="">{effectiveDate}</option>
          )}
          {recentDays.map((d) => (
            <option key={d} value={d}>
              {d}
            </option>
          ))}
        </select>
        <input
          type="date"
          aria-label="日付を指定"
          value={effectiveDate}
          onChange={(e) => selectDate(e.target.value)}
        />
      </div>

      {noData ? (
        <EmptyView message="レースデータがありません" />
      ) : (
        <QueryStateView
          isLoading={isLoading}
          error={error}
          data={day.data}
          isEmpty={(d) => d.items.length === 0}
          loadingLabel="レース一覧を読み込み中…"
          emptyMessage="この日のレースがありません"
        >
          {(d) => <RaceDayBoard races={d.items} />}
        </QueryStateView>
      )}
    </section>
  );
}
