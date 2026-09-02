from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class BaseAppException(Exception):
    status_code: int = 500
    code: str = "INTERNAL_ERROR"
    detail: str = "서버 에러가 발생했습니다."

    def __init__(self, detail: str | None = None, *, code: str | None = None):
        self.detail = detail if detail is not None else self.detail
        self.code = code if code is not None else self.code
        super().__init__(self.detail)


async def exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # 커스텀 예외처리
    if isinstance(exc, BaseAppException):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.detail,
                "code": exc.code,
            },
        )
    # fastapi가 제공하는 데이터 검증 예외처리
    if isinstance(exc, RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "detail": "입력값이 올바르지 않습니다.",
                "code": "VALIDATION_ERROR",
                "errors": jsonable_encoder(exc.errors()),
            },
        )

    raise exc
