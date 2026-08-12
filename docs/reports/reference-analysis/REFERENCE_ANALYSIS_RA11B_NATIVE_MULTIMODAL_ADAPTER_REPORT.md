# Reference Analysis RA11-B Native Multimodal Adapter Report

**Date:** 2026-08-10  
**Result:** `NOT_READY`

## Implemented contract

The native perception provider was hardened to accept a host-managed adapter without coupling the Skill Runtime to an OpenAI API client or an environment-wide setting. The accepted input is limited to:

- validated `Reference Evidence` image paths and SHA-256 hashes;
- the current `reference_id`;
- requested domains, scope, and explicit exclusions;
- bounded image bytes and the existing F12 path policy;
- a versioned structured multimodal message payload.

The adapter result is accepted only as `{findings, limitations}`. Every finding is checked for REFFND schema validity, reference/evidence consistency, requested-domain scope, exclusions, trust status, and forbidden authority keys. The result is never passed directly to synthesis as authority.

## Failure taxonomy

Perception runs now persist a failure class: `TEMPORARILY_UNAVAILABLE`, `INVALID_INPUT`, `MODEL_INVOCATION_FAILED`, or `STRUCTURED_OUTPUT_INVALID`. This makes “host unavailable” distinct from invalid evidence and malformed/untrusted model output.

## Validation evidence

| Check | Result | Notes |
|---|---|---|
| Real PNG fixture generation | `PASS` | Deterministic dashboard, mobile, and prompt-injection fixtures are generated with Pillow in a caller-owned temporary project path. |
| Image evidence hash binding | `PASS` | Payload image parts include the validated evidence reference, MIME type, SHA-256, and bounded bytes. |
| Scope/exclusion binding | `PASS` | Payload contains requested domains and explicit exclusions. |
| Prompt-injection handling | `PASS` at contract level | Image instructions are treated as untrusted data and authority-shaped output is rejected. |
| REFFND structured validation | `PASS` with injected test adapter | Valid synthetic adapter output is accepted only after validation. This is not a live host model call. |
| Invocation failure classification | `PASS` | Host exception is stored as `MODEL_INVOCATION_FAILED`. |
| Malformed output classification | `PASS` | Invalid findings are stored as `STRUCTURED_OUTPUT_INVALID`. |
| Actual host-backed native multimodal invocation | `BLOCKED` | No supported host-to-Python Skill Runtime invocation bridge was available. |
| User attachment injection into project-local evidence | `BLOCKED` | Host-visible attachment handling did not expose a stable, authorized project binding. |

## Security and provenance

The provider does not accept workflow state, role transitions, acceptance decisions, requirements, tool commands, or synthesis authority from model output. Inputs remain tied to the project root and evidence hashes. No API key, OpenAI API client, host cookie, or global configuration was introduced.

## Decision

The adapter contract is ready for a real host bridge, but the real host bridge itself is not present or discoverable in this environment. Per RA11’s strict rule, an injected interface or mock-backed test cannot be reported as production-ready native multimodal capability.

