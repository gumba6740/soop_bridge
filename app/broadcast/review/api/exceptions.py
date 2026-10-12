from app.core.exc import BaseAppException


class StreamerNotFound(BaseAppException):
    status_code = 404
    code = "STREAMER_NOT_FOUND"
    detail = "등록되지 않은 스트리머입니다."


class VodNotFound(BaseAppException):
    status_code = 404
    code = "VOD_NOT_FOUND"
    detail = "VOD를 찾을 수 없습니다."
