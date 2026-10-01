---
title: Telegram image delivery — implementation plan
status: active
created: 2026-10-01
last_reviewed_on: 2026-10-01
review_in: 3 months
applies_to: nephilim
ai_summary: Implementation plan for delivering locally-generated images to the user through the Telegram gateway, and for what /reset does to them. Open before writing any image-delivery, job-queue, media-storage or /reset code — several non-obvious constraints are already settled here with evidence (sendPhoto destroys the PNG by re-encoding to JPEG; a bot cannot delete its own messages after 48h and cannot delete the file from Telegram at all; python-telegram-bot retries nothing and its read_timeout stays 5s even for media; a live path-traversal vector in POST /sessions/import). Model selection and the generation backend are NOT in scope — see research/persona_image_generation_2026-08-19.md.
---

# Telegram image delivery — implementation plan

Scope: getting a locally-generated image from the coordinator into the Telegram chat,
and defining what `/reset` does to it. **Out of scope:** which diffusion model, which
runtime, and whether the output is good enough — those live in
[persona_image_generation_2026-08-19.md](../research/persona_image_generation_2026-08-19.md)
and are gated on a bake-off that has not run.

Research pass 2026-10-01: three agents (Telegram mechanics, job/storage architecture,
repo map), reconciled against the live codebase and this machine.

## Why Telegram before the React UI

The React path needs four new things: somewhere to store bytes, a field on the response,
an `<img>` branch in a hand-rolled markdown renderer, and static file serving. The
coordinator has **no static mounting and no `FileResponse` anywhere** — verified, zero
hits across `src/coordinator` — so file serving is net-new.

Telegram needs two. **Telegram hosts the image itself**, so there is no serving story
and no renderer work. Both processes are on the same host under launchd, so the gateway
can open the file by path.

The media contract still goes on the **coordinator's response**, not in the gateway, so
the React UI later becomes a second consumer rather than a rewrite. This follows
[ADR-011](../decisions/011-conversation-control-commands-as-shared-session-api-endpoints.md):
logic in the coordinator, thin clients.

⚠️ **This touches a stated invariant and the change must be argued, not assumed.**
`services/telegram-gateway/CLAUDE.md` declares the gateway text-only and says "do not add
tool-use, exec, file, or trading access". Sending a file is a deliberate, scoped amendment
to that posture: the gateway gains the ability to **read one path the coordinator names
and upload it to one allowlisted chat**. It gains no file-system discretion, no path
construction, and no new inbound surface. Record that in the gateway's own docs when the
code lands.

## The five findings that shape everything

### 1. `sendPhoto` destroys the image

Telegram re-encodes photos **server-side to JPEG** and flattens alpha. Not configurable,
not opt-out. The MTProto file docs describe every delivered `PhotoSize` as a server-side
resize, the largest (`w`) bounded at 2560×2560.

Our PNGs (1024² to 2048×1152, 1–3 MB) clear every `sendPhoto` limit — 10 MB cap, the
width+height ≤ 10000 sum, the 20:1 ratio. **The limits are not the problem; the re-encode
is.** For AI artwork it is a visible, permanent loss.

> **Decision: `sendDocument` with a self-generated thumbnail.** Original bytes preserved,
> 50 MB cap instead of 10 MB, no dimension or ratio limits, and official clients still
> render a preview (the API's own `thumbnail` docs say it "can be ignored if thumbnail
> generation for the file is supported server-side"). We supply our own 320×320 JPEG
> (< 200 kB) so the preview is ours rather than inferred.
>
> Cost: a document bubble shows filename + size rather than a full-bleed photo, and the
> image does not join the chat's shared-photos gallery. **Verify this on the actual iOS
> and macOS client before committing** — the rendering is client behaviour, not an API
> contract. Fallback if it looks too drab is photo + document as a reply pair, never
> photo alone.

⚠️ `file_id` is **type-locked** — a document `file_id` can never be re-sent as a photo.
Committing to one delivery method is therefore a one-way door for stored ids.

### 2. A bot cannot delete its own messages after 48 hours

`deleteMessage` is documented with a hard limit: "A message can only be deleted if it was
sent **less than 48 hours ago**." In a private chat the bot may delete both its own and
the user's messages — but only inside that window. `deleteMessages` (plural) takes 1–100
ids per call and skips what it cannot find.

**And there is no way to delete the file from Telegram's servers.** The Bot API exposes no
`deleteFile`; `file_id`s are documented as persistent. Whether the blob is eventually
garbage-collected is undocumented in both directions.

> **Consequence for `/reset`: the promise must be scoped honestly.** What it can truthfully
> claim is that the images leave the chat on all the user's devices and that we forget our
> local copies and rows. What it must not claim is that the bytes are gone from Telegram.
> An image older than 48 h cannot be removed by us at all, and `/reset` must **say so with
> a count** rather than swallowing the `BadRequest` and reporting success. Silently
> swallowing it is the exact shape this repo keeps catching: a confident wrong answer on a
> green path.

**`protect_content=True` is the lever that actually works** for a companion system — it
blocks forwarding and saving at send time, which is the realistic privacy concern. Apply
it to every generated image.

### 3. The async job is forced, not chosen

The gateway's HTTP client is pinned at `NEPHILIM_TIMEOUT_SECONDS=180`, every coordinator
route is a sync `def` on the threadpool, and there is no SSE or WebSocket anywhere in
`src/`. A 2–8 minute generation exceeds the timeout before it exceeds anything else.

But **the long work does not live in our process.** ComfyUI's `POST /prompt` returns a
`prompt_id` immediately and the GPU work continues regardless of what the coordinator
does. The worker is a *supervisor of a remote job*. That is what makes restart-safety
cheap: persist the `prompt_id` and a restart becomes a **reconnect**, not a loss.

⚠️ **Do not pattern-match on `fact_extraction_worker.py`.** It is a `queue.Queue` plus one
daemon thread that **drops jobs silently on queue-full and on exception**, with no status
row and no callback. That is precisely the silent-death class this plan must avoid, already
in the repo.

⚠️ **`BackgroundTasks` is actively harmful here**, beyond the usual objections: uvicorn
waits for background tasks on shutdown and `timeout_graceful_shutdown` defaults to wait-
forever. Under launchd that turns every restart into SIGTERM → block → SIGKILL.

### 4. PTB retries nothing

Earlier sessions assumed this gateway had a retry wrapper. **It does not.** Its only retry
is `_with_session_recreate` — a 404 session-recreate around *coordinator* calls.
`bot.send_message` is issued exactly once, at `eeva_telegram/messaging.py:26`.

Underneath, PTB's request layer retries nothing at all. `AIORateLimiter` is opt-in, retries
`RetryAfter` **only**, and defaults `max_retries=0`. Not retried by anything: `TimedOut`,
`NetworkError`, 502, connection resets, DNS failures — precisely the class that cost
eeva-dca a notification for thirteen months ([INV-1](../../../eeva-dca/docs/INVARIANTS.md)).

⚠️ **PTB's `read_timeout` stays at 5.0 s even for media.** It swaps only the *write*
timeout for uploads (`media_write_timeout`, 20 s). After the bytes are up Telegram still
generates its thumbnail ladder before answering. **This is the most likely cause of a
phantom "the image never arrived" bug.** Set `read_timeout=60`, `media_write_timeout=180`,
`connect_timeout=20` at builder level.

### 5. Progress edits are rate-limited with sends, and a ticker is explicitly discouraged

Telegram documents no numeric limit for `editMessageText`. The best source is TDLib
maintainer levlam: *"Limits for message editing and sending are shared"*, and on editing
every 5 seconds — *"This is a **bad behavior pattern** … Such behavior is supposed to be
limited."* The binding constraint in a private chat is ~1 message/second, shared across
sends and edits.

> **Decision: milestone edits, not a ticker.** 4–6 edits across 8 minutes, each driven by a
> **real coordinator event** ("queued", "sampling 20/50", "upscaling", "encoding"), never
> by a timer. The liveness signal is `sendChatAction(UPLOAD_DOCUMENT)` **every 4 seconds**
> — it expires after ≤5 s, so 4 s overlaps and never flickers, and it is the documented
> mechanism for exactly this case.

Per-step percentages are not worth it in a chat transcript, and nobody in the self-hosted
space ships them — Open WebUI shows animated dots and a batch counter.

## Architecture

### Delivery shape: placeholder becomes the artwork

1. On request, reply immediately with a placeholder message; store its `message_id`.
2. Background: chat-action heartbeat every 4 s, cancelled in `finally`. Milestone
   `editMessageText` on coordinator events.
3. Finish with **`editMessageMedia`** (Bot API 7.11+) so the placeholder *becomes* the
   image.

`editMessageMedia` is chosen over delete-then-send for a reason beyond tidiness: **it is
effectively idempotent.** The Bot API has no idempotency key, so a `TimedOut` after a
successful upload is ambiguous and a blind retry posts the image twice. Retrying an *edit*
converges on one message holding one image. One message for the whole lifecycle, no
delete/send race, no duplicate.

⚠️ **Every terminal state must edit the placeholder, in a `finally`** — including `failed`,
`cancelled`, `timed_out` and `orphaned`. The Discord "Bot is thinking…" bug is an unhandled
exception leaving the placeholder forever. That is the same failure wearing a different
platform.

### Completion notification: the gateway polls for pending deliveries

The coordinator has no way to push to the gateway today — no callback URL, no shared table,
no socket, and the gateway exposes no listening port. Three ways to close that:

| Option | Cost |
|---|---|
| Coordinator holds the bot token and calls `sendPhoto` itself | **Token in two processes.** Rejected on secret hygiene, and it makes the coordinator transport-aware |
| Gateway gains `POST /internal/deliver` | New inbound surface on a component whose current virtue is having none |
| **Gateway polls `GET /notifications/pending?channel=telegram`** | A 5 s tick. Preserves flow direction, no new port, no token duplication |

> **Decision: the third.** The coordinator returns fully-formed delivery instructions
> (`chat_id`, `message_id` to edit, media path, caption); the gateway performs **zero
> logic** and is a dumb pump. `POST /notifications/{id}/ack` sets `delivered_at`. PTB's
> `JobQueue.run_repeating(interval=5)` is the idiomatic home.

Correction to an earlier assumption: the 409 double-poller lesson in project memory applies
to `getUpdates` only. A second process calling `sendPhoto` with the same token does **not**
409. Option one is rejected on secret hygiene and coupling, not on 409.

### Job execution: DB table + one asyncio supervisor

A job table in `chats.db` (so it can `FK … REFERENCES chat_sessions(id) ON DELETE CASCADE`
and reuse `BaseRepository` + Alembic), plus a single in-process `asyncio` supervisor started
from `lifespan`. **Concurrency = 1, declared** — one GPU, which deletes most of the races.
No Redis, no Celery, no second launchd process.

Precedent exists: `jupiter/strategy_scheduler.py` already runs an `AsyncIOScheduler` that
`server.py` shuts down in lifespan. Reuse it for the sweeps. The DB layer is sync `sqlite3`,
so DB calls from the async worker go through `asyncio.to_thread`.

**The load-bearing mechanism is the startup sweep, not the shutdown hook.** Build the sweep
first and test it with `kill -9`. Every claimed row carries a `worker_run_id` (UUID at
process start); on boot, any `running` row whose `worker_run_id` differs is *certainly* not
being worked — a certain signal, where a stale heartbeat is only probabilistic.

⚠️ **ComfyUI's history is in-memory only** (Comfy-Org/ComfyUI#15965, open). Re-attach by
`prompt_id` survives *our* restarts but not ComfyUI's. Run ComfyUI under launchd `KeepAlive`
too, and resolve a history miss to an explicit **`orphaned`** state — never `running`, never
a silent success.

### Two upstream defects to guard

- **ComfyUI#11540 (open):** `execution_success` fires **before outputs are persisted**, so
  a client reading `/history/{id}` on that signal can see `output_files: []` and cache an
  empty result. This is the ambiguous-empty-success rule in the wild, in the dependency we
  are integrating. **Never accept an empty output list as success** — retry with backoff,
  then `failed(backend_reported_success_without_output)`.
- **ComfyUI#9330:** `progress_state` messages have been observed arriving 20–30 s *after*
  completion. **Terminal state comes from an explicit terminal event or from history, never
  from the presence or absence of progress messages.**

## Storage

```
<media_root>/
  sessions/<media_dir>/
      img/<job_id>.png
          <job_id>.thumb.jpg      # 320x320, <200kB, for Telegram + future gallery
      .tmp/                       # same filesystem — required for os.replace
  orphans/                        # quarantine, not rm
```

**Per-session directories**, so a reset is one `rmtree` with no refcounting.

⚠️ **The directory name must be a server-generated `media_dir` column, never the session
id.** This is a live vector, not a hypothetical: `POST /sessions/import` accepts a
**client-supplied** session id (`routes/sessions.py:297-311`) and ids are opaque TEXT, so
`id: "../../.."` would become a path. Belt and braces: `resolve()` then
`is_relative_to(root)` — allowlist validation alone is defeated by symlinks.

⚠️ **Lowercase hex only.** This machine's APFS volume is **case-insensitive** (probed and
confirmed), so two ids differing only in case silently collapse into one directory — the
second session writes into the first's, and a reset destroys both. uuid4 hex is safe;
arbitrary imported ids are not.

**No content-addressed storage.** CAS turns delete into a refcounting or mark-and-sweep
problem and destroys the single-`rmtree` property that is the whole point, in exchange for
dedup that never fires — diffusion outputs with different seeds are never byte-identical.
**Keep `sha256` as a column** for integrity checking; reject content-addressed *naming*.

**Write order: row first, file second, status last.** Insert the job row at request time;
write to `.tmp/` → fsync the file → `os.replace()` → fsync the directory; *then* update to
`succeeded`. The only reachable inconsistency is an orphan file, which is cheap and
sweepable — never a dangling row claiming an image that is not there, which is what renders
as a broken image. Anything left in `.tmp/` is self-labelling garbage.

**A reconciliation sweeper is required, not optional.** The cost of skipping it is
documented: open-webui#31455 audits a production instance at 78 dangling links, 27 orphaned
rows, 14 physical uploads. Sweep on startup and daily: orphan files → `orphans/` (move, not
delete); rows with a missing file → `failed(result_missing)`, surfaced not hidden; `.tmp/*`
older than 1 h → delete. **Log per-category counts and a last-run timestamp** — a sweep
reporting "0 orphans" must be distinguishable from a sweep that never ran.

### Two latent DB issues to fix alongside

- **`busy_timeout` is never set** (default 0), so any lock contention raises `database is
  locked` immediately instead of waiting. Set 5000 per connection. Low blast radius,
  independent of this feature, and a job table adds write traffic.
- **`journal_mode` is `delete`, not WAL**, and backups are taken by **hand-copying the
  file**. If WAL is ever enabled, a bare copy can miss committed data in the `-wal` sidecar
  and those backups must move to `VACUUM INTO`. Flagged as a separate R10-shaped decision —
  not part of this work.

## `/reset` semantics

The evidence runs against the instinct here, and the decision is deliberate.

**Every comparable system preserves generated images when a conversation is cleared.**
Open WebUI deletes nothing on disk; LibreChat's `DELETE /api/convos` touches no files and
its proposed fix is opt-in; SillyTavern keeps a per-character gallery; ChatGPT's Library is
a separate store where deleting a chat does not remove the image. Every user complaint found
was about *failure to delete* — which only exists because survival is the default — while
the feature requests all ask for galleries decoupled from chats.

**We are deciding against that, on purpose.** Those are general-purpose assistants where
"new chat" is a workflow action. Here `/reset` means *wipe our history*, the images may be
intimate, and preserving them after an explicit wipe is the wrong default for a companion.

> **Decision: `/reset` deletes the images — to a 30-day quarantine, not `rm` — and reports
> what it did.**
>
> - Local files move to `orphans/<ts>-<media_dir>/`, swept after 30 days. A bug or a regret
>   costs a sweep, not the files.
> - Rows are deleted.
> - Telegram messages are removed via `deleteMessages`, batched 100 at a time.
> - The response body gains an `"images"` key alongside the existing `cleared` dict, and the
>   user-facing message states both numbers: how many were removed, and **how many were
>   older than 48 hours and therefore could not be removed from the chat**.

⚠️ `test_routes_sessions.py:266-281` asserts the `cleared` dict by **exact equality**, so
adding a key breaks it by design. That tripwire is working as intended — update the test
deliberately.

⚠️ **`DELETE /sessions/{id}` is the weaker sibling and will leak.** It does not call
`_clear_derived_session_state`, does not clear the FAISS index, and reports nothing. It is
used by the React UI and not by the gateway. It needs the same media handling or it becomes
the hole the images escape through.

⚠️ **Reset can race an in-flight job.** Mark the session `resetting`, cancel in-flight jobs
via their stored `backend_job_id` (ComfyUI `/interrupt`), *then* move.

## Phases

> **Phase 1 SHIPPED and verified live 2026-10-01** (M1-M5). Coordinator v0.8.0,
> gateway v0.3.0. Backend 3115 -> 3183 tests, gateway 116 -> 165. Flags armed on the
> live system; `/testimage` delivers a 256x256 gradient that arrives as a PNG preview
> at **139,325 bytes**, matching the coordinator's recorded size exactly — a JPEG
> re-encode of that gradient would have been 10-30 KB, so the bytes survived.
>
> **Rescoped during planning:** the job table, asyncio supervisor, startup sweep and
> reconciliation sweeper were CUT from phase 1 and deferred. With a fixture there is
> nothing slow to supervise, and — decisively — every test here uses `TestClient(app)`
> WITHOUT the context manager, so `lifespan` never runs under pytest and a
> lifespan-started supervisor would have shipped with zero coverage. Half its columns
> are also ComfyUI-shaped and no backend is chosen. Prerequisites before building it:
> decide `to_thread` vs a worker thread (there is no asyncio anywhere in the
> coordinator today), add a lifespan test harness, set `busy_timeout`, pick a backend,
> and measure ComfyUI's restart rate against the coordinator's.

1. **Transport, text-path only.** Media fields on `ResponseMetadata` (following the
   `proposal` / `proposal_type` precedent at `schemas.py:206-232`), the job table, the
   supervisor, the sweeper, the storage layout. No generation — a fixture PNG proves the
   pipeline. Ends with: a static test image reaches the Telegram chat losslessly.
2. **Retry and failure handling.** The gateway's first send-retry wrapper, classified per
   exception; placeholder-edit-in-`finally`; every terminal state surfaced.
3. **`/reset` and `DELETE /sessions/{id}`** media handling, with honest reporting.
4. **Wire the generation backend.** Only after the bake-off picks a model.
5. **React UI as the second consumer.** Same metadata contract.

## Invariants this work must establish

Candidates for `docs/INVARIANTS.md` with mechanical checks, following the eeva-exec
`scripts/checks/` idiom — and confirm each checker is **tracked** (`git ls-files`), per the
`.gitignore` `Scripts/` case trap.

1. **`succeeded` requires `result_path` non-null, `result_bytes > 0`, and the file present
   on disk.** The no-ambiguous-empty-success rule as a constraint.
2. **No row stays `running` across a process boundary.** The startup sweep resolves every
   foreign-`worker_run_id` row to re-attached or `orphaned`.
3. **Every terminal transition writes `finished_at`; every non-success writes a
   `failure_code`.** An empty `failure_message` on a `failed` row is itself a bug.
4. **A terminal row with `delivered_at IS NULL` is a work item, not a finished job.**
   eeva-dca's INV-1 generalises verbatim — the notification is the only evidence the user
   has that anything happened.
5. **Every Telegram send is retried**, classified: retry `RetryAfter` (honour
   `retry_after`), `TimedOut`/`NetworkError` on an *edit*; never retry `BadRequest`,
   `Forbidden`, `InvalidToken`, or `Conflict`.
6. **A photo caption carries no `parse_mode`.** `messaging.py` enforces plain text and
   disabled link previews as an anti-exfil and anti-markup-injection rule; a caption is the
   same surface and must inherit it.

On the ambiguous case — a `TimedOut` *after* a media send may mean delivered or not, and
there is no idempotency key. **We choose possible-duplicate over possible-silent-loss**, and
that trade is deliberate and written down rather than accidental. `editMessageMedia` makes
it rare.

## Verify before building

1. ~~**How an image document actually renders**~~ — **ANSWERED 2026-10-01, live on a phone.**
   It renders as a **PNG preview**, not a file-attachment row. Telegram generated the
   thumbnail server-side, exactly as `sendDocument`'s own docs allow ("can be ignored if
   thumbnail generation for the file is supported server-side").

   Three consequences, all of which REMOVE work: `sendDocument` gives lossless bytes AND
   an inline preview, so the trade this plan feared does not exist; **no thumbnail code is
   needed, ever** (Pillow stays uninstalled and the `sips` path, with its silent exit-0
   when the input is missing, is never written); and the photo+document fallback pair is
   not needed, so no second message against the ~1/sec per-chat budget.

   The premortem below is therefore **retired, not deferred**.
2. **ComfyUI's restart rate versus the coordinator's**, over a week. The whole design rests
   on re-attach; if ComfyUI restarts far more often, `orphaned` becomes the common terminal
   state rather than the rare one.

## Premortem — RETIRED 2026-10-01

> **This could fail if** the document bubble reads as a file attachment rather than a
> picture in the companion chat, making every generated image feel like a download
> instead of something she sent — in which case fidelity loses to presentation and we
> fall back to photo + document as a pair, paying a second message against the 1/second
> budget.

**It did not fail.** Verified on a real phone: the document renders as a PNG preview.
Recorded rather than deleted, because the premortem was the right thing to have written
and its cheapness to check — two minutes, one message — is the reusable lesson. Every
alternative design this plan carried for it (custom thumbnails, `sips`, Pillow, the
photo+document pair) was contingency that never had to be built.

## Sources

Telegram Bot API 10.3 (2026-08-24) — `sendPhoto`, `sendDocument`, `sendChatAction`,
`deleteMessage(s)`, `editMessageMedia`, Sending Files, Bot FAQ · core.telegram.org/api/files
(the PhotoSize table, decisive on server-side re-encoding) · tdlib/td#3034 (levlam on shared
send/edit limits) · python-telegram-bot v22.8 source — `_httpxrequest.py`, `_baserequest.py`,
`_aioratelimiter.py`, `constants.py` · Comfy-Org/ComfyUI#15965, #11540, #9330 ·
open-webui#31455, discussion#12280 · LibreChat#13590 · uvicorn server-behavior docs.
