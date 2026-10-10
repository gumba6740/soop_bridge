from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse

from app.review.api.routers import router as review_router
from app.live.api.routers import router as live_router
from app.core.exc import BaseAppException, exception_handler
from app.core.lifespan import lifespan
from app.core.log import setup_logging


setup_logging()


app = FastAPI(lifespan=lifespan)

app.include_router(review_router)
app.include_router(live_router)


# Exception Handler
app.add_exception_handler(BaseAppException, exception_handler)
app.add_exception_handler(RequestValidationError, exception_handler)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "OK"}
