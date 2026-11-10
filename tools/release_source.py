"""Verify the source boundary of a Windows release before changing build trees."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def inspect_source(root: Path = ROOT, *, development: bool = False) -> dict:
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root).decode("utf-8").strip()

    commit = git("rev-parse", "HEAD")
    dirty = bool(git("status", "--porcelain", "--untracked-files=all"))
    if dirty and not development:
        # Do not print paths: an untracked filename can itself contain business data.
        raise ValueError("正式构建要求干净 Git 工作区；先审阅提交，或用 -DevelopmentBuild 生成不可发布的开发包。")
    return {"commit": commit, "development": development, "dirty": dirty}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--development", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source = inspect_source(development=args.development)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(source, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(source))
