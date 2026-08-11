# Reference Analysis RA11-A Live Host Bridge Audit

**Date:** 2026-08-10  
**Result:** `PARTIAL / NOT_READY_FOR_RUNTIME_INJECTION`

## Scope

This audit checked the current Reference Analysis architecture, the repository runtime, the local Codex host capabilities, the in-app Browser Runtime, and the available Python/Node automation surfaces. It did not inspect host cookies, browser profiles, credentials, or unrelated projects.

## Evidence

| Capability | Result | Evidence |
|---|---|---|
| Codex in-app Browser launch | `PASS` | The bundled Browser skill created/reused an in-app tab and navigated to `https://example.com/`. |
| Browser DOM extraction | `PASS` | Title `Example Domain`; DOM snapshot contained the expected heading; bounded DOM payload was 232 bytes. |
| Browser screenshot | `PASS` | Screenshot was returned by the host browser; encoded payload was 16,265 bytes. |
| Host attachment visibility | `AVAILABLE_AT_HOST_LAYER` | The host can expose user-visible attachments to the interactive model surface. A stable project-local binding into this Skill Runtime was not discovered. |
| Programmatic native multimodal call from Python Skill Runtime | `NOT_FOUND` | The existing provider only accepts an injected callable/object adapter. No supported host bridge, invocation endpoint, or supported CLI subcommand was available to the repository process. |
| Python browser automation package | `BLOCKED` | `playwright`, `selenium`, and `pyppeteer` were not installed in the repository Python environment. |
| Browser executable discoverable from repository shell | `NOT_FOUND` | No `playwright`, Chromium, Chrome, or Edge executable was discoverable as a repository-runtime command. |
| Codex CLI bridge from repository shell | `BLOCKED` | The installed `codex.exe` was found, but `codex --help` and `codex exec --help` returned `Access is denied` from the repository shell. |
| F12 network-policy binding for Reference Analysis | `NOT_READY` | The Reference Analysis provider requires explicit resolver and network-authorizer injection. The default role policy remains deny-by-default and does not grant direct Reference Analysis network/browser access. |

## Architecture findings

1. `CodexNativeMultimodalPerceptionProvider` now accepts a host-shaped invocation object with `invoke(payload)` and an optional `model_identity`. It validates a structured result before returning findings. This is an injection boundary, not evidence that a live host provider is available.
2. The multimodal payload binds every image to a validated evidence reference, SHA-256, MIME type, scope, requested domains, and explicit exclusions. The system instruction treats image text as untrusted data and grants it no workflow authority.
3. `PlaywrightReferenceBrowserAdapter` provides a concrete isolated-context adapter when the supported Python Playwright runtime is present. It disables downloads and service workers, uses a temporary context, applies a navigation guard to every request, closes popups, and bounds DOM/style/screenshot capture.
4. `BrowserAcquisitionProvider` still requires explicit F12-compatible resolver and network-authorizer dependencies. It does not silently widen the Reference Analysis role policy.

## Security result

No host cookies, credentials, persistent browser profiles, environment secrets, or cross-project files were reused. No direct network bypass or API-key path was added. The host-browser smoke test was limited to a public HTTPS example page and did not become a Skill Runtime Reference Evidence record.

## Decision

The host has a usable interactive Browser Runtime, but the repository Runtime does not yet have a stable, approved bridge for native multimodal invocation or browser capture injection. RA11 can proceed through contract hardening and host-layer smoke validation, but cannot honestly claim live provider-backed image/web E2E or production closure.

