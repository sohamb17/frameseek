import { BarChart3, Bookmark, ClipboardCheck, FolderGit2, Library, MessagesSquare, Search } from "lucide-react";
import clsx from "clsx";
import { useRoute, type Route } from "./lib/route";
import { DEMO } from "./lib/demo";
import Health from "./components/Health";
import SearchPage from "./pages/SearchPage";
import LibraryPage from "./pages/LibraryPage";
import ChatPage from "./pages/ChatPage";
import SavedPage from "./pages/SavedPage";
import LabelPage from "./pages/LabelPage";
import EvalPage from "./pages/EvalPage";

const NAV: { r: Route; label: string; icon: typeof Search }[] = [
  { r: "search", label: "Search", icon: Search },
  { r: "chat", label: "Chat", icon: MessagesSquare },
  { r: "library", label: "Library", icon: Library },
  { r: "saved", label: "Saved", icon: Bookmark },
  { r: "label", label: "Label", icon: ClipboardCheck },
  { r: "eval", label: "Evaluation", icon: BarChart3 },
];

export default function App() {
  const [r, go] = useRoute();
  // The static demo only has recorded data for these pages.
  const route: Route = DEMO && !["search", "chat", "library"].includes(r) ? "search" : r;
  return (
    <div className="min-h-full">
      <header className="sticky top-0 z-30 border-b border-ink-800/80 bg-ink-950/80 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-[1400px] items-center gap-4 px-4">
          <button onClick={() => go("search")} className="flex items-center gap-2">
            <img src={`${import.meta.env.BASE_URL}favicon.svg`} className="h-7 w-7" alt="" />
            <span className="text-lg font-bold tracking-tight text-white">Frame<span className="gradient-text">Seek</span></span>
          </button>
          <nav className="flex items-center gap-0.5 overflow-x-auto">
            {NAV.filter((n) => !DEMO || ["search", "chat", "library"].includes(n.r)).map((n) => (
              <button key={n.r} onClick={() => go(n.r)}
                className={clsx("btn px-2.5", route === n.r ? "bg-ink-800 text-white" : "btn-ghost text-slate-400")}>
                <n.icon size={15} /> <span className="hidden sm:inline">{n.label}</span>
              </button>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-4">
            {DEMO ? <span className="rounded-full bg-violet-500/15 px-2.5 py-0.5 text-[11px] text-violet-200 ring-1 ring-violet-500/30">recorded demo</span> : <Health />}
            <a href="https://github.com/sohamb17/frameseek" target="_blank" rel="noreferrer" className="text-slate-500 hover:text-white" title="Source"><FolderGit2 size={17} /></a>
          </div>
        </div>
      </header>
      <main>
        {route === "search" && <SearchPage />}
        {route === "chat" && <ChatPage />}
        {route === "library" && <LibraryPage />}
        {route === "saved" && <SavedPage />}
        {route === "label" && <LabelPage />}
        {route === "eval" && <EvalPage />}
      </main>
    </div>
  );
}
