import { useEffect, useState } from "react";
import { createTheme, listThemes } from "./api";
import Pager from "./Pager";

const PAGE_SIZE = 10;

export default function ThemesPage({ onOpen }) {
  const [themes, setThemes] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  async function load(nextPage = page) {
    try {
      const data = await listThemes({ skip: nextPage * PAGE_SIZE, limit: PAGE_SIZE });
      setThemes(data.items);
      setTotal(data.total ?? data.items.length);
      setError("");
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => {
    let cancel = false;
    listThemes({ skip: page * PAGE_SIZE, limit: PAGE_SIZE })
      .then((data) => {
        if (!cancel) {
          setThemes(data.items);
          setTotal(data.total ?? data.items.length);
        }
      })
      .catch((err) => {
        if (!cancel) {
          setError(err.message);
        }
      });
    return () => {
      cancel = true;
    };
  }, [page]);

  async function onSubmit(event) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const theme = await createTheme({ name });
      setName("");
      await load();
      onOpen(theme.themeId);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="layout">
      <section className="panel">
        <h2>New theme</h2>
        <form onSubmit={onSubmit}>
          <label>
            Name
            <input value={name} onChange={(event) => setName(event.target.value)} required maxLength={200} />
          </label>
          <button type="submit" disabled={saving}>
            {saving ? "Saving..." : "Create theme"}
          </button>
        </form>
      </section>
      <section className="panel list-panel">
        <div className="list-head">
          <h2>Themes</h2>
          <p>{total} theme{total === 1 ? "" : "s"}</p>
        </div>
        {error && <p className="error">{error}</p>}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Name</th>
                <th>Progress</th>
                <th>Review</th>
                <th>Missing</th>
              </tr>
            </thead>
            <tbody>
              {themes.map((theme) => (
                <tr key={theme.themeId} onClick={() => onOpen(theme.themeId)}>
                  <td className="id">{theme.themeId}</td>
                  <td><strong>{theme.name}</strong></td>
                  <td>{theme.progress?.processedProps || 0} / {theme.progress?.totalProps || 0}</td>
                  <td>{theme.progress?.reviewRequired || 0}</td>
                  <td>{theme.progress?.missingProps || 0}</td>
                </tr>
              ))}
              {themes.length === 0 && (
                <tr>
                  <td colSpan={5} className="empty">No themes yet.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <Pager page={page} pageCount={Math.max(1, Math.ceil(total / PAGE_SIZE))} onPage={setPage} />
      </section>
    </main>
  );
}
