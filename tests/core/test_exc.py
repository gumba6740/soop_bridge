"""app/core/exc.py 테스트.

uv run pytest tests/core/test_exc.py
"""

import json
from typing import Any

import pytest
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.exc import BaseAppException, exception_handler


class SampleNotFound(BaseAppException):
    """실제 예외(StreamerNotFound 등)처럼 클래스 속성만 바꾼 예외."""

    status_code = 404
    code = "SAMPLE_NOT_FOUND"
    detail = "샘플이 없습니다."


def _request() -> Request:
    # exception_handler는 request를 쓰지 않는다. 자리만 채우는 최소한의 요청.
    return Request({"type": "http"})


def _json(resp: JSONResponse) -> Any:
    # body의 타입이 bytes | memoryview라서 bytes로 바꿔서 읽는다.
    return json.loads(bytes(resp.body))


# ------------------------------------------------------ BaseAppException


def test_defaults():
    exc = BaseAppException()
    assert exc.status_code == 500
    assert exc.code == "INTERNAL_ERROR"
    assert exc.detail == "서버 에러가 발생했습니다."


def test_override_detail_and_code():
    exc = BaseAppException("다른 메시지", code="OTHER")
    assert exc.detail == "다른 메시지"
    assert exc.code == "OTHER"


def test_empty_string_detail_is_kept():
    # None일 때만 기본값을 쓴다. ""는 값을 준 것으로 본다.
    exc = BaseAppException("")
    assert exc.detail == ""


def test_str_is_detail():
    assert str(BaseAppException("메시지")) == "메시지"
    assert str(BaseAppException()) == "서버 에러가 발생했습니다."


def test_subclass_defaults():
    exc = SampleNotFound()
    assert exc.status_code == 404
    assert exc.code == "SAMPLE_NOT_FOUND"
    assert exc.detail == "샘플이 없습니다."


# ------------------------------------------------ exception_handler


@pytest.mark.asyncio
async def test_handler_app_exception():
    resp = await exception_handler(_request(), SampleNotFound())
    assert resp.status_code == 404
    assert _json(resp) == {
        "detail": "샘플이 없습니다.",
        "code": "SAMPLE_NOT_FOUND",
    }


@pytest.mark.asyncio
async def test_handler_validation_error():
    errors = [{"type": "missing", "loc": ("query", "limit"), "msg": "Field required"}]
    resp = await exception_handler(_request(), RequestValidationError(errors))
    assert resp.status_code == 422
    body = _json(resp)
    assert body["detail"] == "입력값이 올바르지 않습니다."
    assert body["code"] == "VALIDATION_ERROR"
    # loc의 튜플은 JSON에서 리스트가 된다.
    assert body["errors"] == [
        {"type": "missing", "loc": ["query", "limit"], "msg": "Field required"}
    ]


@pytest.mark.asyncio
async def test_handler_reraises_other_exceptions():
    with pytest.raises(ValueError, match="원래 에러"):
        await exception_handler(_request(), ValueError("원래 에러"))
