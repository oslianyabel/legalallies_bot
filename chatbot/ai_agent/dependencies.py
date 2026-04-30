"""Dependency injection container for the AI agent.

Provides all external services the agent needs: ERP client, DB services,
WhatsApp client, and per-conversation webhook context.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from chatbot.db.services import Services
from chatbot.messaging.whatsapp import WhatsAppManager


@dataclass
class AgentDeps:
    """Dependencies injected into every agent run via RunContext."""

    db_services: Services
    whatsapp_client: WhatsAppManager
    user_phone: str = ""
    telegram_id: str | None = None
    called_tools: set[str] = field(default_factory=set)
