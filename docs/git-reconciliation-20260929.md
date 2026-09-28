# STOCK repository reconciliation — 2026-09-29

## Scope and evidence

Before reconciliation, local `main` was `ed924f5`: two commits ahead and
54 commits behind `origin/main` (`fc0205e`). Production was at `fc0205e`
with 84 modified/deleted/new source paths. Every one of those paths matched
the local production baseline, verified by SHA-256 (local text normalized
from CRLF to LF). The retirement work had been deployed but not published.

The merge retains both histories without force-pushing. It preserves the
latest published active ETF acquisition histories/logs and the persistent
chart release. Production's existing retirement changes remain authoritative;
retired accounting, market-pulse and V1–V4 products are not restored.

After the user reported concurrent edits, the audit was repeated. Remote and
production had advanced to `d588d4f` (the bedroom A static site). That commit
was merged intact, including its README addition and deploy helper. Among
the 84 production baseline paths, only README had changed. The full suite
also passed in an isolated Linux copy using production's existing virtualenv:
77 tests and 26 subtests. No production secrets were copied into that checkout.

Issuer-fetch and derived-job systemd ownership belongs to the mother project
(`06_arm_server`), including the trading-calendar gate and task ledger.
Obsolete duplicate templates are removed here. The gold monitor template's
background slice matches the already installed production unit.

## Local artifacts

- `.codex/` and root `tmp/` are machine configuration and temporary artifacts;
  they are ignored, preserved locally, and not published.
- The two root `Benson_0050_*.pine` strategies are standalone research source,
  preserved unchanged in a separate commit. They are not used by production.
- Mother-project deployment copies under production's STOCK directory are
  outside this repository's ownership. Preserve them and use exact local
  `.git/info/exclude` entries; do not duplicate or remove those files.
- The old Streamlit secret-rotation helper is historical source, not an
  instruction to rotate secrets or restart services during this cleanup.

## Verification and deployment method

The full local suite passes: 77 tests and 26 subtests. Two old rich-menu
expectations were updated to the already deployed retained-product contract;
the fetch test now mocks Linux host identification on Windows. No live
application logic was changed to make tests pass. Added lines in both local
commits and the merge, plus the Pine sources, were scanned for common secret
and session-URL patterns; no matches were found.

Production's pre-update patch, index, HEAD, changed source files and service
process identifiers are backed up under
`/var/backups/stock-git-cleanup-20260929` (root-only).
Before aligning production Git metadata, require that all existing application
and data files match the incoming tree. Only documentation, tests, Git rules,
non-running helpers/research sources and the already installed gold slice
template may differ. A metadata-only reset preserves working-tree content;
populate only the explicitly verified ancillary paths afterward. Do not run
a hard reset, clean, broad restore, dependency install, daemon reload, or
service restart for this reconciliation.

Verify tracked/untracked status, matching local/origin/production HEADs,
unchanged live application hashes, and unchanged service process IDs afterward.
Do not describe these checks as a fresh full audit of trading/accounting.
