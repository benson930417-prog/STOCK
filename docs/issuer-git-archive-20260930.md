# Issuer archive recovery — 2026-09-30

The September 29 derived run generated its images and received LINE HTTP 200
(`SENT`, one attempt), then failed at `git push`. The production data commit
`1e834b7` and remote bedroom commit `e7121e8` shared parent `5405b20`: the job
tried to push a diverged branch. Commit `1591415` subsequently merged the
branches. This was an archival failure, not a failed LINE publication.

`scripts/archive_issuer_data.py` now captures only the 24 approved issuer
history/log JSON paths. It fetches remote main, builds a commit on that tip
using a temporary index, and pushes without force. A concurrent remote code
push causes a bounded retry. Independently changed issuer data is a conflict
requiring review, never an automatic overwrite. Authentication/network failures
remain failures. The durable `.git/issuer-archive.json` receipt supports the
next run without moving deployed HEAD or staging unrelated files.

Scheduled archival never checks out or merges remote application code into the
running checkout. Consequently production HEAD may intentionally lag archived
data commits, and generated history/log files may remain dirty until a reviewed
deployment. Normal deployments must inspect these paths and preserve current
data; do not hard reset or clean the checkout.

The orchestrator journals failed-step details before removing its temporary
email files. LINE's existing receipt/idempotency gate and all market sources,
accounting, strategy and order controls remain unchanged. Recovery verification
runs only the archive helper; it does not resend LINE or rerun the full pipeline.

Integration tests use local bare repositories and actual Git pushes to verify
parallel code preservation, a race during push, repeated batches/idempotency,
data conflicts, transport failure, and unchanged production HEAD/index/code.
