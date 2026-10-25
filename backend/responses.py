"""统一响应格式 + 错误码 + 业务异常。

service 层只抛 BusinessError，怎么变成 HTTP 响应由 app.py 统一决定，
路由代码因此不用关心状态码和报文格式。
"""

ERROR_OK = 0
ERROR_REPO_INVALID = 1001      # 仓库路径不存在或不是目录
ERROR_NEED_KEY = 1002          # 需要 API Key 才能生成 AI 说明
ERROR_TASK_NOT_FOUND = 1003    # 分析任务不存在
ERROR_INTERNAL = 9999          # 服务内部错误


def ok(data=None, message: str = "ok") -> dict:
    return {"code": ERROR_OK, "message": message, "data": data}


def fail(code: int, message: str, http_status: int = 400) -> tuple[dict, int]:
    # 返回 (响应体, 状态码) 元组，Flask 会自动把第二项当 HTTP 状态码
    return {"code": code, "message": message, "data": None}, http_status


class BusinessError(Exception):
    """业务异常 —— service 层唯一允许抛出的异常类型。"""

    def __init__(self, code: int, message: str, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
