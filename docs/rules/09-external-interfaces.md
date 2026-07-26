# 09 · External Interfaces And Communication

## 9.1 Trust Boundaries

Every external input is untrusted until validated:

- React to Tauri IPC;
- React to local HTTP/WebSocket;
- provider and OpenCode/GitHub HTTP responses;
- browser DOM, URL, clipboard, and network data;
- imported bundles and existing user configuration files.

Parse at the boundary into a typed model. Do not let raw payloads escape into
workflow or persistence code.

## 9.2 Local HTTP Service

- Bind to `127.0.0.1`, never `0.0.0.0`, unless a future approved requirement
  explicitly introduces remote access and authentication.
- All REST endpoints use the `/api` prefix.
- Request and response bodies use Pydantic models with Chinese field
  descriptions.
- Route handlers validate, delegate, and map errors; they do not orchestrate.
- CORS origins remain an explicit allowlist for the Vite and Tauri origins.
- OpenAPI and implementation must be updated together.

Successful responses use an endpoint-specific response model. Errors use a
stable envelope:

```json
{
  "code": "flow_not_found",
  "message": "The requested flow does not exist",
  "details": null
}
```

`code` is a stable machine identifier, `message` is safe user-facing text, and
`details` is optional sanitized structured context. Tracebacks, credentials,
raw third-party responses, and local paths never appear in responses.

## 9.3 HTTP Status Mapping

- `200`/`201`: successful query or creation.
- `202`: accepted long-running operation.
- `400`: malformed semantic request.
- `404`: resource does not exist.
- `409`: invalid current state or idempotency conflict.
- `422`: schema validation failure.
- `429`: local rate or capacity protection, not a copied secret upstream body.
- `500`: unexpected internal failure with a sanitized envelope.
- `503`: required local/external dependency unavailable.

Expected workflow states such as manual intervention and pending payment are
normal resource states, not `500` errors.

## 9.4 WebSocket Contracts

Architecture-defined channels include flow events, manual intervention, and
global logs. Each message includes:

- `event`: stable snake_case event name;
- `version`: event schema version;
- `timestamp`: UTC ISO-8601 timestamp;
- `flow_id` when scoped to a flow;
- `payload`: event-specific validated model.

Requirements:

- Send an initial authoritative snapshot after connection.
- Support reconnect without duplicating side effects.
- Do not treat a connected client as the source of workflow truth.
- Use bounded queues/backpressure; slow clients cannot grow memory without
  limit.
- Do not send secrets, verification codes, or unmasked screenshots/logs.

## 9.5 Tauri IPC

- Commands are narrow, typed, and limited to desktop lifecycle capabilities.
- Rust command names and frontend invoke strings match exactly.
- Serde response fields use one documented naming convention; current frontend
  contracts use camelCase.
- Do not use arbitrary JSON strings or filesystem paths supplied by the UI
  without validation.
- IPC errors are stable and sanitized.
- Long-running business operations go through the local service rather than
  blocking a Tauri command.

## 9.6 Provider Contracts

Provider implementations follow the documented `EmailProvider` behavior:

- create a mailbox;
- wait for a valid code within a timeout;
- dispose of the mailbox best-effort.

Provider rules:

- Normalize addresses and response models at the adapter boundary.
- Set finite connect/read/total timeouts.
- Retry only idempotent operations and use bounded backoff with jitter.
- Respect cancellation and release resources.
- Treat malformed payloads as provider failures, not empty success.
- Log provider name and sanitized failure class, never credentials or message
  bodies containing codes.

## 9.7 Browser Interface

- Selectors live only in `backend/browser/`.
- Validate the current host and page state before each sensitive action.
- Clipboard data is untrusted and validated against the expected API-key
  format before storage.
- Workspace IDs extracted from URLs are validated before interpolation.
- Screenshot capture defaults to disabled for sensitive pages and follows the
  retention rules in `10-data-and-persistence.md` when enabled.
- Unknown verification or payment state returns manual intervention; automation
  never guesses a bypass.

## 9.8 Contract Changes

A contract change updates, in one pull request:

1. boundary model;
2. producer and consumer;
3. tests for success and failure payloads;
4. OpenAPI/event/IPC documentation;
5. schema version or migration note if incompatible.

Last updated: 2026-07-25
