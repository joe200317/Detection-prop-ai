import { useEffect, useState } from "react";
import { getReview, listItems, mediaUrl, reviewProp } from "./api";

function percent(value) {
  if (value === null || value === undefined) {
    return "—";
  }
  return `${Math.round(value * 100)}%`;
}

export default function ReviewPage() {
  const [items, setItems] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [error, setError] = useState("");
  const [reviewer, setReviewer] = useState("");
  const [query, setQuery] = useState("");
  const [choices, setChoices] = useState([]);
  const [choice, setChoice] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    const data = await getReview();
    setItems(data.items);
    setSelectedId((current) => (
      data.items.some((item) => item.propId === current) ? current : data.items[0]?.propId || null
    ));
  }

  useEffect(() => {
    let cancel = false;
    getReview()
      .then((data) => {
        if (cancel) {
          return;
        }
        setItems(data.items);
        setSelectedId(data.items[0]?.propId || null);
      })
      .catch((err) => {
        if (!cancel) {
          setError(err.message);
        }
      });
    return () => {
      cancel = true;
    };
  }, []);

  const selected = items.find((item) => item.propId === selectedId) || null;
  const candidate = selected?.candidates?.[0];

  async function act(action, inventoryItemId) {
    if (!selected) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      await reviewProp(selected.themeId, selected.propId, {
        action,
        inventoryItemId,
        reviewedBy: reviewer,
      });
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function searchItems(event) {
    event.preventDefault();
    try {
      const data = await listItems({ q: query, limit: 20 });
      setChoices(data.items);
      setChoice(data.items[0]?.inventoryId || "");
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <main className="layout review-layout">
      <section className="panel">
        <h2>Needs review</h2>
        {error && <p className="error">{error}</p>}
        <div className="review-list">
          {items.map((item) => (
            <button
              type="button"
              key={item.propId}
              className={item.propId === selectedId ? "" : "secondary"}
              onClick={() => setSelectedId(item.propId)}
            >
              {item.themeId} · {item.detectedObject || "Unidentified"} · {item.matchStatus}
            </button>
          ))}
          {items.length === 0 && <p className="muted">No similar or uncertain matches.</p>}
        </div>
      </section>
      <section className="panel">
        {!selected && <p className="muted">Select a prop to review.</p>}
        {selected && (
          <>
            <h2>{selected.themeName || selected.themeId}</h2>
            <div className="compare">
              <figure>
                <img src={mediaUrl(selected.sourceImage)} alt="Source prop" />
                <figcaption>Source prop image</figcaption>
              </figure>
              <figure>
                {candidate?.bestImageUrl ? (
                  <img src={mediaUrl(candidate.bestImageUrl)} alt="Inventory candidate" />
                ) : (
                  <p className="muted">No candidate image</p>
                )}
                <figcaption>
                  {candidate ? `${candidate.inventoryId} ${candidate.name || ""}` : "No candidate"}
                </figcaption>
              </figure>
            </div>
            <dl className="facts">
              <div><dt>Vector similarity</dt><dd>{percent(selected.vectorSimilarity)}</dd></div>
              <div><dt>Nova confidence</dt><dd>{percent(selected.verificationConfidence)}</dd></div>
              <div><dt>Final confidence</dt><dd>{percent(selected.finalConfidence)}</dd></div>
              <div><dt>Availability</dt><dd>{candidate?.status || "—"}</dd></div>
            </dl>
            <p>{selected.aiReason}</p>
            <label>
              Reviewer
              <input value={reviewer} onChange={(event) => setReviewer(event.target.value)} maxLength={200} />
            </label>
            <div className="actions">
              <button type="button" disabled={busy || !selected.inventoryItemId} onClick={() => act("approve")}>Approve match</button>
              <button type="button" className="secondary" disabled={busy} onClick={() => act("reject")}>Reject match</button>
              <button type="button" className="secondary" disabled={busy} onClick={() => act("missing")}>Mark missing</button>
              <button type="button" className="secondary" disabled={busy} onClick={() => act("rerun")}>Re-run AI</button>
            </div>
            <form className="filters" onSubmit={searchItems}>
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search inventory" aria-label="Search inventory" />
              <button type="submit" className="secondary">Find</button>
            </form>
            {choices.length > 0 && (
              <div className="actions">
                <select value={choice} onChange={(event) => setChoice(event.target.value)} aria-label="Different inventory item">
                  {choices.map((item) => (
                    <option key={item.inventoryId} value={item.inventoryId}>
                      {item.inventoryId} {item.name}
                    </option>
                  ))}
                </select>
                <button type="button" disabled={busy || !choice} onClick={() => act("select", choice)}>
                  Select different item
                </button>
              </div>
            )}
          </>
        )}
      </section>
    </main>
  );
}
