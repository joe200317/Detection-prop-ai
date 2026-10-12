import { useEffect, useState } from "react";
import { getJob, getLogs, getResult, getTheme, mediaUrl, retryTheme, scanTheme, uploadMainImage } from "./api";
import Pager from "./Pager";

const LOG_PAGE = 10;
const PROP_PAGE = 8;

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
  const [checking, setChecking] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [selectedIds, setSelectedIds] = useState([]);
  const [logPage, setLogPage] = useState(0);
  const [logTotal, setLogTotal] = useState(0);
  const [propPage, setPropPage] = useState(0);

  useEffect(() => {
    let stop = false;
    let timer;
    async function load() {
      try {
        const [themeData, result, logData] = await Promise.all([
          getTheme(themeId),
          getResult(themeId),
          getLogs(themeId, { skip: logPage * LOG_PAGE, limit: LOG_PAGE }),
        ]);
        if (stop) {
          return;
        }
        setTheme(themeData);
        setRows(result.rows);
        setSelectedIds((current) => current.filter((id) => result.rows.some((row) => row.propId === id)));
        setLogs(logData.items);
        setLogTotal(logData.total ?? logData.items.length);
        setError("");
        const status = themeData.activeJob?.status;
        const jobId = themeData.activeJob?.jobId;
        if ((status === "queued" || status === "running") && jobId) {
          timer = setTimeout(() => watch(jobId), 1000);
        }
      } catch (err) {
        if (!stop) {
          setError(err.message);
        }
      }
    }
    async function watch(jobId) {
      try {
        const job = await getJob(jobId);
        if (stop) {
          return;
        }
        if (job.status === "queued" || job.status === "running") {
          setTheme((current) => (current ? { ...current, activeJob: job } : current));
          timer = setTimeout(() => watch(jobId), 1000);
          return;
        }
        if (job.status === "failed" && job.error) {
          setError(job.error);
        }
      } catch (err) {
        if (!stop) {
          setError(err.message);
        }
        return;
      }
      if (!stop) {
        setReloadKey((value) => value + 1);
      }
    }
    load();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [themeId, reloadKey, logPage]);

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

  async function waitForJob(jobId) {
    let job = await getJob(jobId);
    while (job.status === "queued" || job.status === "running") {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      job = await getJob(jobId);
    }
    return job;
  }

  async function checkInventory() {
    setChecking(true);
    setError("");
    try {
      const started = await scanTheme(themeId);
      const job = await waitForJob(started.jobId);
      if (job.status === "failed" && job.error) {
        setError(job.error);
      }
      setReloadKey((value) => value + 1);
    } catch (err) {
      setError(err.message);
    } finally {
      setChecking(false);
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

  function toggleProp(propId) {
    setSelectedIds((current) => (
      current.includes(propId) ? current.filter((id) => id !== propId) : [...current, propId]
    ));
  }

  const progress = theme?.progress || {};
  const job = theme?.activeJob;
  const aiChecking = checking || job?.status === "queued" || job?.status === "running";
  const inInventory = rows.filter((row) => row.availability === "available").length;
  const notInInventory = rows.filter((row) => row.availability === "unavailable").length;
  const selectedRows = rows.filter((row) => selectedIds.includes(row.propId));
  const propPageCount = Math.max(1, Math.ceil(rows.length / PROP_PAGE));
  const safePropPage = Math.min(propPage, propPageCount - 1);
  const visibleProps = rows.slice(safePropPage * PROP_PAGE, (safePropPage + 1) * PROP_PAGE);
  const logPageCount = Math.max(1, Math.ceil(logTotal / LOG_PAGE));

  return (
    <main className="stack">
      {aiChecking && (
        <div className="loader-overlay" role="status" aria-live="polite">
          <div className="spinner" />
          <p>AI is checking</p>
        </div>
      )}
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
          <p className="muted">Upload one theme photo. Click the props on it. More than one can stay selected.</p>
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
            <button type="button" disabled={busy || aiChecking || !theme?.mainImage} onClick={checkInventory}>
              {aiChecking ? "AI is checking" : "Check inventory"}
            </button>
            <button type="button" className="secondary" disabled={busy} onClick={() => run(() => retryTheme(themeId, { scope: "failed" }))}>
              Retry failed
            </button>
          </div>
        </div>
        <div className="counts">
          <span>In inventory {inInventory}</span>
          <span>Not in inventory {notInInventory}</span>
          <span>Selected {selectedRows.length}</span>
        </div>
      </section>

      <section className="scene">
        <div className="panel stage-panel">
          {theme?.mainImage ? (
            <div className="stage">
              <img src={mediaUrl(theme.mainImage)} alt="Theme" />
              {rows.map((row) => row.box && (
                <button
                  key={row.propId}
                  type="button"
                  className={`hotspot ${selectedIds.includes(row.propId) ? "on" : ""} ${row.availability || "unavailable"}`}
                  style={{
                    left: `${row.box.x * 100}%`,
                    top: `${row.box.y * 100}%`,
                    width: `${row.box.width * 100}%`,
                    height: `${row.box.height * 100}%`,
                  }}
                  onClick={() => toggleProp(row.propId)}
                >
                  <span>{row.detectedObject || "Prop"}</span>
                </button>
              ))}
            </div>
          ) : (
            <p className="empty">Upload a theme photo to see its props.</p>
          )}
        </div>
        <div className="panel prop-picker">
          <div className="list-head">
            <h2>Props</h2>
            <div className="actions">
              <button type="button" className="secondary" disabled={!rows.length} onClick={() => setSelectedIds(rows.map((row) => row.propId))}>
                Select all
              </button>
              <button type="button" className="secondary" disabled={!selectedIds.length} onClick={() => setSelectedIds([])}>
                Clear
              </button>
            </div>
          </div>
          <div className="prop-list">
            {visibleProps.map((row) => {
              const on = selectedIds.includes(row.propId);
              return (
                <button
                  key={row.propId}
                  type="button"
                  className={`prop-card ${on ? "on" : ""}`}
                  onClick={() => toggleProp(row.propId)}
                  aria-pressed={on}
                >
                  {row.sourceImage && <img src={mediaUrl(row.sourceImage)} alt="" />}
                  <span>
                    <strong>{row.detectedObject || "Unidentified"}</strong>
                    <span className={`status ${row.availability === "available" ? "status-EXACT" : row.availability === "checking" ? "status-PENDING" : "status-MISSING"}`}>
                      {availabilityLabel(row.availability)}
                    </span>
                  </span>
                </button>
              );
            })}
            {rows.length === 0 && <p className="muted">Detected props show up here after the photo is checked.</p>}
          </div>
          <Pager page={safePropPage} pageCount={propPageCount} onPage={setPropPage} />
        </div>
      </section>

      {selectedRows.length > 0 && (
        <section className="panel">
          <h2>Selected props</h2>
          <div className="selected-grid">
            {selectedRows.map((row) => (
              <article key={row.propId} className="selected-card">
                {row.sourceImage && <img src={mediaUrl(row.sourceImage)} alt="" />}
                <div>
                  <strong>{row.detectedObject || "Unidentified"}</strong>
                  <p className="muted">{row.inventoryItemId ? `${row.inventoryItemId} ${row.inventoryName || ""}` : "Not in inventory"}</p>
                  <p>{percent(row.finalConfidence)} confidence</p>
                  {row.aiReason && <p className="muted">{row.aiReason}</p>}
                </div>
              </article>
            ))}
          </div>
        </section>
      )}

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
        <Pager page={logPage} pageCount={logPageCount} onPage={setLogPage} />
      </section>
    </main>
  );
}
