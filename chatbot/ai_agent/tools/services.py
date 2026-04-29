"""Service-layer tools — expose DB services to the AI agent.

Provides tools for querying and managing services and orders from the
database, usable directly by the main agent via RunContext[AgentDeps].
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pydantic_ai import RunContext

from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.db.schema import PaymentStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output structures
# ---------------------------------------------------------------------------


@dataclass
class ServiceInfo:
    code: str
    name: str
    description: str
    price: float
    payment_link: str
    image: str | None
    active: bool


@dataclass
class OrderInfo:
    name: str
    service_code: str
    user_phone: str
    payment_status: str
    amount_remaining: float | None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


async def get_all_services(ctx: RunContext[AgentDeps]) -> list[ServiceInfo]:
    """Return all currently active services available for the user to purchase.

    Use this tool when the user asks about available services, prices, or
    what the business offers.

    Args:
        ctx: Agent run context (injected automatically).

    Returns:
        List of ServiceInfo with code, name, description, price, payment_link
        and image URL for each active service.
    """
    logger.info("[get_all_services] fetching active services")
    rows = await ctx.deps.db_services.get_active_services()
    result = [
        ServiceInfo(
            code=row.code,  # type: ignore[attr-defined]
            name=row.name,  # type: ignore[attr-defined]
            description=row.description,  # type: ignore[attr-defined]
            price=row.price,  # type: ignore[attr-defined]
            payment_link=row.payment_link,  # type: ignore[attr-defined]
            image=row.image,  # type: ignore[attr-defined]
            active=row.active,  # type: ignore[attr-defined]
        )
        for row in rows
    ]
    logger.info("[get_all_services] returned %d services", len(result))
    return result


async def create_order(
    ctx: RunContext[AgentDeps],
    service_code: str,
    has_paid: bool = False,
) -> OrderInfo:
    """Create a new order for the current user for the given service.

    Use this tool when the user confirms they want to purchase a specific
    service. If the user has already made the payment, set has_paid=True
    so the order is created with PENDING status instead of NOT_PAID.

    Args:
        ctx: Agent run context (injected automatically).
        service_code: The code of the service the user wants to purchase
                      (obtained from get_all_services).
        has_paid: Set to True if the user claims to have already paid.
                  The order will be created with PENDING status.

    Returns:
        OrderInfo with the generated order name, service code, user phone,
        initial payment status and amount_remaining.
    """
    logger.info(
        "[create_order] service_code=%r user=%r has_paid=%r",
        service_code,
        ctx.deps.user_phone,
        has_paid,
    )
    order_name = await ctx.deps.db_services.create_order(
        service_code=service_code,
        user_phone=ctx.deps.user_phone,
        has_paid=has_paid,
    )
    order = await ctx.deps.db_services.get_order(order_name)
    result = OrderInfo(
        name=order.name,  # type: ignore[attr-defined]
        service_code=order.service_code,  # type: ignore[attr-defined]
        user_phone=order.user_phone,  # type: ignore[attr-defined]
        payment_status=order.payment_status.value  # type: ignore[attr-defined]
        if isinstance(order.payment_status, PaymentStatus)  # type: ignore[attr-defined]
        else str(order.payment_status),  # type: ignore[attr-defined]
        amount_remaining=order.amount_remaining,  # type: ignore[attr-defined]
    )
    logger.info("[create_order] created order %r", result.name)
    return result


async def get_order_by_name(
    ctx: RunContext[AgentDeps],
    order_name: str,
) -> OrderInfo | str:
    """Retrieve a specific order by its unique name/ID.

    Use this tool when the user asks about the status or details of a
    specific order and provides its ID.

    Args:
        ctx: Agent run context (injected automatically).
        order_name: The unique name/ID of the order, e.g. "SPA260001".

    Returns:
        OrderInfo with order details, or an error string if not found.
    """
    logger.info("[get_order_by_id] order_name=%r", order_name)
    order = await ctx.deps.db_services.get_order(order_name)
    if order is None:
        logger.warning("[get_order_by_id] order %r not found", order_name)
        return f"No se encontró ninguna orden con el identificador '{order_name}'."
    result = OrderInfo(
        name=order.name,  # type: ignore[attr-defined]
        service_code=order.service_code,  # type: ignore[attr-defined]
        user_phone=order.user_phone,  # type: ignore[attr-defined]
        payment_status=order.payment_status.value  # type: ignore[attr-defined]
        if isinstance(order.payment_status, PaymentStatus)  # type: ignore[attr-defined]
        else str(order.payment_status),  # type: ignore[attr-defined]
        amount_remaining=order.amount_remaining,  # type: ignore[attr-defined]
    )
    logger.info(
        "[get_order_by_id] found order %r status=%r", result.name, result.payment_status
    )
    return result


async def confirm_payment(
    ctx: RunContext[AgentDeps],
    order_name: str,
) -> str:
    """Mark an order as PENDING after the user confirms they have made the payment.

    Use this tool when the user explicitly states they have already paid or
    transferred the money for a specific order. This transitions the order
    from NOT_PAID to PENDING so staff can verify and confirm the payment.

    Args:
        ctx: Agent run context (injected automatically).
        order_name: The unique name/ID of the order to update, e.g. "SPA260001".

    Returns:
        A confirmation message string, or an error description if the update failed.
    """
    logger.info(
        "[confirm_payment] order_name=%r user=%r", order_name, ctx.deps.user_phone
    )
    order = await ctx.deps.db_services.get_order(order_name)
    if order is None:
        return f"No se encontró ninguna orden con el identificador '{order_name}'."
    if order.payment_status != PaymentStatus.NOT_PAID:  # type: ignore[attr-defined]
        current = (
            order.payment_status.value  # type: ignore[attr-defined]
            if isinstance(order.payment_status, PaymentStatus)  # type: ignore[attr-defined]
            else str(order.payment_status)  # type: ignore[attr-defined]
        )
        return (
            f"La orden '{order_name}' ya tiene el estado '{current}' "
            f"y no puede cambiarse a PENDING desde aquí."
        )
    updated = await ctx.deps.db_services.update_order_status(
        order_name=order_name,
        status=PaymentStatus.PENDING,
    )
    if not updated:
        return f"No se pudo actualizar el estado de la orden '{order_name}'. Inténtalo de nuevo."
    logger.info("[confirm_payment] order %r set to PENDING", order_name)
    return (
        f"¡Gracias! Tu pago para la orden '{order_name}' ha sido registrado. "
        f"En breve nuestro equipo lo verificará y confirmará."
    )


async def get_orders_by_user(ctx: RunContext[AgentDeps]) -> list[OrderInfo] | str:
    """Retrieve all orders placed by the current user.

    Use this tool when the user asks about their orders, order history,
    or the status of their purchases.

    Args:
        ctx: Agent run context (injected automatically).

    Returns:
        List of OrderInfo for the user's orders (most recent first),
        or an error string if no orders exist.
    """
    logger.info("[get_orders_by_user] user=%r", ctx.deps.user_phone)
    rows = await ctx.deps.db_services.get_user_orders(ctx.deps.user_phone)
    if not rows:
        return "El usuario no tiene órdenes registradas."
    result = [
        OrderInfo(
            name=row.name,  # type: ignore[attr-defined]
            service_code=row.service_code,  # type: ignore[attr-defined]
            user_phone=row.user_phone,  # type: ignore[attr-defined]
            payment_status=row.payment_status.value  # type: ignore[attr-defined]
            if isinstance(row.payment_status, PaymentStatus)  # type: ignore[attr-defined]
            else str(row.payment_status),  # type: ignore[attr-defined]
            amount_remaining=row.amount_remaining,  # type: ignore[attr-defined]
        )
        for row in rows
    ]
    logger.info("[get_orders_by_user] returned %d orders", len(result))
    return result
