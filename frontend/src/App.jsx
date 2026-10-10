import { useState } from "react";
import InventoryPage from "./InventoryPage";
import ReviewPage from "./ReviewPage";
import ThemeDetail from "./ThemeDetail";
import ThemesPage from "./ThemesPage";
import "./App.css";

const VIEWS = [
  ["inventory", "Inventory"],
  ["themes", "Themes"],
  ["review", "Review"],
];

export default function App() {
  const [view, setView] = useState("inventory");
  const [themeId, setThemeId] = useState(null);

  return (
    <div className="app">
      <header className="topbar">
        <div>
          <p className="eyebrow">GPT vision</p>
          <h1>Prop inventory</h1>
        </div>
        <nav className="nav">
          {VIEWS.map(([id, label]) => (
            <button
              key={id}
              type="button"
              className={view === id ? "" : "secondary"}
              onClick={() => {
                setView(id);
                setThemeId(null);
              }}
            >
              {label}
            </button>
          ))}
        </nav>
      </header>
      {view === "inventory" && <InventoryPage />}
      {view === "themes" && !themeId && <ThemesPage onOpen={(id) => setThemeId(id)} />}
      {view === "themes" && themeId && <ThemeDetail themeId={themeId} onBack={() => setThemeId(null)} />}
      {view === "review" && <ReviewPage />}
    </div>
  );
}
