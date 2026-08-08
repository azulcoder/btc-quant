# Collector on GCP — deployment runbook

This lifts the tick collector off the Mac (where laptop sleep means gaps and the
local disk filled to zero on 2026-07-23) onto an always-on Linux VM. Same
collector, same HF dataset lifecycle, same honesty rails — it just never sleeps
and has room to breathe.

What you get: a dedicated unprivileged user, a pinned Python venv, a
`btcquant-collector` service that restarts forever, and a `btcquant-hfsync` timer
that uploads each closed UTC day to `azulcoder/btc-quant-ticks` and frees the local
copy (verify-on-Hub before delete — nothing is removed until it is confirmed on the
Hub).

Recording scope this phase is unchanged from the Mac: one symbol (**BTCUSDT**), with
bybit-linear as the primary full-book leg plus the supplementary cross-venue trade and
depth streams the collector pulls keyless by default. Adding full-depth books and spot
legs is a separate, disk/cost-gated step — see
[Phase 2](#phase-2--recording-the-venue-matrix) at the bottom.

---

## 1. Pick a size and region

The collector is light: a handful of websocket streams and small batched DuckDB
writes. Two sensible options, honest numbers (on-demand, mid-2026 list prices —
check the live [pricing calculator](https://cloud.google.com/products/calculator)):

| Option | Machine | RAM | Region | Compute | 30 GB disk | Notes |
|---|---|---|---|---|---|---|
| **Free-ish** | `e2-micro` | 1 GB | `us-central1` | $0 (free tier¹) | $0 (free tier¹) | 1 GB RAM is tight; watch RSS |
| **Comfortable** | `e2-small` | 2 GB | `asia-southeast1` | ~$12–13/mo | ~$1.30/mo | Singapore ≈ near Bybit/OKX |

¹ GCP Always-Free covers **one** `e2-micro` + 30 GB standard persistent disk **only
in `us-west1`, `us-central1`, `us-east1`**, plus 1 GB/mo North-America egress. The
daily HF upload is an estimated ~30–50 MB (compressed parquet) ≈ ~1–1.5 GB/mo — confirm
against one clean closed day — so you may nudge ~0.5 GB past the free egress cap ≈ a few
cents a month. Inbound market data is free.

Geographic latency does **not** affect data quality — every row is stamped with the
exchange's own event time, so a US region recording an Asian exchange is still
exact; it only means slightly higher websocket round-trip and reconnect frequency
(the collector reconnects automatically, gaps stay honest gaps). Pick free if cost
matters, Singapore if you want the steadiest connection.

Set your choices once:

```bash
export PROJECT=your-gcp-project
export ZONE=asia-southeast1-b        # or us-central1-a for free tier
export MACHINE=e2-small              # or e2-micro
gcloud config set project "$PROJECT"
```

## 2. Create the VM

```bash
gcloud compute instances create btcquant-collector \
  --zone="$ZONE" --machine-type="$MACHINE" \
  --image-family=debian-12 --image-project=debian-cloud \
  --boot-disk-size=30GB --boot-disk-type=pd-standard \
  --no-address                      # no public IP — reach it via IAP (step 4)
```

`--no-address` means no public IP at all: the VM makes only **outbound**
connections (websockets out, HTTPS to the Hub out), so it needs no inbound
exposure. You reach it for admin through IAP tunnelling, below.

If you prefer a plain public-IP VM, drop `--no-address` and instead lock SSH to
your own IP; never open any other port — the collector listens on nothing.

## 3. Bootstrap

SSH in (over IAP, no public IP needed) and run the idempotent bootstrap:

```bash
gcloud compute ssh btcquant-collector --zone="$ZONE" --tunnel-through-iap

# on the VM:
sudo apt-get update -qq && sudo apt-get install -y -qq git
sudo git clone https://github.com/azulcoder/btc-quant.git /opt/btcquant
sudo bash /opt/btcquant/deploy/gcp/bootstrap.sh
```

The script installs packages, creates the `btcquant` user, builds the venv, runs a
**smoke test** (imports + `run_collector.py --help` on the actual installed
runtime — if the Python version is wrong it fails right here, not at 03:00), renders
the systemd units, and starts the collector + daily timer. Re-run it any time to
update (it does `git pull` + re-render + restart).

Default Python is Debian's system `python3` (3.11) — the collector uses no
3.12-only syntax, so it runs clean. For exact CI parity (3.12) run
`sudo PYTHON_STRATEGY=uv bash .../bootstrap.sh` instead (that path pipes the `uv`
installer to `sh` as root — reviewed-installer trade-off, your call).

## 4. Add your Hugging Face token

The collector records without a token; the daily **upload** needs one. Create a
**fine-grained, write-scoped token limited to `azulcoder/btc-quant-ticks`**
(https://huggingface.co/settings/tokens) so a leak can touch nothing else. Then:

```bash
sudo -e /opt/btcquant/deploy/gcp/env      # set HF_TOKEN=hf_...
# (the file is mode 600, owned by btcquant, and gitignored)
sudo systemctl start btcquant-hfsync      # run the first sync now, don't wait for 07:20
journalctl -u btcquant-hfsync -n 40 --no-pager
```

More secure option (optional): keep the token in
[Secret Manager](https://cloud.google.com/secret-manager) and fetch it into the env
file on boot; the env-file approach above is the simple baseline.

## 5. Verify it is alive

```bash
sudo -u btcquant bash /opt/btcquant/deploy/gcp/status.sh
```

Shows: service active, the timer's next fire, the last sync's exit status, the
newest day file's **freshness** (should be touched within the last minute or two
while markets move), and disk headroom. This is the cloud version of your weekly
`make check-ticks` — run it over SSH whenever you want a pulse.

## Operating notes

bootstrap sets the VM to `Asia/Jakarta`, so the timer's `07:20` is 07:20 WIB = 00:20
UTC — after the UTC day rolls and the §3c 5-minute grace window closes, so the
just-closed day is complete. Day-file rotation itself is event-time UTC and does not
depend on the VM clock; the wall-clock matches the Mac.

To update, re-run `sudo bash /opt/btcquant/deploy/gcp/bootstrap.sh`: it re-pulls `main`,
re-renders the units, and restarts. A restart is a ~15 s honest gap, nothing more.

On disk, 30 GB is ample. Budget the store at roughly a few hundred MB per UTC day
(estimate — confirm against one clean closed day); the timer frees each day once it
lands on the Hub, so steady state is one to two days resident, well under 1 GB. If the
timer ever stops (bad token, Hub outage) the store keeps growing until the next
successful sync drains it — `status.sh` shows headroom, and the verify-then-delete rail
means nothing is lost, it just accumulates.

Stop the collector with `sudo systemctl stop btcquant-collector` (SIGTERM triggers a
clean final flush); `disable` keeps it off across reboots. Logs go to journald only —
`journalctl -u btcquant-collector -f`, nothing to rotate.

## Security posture

- No inbound ports. The collector listens on nothing (no `--api-port`); the VM has
  no public IP under the recommended `--no-address`. Admin is IAP-tunnelled SSH.
- Least privilege. Services run as the unprivileged `btcquant` user under systemd
  hardening (`ProtectSystem=strict`, `NoNewPrivileges`, `PrivateTmp`, and a single
  `ReadWritePaths=…/data` — the process cannot write anywhere but the store).
- Secrets. The HF token lives only in the mode-600, gitignored env file (or Secret
  Manager), scoped to the one dataset, and is never printed.

---

## Phase 2 — recording the venue matrix

Today the VM records BTCUSDT with bybit-linear as the primary full-book leg, plus the
supplementary cross-venue trade and depth streams the collector already pulls keyless
(binancef, OKX, Coinbase, Deribit). The T-2 terminal renders seven venue×market legs;
what the collector does *not* yet record is full-depth books for the trades-only legs
and the spot legs. On a roomy VM you may want to add those for research history.

This is deliberately **not** bundled here, because it is a real cost/disk decision,
not a config flip:

- The current footprint is on the order of a few hundred MB per UTC day (estimate —
  confirm against one clean closed day). Full-depth books for the trades-only legs,
  plus the spot legs not yet recorded, are far heavier than trades + top-of-book —
  depth diff streams dominate. Budget on the order of several GB/day before
  compression; measure a single leg for a day first.
- HF egress and dataset size grow proportionally; the daily parquet upload gets
  bigger and the Always-Free egress cap becomes irrelevant.
- The collector's schema and `upload_hf.py` partitioning would need per-leg tables.

When you want it: measure one added leg for 24 h, read the real bytes/day, size the
disk and confirm the HF cost, then extend the collector recording scope behind the
usual pre-registration/greenlight ritual. Say the word and it becomes its own phase.

---

## Phase 2b — raw depth-diff recorder (its own tiny VM, zero credentials)

`DIAG-cost-ledger-001` §2a-2b established that queue position cannot be reconstructed from
1 Hz snapshots at any cadence and that the diff stream cannot be backfilled — every
unrecorded day is permanent loss. `scripts/record_depth_diffs.py` closes that forward, and it
is deliberately **keyless**: it talks only to Binance public endpoints, so the VM that runs it
carries **no service account, no scopes, no tokens, and no secrets of any kind**.

Measured in a 45 s live smoke [DIUKUR]: ~8 frames/s, chain intact, ~164 MB/day compressed —
a 50 GB disk holds roughly ten months before a retention decision is needed.

All commands use placeholders. **Never commit a real project id, account, or email to this
public repo.**

```bash
PROJECT=<YOUR_PROJECT_ID>          # the billing-enabled one
ZONE=asia-northeast1-b             # Tokyo: same metro as the venue's matching engine

gcloud config set project "$PROJECT"
gcloud services enable compute.googleapis.com

# SSH via IAP only — no port 22 from the open internet, and delete the default rule if
# your VPC has one (fresh projects do):
gcloud compute firewall-rules create allow-iap-ssh \
  --direction=INGRESS --action=allow --rules=tcp:22 --source-ranges=35.235.240.0/20
gcloud compute firewall-rules delete default-allow-ssh --quiet || true

# The VM: e2-small, Debian 12, shielded boot, NO service account (nothing to steal),
# bootstrap runs deploy/gcp/bootstrap-depth.sh from the public repo at first boot.
gcloud compute instances create btcq-depth-rec-1 \
  --zone="$ZONE" --machine-type=e2-small \
  --image-family=debian-12 --image-project=debian-cloud \
  --boot-disk-size=50GB --boot-disk-type=pd-balanced \
  --shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring \
  --no-service-account --no-scopes \
  --metadata-from-file=startup-script=deploy/gcp/startup-depth-metadata.sh

# Verify (IAP tunnel; first connect generates an SSH key):
gcloud compute ssh btcq-depth-rec-1 --zone="$ZONE" --tunnel-through-iap -- \
  'systemctl --no-pager status btcquant-depth-recorder | head -12; \
   sudo ls -la /opt/btc-quant/data/depth_diffs/binancef/BTCUSDT/ 2>/dev/null'

# Teardown (reversible, this is the whole point of a disposable recorder):
gcloud compute instances delete btcq-depth-rec-1 --zone="$ZONE" --quiet
```

Cost, order of magnitude [DIASUMSIKAN — verify in your console]: e2-small ≈ $13-17/mo,
50 GB pd-balanced ≈ $5/mo, ingress free, egress negligible until a sync decision is made.
Well inside a $300 trial. Set a budget alert in the console (Billing → Budgets) at e.g. $50;
the budgets API needs its own enablement and is easier clicked than scripted.

**Boot path, learned the hard way [DIUKUR 2026-08-08]:** five consecutive boots failed
exit 128 while the persistent unit kept the OLD recorder running — a silent partial
failure. The first diagnosis blamed raw.githubusercontent CDN caching; that was WRONG, or
at least never proven: a boot running entirely from instance metadata (no CDN in the path)
reproduced the same failure, and the serial console then showed the literal cause —
`git config --global` under the metadata script runner has **no HOME**, so the
safe.directory exception landed nowhere, and the `2>/dev/null` on that very line swallowed
the only warning. The fix is HOME-independent three ways (HOME exported, `--system` config
in /etc/gitconfig, `GIT_CONFIG_*` env vars binding every child git), the startup script
ships in the repo (`deploy/gcp/startup-depth-metadata.sh`) and is passed via
`--metadata-from-file`, and the recovery was verified on the serial console:
`Updating c344d3f..7b24df5, Fast-forward`, service restarted. The durable lessons: a boot
that LOOKS healthy can be running stale code, and an error you silence yourself is the
one that costs five boots.

What is deliberately NOT here: HF sync of the diff tape. That needs a write token on the VM,
and the safe version (fine-grained token scoped to one dataset, rotated) is a separate
decision — the disk buys ~10 months to make it.

## Phase 2c — backup + visibility: append-only GCS, write-only identity (2026-08-08)

The tape was accumulating with **no copy** — the one class of data this project cannot buy
back. Phase 2c gives the VM exactly one new power: *append bytes to one bucket*.

```bash
BUCKET=<YOUR_TAPE_BUCKET>          # globally unique; no project id needed in the name
SA=btcq-tape-writer@<YOUR_PROJECT_ID>.iam.gserviceaccount.com

# Bucket: same region as the VM (zero egress), uniform IAM, versioned, no public access.
gcloud storage buckets create "gs://$BUCKET" --location=asia-northeast1 \
  --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update "gs://$BUCKET" --versioning
# lifecycle: Nearline at 30 d, Coldline at 90 d (see lifecycle JSON in the session log)

# Identity: ONE binding, on the bucket, not the project.
gcloud iam service-accounts create btcq-tape-writer
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member="serviceAccount:$SA" --role=roles/storage.objectCreator

# Attach: requires a STOP (the API refuses set-service-account on a running instance).
# Scope is devstorage.write_only — the OAuth layer repeats the IAM restriction.
gcloud compute instances stop btcq-depth-rec-1 --zone="$ZONE"
gcloud compute instances set-service-account btcq-depth-rec-1 --zone="$ZONE" \
  --service-account="$SA" \
  --scopes=https://www.googleapis.com/auth/devstorage.write_only
gcloud compute instances add-metadata btcq-depth-rec-1 --zone="$ZONE" \
  --metadata=tape-bucket="$BUCKET"
gcloud compute instances start btcq-depth-rec-1 --zone="$ZONE"
```

**The posture change, stated honestly.** "No service account, no scopes, nothing to steal"
was true and is now false in one narrow way. What an attacker who fully owns the VM gains:
the ability to **create objects in this one bucket** — pollute the tape with fabricated
objects (they cannot replace real ones; creation of new names only), and run up storage
cost without bound (mitigate with a budget alert, which is a console click, not IAM). What
they still **cannot** do, each enforced twice (bucket IAM `objectCreator` AND instance
scope `devstorage.write_only`): read any object back, list the bucket, overwrite or delete
anything already written, touch any other bucket or GCP resource, or reach the project's
IAM. Every sync run PROBES this live: it attempts to read back its own upload and expects
HTTP 403; the heartbeat publishes `readback_denied` so posture drift would be visible in
the data, not in an audit nobody re-runs. Versioning is belt-and-braces: even a future
IAM mistake leaves noncurrent versions behind.

**What lands in the bucket** (all written by the units `btcquant-tape-sync.timer` /
`btcquant-tape-heartbeat.timer`, installed by the bootstrap):

- `tape/binancef/BTCUSDT/date=*/chunk-<start>-<end>.gz` — byte-ranges of the day file cut
  at complete gzip-member boundaries; concatenated in offset order they reproduce the
  local file **byte-for-byte**. Verified per chunk against the `md5Hash` the create
  response returns (a write-only principal's only readable fact), and per day against a
  whole-file md5 in `manifest.json`. A torn tail from a mid-flush kill uploads verbatim
  as `.trunc` — recorded, never repaired.
- `heartbeat/date=*/hb-*.json` every 5 min — service state, frames/gaps today (counted
  from the tape bytes, not from the process's own opinion), disk free, sync watermark,
  `readback_denied`. **This is the no-SSH visibility surface**; watch it at:
  `https://console.cloud.google.com/storage/browser/<YOUR_TAPE_BUCKET>/heartbeat?project=<YOUR_PROJECT_ID>`
- `qc/qc-*.json` at each boot — per-hour frame rate, chain verdicts, gap rows, resyncs,
  and `recv_ms − E` latency-proxy percentiles (`deploy/gcp/tape_qc.py`).

Local retention: a day file is deleted **3 days after** its manifest verified — declared
in `tape_sync.py` (`RETAIN_DAYS`), one place, changeable. Until then every byte exists in
two places; after, in one durable versioned place plus the ledger row that says so.

**When the trial ends** (numbers, not reassurance): a $300/90-day trial that ends without
upgrading to a paid account **stops the VM immediately and deletes the project's data
after a grace window** — the tape included. Upgrading keeps everything and starts charging
the card: at current volume that is the VM (~$18-22/mo) plus storage that starts at
pennies (~5 GB/mo of new tape: ≈ $0.12/mo Standard, aging into Nearline ≈ $0.10/mo and
Coldline ≈ $0.04/mo per accumulated month-slab). Evacuating instead is one command from
any machine with read access (`gcloud storage cp -r "gs://$BUCKET/tape" ...`) at ~$0.12/GB
egress — do it BEFORE expiry; a suspended billing account may refuse even reads.
