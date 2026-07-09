import { useStore } from '../store';
import './model-picker.css';

// Editable model picker for the status bar: pick an installed Ollama model OR
// type any model name. If the typed model isn't installed yet, a Pull button
// fetches it (streaming progress) so the user can bring their own model.
export default function ModelPicker() {
  const models = useStore((s) => s.models);
  const model = useStore((s) => s.model);
  const setModel = useStore((s) => s.setModel);
  const pulling = useStore((s) => s.pulling);
  const pullProgress = useStore((s) => s.pullProgress);
  const pullModel = useStore((s) => s.pullModel);

  const trimmed = model.trim();
  const installed = models.includes(trimmed);
  const canPull = trimmed.length > 0 && !installed && !pulling;

  return (
    <div
      className="model-picker"
      title="Active Ollama model — pick an installed one or type any model name"
    >
      <svg viewBox="0 0 24 24" width="13" height="13" fill="none" aria-hidden="true">
        <path
          d="M12 3v3m0 12v3M4.2 7.5 6.8 9M17.2 15l2.6 1.5M4.2 16.5 6.8 15M17.2 9l2.6-1.5"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
        />
        <circle cx="12" cy="12" r="3.2" stroke="currentColor" strokeWidth="1.6" />
      </svg>
      <span className="sr-only">Model</span>
      <input
        className="model-input"
        list="model-options"
        aria-label="Select or type an Ollama model"
        value={model}
        placeholder="model…"
        spellCheck={false}
        autoCapitalize="off"
        autoCorrect="off"
        disabled={pulling}
        onChange={(e) => setModel(e.target.value)}
      />
      <datalist id="model-options">
        {models.map((m) => (
          <option key={m} value={m} />
        ))}
      </datalist>
      {pulling ? (
        <span className="model-pull-status" role="status">
          {pullProgress || 'pulling…'}
        </span>
      ) : canPull ? (
        <button
          type="button"
          className="model-pull-btn"
          onClick={() => pullModel(trimmed)}
          title={`Pull "${trimmed}" from Ollama`}
        >
          Pull
        </button>
      ) : null}
    </div>
  );
}
