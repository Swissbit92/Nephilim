# src/coordinator/tools/registry.py
"""Central tool registry — ADR-009 Phase R.

One registration per tool replaces the pre-ADR-009 scatter (definition in a
generator module, policy in the interceptor's private table, executor wiring
in a bespoke service, formatter in tool_utils). Modeled on the hermes-agent
registry/toolset architecture:

- a **ToolSpec** bundles definition, safety policy, and (optionally) an
  executor + result formatter;
- tools group into **toolsets** ("web", "wallet", later "memory"/"terminal")
  that enable/disable as units;
- personas are granted **toolsets**, not individual tools, via a `toolsets`
  field in the persona JSON — with the legacy `mcp_access` strings accepted
  as aliases during migration (`brave_search` -> web, `solana_wallet` ->
  wallet) and the legacy rarity fallback preserved for personas with neither
  field;
- a per-persona `nsfw` flag is exposed for tools that modulate behavior on it
  (e.g. the web toolset's safesearch clamp — enforced in executors, never in
  prompts).

Two-phase registration keeps imports acyclic: tool modules *declare* specs at
import time (definition + policy — pure data); services *bind* executors at
wiring time (`bind_executor`). The registry itself imports nothing from
`services/` or `config/`.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional, Sequence, Set

logger = logging.getLogger(__name__)

# Toolset -> legacy persona-JSON `mcp_access` alias. Personas predating
# ADR-009 grant toolsets through these strings; the interceptor's
# defence-in-depth re-check speaks the same vocabulary.
TOOLSET_MCP_ALIASES: Dict[str, str] = {
    "web": "brave_search",
    "wallet": "solana_wallet",
}
_MCP_ALIAS_TO_TOOLSET = {v: k for k, v in TOOLSET_MCP_ALIASES.items()}

# Legacy rarity fallback (personas with neither `toolsets` nor `mcp_access`):
# rare+ personas historically received web search only.
_RARITY_FALLBACK_TOOLSETS = {"rare", "epic", "legendary"}


@dataclass(frozen=True)
class ToolSpec:
    """A single registered tool: definition + policy (+ bound runtime hooks)."""

    name: str
    toolset: str
    definition_factory: Callable[[], Dict[str, Any]]
    # Safety policy (consumed by the ADR-004 interceptor).
    blast_radius: str = "none"  # "none" | "low" | "high"
    requires_hitl: bool = False
    # Runtime hooks — bound post-import by the wiring layer.
    executor: Optional[Callable[..., Any]] = None
    result_formatter: Optional[Callable[..., str]] = None
    # True when the tool changes behavior on the persona `nsfw` flag
    # (enforced inside the executor, e.g. the safesearch clamp).
    nsfw_modulated: bool = False

    @property
    def mcp_alias(self) -> str:
        """Legacy mcp_access string for this tool's toolset ('' if none)."""
        return TOOLSET_MCP_ALIASES.get(self.toolset, self.toolset)

    def definition(self) -> Dict[str, Any]:
        return self.definition_factory()


class ToolRegistry:
    """Process-wide tool registry. Thread-safe for the write paths."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolSpec] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ writes

    def register(self, spec: ToolSpec, *, replace_existing: bool = False) -> ToolSpec:
        with self._lock:
            if spec.name in self._tools and not replace_existing:
                # Idempotent re-import: keep the first registration (which may
                # already carry a bound executor).
                return self._tools[spec.name]
            self._tools[spec.name] = spec
            return spec

    def bind_executor(
        self,
        name: str,
        executor: Callable[..., Any],
        result_formatter: Optional[Callable[..., str]] = None,
    ) -> None:
        """Attach runtime hooks to an already-declared tool."""
        with self._lock:
            spec = self._tools.get(name)
            if spec is None:
                raise KeyError(f"cannot bind executor: unknown tool '{name}'")
            self._tools[name] = replace(
                spec,
                executor=executor,
                result_formatter=result_formatter or spec.result_formatter,
            )

    # ------------------------------------------------------------- reads

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def names(self) -> Set[str]:
        return set(self._tools)

    def toolsets(self) -> Set[str]:
        return {s.toolset for s in self._tools.values()}

    def specs_for_toolsets(self, toolsets: Sequence[str]) -> List[ToolSpec]:
        wanted = set(toolsets)
        return [s for s in self._tools.values() if s.toolset in wanted]

    def definitions_for_toolsets(self, toolsets: Sequence[str]) -> List[Dict[str, Any]]:
        return [s.definition() for s in self.specs_for_toolsets(toolsets)]

    # ------------------------------------------------- persona resolution

    @staticmethod
    def persona_nsfw(persona_card: Dict[str, Any]) -> bool:
        return bool(persona_card.get("nsfw", False))

    def toolsets_for_persona(self, persona_card: Dict[str, Any]) -> Set[str]:
        """Resolve a persona card to its granted toolsets.

        Precedence: explicit `toolsets` field > legacy `mcp_access` aliases >
        rarity fallback (matching pre-ADR-009 get_tools_for_persona exactly).
        """
        explicit = persona_card.get("toolsets")
        if explicit is not None:
            known = self.toolsets() | set(TOOLSET_MCP_ALIASES)
            granted = {t for t in explicit if t in known or t in _MCP_ALIAS_TO_TOOLSET}
            unknown = set(explicit) - granted
            if unknown:
                logger.warning(
                    f"persona '{persona_card.get('key')}' grants unknown toolsets "
                    f"{sorted(unknown)} — ignored"
                )
            return {_MCP_ALIAS_TO_TOOLSET.get(t, t) for t in granted}

        mcp_access = persona_card.get("mcp_access")
        if mcp_access is not None:
            return {
                _MCP_ALIAS_TO_TOOLSET[m]
                for m in mcp_access
                if m in _MCP_ALIAS_TO_TOOLSET
            }

        rarity = str(persona_card.get("rarity", "common")).lower()
        return {"web"} if rarity in _RARITY_FALLBACK_TOOLSETS else set()

    def tool_allowlist_for_persona(self, persona_card: Dict[str, Any]) -> Optional[Set[str]]:
        """Optional persona-card `tools` field: a subset restriction WITHIN the
        granted toolsets (not a grant by itself — a tool must still belong to a
        granted toolset). None = no restriction (all tools in granted toolsets,
        the pre-existing behavior). Lets a persona be scoped to e.g. only
        image_search/video_search within the broader `web` toolset, without
        inventing a new toolset per subset combination."""
        allow = persona_card.get("tools")
        if allow is None:
            return None
        return set(allow)

    def specs_for_persona(self, persona_card: Dict[str, Any]) -> List[ToolSpec]:
        specs = self.specs_for_toolsets(sorted(self.toolsets_for_persona(persona_card)))
        allow = self.tool_allowlist_for_persona(persona_card)
        if allow is None:
            return specs
        unknown = allow - {s.name for s in specs}
        if unknown:
            logger.warning(
                f"persona '{persona_card.get('key')}' tools allowlist references "
                f"{sorted(unknown)}, not in its granted toolsets — ignored"
            )
        return [s for s in specs if s.name in allow]

    def definitions_for_persona(self, persona_card: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [s.definition() for s in self.specs_for_persona(persona_card)]

    def denied_for_persona(self, persona_card: Dict[str, Any]) -> Set[str]:
        """Tools in the persona's granted TOOLSETS that its allowlist withholds.

        `specs_for_persona` already computes this set as the complement of its
        filter and then discards it, so until now nothing could state what a
        persona cannot do — only what it can. That asymmetry is the whole defect:
        the model is handed affirmative JSON schemas for its two media tools and
        no indication that general web search was withheld, so on a weather
        question it reaches for the nearest thing it has and invents the rest.

        Returns an empty set for a persona with no allowlist (nothing withheld),
        which is the case for 7 of the 8 shipped personas.
        """
        allow = self.tool_allowlist_for_persona(persona_card)
        if allow is None:
            return set()
        granted_by_toolset = {
            s.name for s in self.specs_for_toolsets(
                sorted(self.toolsets_for_persona(persona_card)))
        }
        return granted_by_toolset - allow

    # -------------------------------------------------- introspection (W3)

    def describe_for_persona(self, persona_card: Dict[str, Any]) -> Dict[str, Any]:
        """Human-readable toolkit summary for a persona (introspection API)."""
        granted = sorted(self.toolsets_for_persona(persona_card))
        by_toolset: Dict[str, List[Dict[str, str]]] = {}
        for spec in self.specs_for_persona(persona_card):
            d = spec.definition()["function"]
            by_toolset.setdefault(spec.toolset, []).append(
                {
                    "name": spec.name,
                    "description": (d.get("description") or "").split(". ")[0][:140],
                    "blast_radius": spec.blast_radius,
                    "requires_hitl": spec.requires_hitl,
                }
            )
        return {
            "persona_key": persona_card.get("key"),
            "nsfw": self.persona_nsfw(persona_card),
            "toolsets": granted,
            "tools": by_toolset,
        }


# Process-wide singleton. Tool modules call registry.register(...) at import.
registry = ToolRegistry()


def register_tool(
    name: str,
    toolset: str,
    definition_factory: Callable[[], Dict[str, Any]],
    *,
    blast_radius: str = "none",
    requires_hitl: bool = False,
    nsfw_modulated: bool = False,
) -> ToolSpec:
    """Convenience declaration helper for tool modules."""
    return registry.register(
        ToolSpec(
            name=name,
            toolset=toolset,
            definition_factory=definition_factory,
            blast_radius=blast_radius,
            requires_hitl=requires_hitl,
            nsfw_modulated=nsfw_modulated,
        )
    )
