from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.broadcast.live.api.routers import router as live_router
from app.broadcast.review.api.routers import router as review_router
from app.core.config import settings
from app.core.exc import BaseAppException, exception_handler
from app.core.lifespan import lifespan
from app.core.log import setup_logging
from app.station.routers import router as station_router

setup_logging()


app = FastAPI(lifespan=lifespan)


# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


app.include_router(station_router)
app.include_router(review_router)
app.include_router(live_router)


# Exception Handler
app.add_exception_handler(BaseAppException, exception_handler)
app.add_exception_handler(RequestValidationError, exception_handler)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "OK"}
