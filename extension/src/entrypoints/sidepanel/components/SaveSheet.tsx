import { useState } from 'react';
import { RefreshCw, Save } from 'lucide-react';
import { t } from '../../../i18n';

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
      <div className="save-title">{t('save.title')}</div>
      <p className="save-hint">{t('save.hint', { count: stepCount })}</p>
      <label className="fld">
        <span>{t('save.name')}</span>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t('save.name_placeholder')}
          disabled={saving}
        />
      </label>
      <label className="fld">
        <span>{t('save.url_label')}</span>
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
        {saving ? t('save.saving') : t('save.save_tour')}
      </button>
    </div>
  );
}
