"""Telegram chatbot entry point using the Ruta del Queso AI agent.

On first interaction (or via /start) the user is asked to provide their phone
number, which is stored in ``_user_phones`` (keyed by Telegram chat_id).
That phone is then passed as ``user_phone`` in AgentDeps on every AI call.
The Telegram chat_id is used as the conversation identifier in the DB and as
``telegram_id`` in AgentDeps.

Run with:
    uv run python scripts/run_telegram_bot.py
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from pydantic_ai.exceptions import UsageLimitExceeded
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from chatbot.ai_agent import get_legalallies_agent
from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.api.utils import message_handler
from chatbot.api.utils.text import strip_markdown
from chatbot.core.config import config
from chatbot.core.logging_conf import init_logging
from chatbot.db.services import services
from chatbot.messaging.telegram_notifier import notify_error
from chatbot.messaging.whatsapp import WhatsAppManager

logger = logging.getLogger(__name__)

HISTORY_SUMMARY_THRESHOLD: int = 30

# ---------------------------------------------------------------------------
# ERP client — created in post_init, closed in post_shutdown
# ---------------------------------------------------------------------------
erp_client: httpx.AsyncClient | None = None

# ---------------------------------------------------------------------------
# Per-user phone registry
# Keys: Telegram chat_id (str). Values: validated phone number (str).
# _pending_phone: chat_ids waiting to provide their phone number.
# ---------------------------------------------------------------------------
_user_phones: dict[str, str] = {}
_pending_phone: set[str] = set()

_noop_whatsapp = WhatsAppManager()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _typing_loop(bot, chat_id: int) -> None:
    """Send 'typing' chat action every 4 s until the task is cancelled."""
    while True:
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        await asyncio.sleep(4)


def _extract_tools_used(result) -> list[str]:
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    tools: list[str] = []
    for msg in result.all_messages():
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    tools.append(part.tool_name)
    return tools


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Welcome message on /start."""
    if not update.message or not update.effective_chat:
        return
    user = update.effective_user
    chat_id = str(update.effective_chat.id)

    if chat_id not in _user_phones:
        _pending_phone.add(chat_id)
        await update.message.reply_text(
            f"¡Hola{', ' + user.first_name if user else ''}! 🧀\n"
            "Soy el asistente de Ruta del Queso.\n\n"
            "Antes de comenzar necesito tu número de teléfono (con código de país, ej: +59899000000):"
        )
    else:
        await update.message.reply_text(
            f"¡Hola{', ' + user.first_name if user else ''}! 🧀\n"
            "Soy el asistente de Ruta del Queso. ¿En qué te puedo ayudar hoy?"
        )


async def _handle_change_phone(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/change_phone allows the user to update their registered phone number."""
    if not update.message or not update.effective_chat:
        return
    chat_id = str(update.effective_chat.id)
    _pending_phone.add(chat_id)
    await update.message.reply_text(
        "Por favor ingresa tu nuevo número de teléfono (con código de país, ej: +59899000000):"
    )
    logger.info("'/change_phone' requested by telegram_id=%s", chat_id)


async def _handle_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/restart clears the conversation history."""
    if not update.message or not update.effective_chat:
        return
    chat_id = str(update.effective_chat.id)
    await services.reset_chat(chat_id)
    await update.message.reply_text("Chat reiniciado. ¿En qué te puedo ayudar?")
    logger.info("'/restart' requested by telegram_id=%s", chat_id)


async def _handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process an incoming text message through the AI agent."""
    from telegram.error import TimedOut

    if not update.message or not update.message.text or not update.effective_chat:
        return

    chat_id_int: int = update.effective_chat.id
    chat_id: str = str(chat_id_int)
    incoming_msg: str = update.message.text

    try:
        # ------------------------------------------------------------------
        # Phone number collection — required before any AI interaction
        # ------------------------------------------------------------------
        if chat_id in _pending_phone:
            # User is responding to the phone request
            phone = incoming_msg.strip()
            _user_phones[chat_id] = phone
            _pending_phone.discard(chat_id)
            logger.info("Phone registered for telegram_id=%s: %s", chat_id, phone)
            await update.message.reply_text(
                f"¡Perfecto! Tu número {phone} fue registrado. ¿En qué te puedo ayudar hoy? 🧀"
            )
            return

        if chat_id not in _user_phones:
            # First interaction — ask for phone before anything else
            _pending_phone.add(chat_id)
            await update.message.reply_text(
                "Antes de continuar necesito tu número de teléfono "
                "(con código de país, ej: +59899000000):"
            )
            return

        logger.info("=" * 80)
        logger.info("telegram_id=%s: %s", chat_id, incoming_msg)

        # Start typing indicator loop in background
        typing_task = asyncio.create_task(_typing_loop(context.bot, chat_id_int))

        try:
            await message_handler.save_user_msg(chat_id, incoming_msg)

            assert erp_client is not None, "ERP client not initialized"
            deps = AgentDeps(
                db_services=services,
                whatsapp_client=_noop_whatsapp,
                user_phone=_user_phones.get(chat_id, ""),
                telegram_id=chat_id,
            )

            agent = get_legalallies_agent()
            history = await services.get_pydantic_ai_history(chat_id, hours=24)
            try:
                result = await agent.run(
                    incoming_msg, deps=deps, message_history=history
                )
                ai_response: str = strip_markdown(result.output)
                tools_used = _extract_tools_used(result)
            except UsageLimitExceeded as ule:
                logger.warning(
                    "UsageLimitExceeded for telegram_id=%s: %s. Summarizing history and retrying...",
                    chat_id,
                    ule,
                )
                await notify_error(
                    ule,
                    context=f"_process_message | user={chat_id} | msg={incoming_msg[:200]} | action=summary_retry",
                )
                await services.reset_chat(chat_id)
                ai_response = "Chat reiniciado por exceder la ventana de contexto del modelo. Iniciemos de nuevo"

            logger.info("Agent response for telegram_id=%s: %s", chat_id, ai_response)
            logger.debug("Tools used: %s", tools_used)

            await message_handler.save_assistant_msg(chat_id, ai_response, tools_used)
            await update.message.reply_text(ai_response)

        finally:
            typing_task.cancel()

    except TimedOut:
        logger.warning(
            "Telegram TimedOut for telegram_id=%s — message dropped", chat_id
        )
    except Exception as exc:
        logger.exception("Error processing Telegram message for %s: %s", chat_id, exc)
        await notify_error(
            exc,
            context=f"telegram_bot._handle_message | chat_id={chat_id} | msg={incoming_msg[:80]}",
        )
        await update.message.reply_text(
            "Ocurrió un error al procesar tu mensaje. "
            "Por favor inténtalo de nuevo o escribe /restart para reiniciar el chat."
        )


# ---------------------------------------------------------------------------
# Application lifecycle
# ---------------------------------------------------------------------------


async def _post_init(application: Application) -> None:
    """Connect to DB and create ERP client on startup."""
    global erp_client
    init_logging()
    logger.info("🤖 Telegram bot starting up")
    await services.database.connect()
    logger.info("✅ DB connected and ERP client ready")


async def _post_shutdown(application: Application) -> None:
    """Disconnect DB and close ERP client on shutdown."""
    global erp_client
    try:
        await services.database.disconnect()
        logger.info("✅ DB disconnected")
    except Exception as exc:
        logger.error("Error disconnecting DB: %s", exc)

    if erp_client:
        try:
            await erp_client.aclose()
            logger.info("✅ ERP client closed")
        except Exception as exc:
            logger.error("Error closing ERP client: %s", exc)


def build_application() -> Application:
    """Build and return the configured PTB Application."""
    if not config.TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is not configured in .env")

    app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", _handle_start))
    app.add_handler(CommandHandler("restart", _handle_restart))
    app.add_handler(CommandHandler("change_phone", _handle_change_phone))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))

    return app
