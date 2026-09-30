from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse

from app.live.routers import router as chat_router
from app.review.routers import router as vod_router
from app.core.exc import BaseAppException, exception_handler
from app.core.lifespan import lifespan
from app.core.log import setup_logging


setup_logging()


app = FastAPI(lifespan=lifespan)

app.include_router(chat_router)
app.include_router(vod_router)


# Exception Handler
app.add_exception_handler(BaseAppException, exception_handler)
app.add_exception_handler(RequestValidationError, exception_handler)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "OK"}


@app.get("/streams/{bid}/live/view")
async def live_view(bid: str) -> HTMLResponse:
    """
    채팅 웹소켓을 api로 받기 위해 만든 라우터.
    """

    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"><title>{bid} live</title></head>
    <body>
        <h1>{bid} 실시간 채팅</h1>
        <ul id="messages"></ul>
        <script>
            const ws = new WebSocket("ws://localhost:8000/streams/{bid}/live");
            ws.onopen = () => console.log("연결됨");
            ws.onmessage = (e) => {{
                const li = document.createElement("li");
                li.textContent = e.data;
                document.getElementById("messages").prepend(li);
            }};
            ws.onclose = (e) => console.log("닫힘", e.code);
        </script>
    </body>
    </html>
    """
    return HTMLResponse(html)
