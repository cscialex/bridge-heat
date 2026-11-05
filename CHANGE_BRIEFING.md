# Integration hardening briefing

The nine uploaded source/configuration files (including both `.bak` files) matched
this repository after newline normalization. All repository files were reviewed;
backups and brand assets are unchanged. Backups are historical and are never
imported. No backend sources were supplied; this change is for review only.

## What changed and why

- TLS verification is enabled on upload, poll, and acknowledgement requests. One
  HA-managed session is reused. Redirects cannot forward the credential. Error
  messages omit response bodies, raw connection errors, secrets, and household data.
- The shared key is removed from active code. Setup accepts an operator-supplied
  credential in a masked field; existing installations set one through Options.
  Blank Options credentials retain the current value. HA configuration/storage
  files and backups need access protection; masking is not encryption.
- Temperature, humidity, pressure, and optional AQ consent apply to every history
  row and retry batch, including historical entities no longer loaded in memory.
  Options reload the integration, cancelling old workers. Data already accepted by
  the server cannot be recalled by a client consent change.
- History uses read-only SQLite, explicit schema checks, guaranteed connection
  closure, and keyset pages of at most 500 scanned rows. At most 20 pages are
  processed per invocation; scheduled catch-up continues after a minute. Attributes
  larger than 8,192 characters, malformed attributes, and invalid/oversized states
  are skipped. Sample attributes are limited to device class, unit, and state class.
  No full history/result list or all-chunks copy is retained.
- HA `Store` atomically persists a random installation ID, timestamp/state-ID
  cursor, one retry batch of at most 500 samples, and one remote command. Each batch
  is saved before POST and its cursor is committed only after HTTP success.
  Later page failures do not replay earlier committed pages. Restarts retain the
  batch and cursor; recovery is no longer clamped to the most recent 24 hours.
- Manual, scheduled, and remote triggers share one lock and cursor. A trigger
  arriving during work is skipped, rather than queued without a bound. Manual and
  remote triggers now catch up uncommitted history instead of resending 24 hours.
  Remote commands use a fixed upper bound, persist completion before the final
  acknowledgement, and retry failed acknowledgements without repeating uploads.
  Poll and acknowledgement failures appear in integration status.
- Retries use jitter and at most three attempts. Upload retries within a request
  are limited to connection establishment failures and HTTP 429. Ambiguous upload
  errors retain the batch for later recovery. Poll/status updates also retry
  transient server failures. Retry-After accepts seconds and HTTP dates; long
  delays use a process-local cooldown instead of keeping a worker asleep indefinitely
  (the cooldown resets on reload/restart).
- Status entities have entry-specific IDs, migrate the old ID when owned by that
  entry, and publish state changes. Entry-bound background tasks and timer cleanup
  cancel and await active operations before the runtime is removed.

## Privacy and migration decisions

Entity IDs and timestamp strings are retained for research/API compatibility;
entity names can disclose room names. Arbitrary attributes (including friendly
names and extra coordinates) are no longer shared. Precise HA location remains
**enabled by default for compatibility** and is now an explicit setup/Options
choice; disabling it omits location from uploads and polls and scrubs buffered
samples on reload. Researchers must assess the effect of missing location and
reduced attributes before upgrading participants.

The former MAC-based device ID is replaced by a persistent random `ha-…` ID.
**Coordinate participant mapping and pending-command reassignment with the backend
operator before rollout.** Existing server records are not automatically linked or
deleted. Cloning HA configuration also clones this identity; an operator-assisted
identity reset/re-enrolment is required for clones. Do not delete storage casually:
it also deletes upload progress and can cause replay.

**The exposed shared credential must be revoked/rotated externally**, even though
it was removed from active code: historical backups and git history still contain
it. Obtain a replacement accepted by the current server before enabling uploads.
Per-install credential issuance, scoping, revocation, and enrolment require backend
coordination; the client does not invent a provisioning endpoint.

## Compatibility and limits

The `/env`, `/pending-uploads`, `/acknowledge-upload/{id}` endpoints, `X-API-Key`,
gzip JSON, sample field names/time formatting, and acknowledgement status vocabulary
are preserved. No new required wire fields or infrastructure were added. JSON has
no comments: `manifest.json` now declares the Recorder dependency so setup can read
its configured URL; new strings/translations label credentials and consent.

Only a local SQLite Recorder with `states`, `states_meta`, `state_attributes`, and
the checked columns is supported. A custom SQLite path is honored; incompatible
schemas or other database engines fail visibly. This is a read-only adapter to
Recorder internals, not a promise of compatibility with future schemas.

First use starts with 24 hours of history. Subsequent recovery depends on Recorder
retention; only the current retry batch is separately buffered. Recorder exclusions,
purges, database replacement, and rows committed late with timestamps behind the
cursor can leave gaps. A five-minute settling delay reduces late-commit risk but
cannot eliminate it. Recorder records state changes, **not guaranteed periodic
samples**. No full scalability or lossless-delivery claim is made.

HTTP 200/201 is assumed to acknowledge the entire batch, matching the existing
client contract. Actual atomic acceptance and command status semantics need server
validation. Partial acceptance within a successful response cannot be reconciled
without a backend contract. An accepted POST followed by a lost response, failed
local cursor write, or crash can replay that batch. Exactly-once delivery requires
server event IDs/idempotency, deduplication, and explicit acceptance watermarks;
these are backend work, not silently added payload requirements. Repeated command
status updates must also be safe on the server. No production endpoint was used
for validation.

## Validation

Run `python -m pip install -r requirements-test.txt`, `python -m pytest -q`,
`ruff check custom_components/bridge_heat tests`, and
`ruff format --check custom_components/bridge_heat tests` in a Python 3.13 environment.
Tests exercise real SQLite and HA storage, restart/outage recovery, partial batch
failure, consent withdrawal, equal timestamps, concurrency, cancellation, poll/ACK
failures, bounded HTTP responses/retries, and the existing wire format. Local UI
review uses Home Assistant 2025.10.4 with the transport stubbed inside the review
process, so no research-server requests are issued. Other HA versions and production backend
acceptance/performance remain unverified.
