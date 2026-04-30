import logging

from fastapi import APIRouter, Depends, HTTPException, status

from chatbot.api.utils.models import (
    CreateServiceRequest,
    Messages,
    Order,
    Service,
    UpdateOrderStatusRequest,
    UpdateServiceRequest,
    User,
)
from chatbot.api.utils.security import get_api_key
from chatbot.db.schema import PaymentStatus, services_table
from chatbot.db.services import services
from chatbot.messaging.whatsapp import whatsapp_manager

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(get_api_key)])


@router.get("/users", response_model=list[User])
async def get_all_users():
    logger.info("Fetching all users")
    return await services.get_all_users()


@router.get("/users/{phone}", response_model=User)
async def get_user(phone: str):
    logger.info(f"Fetching user with phone: {phone}")
    return await services.get_user(phone)


@router.get("/messages/{phone}", response_model=list[Messages])
async def get_messages(phone: str):
    logger.info(f"Fetching messages for phone: {phone}")
    return await services.get_messages(phone)


@router.get("/orders", response_model=list[Order])
async def get_all_orders():
    logger.info("Fetching all orders")
    return await services.get_all_orders()


@router.get("/orders/user/{phone}", response_model=list[Order])
async def get_orders_by_user(phone: str):
    logger.info(f"Fetching orders for user: {phone}")
    return await services.get_user_orders(phone)


@router.get("/services", response_model=list[Service])
async def get_all_services():
    logger.info("Fetching all services")
    return await services.database.fetch_all(services_table.select())


@router.patch("/services/{service_code}", response_model=Service)
async def update_service(service_code: str, body: UpdateServiceRequest):
    logger.info(f"Updating service: {service_code}")
    update_data = body.model_dump(exclude_none=True)
    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No fields provided to update",
        )
    updated = await services.update_service(service_code, **update_data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Service '{service_code}' not found or could not be updated",
        )
    service = await services.database.fetch_one(
        services_table.select().where(services_table.c.code == service_code)
    )
    return service


@router.post("/services", status_code=status.HTTP_201_CREATED)
async def create_service(body: CreateServiceRequest):
    logger.info(f"Creating service: {body.code}")
    created = await services.create_service(
        code=body.code,
        name=body.name,
        description=body.description,
        price=body.price,
        payment_link=body.payment_link,
        image=body.image,
        active=body.active,
    )
    if not created:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Service with code '{body.code}' already exists",
        )
    return {"detail": f"Service '{body.code}' created successfully"}


@router.patch("/orders/{order_name}/status", response_model=Order)
async def update_order_status(order_name: str, body: UpdateOrderStatusRequest):
    logger.info(f"Updating order {order_name} status to {body.status}")
    updated = await services.update_order_status(
        order_name=order_name,
        status=body.status,
        amount_remaining=body.amount_remaining,
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order '{order_name}' not found or could not be updated",
        )
    order = await services.get_order(order_name)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order '{order_name}' not found",
        )

    if body.status == PaymentStatus.CONFIRMED:
        message = (
            f"✅ ¡Tu pago ha sido confirmado!\n\n"
            f"Tu orden *{order_name}* está activa. "
            f"El equipo de Legal Allies ya tiene acceso a tu caso. "
            f"En breve nos pondremos en contacto contigo. ¡Gracias por confiar en nosotros!"
        )
        await whatsapp_manager.send_text(order.user_phone, message)  # type: ignore[attr-defined]
        logger.info(
            f"Payment confirmation sent to {order.user_phone} for order {order_name}"
        )  # type: ignore[attr-defined]

    return order
