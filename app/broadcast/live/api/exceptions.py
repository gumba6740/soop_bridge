from app.core.exc import BaseAppException


class LiveNotFound(BaseAppException):
    status_code = 404
    code = "LIVE_NOT_FOUND"
    detail = "방송 중이 아닙니다."