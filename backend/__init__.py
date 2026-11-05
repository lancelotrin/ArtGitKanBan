"""大豆看板的新后端：FastAPI 只出 JSON API，页面由 web/（React）构建好的静态文件提供。

数据和规则仍在根目录的 *_board.py 里（服务层），这里只是 HTTP 外壳。
以后的聊天 agent 直接调用同一套服务层函数，或者把 /api/v1 的接口当工具用（OpenAPI 在 /api/v1/docs）。
"""
