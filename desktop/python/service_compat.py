"""One-shot engine/config adapter; task ownership lives in the Rust service."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "电力工具箱")]


def dispatch(workspace, method, params):
    from toolbox.runtime import ToolPaths, python_executable
    from toolbox.service import ToolboxService
    from toolbox.workflows import validate
    service = ToolboxService(workspace)
    if method == "__prepare_start":
        tool, action = params.get("tool_id"), params.get("action", "run")
        values = validate(ToolPaths(workspace), tool, action, params.get("values", {}))
        writable = tool in {"market-table", "transaction-info", "power-benefit", "spot-price", "group-upload"}
        cancellable = action not in {"run", "update"} or not (
            writable or tool in {"wps-writer", "guangdong-price"}
            or tool == "summary-upload" and values["mode"] != "summary")
        return dict(values=values, confirmation=service.confirmation(tool, action, values),
                    name=service._tools[tool]["name"], cancellable=cancellable,
                    python=python_executable(), worker=str(ROOT / "desktop/python/task_worker.py"))
    if method in {"start", "tasks", "events", "cancel", "shutdown", "request_close", "installer_close"}:
        raise ValueError("任务控制必须由 Rust 服务执行")
    return service.call(method, params)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    request = json.load(sys.stdin)
    # Reserve stdout for transport even when an engine emits diagnostics.
    output = sys.stdout
    sys.stdout = sys.stderr
    try:
        result = dispatch(args.workspace, request["method"], request.get("params", {}))
        row = dict(result=result)
    except Exception as exc:
        from toolbox_redaction import redact
        row = dict(error=redact(str(exc)))
    output.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


if __name__ == "__main__":
    main()
