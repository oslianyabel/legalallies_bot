"""User data tools — allow the agent to update client information in the DB."""

from __future__ import annotations

import logging

from pydantic_ai import RunContext

from chatbot.ai_agent.dependencies import AgentDeps

logger = logging.getLogger(__name__)


async def update_user_profile(
    ctx: RunContext[AgentDeps],
    name: str | None = None,
    email: str | None = None,
) -> str:
    """Update the current user's name and/or email in the database.

    Use this tool before processing any service request. You MUST collect
    the client's name and email before creating an order.

    Call this tool as soon as you have one or both of these values from the
    user. You do not need both at once — you can call it with just the name
    or just the email.

    Args:
        ctx: Agent run context (injected automatically).
        name: The client's full name as provided by the user.
        email: The client's email address as provided by the user.

    Returns:
        A confirmation string, or an error message if the update failed.
    """
    logger.info(
        "[update_user_profile] user=%r name=%r email=%r",
        ctx.deps.user_phone,
        name,
        email,
    )
    if not name and not email:
        return "No se proporcionaron datos para actualizar."

    kwargs: dict = {}
    if name:
        kwargs["name"] = name
    if email:
        kwargs["email"] = email

    updated = await ctx.deps.db_services.update_user(ctx.deps.user_phone, **kwargs)
    if not updated:
        return "No se pudo actualizar la información del usuario. Inténtalo de nuevo."

    logger.info("[update_user_profile] profile updated for %r", ctx.deps.user_phone)
    return "Información del cliente actualizada correctamente."
