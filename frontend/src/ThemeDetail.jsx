import { useEffect, useState } from "react";
import { analyzeTheme, getLogs, getResult, getTheme, mediaUrl, retryTheme, uploadMainImage, uploadProp } from "./api";

function percent(value) {
  if (value === null || value === undefined) {
    return "—";
  }
  return `${Math.round(value * 100)}%`;
}

export default function ThemeDetail({ themeId, onBack }) {
  const [theme, setTheme] = useState(null);
  const [rows, setRows] = useState([]);
  const [logs, setLogs] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let stop = false;
    let timer;
    async function load() {
      try {
        const [themeData, result, logData] = await Promise.all([
          getTheme(themeId),
          getResult(themeId),
          getLogs(themeId),
        ]);
        if (stop) {
          return;
        }
        setTheme(themeData);
        setRows(result.rows);
        setLogs(logData.items);
        setError("");
        const status = themeData.activeJob?.status;
        if (status === "queued" || status === "running") {
          timer = setTimeout(load, 1500);
        }
      } catch (err) {
        if (!stop) {
          setError(err.message);
        }
      }
    }
    load();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [themeId, reloadKey]);

  async function uploadOne(event, uploader) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      await uploader(themeId, file);
      setReloadKey((value) => value + 1);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function uploadMany(event) {
    const files = [...(event.target.files || [])];
    event.target.value = "";
    setBusy(true);
    setError("");
    try {
      for (const file of files) {
        await uploadProp(themeId, file);
      }
      setReloadKey((value) => value + 1);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function run(action) {
    setBusy(true);
    setError("");
    try {
      await action();
      setReloadKey((value) => value + 1);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const progress = theme?.progress || {};
  const job = theme?.activeJob;

  return (
    <main className="stack">
      <div className="list-head">
        <div>
          <button type="button" className="secondary" onClick={onBack}>Back</button>
          <h2>{theme?.name || themeId}</h2>
          <p className="id">{themeId}</p>
        </div>
        <p>
          {progress.processedProps || 0} / {progress.totalProps || 0} props processed
          {job ? ` · ${job.status}` : ""}
        </p>
      </div>
      {error && <p className="error">{error}</p>}

      <section className="panel theme-tools">
        <div>
          <p className="section-label">Main theme image</p>
          {theme?.mainImage && <img className="preview" src={mediaUrl(theme.mainImage)} alt="Main theme" />}
          <label>
            Upload main image
            <input type="file" accept="image/jpeg,image/png,image/webp" disabled={busy} onChange={(event) => uploadOne(event, uploadMainImage)} />
          </label>
        </div>
        <div>
          <p className="section-label">Individual prop images</p>
          <label>
            Upload prop images
            <input type="file" accept="image/jpeg,image/png,image/webp" multiple disabled={busy} onChange={uploadMany} />
          </label>
          <div className="actions">
            <button type="button" disabled={busy || !theme?.props?.length} onClick={() => run(() => analyzeTheme(themeId))}>
              Analyze theme
            </button>
            <button type="button" className="secondary" disabled={busy} onClick={() => run(() => retryTheme(themeId, { scope: "failed" }))}>
              Retry failed
            </button>
            <button type="button" className="secondary" disabled={busy} onClick={() => run(() => retryTheme(themeId, { scope: "all" }))}>
              Re-run theme
            </button>
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="counts">
          <span>Exact {progress.successfulProps || 0}</span>
          <span>Review {progress.reviewRequired || 0}</span>
          <span>Missing {progress.missingProps || 0}</span>
          <span>Failed {progress.failedProps || 0}</span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Prop</th>
                <th>Inventory</th>
                <th>Availability</th>
                <th>Confidence</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.propId}>
                  <td>
                    <strong>{row.detectedObject || "Unidentified"}</strong>
                    {row.aiReason && <span className="muted">{row.aiReason}</span>}
                  </td>
                  <td className="id">{row.inventoryItemId ? `${row.inventoryItemId} ${row.inventoryName || ""}` : "—"}</td>
                  <td>{row.inventoryStatus || "—"}</td>
                  <td>{percent(row.finalConfidence)}</td>
                  <td><span className={`status status-${row.matchStatus}`}>{row.matchStatus}</span></td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={5} className="empty">Upload individual prop images, then analyze.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <h2>AI log</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Decision</th>
                <th>Object</th>
                <th>Time</th>
                <th>Error</th>
              </tr>
            </thead>
            <tbody>
              {logs.map((log) => (
                <tr key={log.requestId}>
                  <td>{log.finalDecision}</td>
                  <td>{log.detectedObject || "—"}</td>
                  <td>{log.processingTime} ms</td>
                  <td>{log.error || "—"}</td>
                </tr>
              ))}
              {logs.length === 0 && (
                <tr>
                  <td colSpan={4} className="empty">No AI decisions yet.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}
