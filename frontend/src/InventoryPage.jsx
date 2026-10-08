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
  subcategory: "",
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

  function beginEdit(item) {
    setEditingId(item.inventoryId);
    setImageMessage("");
    loadImages(item.inventoryId);
    setForm({
      name: item.name,
      category: item.category,
      subcategory: item.subcategory || "",
      description: item.description || "",
      status: item.status,
    });
    setError("");
  }

  function cancelEdit() {
    setEditingId(null);
    setForm(EMPTY_FORM);
    setConfirmedDistinct(false);
    setImages([]);
    setImageMessage("");
  }

  async function onUploadImage(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !editingId) {
      return;
    }
    setImageMessage("");
    try {
      const saved = await uploadInventoryImage(editingId, file);
      setImageMessage(saved.warning || "Reference image saved.");
      await loadImages(editingId);
    } catch (err) {
      setImageMessage(err.message);
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
      subcategory: form.subcategory.trim(),
      description: form.description.trim(),
      status: form.status,
    };
    try {
      if (editingId) {
        await updateItem(editingId, payload);
      } else {
        await createItem(payload);
      }
      cancelEdit();
      await loadItems(page, query, statusFilter);
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
              Subcategory
              <input name="subcategory" value={form.subcategory} onChange={updateField} maxLength={100} />
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
                  Add image
                  <input type="file" accept="image/jpeg,image/png,image/webp" onChange={onUploadImage} disabled={images.length >= 5} />
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
                      {item.subcategory && <span className="muted">{item.subcategory}</span>}
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
