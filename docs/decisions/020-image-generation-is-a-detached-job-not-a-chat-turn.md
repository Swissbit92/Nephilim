---
title: Image generation is a detached job, not a chat turn
status: Accepted
created: 2026-10-02
last_reviewed_on: 2026-10-02
review_in: 24 months
applies_to: nephilim
ai_summary: "Why image generation runs as a detached subprocess with a durable job table and out-of-band delivery instead of inside a chat turn — 331s per image on a 48 GB box the companion model already occupies. Read before touching the image-gen worker, the resource arbiter, or the notification path."
---

# ADR-020: Image generation is a detached job, not a chat turn

## Status

Accepted — 2026-10-02. Shipped behind `IMAGE_GEN_ENABLED`, default off.

## Context

gwen and eeva should be able to make a picture. The obvious shape — a tool that generates
an image and returns it in the reply — is impossible here, for three reasons all measured
on this machine rather than assumed.

**A generation takes 331 seconds.** Measured end to end: Qwen-Image-2.1 at 1024×1024,
25 steps, 8-bit, through mflux on the M4 Pro. The bake-off predicted 332. The Telegram
gateway's HTTP client gives up at 180 s, and every coordinator route is a synchronous `def`
running in the anyio threadpool — so a turn that waited would both time out and hold a pool
worker for five and a half minutes.

**It needs the whole machine.** The box has 48 GB. The companion model is pinned resident
at 16-19 GiB (it varies by ~2.5 GiB between loads; it is not a constant) and a generation
peaks around 20 GB. Both at once does not fail cleanly: it swap-storms, and the symptom is
a Mac that stops responding for twenty minutes. Ollama has no admission-control API — any
request reloads the model, and it cannot be told to refuse.

**The coordinator restarts.** launchd's KeepAlive means a 331-second job must survive the
process that started it, which rules out any in-memory queue.

## Decision

Image generation is a **detached subprocess tracked by a durable job row**, with the result
delivered **out of band**. Five parts:

1. **A supervisor** spawns the generator with `start_new_session=True` and an inherited
   `flock`, so it survives a coordinator restart and its liveness is still readable
   afterwards.
2. **A resource arbiter** takes an exclusive lease on the machine, evicts the chat model,
   and refuses every path that would reload it for the duration.
3. **A job table in SQLite** is the queue, because the queue has to survive a restart.
4. **A worker thread** polls that table and runs one job at a time.
5. **A notification endpoint** the Telegram gateway polls, returning a chat-shaped payload
   it already knows how to deliver.

The persona emits **structured intent** (subject, setting, mood, style) and code composes
the diffusion prompt. It never writes the prompt itself.

## Consequences

**gwen is unavailable for about five and a half minutes per image.** This is the real cost
and it is not hidden: the arbiter unloads her model, and chat turns during the window are
refused with an honest message rather than queued — queueing would hand the user a timeout
instead of a reply. Eviction costs 0.17 s and the reload 6.4 s, so the overhead either side
is ~2% of the job.

**Three flags must all be on** for anything to happen: `IMAGE_GEN_ENABLED`,
`IMAGE_GEN_DEV_ENDPOINT` (for the manual trigger) and `TG_MEDIA_ENABLED`. All default off,
and [INV-2](../INVARIANTS.md) fails the build if that drifts.

**The chat path grew a branch.** `routes/chat.py` is the hottest code in the repo, so the
edit is flag-gated and a test asserts the offered tool surface is *identical* with
generation off — the same discipline ADR-019 uses, where byte-identity with the flag off is
the boundary between the safe change and the risky one.

**A generated image belongs to its session.** The worker stores the finished PNG under the
session's media directory rather than leaving it in the scratch job directory, so the
existing `/reset` quarantine covers it with no new deletion code.

## What was rejected, and why

**MCP.** The original request was for an image-generation MCP tool. MCP is synchronous with
client timeouts of 30-120 s against a 331-second job, and the backend is the caller rather
than the client — the integration shape does not fit. Rejected before anything was built.

**`sendPhoto`.** Telegram re-encodes photos to JPEG and flattens alpha. `sendDocument`
preserves the bytes, and the user confirmed it still renders as a preview on a phone.

**Blocking the chat turn.** Covered above: a guaranteed timeout on a *successful*
generation.

**A negative prompt.** mflux only runs the negative pass with `guidance > 1`, and
Qwen-Image-2.1 is trained to be sampled without guidance. Enabling CFG to make negatives
work would change the sampling regime and double a 331-second job.

**Letting the persona write the diffusion prompt.** Every production system found inserts a
composition step — DALL-E 3 rewrites through GPT-4 and returns `revised_prompt`, Fooocus
expands through a fine-tuned GPT-2, T2I-Copilot interprets into a structured report. Ours
is deterministic rather than stochastic like Fooocus's, because "it drew something I didn't
ask for" is indistinguishable from a bug in a companion.

**Putting `generate_image` in the `web` toolset.** It would have been less work — the chat
path filters hard on `toolset == "web"` — but web tools read the internet and this one
spends five and a half minutes of the whole machine. Grouped, any persona granted search
would silently also hold a GPU job.

## What this cost to learn

Four claims made during the work were measured **wrong** and corrected in place. They are
recorded because each is a class of error rather than an incident.

**`mx.set_memory_limit()` does not raise.** It is advisory: 4 GiB allocated against a 2 GiB
limit succeeded silently on mlx 0.32.3. The first version of the wrapper claimed it
"converts an overrun into an exception the supervisor can report" — written from the API's
shape, never tested. The real protection is an external watchdog that can kill the process.
MLX's default limit here is also 45.60 GiB, not the ~56 GiB claimed from a 1.5× rule this
build does not use.

**The memory floor was set by argument and would have killed successful jobs.** 6.0 GiB,
reasoned. A healthy generation bottoms at **5.28 GiB** during the final VAE decode — the
true peak, not the denoise loop, which sits at 19.2 GiB. The watchdog entered its kill
grace at t≈310 s and the job finished at 331 s: it survived by about a second. Refitted to
3.0 GiB against the measurement. No test could have found this; every watchdog test passed,
because they all used numbers reasoned into existence by the same person who wrote the
code.

**Both obvious memory instruments are blind here.** `ps` RSS reported 0.03 GiB while MLX
held 8.00 GiB, and 8 MiB while Ollama held 16.40 GiB — Metal buffers are not in RSS.
`kern.memorystatus_vm_pressure_level`, which the research recommended as the primary
signal, stayed NORMAL from 13.17 GiB free down to 7.23 GiB. Only `vm_stat` free+inactive
tracked both honestly.

**Ollama's unload response is not evidence.** Sent mid-generation it returns
`{"done_reason": "unload"}` in 2 ms while the model stays fully resident until the
in-flight request finishes. This is the third instance of one shape in this ecosystem: a
success response that is not evidence of the outcome (mflux exits 0 having written nothing;
a failed KuCoin call rendered as `+0.00 funding` for six days). Eviction is now confirmed
by polling for absence, never by the reply.

Two smaller ones worth keeping. The duplicate-request guard's threshold was 0.8 by feel and
**missed the case it exists for** — a reworded repeat scores 0.600 — and is now fitted to a
measured gap with both populations pinned. And the cooldown used `0.0` as a "never started"
sentinel, so a legitimate monotonic `0.0` read as falsy; in production the clock is never
0.0, so it would have been invisible forever, and it was caught only because a test
injected it.

## Revisit when

- **A second GPU workload appears.** The arbiter assumes one heavy tenant; two would need a
  real scheduler rather than a lease.
- **The React UI needs images.** `MediaItem.path` is an absolute local path, which works
  only because every consumer runs on this host. The UI will need a `url` field **added**,
  not this one repurposed — the gateway reads the file directly and always will.
- **Generation drops below the gateway's 180 s client timeout.** The whole out-of-band path
  exists because of that gap; a faster model would make a synchronous reply possible again.
