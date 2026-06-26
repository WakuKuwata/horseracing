export function Pagination({
  page,
  pageSize,
  total,
  hasNext,
  onPage,
}: {
  page: number;
  pageSize: number;
  total: number;
  hasNext: boolean;
  onPage: (page: number) => void;
}) {
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);
  return (
    <div className="pagination">
      <button type="button" disabled={page <= 1} onClick={() => onPage(page - 1)}>
        ← 前へ
      </button>
      <span>
        {from}–{to} / {total} 件(ページ {page})
      </span>
      <button type="button" disabled={!hasNext} onClick={() => onPage(page + 1)}>
        次へ →
      </button>
    </div>
  );
}
