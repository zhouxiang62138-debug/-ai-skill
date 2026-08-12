# Reference Analysis RA11-C Real Browser Adapter Report

**Date:** 2026-08-10  
**Result:** `READY_WITH_LIMITATIONS / RUNTIME_NOT_READY`

## Implemented adapter

`PlaywrightReferenceBrowserAdapter` is now a concrete `BrowserCaptureAdapter` implementation. When the supported Python Playwright runtime is installed, it:

- creates a temporary isolated browser context rather than reusing a host profile;
- disables downloads and service workers;
- applies the injected navigation guard to every request, including redirects and subresources;
- requires the provider’s HTTPS/public-host/F12 authorization checks;
- waits for `DOMContentLoaded` plus a bounded settle interval;
- captures bounded DOM, selected computed styles, and screenshots;
- records runtime identity, redirect chain, console errors, failed requests, and document URLs;
- closes popups and the temporary context on completion.

The implementation is fail-closed when Python Playwright is unavailable.

## Live host-browser smoke evidence

The Codex in-app Browser skill successfully launched a real host browser tab against `https://example.com/` and returned:

- title: `Example Domain`;
- expected heading in the DOM snapshot;
- bounded DOM size: 232 bytes;
- screenshot payload: 16,265 bytes.

This proves host-layer browser availability only. The tab was not treated as a Reference Analysis acquisition record because it was not connected to the repository provider’s F11/F12 resolver, authorizer, evidence store, or project root.

## Runtime validation

The repository Python environment does not contain `playwright`, `selenium`, or `pyppeteer`, and no supported browser executable was discoverable from the repository shell. Therefore the concrete Python adapter could not perform a live repository-side capture in this run. Existing fake-adapter security and acquisition tests remain the deterministic validation path.

## Security result

No login, popup interaction, download, cookie/profile reuse, credential access, cross-origin redirect acceptance, or direct network bypass was performed. The adapter’s navigation hook is designed to deny unauthorized requests before capture.

## Decision

The host Browser Runtime is available, and the repository adapter is structurally ready, but a supported Python runtime plus an approved F12-bound bridge is still required before RA11-C can be called production-ready.

