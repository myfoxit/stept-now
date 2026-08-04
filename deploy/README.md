# Deploying Stept

Production runs on a single host behind Caddy. Images are built by GitHub Actions
and pinned to a commit SHA; nothing is built on the server.

```
                     ┌── :80/:443 ── caddy ──┬── web    (nginx: SPA + widget bundle)
  app.stepped.ai ────┤                       ├── api    (uvicorn ×4, scheduler OFF)
  stepped.ai ────────┘                       └── poki-web  (separate stack, /poki only)
                                                  │
                              api ── postgres (pgvector) ── redis
                                        ▲                     ▲
                              worker ───┘   scheduler ────────┘
```

## Why five app processes instead of one

| Service | Job | Why separate |
|---|---|---|
| `api` | HTTP + WebSockets | Multiple uvicorn workers for throughput |
| `worker` | ARQ: AI runs, webhook delivery, RAG ingestion | Slow work must not occupy a request worker |
| `scheduler` | SLA scans, campaign dispatch, knowledge re-sync | **Exactly one.** The API also runs these in its lifespan, which is right for a single-process dev server — under N workers it becomes N duplicate campaign sends and N breach events, so the API sets `STEPT_SCHEDULER_ENABLED=false` and this owns them |
| `web` | Static SPA + widget bundle | nginx serves files better than Python, and keeps the API image node-free |
| `caddy` | TLS + routing | Sole public entry point |

Because there are multiple API workers, `STEPT_REDIS_URL` is not optional in this
topology: pub/sub, the task queue and rate limiting all have to be shared state
or they silently mean something different per worker.

## First-time setup

1. **Generate a deploy key** for GitHub Actions (its own key, not a personal one):

   ```sh
   ssh-keygen -t ed25519 -N '' -C 'github-actions-deploy' -f ./stept-deploy
   ssh-copy-id -i ./stept-deploy.pub root@178.104.12.226
   ssh-keyscan -t ed25519 178.104.12.226      # value for DEPLOY_KNOWN_HOSTS
   ```

2. **Add repository secrets** (`gh secret set <NAME>`):

   | Secret | Value |
   |---|---|
   | `DEPLOY_SSH_KEY` | contents of `stept-deploy` (the private key) |
   | `DEPLOY_KNOWN_HOSTS` | `ssh-keyscan` output — pinned, so the deploy cannot hand a root shell to an impostor |
   | `DEPLOY_HOST` | `178.104.12.226` |
   | `DEPLOY_USER` | `root` |
   | `DEPLOY_DOMAIN` | `stepped.ai` |
   | `DEPLOY_APP_DOMAIN` | `app.stepped.ai` |
   | `ACME_EMAIL` | address for Let's Encrypt notices |
   | `STEPT_SECRET_KEY` | `python -c 'import secrets; print(secrets.token_urlsafe(48))'` |
   | `POSTGRES_PASSWORD` | `python -c 'import secrets; print(secrets.token_urlsafe(24))'` |

   `STEPT_SECRET_KEY` signs every JWT **and** derives the Fernet key that encrypts
   stored AI-provider credentials. Rotating it invalidates all sessions and makes
   existing provider keys undecryptable — treat it as permanent.

3. **Point DNS** at the host, DNS-only (grey cloud on Cloudflare). Caddy uses
   HTTP-01, which a proxied record breaks.

4. Push to `master`. The workflow gates on the full test suite, builds, deploys,
   verifies the live site, and rolls back if the verification fails.

## Operating it

```sh
ssh root@178.104.12.226
cd /opt/stept

docker compose ps                       # what is running
docker compose logs -f api              # follow the API
grep -E '^(API|WEB)_IMAGE=' .env        # which build is live
docker compose run --rm api migrate     # migrations only
docker compose run --rm api python -m app.seed   # demo data (non-empty DB: read seed.py first)
```

**Roll back by hand:**

```sh
cd /opt/stept && cp .env.previous .env && docker compose up -d
```

`.env.previous` is only updated after a deploy passes its live checks, so it
always names a release that actually served traffic.

**Back up:**

```sh
docker compose exec -T postgres pg_dump -U stept stept | gzip > /root/stept-$(date +%F).sql.gz
docker run --rm -v stept_uploads:/data -v /root:/backup alpine \
  tar czf /backup/stept-uploads-$(date +%F).tar.gz -C /data .
```

The database and uploads live in the `stept_pgdata` and `stept_uploads` volumes.
`docker compose down` leaves them; `docker compose down -v` destroys them.

## The poki co-tenant

`poki` is an independent compose project in `/opt/poki` that attaches to the
network `stepped_stepped` so a shared Caddy can reach it. This stack declares that
network as **external** and joins it, which is what keeps `stepped.ai/poki/*`
working — poki's own nginx serves that whole prefix, so Caddy passes the path
through unrewritten.

Consequences worth knowing: this project must never be the one to create or remove
`stepped_stepped`, and `docker compose down` here will report that it cannot
remove the network. That is correct — poki is still attached to it.
