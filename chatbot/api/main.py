import logging
from contextlib import asynccontextmanager

from asgi_correlation_id import CorrelationIdMiddleware
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from chatbot.api.chat_router import router as chat_router
from chatbot.api.utils.filesystem import create_dirs
from chatbot.api.whatsapp_router import router as whatsapp_router
from chatbot.core.config import config
from chatbot.core.logging_conf import init_logging
from chatbot.core.sentry import init_sentry
from chatbot.db.services import services

init_logging()
logger = logging.getLogger(__name__)

# Database connection retry settings
DB_MAX_RETRIES = 5
DB_RETRY_DELAY = 3  # seconds


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Starting App")
    await services.database.connect()
    logger.info("✅ Connected to database")
    init_sentry()
    create_dirs()

    yield

    await services.database.disconnect()
    logger.info("✅ Disconnected from database")


app = FastAPI(
    title="LegalAllies Bot",
    description="Bot de WhatsApp LegalAllies: +1 (555) 188-6623",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(whatsapp_router, prefix="/whatsapp")
app.include_router(chat_router)


@app.get("/sentry-debug")
async def trigger_error():
    division_by_zero = 1 / 0


@app.get("/health")
async def health_check():
    logger.info("Health check requested")
    return {
        "status": "healthy",
        "environment": config.ENV_STATE,
        "ERP_HOST": config.ERP_HOST,
        "USE_FFMPEG": config.USE_FFMPEG,
        "WHATSAPP_BOT_NUMBER": config.WHATSAPP_BOT_NUMBER,
    }


@app.get("/")
async def root():
    logger.info("Root")
    return {
        "message": "Welcome to LegalAllies Bot",
        "version": "1.0.0",
        "docs": "/docs",
    }
