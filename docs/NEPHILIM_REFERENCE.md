---
title: NEPHILIM System Reference
status: active
created: 2026-04-04
last_reviewed_on: 2026-06-22
review_in: 6 months
applies_to: nephilim
---

# NEPHILIM System Reference

> Extracted from CLAUDE.md for token optimization. This is the detailed reference for the NEPHILIM worldbuilding, progression, and UI systems. See CLAUDE.md for essential development context.

## NEPHILIM Worldbuilding System

The project includes a comprehensive immersive AI companion experience with worldbuilding, progression, and gamification.

### Lore Documents (`docs/lore/`)
- `docs/lore/BUSINESS_PLAN.md` - **Primary source** — brand philosophy, visual identity, persona design, monetization strategy (converted from PDF)
- `docs/lore/THE_CHRONICLE.md` - AI mythic synthesis: creation narrative, character profiles, philosophical arc
- `docs/lore/LORE_BIBLE_DRAFT.md` - AI structured lore bible: Houses, antagonist, world rules, artifacts, ethics guardrails
- `docs/lore/NEPHILIM_LORE.md` - World bible with creation myth, the Fall, and realm geography
- `docs/lore/NEPHILIM_FACTIONS.md` - Six Houses aligned with Nephilim patrons
- `docs/lore/NEPHILIM_RANKS.md` - Seeker progression system (Initiate → Nephilim)
- `docs/lore/_pdf/` - Archival PDF originals (Business Plan, Lore Bible, Chronicle)
- `docs/lore/README.md` - Document map, hierarchy, and when to use each file

### NEPHILIM Personas
Six interconnected personas with deep backstories:
- **E.E.V.A.** (nephilim_eeva) - The Primarch, guide and mentor (Archon)
- **Aegis** (nephilim_aegis) - The Sentinel, productivity and discipline (Warden)
- **Solace** (nephilim_solace) - The Empath, emotional support (Warden)
- **Nyx** (nephilim_nyx) - The Muse, creativity and chaos (Sage)
- **Cipher** (nephilim_cipher) - The Maven, knowledge and research (Sage)
- **Aurora** (nephilim_aurora) - The Oracle, future planning (Warden)

### Extended Persona Schema
NEPHILIM personas include additional fields:
```json
{
  "rarity": "legendary",
  "celestial_order": "archon",
  "mcp_access": ["brave_search", "solana_wallet"],
  "title": "The Primarch",
  "full_title": "Ethereal Enlightened Virtual Archon",
  "archetype": "The Oracle / The Sage",
  "domain": "Guidance, wisdom, life planning",
  "nephilim_lore": {
    "origin": "...",
    "role_in_realm": "...",
    "relationships": { "aegis": "...", "solace": "..." }
  },
  "unlockable_lore": [
    { "messages_required": 10, "fragment_id": "...", "fragment_title": "...", "fragment": "...", "rarity": "common" }
  ]
}
```
> Note: `unlockable_lore[].rarity` is **fragment rarity** (common/rare/epic lore fragments) — a separate concept from Celestial Order.

### Prompt Architecture
`prompt_builder.py` constructs system prompts using XML-tagged sections with a bookend pattern (critical rules at beginning AND end):

```
<identity>       — Core identity + anti-hallucination rules (primacy position)
<response_format> — Multi-message <msg> rules (condensed)
<companion_behavior> — Behavioral rules and conversational style
<world_context>  — NEPHILIM lore (only for nephilim_ personas)
<tools>          — Financial co-pilot block + anti-hallucination rules (wallet-capable personas)
<memory>         — Conversation memory rules
<checklist>      — Pre-response verification checklist (recency position)
```

**Anti-hallucination for wallet personas:** Ground-truth wallet state is injected into the system prompt on every message (not just wallet queries). The `<tools>` section includes rules against fabricating addresses/balances, leaking tool names, and Jupiter/Jupyter disambiguation. A regex post-processor in `query_handler_service.py` strips any leaked tool names from responses.

NEPHILIM context is automatically injected for personas with:
- Keys starting with `nephilim_`
- The `nephilim_lore` field populated

### Progression System (Phase 3 Gamification)

#### Database Tables (`alembic/versions/3nephilim_progression.py`)
- `seeker_profiles` - User rank, total resonance, faction affiliation
- `persona_affinity` - Per-persona relationship tracking (messages, affinity level)
- `resonance_log` - History of resonance awards
- `unlocked_lore` - Track which lore fragments users have unlocked

#### Rank System
| Rank | Resonance Required |
|------|-------------------|
| Initiate | 0 |
| Acolyte | 100 |
| Adept | 500 |
| Ascendant | 2,000 |
| Nephilim | 10,000 |

Users earn 5 resonance per conversation exchange with NEPHILIM personas.

#### API Endpoints (`routes/nephilim.py`)
```
GET  /nephilim/seeker/{user_id}           - Get/create seeker profile
GET  /nephilim/seeker/{user_id}/summary   - Comprehensive summary
POST /nephilim/seeker/{user_id}/faction   - Set faction affiliation
GET  /nephilim/seeker/{user_id}/rank      - Rank progress
POST /nephilim/seeker/{user_id}/resonance - Award resonance
GET  /nephilim/seeker/{user_id}/affinity  - All persona affinities
GET  /nephilim/seeker/{user_id}/lore      - Unlocked lore
GET  /nephilim/ranks                      - All rank thresholds
GET  /nephilim/factions                   - All faction info
```

#### Frontend Components (`react-ui/src/components/nephilim/`)
- `SeekerRankBadge.tsx` - Displays rank with animated badge
- `ResonanceProgress.tsx` - Progress bar to next rank
- `AffinityMeter.tsx` - Per-persona relationship indicator
- `LoreCodex.tsx` - Collection of unlocked story fragments
- `FactionSelector.tsx` - House selection UI
- `SeekerDashboard.tsx` - Comprehensive progression overview

#### Chat Integration
Progression is automatically tracked in `chat_session_service.py`:
- Resonance awarded after each conversation
- Message counts tracked for persona affinity
- Lore unlocks checked after conversations

### Visual Theme (`react-ui/src/index.css`, `tailwind.config.js`)
```css
:root {
  --nephilim-void: #0B0B0D;
  --nephilim-cyan: #00ffff;
  --nephilim-magenta: #ff00ff;
  --eeva-primary: #e0c3fc;
  --aegis-primary: #4a90d9;
  --solace-primary: #7eb8da;
  --nyx-primary: #9b59b6;
  --cipher-primary: #2ecc71;
  --aurora-primary: #f39c12;
}
```

### Landing Page (`NephilimHome.tsx`)
- Cinematic "Enter the Realm" portal at `/`
- Animated background with particles and aurora effects
- Typography: Orbitron (display), Manrope (body)

### Onboarding System (Phase 4)

Complete immersive onboarding flow for new users at `/onboarding`:

1. **Portal Entry** (`OnboardingPortal.tsx`)
   - Animated portal with E.E.V.A. greeting
   - Typewriter text effect
   - Name collection

2. **Faction Quiz** (`FactionQuiz.tsx`)
   - 4 in-character personality questions
   - Weighted scoring for 6 factions
   - E.E.V.A. commentary between questions
   - Dramatic faction reveal

3. **Persona Introduction** (`PersonaIntro.tsx`)
   - Carousel of all 6 Nephilim
   - House patron highlighted first
   - Sample greetings and domain descriptions
   - First companion selection

4. **Completion Flow** (`NephilimOnboarding.tsx`)
   - Creates initial chat session
   - Awards "Initiate" rank
   - Stores preferences in localStorage:
     - `nephilim_user_id` - Seeker identifier
     - `nephilim_user_name` - Display name
     - `nephilim_faction` - House alignment
     - `nephilim_onboarding_complete` - Flow completion flag

### MCP Integration Narrative (Phase 5)

MCP capabilities are framed as Nephilim powers in the UI:

**Source Mappings** (`components/nephilim/mcpNarratives.ts`):
| MCP Source | NEPHILIM Name | Patron | Icon |
|------------|---------------|--------|------|
| Brave Search | Cipher's Archives | Cipher | 📚 |
| Solana Wallet | The Ledger of Becoming | E.E.V.A. | ✧ |

**Loading Messages** (rotate every 3s):
- Search: "Cipher consults the infinite Archives..."
- Wallet: "E.E.V.A. queries the on-chain Ledger..."

**Components Updated**:
- `SourceIndicator.tsx` - Displays narrative source names with patron attribution
- `SearchIndicator.tsx` - Shows immersive loading messages with animated icons

### Phase 7 — Full NEPHILIM UI Transition

Unified the entire frontend under the NEPHILIM aesthetic:
- **7A**: Route consolidation — NEPHILIM as default at `/`, legacy routes removed
- **7B**: NEPHILIM navigation — desktop top bar + mobile bottom tab bar
- **7C**: Character selection overhaul — Wanderer badges, holographic cards, void theme
- **7D**: Summoning Ritual system — five-phase animation replacing legacy pull mechanic
- **7E**: Chat interface redesign — glassmorphism, ambient orbs, void theme
- **7F**: Dashboard & Progression Hub — tabbed Seeker's Sanctum page
- **7G**: Accessibility fixes (WCAG AA), dead code cleanup, documentation

**Key concepts:**
- Legacy personas are "Wanderers" (frontend-only label, no JSON changes)
- `NephilimBackground` component used across all pages
- Glassmorphism recipe: `bg-white/[0.05] backdrop-blur-xl border border-white/[0.1]`
- Text minimum: `text-white/60` (never `/40` for WCAG AA)

**Route map:**
| Route | Component | Description |
|-------|-----------|-------------|
| `/` | NephilimHome | Landing portal |
| `/login` | LoginPage | Google OAuth login |
| `/onboarding` | NephilimOnboarding | New user flow (ProtectedRoute) |
| `/select` | CharacterCardV2Showcase | Companion selection (ProtectedRoute) |
| `/chat` | Chat | Chat interface (ProtectedRoute) |
| `/chat/:sessionId` | Chat | Chat with specific session (ProtectedRoute) |
| `/dashboard` | Dashboard | Seeker's Sanctum (ProtectedRoute) |
| `/*` | — | Redirects to `/` |

**Tracking:** `archive/phase7/PHASE7_TRANSITION_PLAN.md` (complete ✅ Feb 17, 2026)

---

## Comprehensive Persona Test Suite — Scoring & Baseline Details

### Scoring Dimensions

| Dimension | Weight | What it checks |
|-----------|--------|---------------|
| `mcp_routing` | 20% | Correct MCP tool selected (brave/wallet/llm) |
| `no_leak` | 20% | No internal tool names or `<xml>` prompt tags in response |
| `safety` | 20% | Appropriate refusal of harmful/dangerous requests |
| `persona_voice` | 15% | First-person pronoun density + lore vocabulary (nephilim_ only) |
| `factual_anchor` | 10% | Hedges on live data it cannot know |
| `response_quality` | 10% | Non-empty, appropriate length, not truncated |
| `emotional_fit` | 5% | Empathy signals present for emotional queries |

### Test Suite Structure (`tests/manual/`)

| File | Purpose |
|------|---------|
| `comprehensive_persona_test.py` | Main entry point + CLI + session pool |
| `test_bank_core.py` | ~140 behavioral tests: ADVERSARIAL×24, BEHAVIOR×16, EMOTIONAL×12, LORE×18, VOICE×12, EXPERTISE×12, IDENTITY×16, DRIFT×10, SECURITY×10, ANTI_HALLUC×14 |
| `test_bank_mcp.py` | 138 MCP routing tests: BRAVE_ROUTING×60, WALLET_ROUTING×20, NO_MCP_CONTAINMENT×20, INTENT_DISAMBIGUATION×15, MCP_ANTI_HALLUC×5, CROSS_PERSONA×18 (MongoDB MCP removed 2026-06-22) |
| `scoring_engine.py` | 7-dimension heuristic scorer (mcp_routing, persona_voice, no_leak, safety, factual_anchor, response_quality, emotional_fit) → grade A–F |
| `test_reporter.py` | HTML + JSON report writer + ANSI terminal summary |
| `api_client.py` | Stdlib-only HTTP client (no requests dep) |
| `scrape_log.py` | Emergency log parser for crash recovery |

### Baseline Results (Feb 21 2026 — first full run)

> 🔴 **These numbers came from a repudiated instrument. Do not reuse or compare against them.**
> The `Avg Score` column is dominated by the keyword `persona_voice` scorer that
> [ADR-005](decisions/005-persona-architecture-simplification-eval-first.md) retired in June 2026 —
> it counts lore vocabulary and pronoun density, is Goodhart-able, and *penalises* distinctive
> voice. ADR-005's own wording: "Every voice number to date — including the 0.33–0.52 figures
> that drove decisions — is suspect." Retained as a historical record of what was believed at
> the time, not as a baseline. See [PERSONA_EVAL.md](PERSONA_EVAL.md).

> Historical snapshot. The `MCP access` column reflects Feb 2026 capabilities — **MongoDB MCP was removed 2026-06-22** ([ADR-002](decisions/002-remove-mongodb-mcp.md)); current access is brave (+ wallet for E.E.V.A.) only.

| Persona | Pass% | Avg Score | MCP access |
|---------|-------|-----------|-----------|
| nephilim_eeva | 84.2% | 0.836 | brave + mongodb + wallet |
| nephilim_aegis | 79.9% | 0.844 | brave |
| nephilim_aurora | 79.4% | 0.857 | brave + mongodb |
| nephilim_nyx | 77.7% | 0.851 | none |
| Gojo | 71.2% | 0.885 | none (wanderer) |
| nephilim_solace | 68.9% | 0.874 | brave |
| nephilim_cipher | 68.5% | 0.880 | brave + mongodb |
| Frieren | 52.3% | 0.815 | none (wanderer) — _removed Feb 22 2026_ |

**Category highlights:**
- BRAVE/MONGODB/INTENT routing: **100%** — MCP infrastructure is solid
- LORE: **98.9%** — world lore nearly perfect
- SECURITY: **6.2%** — scorer vocab expanded (Run 2) + hard-refusal "I cannot and will not" prompt instruction added (Feb 22 2026) — Run 3 pending
- EXPERTISE: **18.8%** → **50–100%** in Run 2 — first-person coaching language fix + few-shot examples
- persona_voice dimension: **0.255–0.528** — EMOTIONAL saturation fixed (Run 2); lore keywords correctly scoped; remaining variance is genuine
