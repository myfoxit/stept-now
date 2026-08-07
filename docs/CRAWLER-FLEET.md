# Crawler fleet — multiple workers with assigned egress IPs

Goal: crawl syncs can run on N workers, each egressing from a **distinct,
operator-assigned public IP** (rate-limit isolation, per-customer IPs,
geo-neutrality). Design chosen for the current estate: app stack on the
Hetzner box (`ondoki-01`), a Proxmox host at OVH (`51.83.96.40`, vmbr1 =
10.10.10.0/24 NAT) with 4 running VMs (`saas1-4`) and `debian13-base` /
`rocky9-base` templates.

## Decision: decouple egress from compute

Workers stay on the app box (same compose file, same Redis/Postgres, zero new
data-plane surface). **Egress IP is a per-worker HTTP proxy**, hosted wherever
the IP lives. `STEPT_CRAWL_PROXY_URL` (already in `app/core/config.py`, wired
through every crawl fetch) selects the proxy per worker container.

```
compose:                          Proxmox (per egress IP):
  worker            (no proxy)     saas1: tinyproxy, bound to failover IP A
  worker-egress-a   ┐              saas2: tinyproxy, bound to failover IP B
    STEPT_CRAWL_PROXY_URL=…A ──────► …
  worker-egress-b   ┘
```

Why not run workers on the Proxmox VMs directly: they would need Redis +
Postgres + the uploads volume across the WAN (WireGuard mesh, object storage
migration, latency on every DB round-trip). A proxy needs none of that and the
"assign an IP" operation becomes: boot a VM clone, attach the IP, add one
compose service.

## Prerequisites (in `app/rag`, mostly landed with the crawler hardening)

1. `settings.crawl_proxy_url` honored by every crawl fetch — done.
2. Unique index on `documents(source_id, uri)` + claim-style advisory lock on
   `sync_source` so two workers can never double-sync one source — REQUIRED
   before scaling `worker` past 1 (recon found select-then-insert races).
3. Drop the process-local `_task_lock` on the Postgres path (it serializes all
   RAG work per worker; it exists for SQLite's StaticPool only).
4. Optional queue lanes: a dedicated `crawl` ARQ queue so a 200-page crawl
   never starves agent runs (`STEPT_QUEUE_LANE` env per worker).

## Infra steps (needs operator decisions)

1. **Order additional/failover IPs** in the OVH manager for the dedicated
   server (per-IP setup fee, no monthly on most ranges). Each IP is routed to
   the Proxmox host and bridged (`vmbr0` alias or direct VM MAC binding —
   OVH "virtual MAC" per VM).
2. **Per IP: one micro VM** (clone `debian13-base`, 1 vCPU / 512 MB):
   netplan with the failover IP + virtual MAC, `tinyproxy` listening on the
   internal 10.10.10.0/24 address, ACL'd to the WireGuard/SSH-tunneled client,
   BasicAuth credentials in the proxy URL.
   (Reuse of `saas1-4` possible instead — pending confirmation they're free.)
3. **Private path from ondoki-01 to the proxies**: simplest robust option is
   WireGuard (one interface on ondoki, peers on each proxy VM); the proxy URL
   becomes `http://user:pass@10.66.0.11:8888` over the tunnel — no proxy port
   ever exposed publicly.
4. **Compose**: add `worker-egress-<name>` services (same image/env as
   `worker` plus `STEPT_CRAWL_PROXY_URL`), scale as needed.
5. **Assignment UX (later)**: per-source egress selection — a
   `config.egress` key on the knowledge source routed to the matching queue
   lane, so "this customer's crawls always come from IP B" is a dropdown.

## Caveats

- With a proxy set, `assert_public_url`'s local DNS check is advisory (the
  proxy resolves); the SSRF boundary moves to the proxy host — keep proxy VMs
  outside the app's network and ACL tinyproxy to CONNECT/GET on 80/443 only.
- The Proxmox host is a single failure domain for all egress IPs; crawls
  degrade to direct egress (no proxy) if a proxy is unreachable — fail open,
  loudly, in the sync error summary.
