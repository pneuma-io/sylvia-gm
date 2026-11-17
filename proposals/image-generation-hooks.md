# Image-generation hooks

Status: selected planning scope, PL-10. No provider is connected or called by this draft.

## User outcome

Give Sylvia an extensible way to send an image-generation request to a user-selected integration and receive a usable result. Include selected GPT Image and ComfyUI integrations where their actual capabilities support the chosen request. A provider can expose supported editing operations through the same reviewed request/result lifecycle.

This selects integration hooks, not a general promise that Sylvia will automatically generate all campaign art or that any arbitrary API works. Existing portrait/background/Builder use cases are consumers to evaluate, not permission to build every art feature or replace selected assets.

## Current baseline

Sylvia already has provider/settings, content and saved-campaign boundaries to reuse. The inspected desktop frontend is wired through its existing host bridge; no GPT Image/ComfyUI request/result journey was established by this planning review. Mechanics helpers named for spell images are not evidence of an image-service integration. Confirm the owning asset/provider surfaces before implementation rather than adding another independent media store.

## Proposed integration boundary

- Define a small image request/result contract with operation identity, selected adapter, supported capability, status, progress where available, cancellation, errors and result/provenance references. Advertise actual supported generation/edit/input/size/workflow capabilities; reject unsupported requests clearly.
- Put provider-specific authentication, request mapping, asynchronous job tracking and output decoding inside adapters. Adding another adapter should not require changing campaign mechanics or assuming every service has the same schema.
- For GPT Image, select the supported official API/model/capabilities at implementation time using [OpenAI's image documentation](https://developers.openai.com/api/docs/guides/image-generation). For ComfyUI, select the actual user-controlled server/workflow contract against [its upstream documentation/source](https://github.com/Comfy-Org/ComfyUI). Do not infer compatibility from a provider name alone.
- The user chooses the integration and destination. No mandatory provider, subscription, purchase, local model download, ComfyUI installation, hosted service or new account is selected here. Treat a user-entered endpoint as a real privacy/network boundary, not a route for arbitrary file access or secrets.
- Make requests deliberate and expose any chosen cost/remote-data implications. Cancellation or retries must not silently submit another paid job. A late result must remain tied to its original request/session.
- Keep a previous chosen asset until the user deliberately adopts a successful result. Validate the returned type/size and safe asset handling; a provider URL/filename is untrusted data. Persist the selected result and provenance through normal saved-content paths.
- Preserve PL-10's ComfyUI privacy boundary: protected prompts, generated outputs and output filenames stay sealed. Engineering verification uses placeholders, workflow/schema structure, statuses and sanitized logs; no inspection of protected prompt/media values is authorized by this proposal. Do not upload personal campaign content or third-party assets.

## Acceptance and future tests

1. Run the same request/result lifecycle against synthetic adapters, including one asynchronous workflow shape, then exercise each selected supported GPT Image/ComfyUI capability with explicitly authorized disposable inputs.
2. Test unsupported operations, malformed/error responses, rate limits, refused credentials, unavailable server, cancellation and interrupted/late results. Keep operation/session identity and prevent unintended duplicate paid submissions.
3. Verify request parameters and result adoption without changing a previous asset on failure. Save/reopen a selected synthetic result through the real content path.
4. Validate provider-specific capability differences and a small additional synthetic adapter to demonstrate extensibility. Do not claim every API or workflow is supported.
5. Verify protected prompt/media/filename values and credentials are absent from routine logs, exported diagnostics and public fixtures. No real provider call, media inspection or purchase occurs in this planning PR.

## Decisions for implementation

Choose exact supported APIs/workflows, output/adoption semantics and authentication/retention handling for each integration. Preserve complete PL-10 references, but apply Daniel's current hook-focused selection rather than silently expanding to full art generation throughout the product.

## Full specification and implementation boundary

Planning references: PL-10 — `b93bb0f5-6408-4610-b72d-e67bc6a2fbf5`. The complete current Desk specifications and their linked original acceptance/source material remain authoritative. Retrieve all of them through authorized access before implementation; these public notes are an index and selected scope, not a replacement handoff. Daniel's latest selection here supersedes conflicting older framing. The coordinator owns Desk reconciliation.

This PR changes one proposal document only. No feature implementation, external configuration, credentials, installation, paid calls, tests of the feature, merge or release is delivered. No GitHub Actions before November 2026. Future implementation and final-byte acceptance remain separate work.
