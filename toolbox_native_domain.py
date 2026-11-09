"""Small, explicit transport boundary for migrated Rust business domains."""
from __future__ import annotations

import os
import subprocess
import warnings
from functools import wraps

from toolbox_native_excel import call, native_binary
from toolbox_native_excel import excel_session


def backend_mode():
    mode = os.environ.get("TOOLBOX_DOMAIN_BACKEND", os.environ.get("TOOLBOX_XLSX_BACKEND", "auto"))
    if mode not in {"auto", "python", "rust"}:
        raise ValueError(f"未知业务计算后端: {mode}")
    return mode


def with_native_session(function):
    """Reuse one lazy native process during a complete business operation."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        with excel_session():
            return function(*args, **kwargs)
    return wrapped


def invoke(domain, action, payload, *, side_effects=False):
    """Return a native result or None when a pure auto operation can fall back.

    None is reserved for implementation selection, so domain operations return
    an object/list/scalar rather than JSON null. No payload values are logged.
    Mutating operations propagate all failures after selecting their process.
    """
    mode = backend_mode()
    if not isinstance(payload, dict) or "action" in payload:
        raise ValueError("业务计算参数必须是对象，且不能覆盖 action")
    if mode == "python":
        return None
    binary = native_binary()
    if binary is None:
        if mode == "rust":
            raise RuntimeError("未安装 Rust 业务后端，请构建 native/price-xlsx")
        return None
    try:
        response = call(binary, domain, "", payload={"action": action, **payload})
        if not isinstance(response, dict) or "result" not in response or response["result"] is None:
            raise ValueError("Rust 业务响应缺少有效 result")
        return response["result"]
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        if mode == "rust" or side_effects:
            if not side_effects and isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
                detail = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
                raise ValueError(detail.strip()) from exc
            raise
        warnings.warn(f"Rust {domain}.{action} 暂不可用，采用兼容实现（{type(exc).__name__}）",
                      RuntimeWarning, stacklevel=2)
        return None
