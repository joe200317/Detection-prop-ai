export default function Pager({ page, pageCount, onPage }) {
  if (pageCount <= 1) {
    return null;
  }
  return (
    <div className="pager">
      <button type="button" className="secondary" disabled={page <= 0} onClick={() => onPage(page - 1)}>
        Previous
      </button>
      <span>
        Page {page + 1} of {pageCount}
      </span>
      <button type="button" className="secondary" disabled={page + 1 >= pageCount} onClick={() => onPage(page + 1)}>
        Next
      </button>
    </div>
  );
}
