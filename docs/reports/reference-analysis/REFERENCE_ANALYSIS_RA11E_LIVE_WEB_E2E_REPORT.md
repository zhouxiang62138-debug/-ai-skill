# Reference Analysis RA11-E Live Web E2E Report

**Date:** 2026-08-10  
**Result:** `BLOCKED_BY_RUNTIME_BRIDGE`

## Host-layer result

The real Codex in-app Browser Runtime opened the public HTTPS URL `https://example.com/`, extracted the expected DOM heading/title, and returned a screenshot. This is a valid host-browser smoke result.

## Reference Analysis result

The host tab was not connected to `BrowserAcquisitionProvider` with the project’s F11/F12 resolver, network authorizer, isolated evidence directory, redirect revalidation, and acquisition manifest. The repository Python environment also lacks the supported Playwright package needed by the concrete adapter. No web page was therefore converted into a production Reference Evidence bundle or REFFND set.

## Security result

The smoke page was public HTTPS and used no login, download, popup, credential, cookie, or host-profile state. No unauthorized redirect or cross-project read was attempted. The repository adapter and existing fake-adapter tests preserve deny-by-default policy and bounded capture requirements.

## Decision

RA11-E cannot be marked PASS. A host-browser smoke test is not equivalent to a policy-bound Reference Acquisition run. The remaining work is to expose a stable authorized browser binding or install/provision the approved runtime under the project’s controlled execution path.

