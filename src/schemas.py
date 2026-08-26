"""API 응답 포맷을 앱 전역에서 통일하기 위한 공용 스키마.

성공 응답: {"success": true, "result": ...}
에러 응답:  {"success": false, "error": {"code": "...", "message": "..."}}
"""

from http import HTTPStatus
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    success: bool = True
    result: T


class ApiError(BaseModel):
    code: str
    message: str
    # 기계가 읽어야 하는 부가 정보(예: /health의 다운된 model_id 목록).
    # message 문자열을 파싱하게 만들지 않기 위한 필드다.
    # 값이 없을 때는 응답에 나타나지 않는다(핸들러가 exclude_none으로 직렬화).
    details: dict[str, Any] | None = None


class ApiErrorResponse(BaseModel):
    success: bool = False
    error: ApiError


def error_code_for_status(status_code: int) -> str:
    """HTTP 상태 코드를 에러 코드 문자열로 변환한다 (예: 404 -> NOT_FOUND)."""
    try:
        return HTTPStatus(status_code).phrase.upper().replace(" ", "_").replace("-", "_")
    except ValueError:
        return "ERROR"
