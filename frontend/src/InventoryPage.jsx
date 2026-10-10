import { useCallback, useEffect, useState } from "react";
import { createItem, deleteInventoryImage, getHealth, listImages, listItems, mediaUrl, updateItem, uploadInventoryImage } from "./api";
import "./App.css";

const STATUSES = [
  ["available", "Available"],
  ["reserved", "Reserved"],
  ["in_use", "In use"],
  ["damaged", "Damaged"],
  ["missing", "Missing"],
  ["maintenance", "Maintenance"],
  ["unavailable", "Unavailable"],
];

const EMPTY_FORM = {
  name: "",
  category: "Prop",
  description: "",
  status: "available",
};

const PAGE_SIZE = 50;

function statusLabel(value) {
  return STATUSES.find(([status]) => status === value)?.[1] || value;
}

function formatWhen(value) {
  if (!value) {
    return "";
  }
  return new Date(value).toLocaleString();
}

export default function InventoryPage() {
  const [form, setForm] = useState(EMPTY_FORM);
  const [editingId, setEditingId] = useState(null);
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [database, setDatabase] = useState("");
  const [nameMatches, setNameMatches] = useState([]);
  const [confirmedDistinct, setConfirmedDistinct] = useState(false);
  const [images, setImages] = useState([]);
  const [pendingFiles, setPendingFiles] = useState([]);
  const [imageMessage, setImageMessage] = useState("");

  const loadItems = useCallback(async (nextPage = 0, nextQuery = "", nextStatus = "") => {
    setLoading(true);
    setError("");
    try {
      const [health, data] = await Promise.all([
        getHealth(),
        listItems({
          q: nextQuery,
          status: nextStatus,
          skip: nextPage * PAGE_SIZE,
          limit: PAGE_SIZE,
        }),
      ]);
      setDatabase(health.database);
      setItems(data.items);
      setTotal(data.total);
      if (health.database !== "connected") {
        setError("MongoDB is not available. Start MongoDB on localhost:27017.");
      }
    } catch (err) {
      setItems([]);
      setTotal(0);
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const handle = setTimeout(() => {
      setPage(0);
      loadItems(0, query, statusFilter);
    }, 250);
    return () => clearTimeout(handle);
  }, [loadItems, query, statusFilter]);

  useEffect(() => {
    const name = form.name.trim();
    const handle = setTimeout(async () => {
      if (!name) {
        setNameMatches([]);
        return;
      }
      try {
        const data = await listItems({ name, limit: 5 });
        setNameMatches(data.items.filter((item) => item.inventoryId !== editingId));
        setConfirmedDistinct(false);
      } catch {
        setNameMatches([]);
      }
    }, 300);
    return () => clearTimeout(handle);
  }, [form.name, editingId]);

  function updateField(event) {
    const { name, value } = event.target;
    setForm((current) => ({ ...current, [name]: value }));
  }

  async function loadImages(inventoryId) {
    try {
      const data = await listImages(inventoryId);
      setImages(data.items);
    } catch (err) {
      setImages([]);
      setImageMessage(err.message);
    }
  }

  function clearPending() {
    setPendingFiles((current) => {
      current.forEach((item) => URL.revokeObjectURL(item.url));
      return [];
    });
  }

  function beginEdit(item) {
    clearPending();
    setEditingId(item.inventoryId);
    setImageMessage("");
    loadImages(item.inventoryId);
    setForm({
      name: item.name,
      category: item.category,
      description: item.description || "",
      status: item.status,
    });
    setError("");
  }

  function cancelEdit() {
    clearPending();
    setEditingId(null);
    setForm(EMPTY_FORM);
    setConfirmedDistinct(false);
    setImages([]);
    setImageMessage("");
  }

  function onPickNewImages(event) {
    const files = [...(event.target.files || [])];
    event.target.value = "";
    setPendingFiles((current) => {
      const room = 5 - current.length;
      const added = files.slice(0, room).map((file) => ({
        id: `${file.name}-${file.size}-${crypto.randomUUID()}`,
        file,
        url: URL.createObjectURL(file),
      }));
      return [...current, ...added];
    });
  }

  function removePending(id) {
    setPendingFiles((current) => {
      const target = current.find((item) => item.id === id);
      if (target) {
        URL.revokeObjectURL(target.url);
      }
      return current.filter((item) => item.id !== id);
    });
  }

  async function onUploadImage(event) {
    const files = [...(event.target.files || [])];
    event.target.value = "";
    if (!files.length || !editingId) {
      return;
    }
    setImageMessage("");
    const room = Math.max(0, 5 - images.length);
    const batch = files.slice(0, room);
    if (!batch.length) {
      setImageMessage("An item can have at most 5 reference images.");
      return;
    }
    const notes = [];
    try {
      for (const file of batch) {
        const saved = await uploadInventoryImage(editingId, file);
        if (saved.warning) {
          notes.push(saved.warning);
        }
      }
      setImageMessage(notes[0] || `${batch.length} reference image${batch.length === 1 ? "" : "s"} saved.`);
      await loadImages(editingId);
    } catch (err) {
      setImageMessage(err.message);
      await loadImages(editingId);
    }
  }

  async function onDeleteImage(imageId) {
    try {
      await deleteInventoryImage(editingId, imageId);
      await loadImages(editingId);
    } catch (err) {
      setImageMessage(err.message);
    }
  }

  async function onSubmit(event) {
    event.preventDefault();
    setSaving(true);
    setError("");
    const payload = {
      name: form.name.trim(),
      category: form.category.trim(),
      description: form.description.trim(),
      status: form.status,
    };
    try {
      const notes = [];
      if (editingId) {
        await updateItem(editingId, payload);
      } else {
        const created = await createItem(payload);
        for (const pending of pendingFiles) {
          try {
            const saved = await uploadInventoryImage(created.inventoryId, pending.file);
            if (saved.warning) {
              notes.push(saved.warning);
            }
          } catch (err) {
            notes.push(err.message);
          }
        }
      }
      cancelEdit();
      await loadItems(page, query, statusFilter);
      if (notes.length) {
        setError(notes.join(" "));
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function changePage(nextPage) {
    setPage(nextPage);
    await loadItems(nextPage, query, statusFilter);
  }

  const needsDistinctConfirm = nameMatches.length > 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <main className="layout">
        <section className="panel">
          <h2>{editingId ? `Edit ${editingId}` : "Add item"}</h2>
          <form onSubmit={onSubmit}>
            <label>
              Name
              <input name="name" value={form.name} onChange={updateField} required maxLength={200} />
            </label>
            <label>
              Category
              <input name="category" value={form.category} onChange={updateField} required maxLength={100} list="categories" />
              <datalist id="categories">
                <option value="Prop" />
                <option value="Clothing" />
                <option value="Furniture" />
                <option value="Accessory" />
                <option value="Background" />
                <option value="Setup" />
              </datalist>
            </label>
            <label>
              Description
              <textarea name="description" value={form.description} onChange={updateField} maxLength={2000} rows={4} />
            </label>
            <label>
              Physical status
              <select name="status" value={form.status} onChange={updateField}>
                {STATUSES.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>

            {needsDistinctConfirm && (
              <div className="warning">
                <p>
                  {nameMatches.map((item) => item.inventoryId).join(", ")} already uses this name.
                  Add another record only if this is a different physical item.
                </p>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={confirmedDistinct}
                    onChange={(event) => setConfirmedDistinct(event.target.checked)}
                  />
                  This is a different physical item
                </label>
              </div>
            )}

            {!editingId && (
              <div className="references">
                <p className="section-label">Prop images ({pendingFiles.length}/5)</p>
                <p className="muted">Add the prop photos now. You can choose more than one.</p>
                <div className="thumbs">
                  {pendingFiles.map((image) => (
                    <figure key={image.id}>
                      <img src={image.url} alt={image.file.name} />
                      <figcaption>{image.file.name}</figcaption>
                      <button type="button" className="secondary" onClick={() => removePending(image.id)}>
                        Remove
                      </button>
                    </figure>
                  ))}
                </div>
                <label>
                  Add images
                  <input
                    type="file"
                    accept="image/jpeg,image/png,image/webp"
                    multiple
                    onChange={onPickNewImages}
                    disabled={pendingFiles.length >= 5}
                  />
                </label>
              </div>
            )}

            {editingId && (
              <div className="references">
                <p className="section-label">Reference images ({images.length}/5)</p>
                <div className="thumbs">
                  {images.map((image) => (
                    <figure key={image.imageId}>
                      <img src={mediaUrl(image.imageUrl)} alt={image.metadata?.filename || image.imageId} />
                      <figcaption>{image.embeddingReady ? "Embedded" : "No embedding"}</figcaption>
                      <button type="button" className="secondary" onClick={() => onDeleteImage(image.imageId)}>
                        Remove
                      </button>
                    </figure>
                  ))}
                </div>
                <label>
                  Add images
                  <input type="file" accept="image/jpeg,image/png,image/webp" multiple onChange={onUploadImage} disabled={images.length >= 5} />
                </label>
                {imageMessage && <p className="muted">{imageMessage}</p>}
              </div>
            )}

            <div className="actions">
              <button type="submit" disabled={saving || (needsDistinctConfirm && !confirmedDistinct)}>
                {saving ? "Saving..." : editingId ? "Save changes" : "Add item"}
              </button>
              {editingId && (
                <button type="button" className="secondary" onClick={cancelEdit}>
                  Cancel
                </button>
              )}
            </div>
          </form>
        </section>

        <section className="panel list-panel">
          <div className="list-head">
            <h2>Items</h2>
            <p>{loading ? "Loading..." : `${total} item${total === 1 ? "" : "s"}`}</p>
          </div>
          <div className="filters">
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search name"
              aria-label="Search name"
            />
            <select
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value)}
              aria-label="Filter by status"
            >
              <option value="">All statuses</option>
              {STATUSES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </div>

          {error && <p className="error">{error}</p>}
          {!error && database === "disconnected" && (
            <p className="error">MongoDB is not available. Start MongoDB on localhost:27017.</p>
          )}

          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Name</th>
                  <th>Category</th>
                  <th>Status</th>
                  <th>Updated</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr
                    key={item.inventoryId}
                    className={item.inventoryId === editingId ? "selected" : ""}
                    onClick={() => beginEdit(item)}
                  >
                    <td className="id">{item.inventoryId}</td>
                    <td>
                      <strong>{item.name}</strong>
                    </td>
                    <td>{item.category}</td>
                    <td>
                      <span className={`status status-${item.status}`}>{statusLabel(item.status)}</span>
                    </td>
                    <td className="when">{formatWhen(item.updatedAt)}</td>
                  </tr>
                ))}
                {!loading && items.length === 0 && (
                  <tr>
                    <td colSpan={5} className="empty">
                      No inventory items yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {total > PAGE_SIZE && (
            <div className="pager">
              <button type="button" className="secondary" disabled={page === 0} onClick={() => changePage(page - 1)}>
                Previous
              </button>
              <span>
                Page {page + 1} of {pageCount}
              </span>
              <button
                type="button"
                className="secondary"
                disabled={page + 1 >= pageCount}
                onClick={() => changePage(page + 1)}
              >
                Next
              </button>
            </div>
          )}
        </section>
    </main>
  );
}
