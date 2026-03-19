from __future__ import annotations

import logging
from datetime import datetime, timezone

from pydantic_ai import Agent, RunContext
from pydantic_ai.builtin_tools import WebSearchTool
from pydantic_ai.settings import ModelSettings

from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.ai_agent.models import GoogleModel
from chatbot.ai_agent.prompts import SYSTEM_PROMPT
from chatbot.ai_agent.tools.date_resolver import resolve_relative_date

logger = logging.getLogger(__name__)
ERP_TIMEOUT_SECONDS = 15.0


AGENT_TOOLS = [resolve_relative_date, WebSearchTool]


# ---------------------------------------------------------------------------
# Lazy singleton
# ---------------------------------------------------------------------------

_legalallies_agent: Agent[AgentDeps, str] | None = None


def get_legalallies_agent() -> Agent[AgentDeps, str]:
    """Return the singleton legalallies agent, creating it on first call."""
    global _legalallies_agent  # noqa: PLW0603
    if _legalallies_agent is None:
        _legalallies_agent = Agent(
            model=GoogleModel.Gemini_Flash_Latest,
            system_prompt=SYSTEM_PROMPT,
            deps_type=AgentDeps,
            tools=AGENT_TOOLS,
            model_settings=ModelSettings(temperature=0),
        )

        @_legalallies_agent.instructions
        def current_datetime_prompt(
            ctx: RunContext[AgentDeps],
        ) -> str:
            now = datetime.now(tz=timezone.utc).astimezone()
            return (
                f"Fecha y hora actual: {now.strftime('%A %d de %B de %Y, %H:%M')} "
                f"(zona horaria del servidor: {now.strftime('%Z %z')}). "
            )

        logger.info("Agent initialized with %d tools", len(AGENT_TOOLS))
    return _legalallies_agent
