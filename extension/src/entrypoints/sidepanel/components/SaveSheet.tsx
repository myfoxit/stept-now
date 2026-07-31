import { useState } from 'react';
import { RefreshCw, Save } from 'lucide-react';

/** Post-recording save form: name + the suggested `url_pattern`. Saving
 * creates a DRAFT tour — publishing stays a deliberate act in the dashboard. */
export function SaveSheet({
  defaultName,
  defaultUrlPattern,
  saving,
  stepCount,
  onSave,
}: {
  defaultName: string;
  defaultUrlPattern: string;
  saving: boolean;
  stepCount: number;
  onSave: (opts: { name: string; urlPattern?: string }) => void;
}) {
  const [name, setName] = useState(defaultName);
  const [urlPattern, setUrlPattern] = useState(defaultUrlPattern);
  const canSave = name.trim().length > 0 && !saving && stepCount > 0;

  return (
    <div className="save-sheet">
      <div className="save-title">Save as a draft tour</div>
      <p className="save-hint">
        {stepCount} step{stepCount === 1 ? '' : 's'} will be saved. You can edit and publish it in the
        Stept dashboard.
      </p>
      <label className="fld">
        <span>Name</span>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Create your first automation"
          disabled={saving}
        />
      </label>
      <label className="fld">
        <span>Show on pages matching — leave blank to trigger it manually</span>
        <input
          value={urlPattern}
          onChange={(e) => setUrlPattern(e.target.value)}
          placeholder="https://app.example.com/settings*"
          disabled={saving}
        />
      </label>
      <button
        className="btn primary big"
        disabled={!canSave}
        aria-busy={saving}
        onClick={() => onSave({ name: name.trim(), urlPattern: urlPattern.trim() || undefined })}
      >
        {saving ? <RefreshCw size={14} className="spin" /> : <Save size={14} />}
        {saving ? 'Saving…' : 'Save tour'}
      </button>
    </div>
  );
}
