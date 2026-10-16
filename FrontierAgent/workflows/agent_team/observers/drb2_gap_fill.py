"""Bounce the first DRB2 finalize so the coordinator must run a gap pass."""

from __future__ import annotations

import logging

from frontier_agent.core.loop_types import BaseObserver, Intervention, TurnContext

logger = logging.getLogger(__name__)

_GAP_MESSAGE = (
    "Mandatory gap-fill pass. Do not submit the report yet. Compare your "
    "notes against every required deliverable in the user prompt (tables, "
    "named countries/programs, explicit figures, analysis questions). Spawn "
    "sub-agents only for missing items. If a URL already failed, switch "
    "source. After the gaps are filled, write the complete Markdown report "
    "as your last message and also save it to /outputs/report.md."
)


class Drb2GapFillObserver(BaseObserver):
    """Block the first plain-text finalize on DeepResearch Bench II tasks."""

    critical = True

    def __init__(self) -> None:
        self._bounced = False

    async def on_llm_response(self, ctx: TurnContext) -> Intervention | None:
        metadata = ctx.metadata if isinstance(ctx.metadata, dict) else {}
        if metadata.get("benchmark") != "deepresearch_bench_ii":
            return None
        if ctx.tool_calls or not (ctx.ai_text or "").strip():
            return None
        if ctx.turn >= ctx.max_turns - 1 or self._bounced:
            return None
        if len(ctx.ai_text.strip()) < 400:
            return None
        self._bounced = True
        metadata["drb2_gap_fill_requested"] = True
        logger.info(
            "Drb2GapFillObserver bounced first finalize (turn=%d, len=%d)",
            ctx.turn,
            len(ctx.ai_text),
        )
        return Intervention(continue_to_next_turn=True, inject_messages=[_GAP_MESSAGE])
