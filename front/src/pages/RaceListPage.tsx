import { useSearchParams } from "react-router-dom";

import { useRaces } from "../api/queries";
import { DayRefreshButton } from "../components/DayRefreshButton";
import { RaceDayBoard } from "../components/RaceDayBoard";
import { EmptyView, QueryStateView } from "../components/StateView";

// A JRA race day has at most ~36 races (3 venues × 12); 200 comfortably covers a single day.
const DAY_PAGE_SIZE = 200;

export function RaceListPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const urlDate = searchParams.get("date") ?? "";

  // One recent race → the latest available race day, used only as the default until the user picks
  // a date from the calendar. (Day navigation is the date picker; no dropdown.)
  const recent = useRaces({ page_size: 1 });
  const latestDate = recent.data?.items[0]?.race_date ?? "";

  // Default to the latest available day until the user picks one.
  const effectiveDate = urlDate || latestDate || "";

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

  // Truly empty DB (no races at all): recent finished, no latest date, no explicit pick.
  const noData =
    !urlDate && !recent.isLoading && !recent.isError && !latestDate;

  const isLoading =
    (!urlDate && recent.isLoading) || (!!effectiveDate && day.isLoading);
  const error = day.error ?? (!urlDate ? recent.error : null) ?? null;

  return (
    <section>
      <div className="toolbar">
        <label htmlFor="day-input">開催日</label>
        <input
          id="day-input"
          type="date"
          aria-label="開催日を選択"
          value={effectiveDate}
          onChange={(e) => selectDate(e.target.value)}
        />
        {/* US2: refresh ALL races on the selected day from netkeiba (ops write service). */}
        {effectiveDate && <DayRefreshButton date={effectiveDate} />}
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
