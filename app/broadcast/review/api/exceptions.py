from app.core.exc import BaseAppException


class VodNotFound(BaseAppException):
    status_code = 404
    code = "VOD_NOT_FOUND"
    detail = "VOD를 찾을 수 없습니다."
