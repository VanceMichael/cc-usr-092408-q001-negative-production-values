"""稳定的错误响应信封与业务异常。

所有写入路径（创建 / 修改 / 批量导入 / 复核处置）失败时都返回同一结构：

    {
      "error": {
        "code": "validation_error",
        "message": "……",
        "fields": [{"field": "area", "code": "non_positive", "message": "……"}]
      },
    }

fields 以请求体中的字段路径为键（批量导入时形如 items[2].quantity），
调用方可据此稳定地定位并提示具体字段。
"""

from typing import Any, Dict, List, Optional


class FieldError(Exception):
    """带稳定字段错误列表的业务异常基类。"""

    code = "business_error"
    status_code = 400

    def __init__(
        self,
        message: str,
        fields: Optional[List[Dict[str, str]]] = None,
        code: Optional[str] = None,
        status_code: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.fields: List[Dict[str, str]] = fields or []

    def to_body(self) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "error": {
                "code": self.code,
                "message": self.message,
                "fields": list(self.fields),
            }
        }
        return body


class ValidationError(FieldError):
    code = "validation_error"
    status_code = 422


class NotFoundError(FieldError):
    code = "not_found"
    status_code = 404


class ConflictError(FieldError):
    """状态冲突，例如已签署批次被改写、并发重复冲正。"""

    code = "conflict"
    status_code = 409


class StateConflictError(ConflictError):
    code = "state_conflict"


class DuplicateReversalError(ConflictError):
    code = "duplicate_reversal"
