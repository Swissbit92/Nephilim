---
title: Persona image generation — model + architecture research and decision
status: active
created: 2026-08-19
last_reviewed_on: 2026-09-30
review_in: 6 months
applies_to: nephilim
ai_summary: >
  Decision/park record for on-demand persona image generation, local and on-Mac.
  Open before ANY image-generation, diffusion-model, ComfyUI, LoRA or persona-media
  proposal: a runtime, two base-model families, a consistency mechanism and an
  integration shape are already chosen, and several plausible alternatives are
  already ruled out for recorded reasons. Read the 2026-09-30 amendments FIRST —
  they narrow the identity problem to one mode of one persona, supersede the memory
  costing with measurements, reject MCP as the integration shape, and record an
  undocumented spike on disk at ~/image-gen. Section 11 re-scans the model landscape
  and carries four corrections to the amendment itself, including the finding that
  within the Qwen family the SMALLER model scores higher. Not an implementation
  record; the ADR is written at build time.
---

# Persona image generation — research + decision (park record)

On-demand, in-character image generation for the NEPHILIM personas. This note records the
**decided direction**, the **parked option**, the **durable findings**, and the **open
questions** from a 2026-08-19 research + sparring session. **Nothing is built yet** — this is a
decision/park record, not an implementation. A formal ADR is written at build time, not before.

Research inputs: a 17-agent web sweep (runtimes, SFW/NSFW models, character consistency,
Apple-Silicon performance, licensing, integration, quantization, 2026 frontier scan incl.
Ideogram 4 / WAN) plus a `/crucible:spar-with-me` pass, reconciled against the actual persona
art in the repo.

## 2026-09-30 amendments — read before the original body

Triggered by an operator proposal to add image generation as an **MCP tool** on the ADR-008
tool-brain surface, gated to gwen and eeva, using **Qwen-Image**. Research pass: four agents
(two internal, two web) plus direct measurement of the machine. **Not a scheduled review** —
`review_in` still runs to 2027-02-19; this amends because the content moved.

Nothing below reverses the decided direction. The runtime (ComfyUI), the exclusive-mode
framing, the two-base style split and the quality pipeline all stand. What changes is the
**scope of the identity problem**, the **integration shape**, the **memory arithmetic**, and
one **ruled-out mechanism that turns out not to be ruled out**.

### 1. The spike exists on disk and stalled at zero images `[inspected]`

`~/image-gen` — 15 GB, created 2026-08-19 23:50 to 2026-08-20 00:14, i.e. the same night as
this note. In no repo, no git, no launchd plist, and referenced by no document until now.

- ComfyUI installed with its own `.venv`; `comfyui.log` confirms a clean start on
  `Device: mps`, `vram state: SHARED`, server on `127.0.0.1:8188`.
- Both decided checkpoints downloaded: `NoobAI-XL-v1.1` (6.6 GB), `ponyDiffusionV6XL` (6.5 GB).
- `gen.py` — a complete SDXL txt2img driver doing submit → poll `/history` → fetch `/view`,
  explicitly described in its own docstring as the shape the coordinator integration follows.
- `refs/gwen/` — four files (`card`, `avatar`, `logo`, `bg`).
- `ComfyUI/output/` **empty**; `outputs/` never created. ComfyUI is not running.

So the infrastructure is finished and **not one image was ever generated**. The plan called
for ~20–30 varied reference images; four were staged. `[assumed]` the blocker was the
reference bootstrap rather than anything technical — the evidence fits but is circumstantial.

**Record this directory.** A session that does not know it exists will rebuild it from zero.

### 2. The note conflated two use cases; only one has an identity problem

This record frames generation entirely as persona **self-portraiture**. Operator
clarification, 2026-09-30:

- **gwen** — both self-portraits and scenes.
- **eeva** — general illustration accompanying her explanations. **No self-portraits.**

Illustration needs no reference image, no appearance block, no LoRA and no consistency pilot.
It needs only the transport (§8). That splits the feature into tracks with different gates:

| Track | Needs | Gated on |
|---|---|---|
| Transport | artifact row, response media field, `<img>` render, file serving, `image` toolset, async job + status | nothing — ordinary work |
| eeva illustration | plain txt2img | transport only |
| gwen `scene` | plain txt2img | transport only |
| gwen `face` / `full` | reference conditioning, versioned appearance block, the pilot | the §3 experiment |

**Consequence: the "single-persona LoRA pilot before committing the recipe" first step is
superseded.** It gated the whole feature on the hardest, least certain part. The identity
track now gates *one mode of one persona*, and a visible feature ships before it.

Adopt a fixed mode enum — `face` / `full` / `scene` — with forced aspect ratios; the enum
also selects reference-on vs reference-off, so the two code paths hang off a declared value
rather than a heuristic.

### 3. The face-lock rejection is narrower than it reads — Qwen-Image-Edit is not covered

The durable finding below rules out **InstantID / IP-Adapter FaceID / PuLID** because they
depend on InsightFace/ArcFace embeddings trained on photoreal faces that do not map to
anime/cartoon proportions. **That reasoning is unchanged and still correct.**

**Qwen-Image-Edit-2511 is a different mechanism** — native image conditioning inside the DiT,
not a face-embedding adapter. It accepts up to three reference images and explicitly targets
character drift. It is therefore **not covered by the rejection** and must be judged on its
own evidence.

Why this matters more than a spec bump: **it needs 1–3 reference images, not 20–30.** That is
precisely the step at which the spike stalled (§1). The landscape change does not merely
improve the plan — it removes the thing that stopped it.

Tooling that arrived since this note: **mflux 0.20.0 (2026-09-21)** added Qwen-Image 2.1 and
Edit 2509/2511.

This **partially** meets revisit trigger (c). Evidence for Mac-native *LoRA training* on
Qwen remains thin (a single practitioner blog post). But reference conditioning may make a
LoRA unnecessary for this use — a different route to the same outcome, and a cheaper one.

> ⚠️ **CORRECTED later on 2026-09-30 — the Mac path for this is uncertain, and this section
> overstated it.** Two defects found after the amendment was committed:
> - **mflux's Qwen-2.1 port is text-to-image ONLY.** Its own README states editing,
>   multi-reference and RGBA output are not ported; those are CUDA-only today. The three
>   features that make 2.1 interesting for identity work do not run here.
> - **ComfyUI issue #16433 (OPEN, 2026-09-20): Qwen-Image-2.1 VAE *encode* is broken on MPS**
>   — 6.60 dB PSNR round-trip vs 49.10 dB on CPU, root-caused to `F.pad` in `AvgDown3D`, and
>   **not** a precision problem (`--fp32-vae` is identical). Encode is exactly the operation
>   reference conditioning depends on, and it fails *silently* — txt2img is clean, every
>   img2img path is degraded with no error. Workaround: `--cpu-vae`.
>
> Not dead: Edit-2511 is a separate 20B checkpoint with a different VAE, and
> stable-diffusion.cpp supports Edit-2509 on Metal. But "reference conditioning dissolves the
> bootstrap problem" is now a **hypothesis with a doubtful runtime path**, not a finding.
> Verify the encode path produces sane output before building anything on it.

### 4. Model selection — refined, and the version matters

The durable finding "model size ≠ anime/cartoon quality" is **unchanged** and still governs
gwen's cartoon face. It does **not** govern general illustration, where prompt adherence and
scene coherence are exactly what a large model buys.

- **eeva illustration + gwen `scene`** → **Qwen-Image-2512**, Apache-2.0, GGUF ladder
  7.8–16.9 GB. The operator's original instinct, correct for the use case this note did not
  model.
- **gwen `face` / `full`** → SDXL specialist (Pony V6) plus reference conditioning, or
  Edit-2511 if §3's experiment shows it holds her look.

⚠️ **Take 2512, not 2.1.** Everything up to and including 2512 is Apache-2.0; **Qwen-Image
2.1 (2026-09-20) is the Qwen Research License, non-commercial.** Immaterial for private use,
free to prefer, and it keeps the note's own licensing discipline intact.

> ⚠️ **CORRECTED later on 2026-09-30 — the quant recommendation above is wrong for Qwen, and
> the licence framing is now only half the story.**
> - **Q6 is the one quant to avoid on this model family.** mflux's own maintainers warn
>   "6-bit or below can degrade the image a lot more compared to Flux". Worse,
>   stable-diffusion.cpp #1385 (OPEN since 2026-04-01) has **Q4_K/Q5_K/Q4_0/Q4_1/Q5_1
>   producing solid black images** on Qwen-Image-2512 via activation overflow — only **Q5_0
>   and Q8_0** work. Use **bf16 or Q8_0 (21.76 GB transformer)**, nothing below.
> - **There is no speed penalty for that choice.** On M4, quantisation buys memory and
>   nothing else: fp8 matmul is *emulated* at 0.94× fp16 throughput (arXiv:2606.12765,
>   reverse-engineering Metal 4.1), and measured end-to-end bf16 is the *fastest* precision
>   (5.1 s/step vs 4-bit 5.8, 8-bit 6.0, 6-bit 6.8). **Run the highest precision that fits.**
> - **"2512 over 2.1" no longer holds on quality.** Independent arena Elo puts
>   **Qwen-Image-2.1 (7B) ~100 Elo ABOVE Qwen-Image-2512 (20B)** and #1 among all open-weight
>   models. Within this family, the smaller model is the better one — the licence is now the
>   only argument for 2512, not quality. See §11 for where each one does win.

Additional runtimes ruled out since: **DiffusionKit** archived by its owner 2026-03-21 (last
real commit ~Apr 2025) — do not build on it; **Fooocus** is SDXL-only by explicit policy and
will never adopt FLUX.

### 5. Memory costing superseded — measured `[executed]`

Exclusive mode stands, but its justification was asserted, never measured. Actual state of
the box on 2026-09-30:

| Measure | Value |
|---|---|
| `llama-server` RSS (the pinned 24B) | **16.57 GB** |
| Wired down, system-wide | **2.00 GB** |
| Pages inactive (reclaimable) | 20.83 GB |
| macOS memory pressure | **normal, 91% free** |
| Docker VM process RSS | 11.4 GB |
| Containers actually using | **4.72 GB** (mongo 3.31, neo4j 1.41) |
| MongoDB WiredTiger cache | **0.20 GB** used of 3.37 GB configured |

> ⚠️ **Model-size correction (later 2026-09-30):** figures quoted elsewhere in this note as
> "Qwen-Image … 33.12 GB at bf16" are **Qwen-Image-2.1 (7B)**, not the 20B. Measured from HF
> blob sizes: **2512 = 57.69 GB** bf16 (transformer 40.86 + TE 16.58 + VAE 0.25) and cannot
> load flat in 48 GB; **2.1 = 33.11 GB** (transformer 14.23 + TE 17.53 + VAE 1.35), where the
> text encoder is *larger than the transformer*. This is why text-encoder eviction, not
> quantisation, is the mechanism that decides what fits — see the Draw Things note in
> "Revised sequencing" and §11.

Three corrections:

1. **The resident model is not wired.** It is evictable; "committed" overstates it. What it
   costs is not a hard reservation but the working set everything else competes with.
2. **~5–6 GB of Docker VM slack is reclaimable at zero latency cost** — strictly cheaper than
   unpinning the LLM, and nobody had looked. This also answers the open item in
   [NEO4J_COMPONENT.md](../../../docs/architecture/NEO4J_COMPONENT.md): MongoDB is not
   claiming half of RAM, it is using 0.20 GB.
3. **The unload/reload cost is ~3–8% of a single generation.** Reload is 2–10s; a
   quality-pipeline image is 2–5 minutes; and the cost is paid only on the first chat turn
   *after* an image. Exclusive mode is therefore near-free in practice, which **strengthens**
   the original design rather than qualifying it.

Two things that did move against us since this note was written: the **162k-pageout incident**
(`config/llm.py`, 2026-08-22) postdates it, and **Neo4j has since gone live** as the system of
record for persona identity. The envelope is tighter than when exclusive mode was costed —
still adequate, but re-measure rather than re-assume.

⚠️ An unload/reload path must not trip the silent-wrong-model trap: `PERSONA_MODEL` is guarded
by `require_model_configured()` **at startup only**, not per turn.

### 6. MCP is the wrong integration shape — rejected

MCP exists so an **LLM host** can decide at runtime to call a tool. Here the FastAPI
coordinator is the caller; there is no such decision point to buy. Against that, MCP costs:

- Tool calls are **synchronous at protocol level**, and clients/proxies commonly time out at
  **30–120s** against a **30s–5min** job. The `io.modelcontextprotocol/tasks` extension
  graduated in the 2026-07-28 spec revision, but SDKs still expose it as experimental.
- The one real Draw Things MCP server in the wild is **literally a translator to the HTTP API
  you would otherwise call directly.**

**Decision: plain HTTP from Python plus a job row.** Submit → poll → fetch, the shape `gen.py`
already implements. Revisit only if something outside the coordinator must decide to generate
— and note the tool-brain surface already gives a persona that decision without MCP.

### 7. Surface wiring — the open question below is now answerable

`generate_image` must **not** join the `web` toolset alongside `image_search`, which was this
note's stated candidate. [LESSONS_LEARNED](../LESSONS_LEARNED.md) records that the ADR-004
interceptor re-enforces at **toolset** granularity: gwen is granted `image_search` and denied
`web_search`, both live in `web`, and the interceptor would have permitted the call it exists
to stop. Putting generation in `web` hands it to every persona holding any web tool, on an
uncensored local checkpoint.

**Give it its own `image` toolset.** That is the designed grant mechanism, needs no card edits
for the seven uninvolved personas, and avoids the `tools`-allowlist tripwire (only gwen and
gwen_dev carry one, and `test_legacy_path_allowlist.py` pins that by design).

⚠️ **The easiest thing to get wrong:** eeva resolves grants through `mcp_access` and has no
`toolsets` field. `toolsets` takes precedence in the registry, but `tool_interceptor` still
checks `policy.mcp not in mcp_access` — **both fields must agree or the interceptor denies
every call**, which presents as a model failure rather than a config one.

Also note `capability_scope.py` (2026-09-23) postdates this note: any new tool on a persona's
surface must be reconciled there, and `GENERAL_LOOKUP_TOOLS` deliberately excludes media tools.

### 8. The transport does not exist — this is the actual project `[inspected]`

Registry and gating are small: one `register_tool`, one definition factory, one executor
binding, one toolset, a few card edits. **Getting a picture into a message is the work**, and
none of it exists:

- `SearchResult` is `title / url / description` only — the SearXNG client **discards
  `img_src` / `thumbnail_src`**, so even `image_search` yields a page URL, never an image.
- `ResponseMetadata` has **no media/attachment field**. The only structured non-text precedent
  is `proposal` + `proposal_type`, which the UI routes to dedicated cards — that is the
  pattern to follow.
- `RichContent.tsx` is a **hand-rolled** markdown subset with no `![alt](url)` branch; an
  image markdown would render as an anchor with a stray `!`.
- The Telegram gateway is text-only — no `send_photo` anywhere.

Scope accordingly: a result type carrying an image reference, a response field, an `<img>`
render path, and a static-file or data-URI serving story.

### 9. Two mechanisms required from the first commit

**(a) The persona must not author the diffusion prompt.** She emits a small structured intent
(`framing`, `setting`, `pose`, `outfit`, `mood`); a deterministic composer assembles the final
prompt from her **versioned appearance block** plus style prefix and negative. If she writes
it freehand her appearance drifts every turn *by construction*, and consistency is lost before
any model choice matters. Precedent: [ADR-010](../decisions/010-image-search-result-quality-and-spurious-refusal-handling.md)
— the model authored an `image_search` query freely and collided on an artist's surname. Log
the final prompt with the artifact.

**(b) Explicit status, and render from the artifact row — never from her prose.** A local
diffusion backend is the archetype of a silently-failing tool: it OOMs and returns nothing.
This repo already owns the defect class — `capability_scope.py` records gwen firing
`image_search` 3/3 on a weather question and answering "103°F" **with a citation block,
because a tool had run**. The published measurements are stark: silently-failed tools produce
**45.3%** fabrication while tools that signal an error explicitly produce **0.0%**
(arXiv 2609.14758), and independent verification of the result drops false-success from
45–48% to **3%** (arXiv 2606.09863). So: `generate_image` returns
`{status, reason, artifact_id}`, and **no artifact row means the UI states the failure
regardless of what she said.** Fix it as a class, not an instance — this is the same OPEN
finding as "a tool firing is not evidence of grounding", reaching a second toolset.

### 10. Hermes / NousResearch is not a local-generation reference

Cited by the operator as a reference implementation. Verified 2026-09-30 against their own
docs: *"Hermes Agent generates images from text prompts via FAL.ai."* It is a **remote API
gateway** (FAL, OpenAI, OpenRouter, xAI), not a local pipeline; Nous does local *text*
inference only. Recording this so the reference is not re-cited as precedent for a local stack.

One idea there **is** worth taking: the agent emits a `MEDIA:<url>` tag that per-platform
adapters convert to native media — one mechanism serving both the React UI and the Telegram
gateway.

### Revised sequencing

1. **Pilot in `~/image-gen`, no nephilim code.** Baseline Pony V6; then test identity two
   ways — SDXL + reference conditioning, and Qwen-Image-Edit-2511 with gwen's existing avatar
   — ~30 images each, eyeball consistency. Also yields the real per-image time on this box:
   the only published M4 Pro/48 GB figure (~13 s/step, ~5 min/image at 1024²) is low-authority
   and is the weakest load-bearing number here. Note that on Apple Silicon **quantization buys
   memory, not speed** — now confirmed with a mechanism (fp8 matmul is emulated at 0.94× fp16,
   arXiv:2606.12765) and end-to-end, where bf16 is the *fastest* precision measured. So take
   **bf16 where it fits, else Q8_0**, and never Q6-or-below on Qwen (§4). Run the four-way
   bake-off in §11, not just Pony V6 — the comparison it settles has never been published.
2. **Build the transport** (§8). Independent of the pilot's outcome.
3. **Ship eeva illustration + gwen `scene`.** Visible feature, no identity machinery.
4. **Identity track for gwen `face`/`full`**, gated on step 1.

Do not re-platform to Draw Things before step 1 produces an image. Its headless
`gRPCServerCLI` with an A1111-compatible HTTP API is a genuine improvement (~20% faster than
ComfyUI, official Qwen-Image-2512 support) — but ComfyUI is already installed and verified
working here, and switching runtimes before generating anything is a cost with no measurement
behind it.

> ⚠️ **REVERSED later on 2026-09-30.** The "~20% faster" framing was the wrong reason to
> consider Draw Things and led to the wrong call. The real reason is **sequential stage
> eviction**: Draw Things encodes the prompt, *evicts the text encoder from memory*, then
> loads the DiT. That is the only thing that makes a 20B model at **bf16** fit this machine
> (~30 GiB peak — Draw Things' own preset targets "48 GiB+ total, M3+", i.e. exactly this
> box). ComfyUI's memory manager reads free RAM with **no notion that GPU and OS share the
> pool**, and mflux never quantises the text encoder at all, which is why Qwen-2.1 at bf16
> peaks ~46 GB there and does not fit. This is not a speed preference — it decides whether
> the high-quality configuration is reachable.
>
> ⚠️ But verify before committing: **Draw Things #130 is a reproducible Metal MFA failure on
> M4 Pro / macOS 26.6.2** — this exact chip and OS family — and **#136 (Q8 fp16 overflow on
> Qwen-2512, NaN latent, no image) was filed 2026-09-30**, the same day. Run it before
> building on it.

### 11. Model landscape re-scanned 2026-09-30 (three-agent pass)

The scan was asked for on a "quality and size over small and fast" brief. **It returned the
opposite of what that brief assumed, from three independent directions:**

1. **Within Qwen, the smaller model is the better one.** Independent arena Elo puts
   Qwen-Image-2.1 (7B) ~100 above Qwen-Image-2512 (20B), and #1 among all open weights.
2. **The specialisation gap still holds** — confirmed by an agent that was not told it was
   the standing position, and by a hands-on anime test published 2026-09-23, three days
   after 2.1's release, concluding it "might not be good at anime-style illustrations."
3. **The industry agrees.** *Anima* (2B, 2026-05-15) is a modern DiT trained only on anime,
   which exists precisely because scaling general models on general data does not deliver
   this aesthetic.

**Per-category arena data** (parsed directly from server-rendered rows; the category boards
were purpose-built in Feb 2026 from 4M+ real prompts, so this is a designed instrument):

| Model | Cartoon | Photoreal | Δ | Read |
|---|---|---|---|---|
| Qwen-Image-2.1 | 1227 | 1235 | −8 | no stylised penalty; **#1 open on "Art"** |
| FLUX.2-dev | 1143 | 1143 | 0 | neutral; **underperforms on Portraits** |
| Qwen-Image-2512 | 1126 | 1135 | −9 | neutral overall; **6th in Portraits** vs #13 overall |
| Z-Image-Turbo | 1068 | 1118 | **−50** | ⚠️ photoreal specialist — **do not pick** |

Z-Image is the trap: best Apple-Silicon support of any Chinese model, worst stylised penalty
on the board.

⚠️ **The decisive comparison has never been made by anyone.** No anime specialist appears on
any arena — zero entries for Illustrious, NoobAI, Pony, Animagine, NovelAI — and Civitai
publishes downloads, not preference scores. The frontier arenas and the anime community are
two disjoint measurement universes. **Nobody has run Illustrious against Qwen-Image-2.1 in
one blind instrument**, so this must be settled locally, per `eval-first`.

Other corrections from the scan:

- **Qwen-Image-3.0 (2026-07-21) is closed** — API-only, no weights, no published benchmarks.
  The newest *open* Qwen is 2.1. The line went open → closed.
- **Seedream, not Seedance.** Seedream is ByteDance's image line (now 5.0 Pro); Seedance is
  their video line. **Both have been closed at every version** — no weights ever released.
  Reference point only.
- **FLUX.2-dev is out**: 112.81 GB at bf16; at Q4 it pins ~37.7 GB because its 24B Mistral
  text encoder never evicts (Draw Things #113 is an *open feature request* for that); its
  Exact/f16 path has produced woven-texture garbage since 2026-03-16 with no maintainer
  response (#57); and it underperforms on Portraits regardless.
- **Memory figures, measured from HF blob sizes:** Qwen-Image-2512 (20B) is **57.69 GB** at
  bf16, *not* the 33.12 GB quoted in §5 — that figure is **Qwen-Image-2.1 (7B)**, where the
  text encoder (17.53 GB) is larger than the transformer (14.23 GB).
- **The open↔closed gap is ~160–200 Elo** and is not closing. Not actionable, but it sets
  expectations against the closed models the personas will be compared to.

**Candidates for the bake-off in step 1** — all four, since the comparison does not exist:

| Candidate | Config | Peak | Time/image |
|---|---|---|---|
| Qwen-Image-2512 (20B) | bf16, Draw Things | ~30 GiB | 3.5–8 min *(wide error bar)* |
| Qwen-Image-2.1 (7B) | q8, mflux | ~30.7 GB | ~8 min *(scaled, not measured)* |
| Illustrious / NoobAI SDXL | fp16 | ~7 GB | **20–40 s (measured on this GPU)** |
| Anima (2B, anime-native) | 8-bit S, Draw Things | small | fast |

**Prediction, recorded so it can be wrong:** a Qwen model wins eeva's illustration and gwen's
painted/`scene` work; an SDXL specialist or Anima wins gwen's character portraits. The
original two-base split survives — the bases change.

## Decided direction (local, on-Mac, occasional/exclusive-mode)

Generation is an **occasional, ad-hoc action** invoked by persona behavior and/or user prompt —
not an always-on service. It therefore runs in **exclusive mode**: the resident ~17 GB companion
LLM may be briefly unloaded during a generation session, freeing the full 48 GB. This removes the
memory pressure that would otherwise force small models and cheap sampling.

- **Style scope (decided 2026-08-19 — keep distinct, two bases):** each persona keeps her own
  established look rather than unifying to one house style. gwen → cartoon/NSFW (Pony); eeva and
  all other personas → anime (Illustrious/NoobAI). Per-persona LoRAs make two bases cheap to run.
- **Serving runtime:** **ComfyUI**, run headless as a launchd-style local HTTP/WebSocket server,
  driven by the FastAPI coordinator (templated workflow-JSON per persona; submit → poll/`/ws` →
  fetch PNG; async job, never a synchronous route — a single quality-pipeline image is tens of
  seconds).
- **SFW personas (Japanese-anime look):** **Illustrious-XL / NoobAI-XL** (SDXL-family anime
  specialists) — native fit for the gacha-anime persona art.
- **gwen (NSFW, Western-cartoon look):** **Pony Diffusion V6 XL** — *not* an Illustrious-lineage
  NSFW checkpoint. Pony is the native base for Western-cartoon / painted / NSFW art; the style
  match outweighs the convenience of a single shared model family. This deliberately **overturns**
  the initial web-research recommendation (WAI-NSFW-illustrious for lineage uniformity).
- **Per-persona consistency:** one trained **character LoRA per persona**, trained **on-device via
  Draw Things** (Metal-native SDXL trainer, ~10 GB at 512px / ~20 GB at 768px — comfortable in
  exclusive mode). This is the load-bearing mechanism.
- **Quality pipeline:** because exclusive mode frees memory and speed is explicitly deprioritized
  (quality > speed), run the full pipeline — high-res generation → upscale pass → refiner pass →
  dedicated face-fix (ADetailer-style). For this art, this is a larger quality gain than swapping
  to a bigger base model.

**First step when building:** a **single-persona pilot** — bootstrap ~20–30 varied reference
images of one persona (current art is too thin to train on directly), train her LoRA, generate
~30 test images, eyeball consistency — *before* committing the recipe to all six. The
"recognizably herself across many generations" bar is unproven for this art until this pilot runs.

> ⚠️ **Superseded 2026-09-30 — see §2 and "Revised sequencing" above.** This gated the whole
> feature on its hardest part. Illustration (eeva, and gwen's `scene` mode) has no identity
> problem and ships on the transport alone; the pilot now gates only gwen's `face`/`full`
> modes. The reference bootstrap this step describes is also, on the evidence in §1, what
> stalled the attempt — and §3 removes the need for it.

## Parked option (revisit later) — cloud-trained big-model quality

Explicitly deferred, not rejected. The **one-time cloud-GPU LoRA training** step (~$5–20/persona,
one-time) would unlock the higher-quality frontier bases whose LoRA training is unreliable on
Apple Silicon:

- **SFW:** Qwen-Image (20B, Apache-2.0, Q8 ≈ 22 GB) — strongest coherence/prompt-adherence of the
  Mac-runnable anime-capable frontier models; large adapter ecosystem.
- **NSFW:** Chroma1-HD (FLUX-class, Apache-2.0, uncensored-by-design) — highest raw fidelity for
  the NSFW persona, but weakest Mac tooling today (MPS-only, no MLX port) → slow inference.

**Revisit triggers:** (a) the on-Mac SDXL + quality-pipeline output proves insufficient in
practice; (b) willingness to spend the one-time cloud-training cost; or (c) a Mac-native LoRA
trainer for FLUX/Qwen matures. The operator declined the cloud step for now (2026-08-19).

## Durable findings

- **The personas span TWO art styles, not one.** SFW personas (e.g. nyx) are Japanese-anime /
  gacha style; gwen is a Western-cartoon / painted (NSFW) style from a different source. **No
  single base model natively serves both** — this is the core reason the SFW and NSFW paths use
  different model families, and it is invisible to any web research that assumes "6 anime personas".
- **Model size ≠ anime/cartoon quality.** SDXL (~2.6B) anime/cartoon specialists beat much larger
  general models (FLUX 12–32B, Qwen 20B) at *this* aesthetic, because the big models spent their
  capacity on photoreal coherence and prompt-following. Quality here comes from **specialization +
  a refinement pipeline**, not base-model parameter count.
- **On-Mac LoRA training is SDXL-only in practice.** Draw Things trains SDXL LoRAs on-device
  reliably; FLUX/Qwen LoRA training on Apple Silicon (MPS) is documented as unreliable
  (ai-toolkit self-labels Mac support experimental; FLUX convergence failures on MPS). Big-model
  character consistency therefore *implies* cloud training — hence the park.
- **Face-lock adapters are a dead end for this art.** InstantID / IP-Adapter FaceID / PuLID all
  depend on InsightFace/ArcFace embeddings trained on photoreal human faces, which do not map onto
  anime/cartoon proportions (acknowledged upstream). Plain (non-face) IP-Adapter is usable only as
  a loose pose/composition helper, not an identity lock. Identity = the LoRA.
  > ⚠️ **Scope narrowed 2026-09-30 — see §3.** This holds for the three named adapters and the
  > reason is unchanged. It does **not** cover **Qwen-Image-Edit-2511**, which conditions on a
  > reference image natively inside the DiT rather than through a face embedding, and needs
  > 1–3 references instead of 20–30. "Identity = the LoRA" is now an open question, not a
  > finding.
- **Frontier scan — mostly not Mac-actionable today.** Ideogram 4 did surprise-release open
  weights (June 2026) but is **non-commercial + likely CUDA-blocked** (nf4/bitsandbytes) — stays a
  cloud/curiosity option, never for the NSFW persona (cloud ToS forbids it). **WAN (Wan2.1/2.2) is
  CUDA-only and video-first** — ruled out for local stills; noted only as a *future animated-clip*
  angle for the [ADR-013](../decisions/013-embodied-companion-voice-before-face-live2d-primary-3d-desktop-as-a-separate-track.md)
  embodiment/Live2D track if a Mac port ever appears.
- **Licensing flags to remember** (fine for personal use, would bite on any future monetization):
  NoobAI-XL bans all commercialization; SD3.5 forbids NSFW even self-hosted; FLUX.1-dev / Ideogram 4
  are non-commercial. Cleanest: Animagine XL, Pony V6 (personal-use free), Qwen-Image/Chroma
  (Apache-2.0). Read the exact checkpoint license at download time — Illustrious's terms changed
  between versions.

## Open questions (not yet decided)

- **Exact checkpoints/merges** for each family (settle at build time). *Partly answered
  2026-09-30 — see §4: Qwen-Image-2512 (Apache-2.0, not 2.1) for illustration and `scene`;
  Pony V6 remains the base for gwen's face.*
- ~~**Trigger + surface wiring:**~~ **Answered 2026-09-30 — see §6, §7, §8.** Not MCP: plain
  HTTP plus a job row. Its own `image` toolset, **never** `web` (the candidate named here
  would have leaked it to every web-capable persona). And the rendering half of this question
  turns out to be the bulk of the work — no image transport exists anywhere in the stack.

**Open after the 2026-09-30 pass:**

- **Does reference conditioning hold gwen's look?** (§3) The single load-bearing unknown, and
  now cheap to test. If yes, the LoRA track closes and Qwen becomes the answer for both
  personas and all modes.
- **Real per-image time on this box.** No credible primary benchmark exists for any
  Qwen-Image variant on an M4 Pro / 48 GB.
- **Retention policy for generated images.** No documented practice found in any comparable
  system; ~1–2 MB per PNG against ~608 GB free makes it a filing question, not a capacity one.

## Key sources

Runtimes/perf: Draw Things engineering blog (on-device SDXL LoRA trainer), ComfyUI API docs.
Models: OnomaAIResearch/Illustrious-XL-v2.0, Laxhar/noobai-XL-Vpred-1.0, Pony Diffusion V6 XL,
lodestones/Chroma1-HD, Qwen/Qwen-Image (+ mflux, mlx-community quants), circlestone-labs/Anima.
Consistency: InstantID GitHub #203 (anime face-ID failure), AnimeAdapter (arXiv 2605.20237).
Frontier: bfl.ai FLUX.2/FLUX 3, Tongyi-MAI/Z-Image-Turbo, ideogram-ai HF org, Wan-Video/Wan2.2.
