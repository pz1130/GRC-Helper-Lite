from collections.abc import Iterable, Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    """业务错误。

    `code` 是**给前端查词条用的键**，`message` 是中文句子，只在前端还没有
    这个 code 的词条时兜底显示（`api.ts` 的 `defaultValue`）。所以：

        raise NotFound("审计项目不存在", code="audit.engagement_not_found")

    不带 `code` 时回落到 HTTP 类别（`not_found` / `conflict` / …）。那是
    迁移前的原状：40 多种"某某不存在"共用一个 `not_found`，前端翻不了，
    只能把中文原样显示。**新写的错误一律带 code**，老的逐批迁。

    句子里有变量的，变量跟着 `params` 一起过去，由前端的词条排版：

        raise Conflict(
            f"还有 {n} 条证据在用这个类型",
            code="evidence_type.in_use",
            params={"count": n},
        )

    中文那句仍然自己把值填好——它是前端还没有这条词条时的兜底。
    """

    status_code = 400
    code = "bad_request"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        params: dict[str, object] | None = None,
    ) -> None:
        self.message = message
        self.params = params
        if code is not None:
            self.code = code
        super().__init__(message)


class BadRequest(AppError):
    status_code = 400
    code = "bad_request"


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class PayloadTooLarge(AppError):
    status_code = 413
    code = "payload_too_large"


def describe_validation(errors: Iterable[Mapping[str, Any]]) -> str:
    """把 Pydantic 的校验错误写成给人看的一行：「字段: 问题; 字段: 问题」。

    原来直接用 `str(ValidationError)`：带模型名、`input_value=…`、文档链接，
    是写给开发者的，审核人在确认队列里看到的就是这一坨。
    """
    parts = []
    for error in errors:
        location = ".".join(str(part) for part in error.get("loc", ()) if part != "body") or "(root)"
        message = str(error.get("msg", "")).removeprefix("Value error, ")
        parts.append(f"{location}: {message}")
    return "; ".join(parts)


def install_error_handlers(application: FastAPI) -> None:
    @application.exception_handler(RequestValidationError)
    async def _handle_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # 默认的 422 只有 `detail` 列表，没有 code/message，前端只能显示「请求失败」，
        # 用户不知道哪一栏错了。`detail` 原样保留，另外补上 code 与一行可读的说明。
        summary = describe_validation(exc.errors())
        # 用 AppError 组出来而不是手写字典：`code=` 字面量才扫得进 i18n-check。
        error = AppError(summary, code="request.invalid", params={"detail": summary})
        return JSONResponse(
            status_code=422,
            content={
                "code": error.code,
                "message": error.message,
                "params": error.params,
                "detail": jsonable_encoder(exc.errors()),
            },
        )

    @application.exception_handler(AppError)
    async def _handle(_: Request, exc: AppError) -> JSONResponse:
        body: dict[str, object] = {"code": exc.code, "message": exc.message}
        # 绝大多数错误没有变量，不给响应体凭空加一个 null 字段。
        if exc.params:
            body["params"] = exc.params
        return JSONResponse(status_code=exc.status_code, content=body)
