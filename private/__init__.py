"""可选的私有 overlay（不属于公开仓库）。

公开仓库只保留这个空桩，保证 `docker build` 永远成功；私有部署时把功能模块
直接放进本包（已被 .gitignore 忽略），`app.main` 会在核心路由注册完成后调用
本函数，自动发现并加载所有不以 `_` 开头的子模块。

每个子模块必须提供 `register(app, ctx)`；`ctx` 包含 config / store / audit /
require_auth / logger，与核心路由使用同一套依赖。
"""


def register(app, ctx) -> list[str]:
    """加载所有子模块，返回已成功加载的模块名。"""
    import importlib
    import pkgutil

    loaded: list[str] = []
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        hook = getattr(module, "register", None)
        if hook is None:
            raise AttributeError(f"{module.__name__} 必须定义 register(app, ctx)")
        hook(app, ctx)
        loaded.append(info.name)
    return loaded
