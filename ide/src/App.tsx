import { useEffect } from 'react';
import { useStore } from './store';
import ActivityBar from './components/ActivityBar';
import StatusBar from './components/StatusBar';
// Panel owners create these to the shared contract paths. They are imported
// here even before they exist — App places them with NO props.
import FileExplorer from './components/FileExplorer';
import ProblemsPanel from './components/ProblemsPanel';
import EditorTabs from './components/EditorTabs';
import EditorPane from './components/EditorPane';
import ChatPanel from './components/ChatPanel';
import AgentPanel from './components/AgentPanel';
import './components/app.css';

// Fired on Cmd/Ctrl+K so the editor owner can open its inline-edit widget
// without the shell reaching into Monaco internals.
export const INLINE_EDIT_EVENT = 'ide:inline-edit';

function isSaveChord(e: KeyboardEvent): boolean {
  return (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's';
}
function isInlineEditChord(e: KeyboardEvent): boolean {
  return (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k';
}

export default function App() {
  const activity = useStore((s) => s.activity);
  const chatOpen = useStore((s) => s.chatOpen);
  const loadTree = useStore((s) => s.loadTree);
  const loadModels = useStore((s) => s.loadModels);
  const loadWorkspace = useStore((s) => s.loadWorkspace);
  const saveActive = useStore((s) => s.saveActive);

  // Bootstrap the shell: active workspace + file tree + model list.
  useEffect(() => {
    void loadWorkspace();
    void loadTree();
    void loadModels();
  }, [loadWorkspace, loadTree, loadModels]);

  // Global keyboard: Cmd/Ctrl+S saves, Cmd/Ctrl+K triggers inline edit.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (isSaveChord(e)) {
        e.preventDefault();
        void saveActive();
      } else if (isInlineEditChord(e)) {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent(INLINE_EDIT_EVENT));
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [saveActive]);

  return (
    <div className="app-shell">
      <div className="app-body">
        <ActivityBar />

        <aside className="app-sidebar" aria-label="Sidebar">
          {activity === 'review' ? <ProblemsPanel /> : <FileExplorer />}
        </aside>

        <main className="app-editor" aria-label={activity === 'agent' ? 'Agent' : 'Editor'}>
          {activity === 'agent' ? (
            <AgentPanel />
          ) : (
            <>
              <EditorTabs />
              <EditorPane />
            </>
          )}
        </main>

        {chatOpen && (
          <aside className="app-chat" aria-label="AI Chat">
            <ChatPanel />
          </aside>
        )}
      </div>

      <StatusBar />
    </div>
  );
}
