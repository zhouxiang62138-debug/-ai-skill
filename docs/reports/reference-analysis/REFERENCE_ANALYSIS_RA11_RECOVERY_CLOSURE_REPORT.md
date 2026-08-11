# Reference Analysis RA11 Recovery Closure Report

**Date:** 2026-08-10  
**Recovery mode:** Host Bridge Enablement → Live E2E Completion  
**Result:** `NOT_READY`

## RA11 Recovery Result

| Track | Result | Evidence |
|---|---|---|
| B1 Multimodal Host Bridge | `HOST_CAPABILITY_AVAILABLE / PROGRAMMATIC_BRIDGE_UNEXPOSED` | Host exposes multimodal input to the current interactive model, but no callable Skill Runtime invocation tool or attachment-forwarding interface is exposed. |
| B2 Attachment Binding | `CONTRACT_IMPLEMENTED / HOST_METADATA_BLOCKED` | Strict binding implementation and tests are complete; the current recovery attachment is only a prompt text file and carries no stable attachment/project/root metadata. |
| B3 Browser F12 Runtime | `WIRING_IMPLEMENTED / RUNTIME_BLOCKED` | Existing F12 Capability/Network Policy is wired for a controlled `example.com` service; Python Playwright and the lease-bound Reference Analysis browser execution path remain unavailable. |
| Image live E2E | `NOT_COMPLETED` | No real user image and no native host multimodal invocation entered the Skill Runtime. |
| Web live E2E | `HOST_SMOKE_ONLY` | Host Browser successfully captured `example.com`; the repository F12 acquisition provider did not execute the capture. |
| Full product loop | `NOT_EXECUTED` | Withholding execution avoids synthetic findings and false PASS. |

## B1 Host Bridge Audit

The current Host tool surface was inspected for model invocation, multimodal transport, attachment forwarding, and file references. The available browser binding exposes browser navigation, DOM inspection, screenshots, and two optional capabilities (`visibility`, `viewport`), but no model invocation or current-turn attachment binding capability.

Current request metadata exposes turn/session/workspace metadata only; it does not expose an attachment identity, project binding, or host-native invocation handle. The repository shell found the installed Codex executable, but direct CLI help invocation returned `Access is denied`. No OpenAI API client, API key, external Responses call, or second Agent runtime was introduced.

The final classification is therefore deliberately:

```text
HOST_CAPABILITY_AVAILABLE
PROGRAMMATIC_BRIDGE_UNEXPOSED
```

It is not reported as “vision unavailable”.

## B2 Attachment → Project / Reference Evidence Binding

Added `HostAttachment`, `AttachmentBindingStore`, and a versioned attachment-binding schema. The binding requires and records only the minimum provenance needed for the handoff:

- stable `attachment_id`;
- target `project_id`;
- SHA-256 of the resolved project root;
- `reference_id`;
- generated `REFEV` identity;
- content hash, size, MIME type, and authorized project-relative artifact path.

The source path is never written into the binding record. Materialization rejects symlinks/reparse points, directories, oversized content, hash mismatches, project-root mismatches, cross-project target mismatches, and malformed filenames. The artifact and evidence record are written append-only; retries use an idempotency key based on project, root hash, reference, attachment identity, and content hash.

The current attached file is `pasted-text.txt`, not an image, and the Host did not provide the required stable attachment/project/root metadata. It was therefore not promoted into a live project Reference Evidence record.

## B3 Browser → F12 Runtime Wiring

Added `F12ReferenceBrowserRuntime`. It requires an explicit resolver supplied by the Host/F12 layer, checks `browser.access` through the existing `CapabilityPolicy`, and authorizes each URL through the existing `NetworkPolicy`. The repository configuration adds only the controlled public smoke service:

```text
reference_public_web → example.com:443 → read
```

No second network policy was created, no arbitrary public-host allowlist was added, and no credentials/cookies/host profile are available to the Reference Analysis module.

The concrete `PlaywrightReferenceBrowserAdapter` remains isolated and bounded, but the repository Python environment does not have the Playwright package. The F12 runtime wiring therefore reports `BLOCKED_BY_ENVIRONMENT` rather than claiming production availability. A lease-bound `ExecutionBroker` browser execution entrypoint for Reference Analysis is still absent; the current wiring stops at the existing Capability/Network Policy boundary.

## Host Browser Smoke Evidence

The real Codex in-app Browser Runtime was run against the public HTTPS URL `https://example.com/`:

```text
title: Example Domain
url: https://example.com/
DOM contains expected heading: true
DOM bytes: 232
screenshot bytes: 16265
```

This is Host-layer evidence only. It was not written as a production Reference Evidence record because it did not pass through `BrowserAcquisitionProvider`, F12 resolver authorization, project artifact staging, and the acquisition manifest.

## Capability Matrix

| Capability | Status | Notes |
|---|---|---|
| Text Reference | `SUPPORTED` | Existing deterministic text adapter and protocol path. |
| User-attached Image | `BLOCKED` | No image attachment or stable Host binding in this recovery turn. |
| Project-local Image | `SUPPORTED_WITH_LIMITATIONS` | Hash/integrity/path binding works; native semantic perception remains unexposed. |
| Public Web URL | `SUPPORTED_WITH_LIMITATIONS` | Host smoke and controlled policy wiring exist; repository acquisition runtime is blocked. |
| Authenticated Web | `BLOCKED` | Credentials, cookies, saved passwords, and host profiles are denied. |
| Multi-page Web | `DEFERRED` | Single public URL is the only permitted recovery scope. |
| Multi-reference | `SUPPORTED_WITH_LIMITATIONS` | Existing deterministic artifact graph supports it; live multimodal fusion is unavailable. |
| Design Exploration | `SUPPORTED` | Existing Planner workflow and design-preview protocol are preserved. |
| Generator Integration | `SUPPORTED_WITH_LIMITATIONS` | Existing approved-plan/provenance gates remain intact; no live image/web source entered. |
| Evaluator Conformance | `SUPPORTED_WITH_LIMITATIONS` | Existing conformance path remains available; live visual input was not produced. |
| Change Request | `SUPPORTED` | Existing CR protocol was not rewritten or bypassed. |

## Runtime / Security / Context Audit

- Only Planner, Generator, and Evaluator remain Agents; no fourth Agent was added.
- Reference input and model output remain untrusted; output cannot change requirements, approvals, plans, CAS state, role routing, or acceptance results.
- No API key, OpenAI API client, global environment setting, Windows security change, Chrome profile access, credential-store access, or direct network bypass was used.
- Attachment binding stores hashes and project-relative references, not host absolute paths or secrets.
- Browser capture remains HTTPS-only, public-IP checked, redirect-revalidated, same-origin by default, temporary-profile only, download-disabled, popup-closed, and bounded.
- No project data was written into the Skill repository as a managed project instance.

## Tests and Full Regression

```text
RA11 contract tests: 5 passed
RA11 recovery tests: 4 passed
Targeted protocol/recovery checks: 24 passed
Full regression: 732 passed
Skipped: 5
Subtests: 113 passed
```

All 5 skipped tests require an unavailable Docker daemon. `compileall` passed and `git diff --check` passed.

## Remaining Blockers

1. Host must expose a documented native multimodal invocation bridge that accepts the validated payload and returns bounded structured output with auditable model identity.
2. Host must expose stable attachment identity plus project/root binding metadata for the current turn, or provide an approved materialization callback.
3. F12 must expose a lease-bound Reference Analysis browser execution entrypoint or approved Playwright runtime provisioning path, including resolver, network authorization, browser launch, and evidence commit under the same execution authority.
4. After those blockers are available, rerun real Image E2E, real Web E2E, and the full Planner → Generator → Evaluator product loop.

## Production Recommendation

Keep the release state at `NOT_READY`. The deterministic text/project-local image paths and the security contracts may be used with their declared limitations. Do not claim “full visual/web reference ready”, `SUPPORTED` native image perception, or production web acquisition until B1, B2 host metadata, B3 runtime execution, and RA11-D/E/F all have reproducible evidence.

This recovery report is additive; the prior `REFERENCE_ANALYSIS_RA11_LIVE_ADAPTER_PRODUCTION_CLOSURE_REPORT.md` remains unchanged as historical evidence.

