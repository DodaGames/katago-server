"""API 응답 포맷을 앱 전역에서 통일하기 위한 공용 스키마.

성공 응답: {"success": true, "result": ...}
에러 응답:  {"success": false, "error": {"code": "...", "message": "..."}}
"""

from http import HTTPStatus
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    success: bool = True
    result: T


class ApiError(BaseModel):
    code: str
    message: str


class ApiErrorResponse(BaseModel):
    success: bool = False
    error: ApiError


def error_code_for_status(status_code: int) -> str:
    """HTTP 상태 코드를 에러 코드 문자열로 변환한다 (예: 404 -> NOT_FOUND)."""
    try:
        return HTTPStatus(status_code).phrase.upper().replace(" ", "_").replace("-", "_")
    except ValueError:
        return "ERROR"
