import { useEffect, useState } from "react";
import { getLogs, getResult, getTheme, mediaUrl, retryTheme, scanTheme, uploadMainImage } from "./api";

function availabilityLabel(value) {
  if (value === "available") {
    return "Available";
  }
  if (value === "checking") {
    return "Checking";
  }
  return "Not available";
}

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
  const inInventory = rows.filter((row) => row.availability === "available").length;
  const notInInventory = rows.filter((row) => row.availability === "unavailable").length;

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
      {!error && job?.status === "failed" && job.error && <p className="error">{job.error}</p>}

      <section className="panel theme-tools">
        <div>
          <p className="section-label">Theme photo</p>
          <p className="muted">Upload the theme photo only. Every prop in that photo is checked against inventory.</p>
          {theme?.mainImage && <img className="preview" src={mediaUrl(theme.mainImage)} alt="Theme" />}
          <label>
            Upload theme photo
            <input
              type="file"
              accept="image/jpeg,image/png,image/webp"
              disabled={busy}
              onChange={(event) => uploadOne(event, async (id, file) => {
                await uploadMainImage(id, file);
                await scanTheme(id);
              })}
            />
          </label>
          <div className="actions">
            <button type="button" disabled={busy || !theme?.mainImage} onClick={() => run(() => scanTheme(themeId))}>
              Check inventory
            </button>
            <button type="button" className="secondary" disabled={busy} onClick={() => run(() => retryTheme(themeId, { scope: "failed" }))}>
              Retry failed
            </button>
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="counts">
          <span>In inventory {inInventory}</span>
          <span>Not in inventory {notInInventory}</span>
          <span>Theme props {progress.totalProps || rows.length}</span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Prop</th>
                <th>Inventory</th>
                <th>Availability</th>
                <th>Confidence</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.propId}>
                  <td>
                    <div className="prop-name">
                      {row.sourceImage && <img src={mediaUrl(row.sourceImage)} alt="" />}
                      <strong>{row.detectedObject || "Unidentified"}</strong>
                    </div>
                    {row.aiReason && <span className="muted">{row.aiReason}</span>}
                  </td>
                  <td className="id">{row.inventoryItemId ? `${row.inventoryItemId} ${row.inventoryName || ""}` : "—"}</td>
                  <td>
                    <span className={`status ${row.availability === "available" ? "status-EXACT" : row.availability === "checking" ? "status-PENDING" : "status-MISSING"}`}>
                      {availabilityLabel(row.availability)}
                    </span>
                  </td>
                  <td>{percent(row.finalConfidence)}</td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={4} className="empty">Upload the theme photo. Props in that photo are checked against inventory.</td>
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
