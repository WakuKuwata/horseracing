import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import { useRaces } from "../api/queries";
import { Pagination } from "../components/Pagination";
import { RaceTable } from "../components/RaceTable";
import { QueryStateView } from "../components/StateView";

const PAGE_SIZE = 20;

export function RaceListPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [date, setDate] = useState(searchParams.get("date") ?? "");
  const page = Number(searchParams.get("page") ?? "1");

  const query = useRaces({
    page,
    page_size: PAGE_SIZE,
    date: date || undefined,
  });

  function setPage(next: number) {
    const sp = new URLSearchParams(searchParams);
    sp.set("page", String(next));
    setSearchParams(sp);
  }

  function applyDate(value: string) {
    setDate(value);
    const sp = new URLSearchParams(searchParams);
    if (value) sp.set("date", value);
    else sp.delete("date");
    sp.set("page", "1");
    setSearchParams(sp);
  }

  return (
    <section>
      <div className="toolbar">
        <label htmlFor="date-filter">開催日</label>
        <input
          id="date-filter"
          type="date"
          value={date}
          onChange={(e) => applyDate(e.target.value)}
        />
      </div>

      <QueryStateView
        isLoading={query.isLoading}
        error={query.error ?? null}
        data={query.data}
        isEmpty={(d) => d.items.length === 0}
        loadingLabel="レース一覧を読み込み中…"
        emptyMessage="該当するレースがありません"
      >
        {(d) => (
          <>
            <RaceTable races={d.items} />
            <Pagination
              page={d.page}
              pageSize={d.page_size}
              total={d.total}
              hasNext={d.has_next}
              onPage={setPage}
            />
          </>
        )}
      </QueryStateView>
    </section>
  );
}
