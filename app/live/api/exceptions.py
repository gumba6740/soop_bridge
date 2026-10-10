from app.core.exc import BaseAppException


class StreamerNotFound(BaseAppException):
    status_code = 404
    code = "STREAMER_NOT_FOUND"
    detail = "등록되지 않은 스트리머입니다."


class LiveNotFound(BaseAppException):
    status_code = 404
    code = "LIVE_NOT_FOUND"
    detail = "방송 중이 아닙니다."