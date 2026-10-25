"""Flask 应用入口：工厂注册蓝图和统一错误处理。

启动方式：在 backend/ 目录下执行 `python app.py`（127.0.0.1:5000，debug 模式）。
注意：debug 的热重载在代码变动时会重启进程，内存任务表会被清空——
这是内存任务模型的已知现象，暂不修。
"""

import logging

from flask import Flask
from werkzeug.exceptions import HTTPException

from api.routes import api_bp
from responses import BusinessError, fail, ERROR_INTERNAL


def create_app() -> Flask:
    """应用工厂：注册蓝图、统一错误处理。"""
    app = Flask(__name__)
    # jsonify 直接输出中文而不是 \uXXXX，curl 和浏览器里都好读
    app.json.ensure_ascii = False
    app.register_blueprint(api_bp)

    @app.errorhandler(BusinessError)
    def handle_business_error(exc: BusinessError):
        # 业务异常是"预期内的失败"，转成统一响应即可，不需要堆栈
        return fail(exc.code, exc.message, exc.http_status)

    @app.errorhandler(Exception)
    def handle_unexpected(exc: Exception):
        if isinstance(exc, HTTPException):
            return exc   # 普通 404/405 保持 Flask 默认行为，别误报成 500
        # 未预期异常必须记堆栈，否则线上查不出问题
        app.logger.exception("未捕获异常")
        return fail(ERROR_INTERNAL, "服务内部错误", http_status=500)

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    create_app().run(host="127.0.0.1", port=5000, debug=True)
