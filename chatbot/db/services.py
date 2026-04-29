import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import sqlalchemy
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from chatbot.db.schema import (
    PaymentStatus,
    init_db,
    message_table,
    orders_table,
    services_table,
    users_table,
)

logger = logging.getLogger(__name__)


class Services:
    def __init__(self, database, debug=False):
        self.database = database
        self.debug = debug

    async def get_user(self, phone: str):
        query = users_table.select().where(users_table.c.phone == phone)
        if self.debug:
            logger.debug(query)

        user = await self.database.fetch_one(query)
        return user

    async def get_all_users(self):
        query = users_table.select()
        if self.debug:
            logger.debug(query)

        users = await self.database.fetch_all(query)
        return users

    def _normalize_user_data(self, **kwargs) -> dict:
        """Normaliza y filtra datos de usuario, eliminando None y espacios."""
        normalized = {}
        for key, value in kwargs.items():
            if value is not None:
                normalized[key] = value.strip() if isinstance(value, str) else value
        return normalized

    async def create_user(
        self, phone: str, permissions: str = "user", **kwargs
    ) -> bool:
        data = {"phone": phone, "permissions": permissions}
        data.update(self._normalize_user_data(**kwargs))
        ok = await self._create_user_with_data(phone, data)
        return ok

    async def _create_user_with_data(self, phone: str, data: dict) -> bool:
        data["phone"] = phone
        query = users_table.insert().values(data)
        if self.debug:
            logger.debug(query)

        try:
            await self.database.execute(query)
        except asyncpg.exceptions.UniqueViolationError:  # llave duplicada
            logger.warning(f"create_user: {phone} already exists in the database")
            return False

        logger.debug(f"{phone} created in the database")
        return True

    async def _update_user_data(self, phone: str, data: dict) -> bool:
        data["updated_at"] = sqlalchemy.func.now()
        query = users_table.update().where(users_table.c.phone == phone).values(**data)
        if self.debug:
            logger.debug(query)

        try:
            await self.database.execute(query)
            logger.debug(f"{phone} updated in the database")
            return True
        except Exception as exc:
            logger.error(exc)
            return False

    async def update_user(self, phone: str, **kwargs) -> bool:
        update_data = self._normalize_user_data(**kwargs)

        if not update_data:
            logger.warning(f"update_user: invalid data for update {phone}")
            return False

        return await self._update_user_data(phone, update_data)

    async def create_or_update_user(
        self, phone: str, permissions: str = "user", **kwargs
    ) -> bool:
        created = await self.create_user(phone, permissions=permissions, **kwargs)
        if not created:
            return await self.update_user(phone, **kwargs)
        return True

    async def create_or_update_user_with_data(self, phone: str, data: dict) -> bool:
        created = await self._create_user_with_data(phone, data)
        if not created:
            return await self._update_user_data(phone, data)
        return True

    async def create_message(
        self, phone: str, role: str, message: str, tools_used: list[str] | None = None
    ):
        if not await self.get_user(phone):
            await self.create_user(phone)

        data = {"user_phone": phone, "role": role, "message": message}
        if tools_used is not None:
            data["tools_used"] = json.dumps(tools_used)

        query = message_table.insert().values(data)
        if self.debug:
            logger.debug(query)

        await self.database.execute(query)

    async def reset_chat(self, phone: str):
        logger.warning(f"Deleting chats from {phone}")
        user = await self.get_user(phone)
        if not user:
            return f"reset_chat: {phone} no existe"

        query = message_table.delete().where(message_table.c.user_phone == phone)
        if self.debug:
            logger.debug(query)

        await self.database.execute(query)

    async def get_recent_messages(self, phone: str, hours: int = 24) -> list:
        """Return all messages for *phone* created within the last *hours* hours."""
        since: datetime = datetime.now(UTC).replace(tzinfo=None) - timedelta(
            hours=hours
        )
        query = (
            message_table.select()
            .where(message_table.c.user_phone == phone)
            .where(message_table.c.created_at >= since)
            .order_by(message_table.c.created_at.asc())
        )
        if self.debug:
            logger.debug(query)
        return await self.database.fetch_all(query)

    async def get_last_user_message(self, phone: str):
        """Return the most recent message sent by the user (role='user').

        Used to verify the META WhatsApp 24-hour free-messaging window.
        """
        query = (
            message_table.select()
            .where(message_table.c.user_phone == phone)
            .where(message_table.c.role == "user")
            .order_by(message_table.c.created_at.desc())
            .limit(1)
        )
        if self.debug:
            logger.debug(query)
        return await self.database.fetch_one(query)

    async def get_pydantic_ai_history(
        self, phone: str, hours: int = 24
    ) -> list[ModelMessage]:
        """Return the last *hours* hours of conversation as PydanticAI ModelMessage objects.

        Reconstructs ModelRequest/ModelResponse pairs from the stored text rows so
        the agent can continue the conversation with full context.
        """
        rows = await self.get_recent_messages(phone, hours=hours)
        history: list[ModelMessage] = []
        for row in rows:
            role: str = row.role  # type: ignore[attr-defined]
            raw: str = row.message  # type: ignore[attr-defined]
            content = raw.removeprefix("Usuario - ").removeprefix("Bot - ")
            if role == "user":
                history.append(ModelRequest(parts=[UserPromptPart(content=content)]))  # type: ignore
            elif role == "assistant":
                history.append(
                    ModelResponse(
                        parts=[TextPart(content=content)], model_name="restored"
                    )
                )
            elif role == "system":
                history.append(ModelRequest(parts=[SystemPromptPart(content=content)]))
        logger.debug(
            "Loaded %d history messages for %s (last %dh)", len(history), phone, hours
        )
        return history

    async def get_messages(self, phone: str):
        query = (
            message_table.select()
            .where(message_table.c.user_phone == phone)
            .order_by(message_table.c.created_at.asc())
        )
        if self.debug:
            logger.debug(query)

        return await self.database.fetch_all(query)

    async def get_chat(self, phone: str) -> list[dict]:
        messages_obj = await self.get_messages(phone)
        chat = []
        for msg in messages_obj:
            message_dict = {"role": msg.role, "content": msg.message}  # type: ignore
            if msg.tools_used:  # type: ignore
                message_dict["tools_used"] = json.loads(msg.tools_used)  # type: ignore
            chat.append(message_dict)
        return chat

    async def get_chat_str(self, phone: str) -> str:
        messages = await self.get_chat(phone)
        return json.dumps(messages)

    # ── Services ──────────────────────────────────────────────────────────────

    async def create_service(
        self,
        code: str,
        name: str,
        description: str,
        price: float,
        payment_link: str,
        image: str | None = None,
        active: bool = True,
    ) -> bool:
        data: dict = {
            "code": code,
            "name": name,
            "description": description,
            "price": price,
            "payment_link": payment_link,
            "active": active,
        }
        if image is not None:
            data["image"] = image

        query = services_table.insert().values(data)
        if self.debug:
            logger.debug(query)
        try:
            await self.database.execute(query)
            logger.debug(f"Service {code} created")
            return True
        except asyncpg.exceptions.UniqueViolationError:
            logger.warning(f"create_service: service {code!r} already exists")
            return False

    async def get_active_services(self) -> list:
        query = services_table.select().where(services_table.c.active == True)  # noqa: E712
        if self.debug:
            logger.debug(query)
        return await self.database.fetch_all(query)

    async def update_service(self, service_code: str, **kwargs) -> bool:
        if not kwargs:
            logger.warning(f"update_service: no data provided for {service_code}")
            return False
        query = (
            services_table.update()
            .where(services_table.c.code == service_code)
            .values(**kwargs)
        )
        if self.debug:
            logger.debug(query)
        try:
            await self.database.execute(query)
            logger.debug(f"Service {service_code} updated")
            return True
        except Exception as exc:
            logger.error(f"update_service error: {exc}")
            return False

    # ── Orders ────────────────────────────────────────────────────────────────

    async def _generate_order_name(self, service_code: str) -> str:
        year_suffix = str(datetime.now().year)[-2:]
        prefix = f"{service_code}{year_suffix}"
        count_query = (
            sqlalchemy.select(sqlalchemy.func.count())
            .select_from(orders_table)
            .where(orders_table.c.name.like(f"{prefix}%"))
        )
        count: int = await self.database.fetch_val(count_query) or 0
        return f"{prefix}{count:04d}"

    async def get_all_orders(self) -> list:
        query = orders_table.select().order_by(orders_table.c.created_at.desc())
        if self.debug:
            logger.debug(query)
        return await self.database.fetch_all(query)

    async def get_order(self, order_name: str):
        query = orders_table.select().where(orders_table.c.name == order_name)
        if self.debug:
            logger.debug(query)
        return await self.database.fetch_one(query)

    async def get_user_orders(self, phone: str) -> list:
        query = (
            orders_table.select()
            .where(orders_table.c.user_phone == phone)
            .order_by(orders_table.c.created_at.desc())
        )
        if self.debug:
            logger.debug(query)
        return await self.database.fetch_all(query)

    async def create_order(
        self,
        service_code: str,
        user_phone: str,
        has_paid: bool = False,
    ) -> str:
        if not await self.get_user(user_phone):
            await self.create_user(user_phone)

        service = await self.database.fetch_one(
            services_table.select().where(services_table.c.code == service_code)
        )
        if service is None:
            raise ValueError(f"Service with code '{service_code}' not found")
        amount_remaining: float = service.price  # type: ignore[attr-defined]

        initial_status = PaymentStatus.PENDING if has_paid else PaymentStatus.NOT_PAID

        for _ in range(5):
            name = await self._generate_order_name(service_code)
            data: dict = {
                "name": name,
                "service_code": service_code,
                "user_phone": user_phone,
                "payment_status": initial_status,
                "amount_remaining": amount_remaining,
            }

            query = orders_table.insert().values(data)
            if self.debug:
                logger.debug(query)
            try:
                await self.database.execute(query)
                logger.debug(f"Order {name} created for {user_phone}")
                return name
            except asyncpg.exceptions.UniqueViolationError:
                logger.warning(f"create_order: name collision for {name}, retrying...")
                continue

        raise RuntimeError(
            f"Could not generate unique order name for service_code={service_code}"
        )

    async def update_order_status(
        self,
        order_name: str,
        status: PaymentStatus,
        amount_remaining: float | None = None,
    ) -> bool:
        values: dict = {"payment_status": status}
        if status == PaymentStatus.INCOMPLETE:
            if amount_remaining is None:
                raise ValueError(
                    "amount_remaining is required when status is INCOMPLETE"
                )
            values["amount_remaining"] = amount_remaining
        elif amount_remaining is not None:
            values["amount_remaining"] = amount_remaining

        query = (
            orders_table.update()
            .where(orders_table.c.name == order_name)
            .values(**values)
        )
        if self.debug:
            logger.debug(query)
        try:
            await self.database.execute(query)
            logger.debug(f"Order {order_name} status updated to {status}")
            return True
        except Exception as exc:
            logger.error(f"update_order_status error: {exc}")
            return False


database = init_db()
services = Services(database)


if __name__ == "__main__":
    import asyncio

    async def test():
        await database.connect()
        phone = "+53 12345678"
        user = await services.get_user(phone)
        print("User:", user)
        await database.disconnect()

    asyncio.run(test())
