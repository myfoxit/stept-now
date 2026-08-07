# Launch setup — turning on social login and billing in production

The **code** for Google/GitHub login and Stripe billing is shipped, hardened and
deployed. Both stay dark until their provider apps exist and their secrets are
set as GitHub Actions secrets (the deploy workflow writes `/opt/stept/.env` from
them — see `.github/workflows/deploy.yml`). None of the steps below need a code
change; set the secrets, then re-run the latest Deploy (or push any commit).

All redirect/callback URLs are already correct for `app.stepped.ai` and route
through Caddy's existing `/api/*` handle — no ingress change needed.

---

## 1. Social login — Google

1. Google Cloud Console → **APIs & Services → Credentials → Create OAuth client ID
   → Web application**.
2. **Authorized redirect URI** (exactly):
   `https://app.stepped.ai/api/v1/auth/oauth/google/callback`
3. Consent screen: scopes `openid`, `email`, `profile` (no sensitive scopes).
4. Copy the client ID + secret, then set them as repo secrets:

   ```
   gh secret set STEPT_GOOGLE_LOGIN_CLIENT_ID     -R myfoxit/stept-now
   gh secret set STEPT_GOOGLE_LOGIN_CLIENT_SECRET -R myfoxit/stept-now
   ```

> These are *login* credentials, deliberately separate from the workspace
> **integrations** Google app (`STEPT_GOOGLE_CLIENT_ID/_SECRET`). If you only
> have the integrations app, login falls back to it — but then that same Google
> client must ALSO list the login callback above alongside its integrations one.

## 2. Social login — GitHub

1. GitHub → Settings → Developer settings → **OAuth Apps → New OAuth App**.
2. **Authorization callback URL** (exactly):
   `https://app.stepped.ai/api/v1/auth/oauth/github/callback`
3. `STEPT_GITHUB_CLIENT_ID` is already set. Set the secret (and re-set the ID if
   the app is new):

   ```
   gh secret set STEPT_GITHUB_CLIENT_SECRET -R myfoxit/stept-now
   ```

Once either provider has **both** id and secret, `GET /api/v1/auth/oauth/providers`
returns it and the "Continue with …" buttons appear on the login + signup pages
automatically. The callback sets the same rotating refresh cookie a password
login does, so the session behaves identically (and is protected by the new
login-CSRF nonce cookie).

## 3. Stripe billing

Billing is a no-op until `STEPT_STRIPE_SECRET_KEY` is present (self-hosted stays
free + fully entitled). To turn it on for the hosted instance:

1. Stripe Dashboard → **Products**: create two recurring prices —
   - **Cloud** — $19 / seat / month
   - **Business** — $49 / seat / month
   Copy each **price id** (`price_…`).
2. **Developers → Webhooks → Add endpoint**:
   - URL: `https://app.stepped.ai/api/stripe/webhook`
   - Events: `checkout.session.completed`, `customer.subscription.created`,
     `customer.subscription.updated`, `customer.subscription.deleted`,
     `invoice.payment_failed`
   - Copy the **signing secret** (`whsec_…`).
3. Set the secrets:

   ```
   gh secret set STEPT_STRIPE_SECRET_KEY      -R myfoxit/stept-now   # sk_live_…
   gh secret set STEPT_STRIPE_PUBLISHABLE_KEY -R myfoxit/stept-now   # pk_live_…
   gh secret set STEPT_STRIPE_WEBHOOK_SECRET  -R myfoxit/stept-now   # whsec_…
   gh secret set STEPT_STRIPE_PRICE_CLOUD     -R myfoxit/stept-now   # price_… (cloud)
   gh secret set STEPT_STRIPE_PRICE_BUSINESS  -R myfoxit/stept-now   # price_… (business)
   ```

Entitlements enforced once billing is on (self-hosted keeps everything):
custom roles, SLA management, and the audit log require a paid plan; AI usage is
metered off completed `AgentRun`s. Settings → Billing shows plan/usage and opens
Stripe Checkout / the customer portal.

## 4. Apply

`gh secret set` values are only read at deploy time. After setting them:

```
gh workflow run Deploy -R myfoxit/stept-now --ref master
# or just push any commit to master
```

Verify afterward:

```
curl -s https://app.stepped.ai/api/v1/auth/oauth/providers      # -> {"providers":["google","github"]}
# Billing: sign in, open Settings → Billing; it should offer Upgrade, not "billing disabled".
```
