import { useState } from 'react';
import { KeyRound, LogIn, Route, ShieldCheck, Sparkles } from 'lucide-react';
import { t } from '../../../i18n';
import type { SignInResult, SimpleResult } from '../../../messages';
import type { AuthState, WorkspaceChoice } from '../../../types';
import { Logo, sendBg } from '../lib';

/** Sign-in with the REAL Stept account (the old PKCE + pairing-code flow is
 * gone): email + password → workspace picker → a long-lived, workspace-scoped
 * extension token. The password and the access token never touch storage. */
export function Login({ auth }: { auth: AuthState }) {
  const [apiBase, setApiBase] = useState(auth.apiBase);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(auth.error);
  const [choices, setChoices] = useState<WorkspaceChoice[] | null>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    const r = await sendBg<SignInResult>({
      type: 'sign-in',
      apiBase: apiBase.trim(),
      email: email.trim(),
      password,
    });
    setBusy(false);
    setPassword(''); // never keep it in component state longer than the request
    if (!r?.ok) {
      setError(r?.error ?? t('login.signin_failed'));
      return;
    }
    if (r.workspaces?.length) setChoices(r.workspaces);
  };

  if (choices) return <WorkspaceChooser choices={choices} onBack={() => setChoices(null)} />;

  return (
    <div className="login">
      <div className="login-hero">
        {/* The panel's front door — the one screen here where the logo is the
            subject, so it gets the split rather than the chrome mono. */}
        <Logo size={46} variant="split" />
        <h1 className="login-title">{t('login.title')}</h1>
        <p className="login-sub">{t('login.subtitle')}</p>
      </div>
      <ul className="login-points">
        <li>
          <Sparkles size={13} /> {t('login.point_record')}
        </li>
        <li>
          <Route size={13} /> {t('login.point_replay')}
        </li>
        <li>
          <ShieldCheck size={13} /> {t('login.point_masked')}
        </li>
      </ul>

      <label className="fld">
        <span>{t('login.stept_url')}</span>
        <input
          value={apiBase}
          onChange={(e) => setApiBase(e.target.value)}
          placeholder="http://localhost:8600"
          autoComplete="url"
        />
      </label>
      <label className="fld">
        <span>{t('login.email')}</span>
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          autoComplete="username"
        />
      </label>
      <label className="fld">
        <span>{t('login.password')}</span>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          onKeyDown={(e) => {
            if (e.key === 'Enter' && email && password) void submit();
          }}
        />
      </label>

      {error && (
        <div className="banner danger" role="alert">
          <span className="banner-body">{error}</span>
        </div>
      )}

      <button
        className="btn primary big"
        disabled={busy || !apiBase.trim() || !email.trim() || !password}
        onClick={() => void submit()}
      >
        <LogIn size={14} /> {busy ? t('login.signing_in') : t('login.sign_in')}
      </button>
      <p className="login-consent">{t('login.consent')}</p>

      <TokenFallback apiBase={apiBase} />
    </div>
  );
}

function WorkspaceChooser({ choices, onBack }: { choices: WorkspaceChoice[]; onBack: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="login">
      <div className="login-hero">
        <Logo size={40} />
        <h1 className="login-title">{t('login.choose_workspace')}</h1>
        <p className="login-sub">{t('login.tours_saved_here')}</p>
      </div>
      {error && (
        <div className="banner danger" role="alert">
          <span className="banner-body">{error}</span>
        </div>
      )}
      <div className="ws-list">
        {choices.map((w) => (
          <button
            key={w.id}
            className="ws-row"
            disabled={!!busy}
            onClick={async () => {
              setBusy(w.id);
              setError(null);
              const r = await sendBg<SimpleResult>({ type: 'choose-workspace', workspaceId: w.id });
              setBusy(null);
              if (!r?.ok) setError(r?.error ?? t('login.workspace_connect_failed'));
            }}
          >
            <span className="ws-name">{w.name}</span>
            <span className="ws-role">{busy === w.id ? t('login.connecting') : w.role}</span>
          </button>
        ))}
      </div>
      <button className="btn ghost" onClick={onBack}>
        {t('login.back')}
      </button>
    </div>
  );
}

/** Advanced: paste a token minted in the dashboard (extension or legacy
 * recorder token). Useful for shared/kiosk machines and for the e2e harness. */
function TokenFallback({ apiBase }: { apiBase: string }) {
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <details className="adv">
      <summary>
        <KeyRound size={12} /> {t('login.advanced_token')}
      </summary>
      <div className="login-adv">
        <label className="fld">
          <span>{t('login.token_label')}</span>
          <input value={token} onChange={(e) => setToken(e.target.value)} placeholder="eyJhbGciOi…" />
        </label>
        {error && (
          <div className="banner danger" role="alert">
            <span className="banner-body">{error}</span>
          </div>
        )}
        <button
          className="btn"
          disabled={busy || token.trim().length < 10}
          onClick={async () => {
            setBusy(true);
            setError(null);
            const r = await sendBg<SimpleResult>({ type: 'adopt-token', apiBase, token: token.trim() });
            setBusy(false);
            if (!r?.ok) setError(r?.error ?? t('login.token_invalid'));
          }}
        >
          {busy ? t('login.checking') : t('login.use_token')}
        </button>
      </div>
    </details>
  );
}
