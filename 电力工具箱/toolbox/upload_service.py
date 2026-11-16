"""Windows upload staging and generated workbook copy-back, independent of Tk."""
from __future__ import annotations
import shutil
import tempfile
from pathlib import Path


def copy_upload_file(source: Path, destination_dir: Path) -> Path:
    source = Path(source)
    destination = Path(destination_dir) / source.name
    shutil.copy2(source, destination)
    return destination


def run_group_upload_with_staging(module, paths: list[Path], *, force: bool, on_log) -> None:
    target_date, original_uploads = module.prepare_upload_files(paths)
    original_province = next((item for item in original_uploads if item.kind == "省内"), None)
    original_energy = next(item for item in original_uploads if item.kind == "能销")

    with tempfile.TemporaryDirectory(prefix="electricity-group-upload-") as directory:
        staging_dir = Path(directory)
        on_log("正在暂存所选 Excel 到临时目录...")
        staged_paths = [
            item.path if item.builtin_template else copy_upload_file(item.path, staging_dir)
            for item in original_uploads
        ]
        _, staged_uploads = module.prepare_upload_files(staged_paths)
        staged_province = next((item for item in staged_uploads if item.kind == "省内"), None)
        staged_output = None
        if staged_province:
            output_options = {"output_dir": staging_dir} if staged_province.builtin_template else {}
            staged_output = module.province_output_path(staged_province.path, target_date, **output_options)

        module.run_api_upload(staged_paths, force=force)

        if original_province is None or staged_output is None:
            return

        copied_output = copy_upload_file(
            staged_output,
            original_energy.path.parent if original_province.builtin_template else original_province.path.parent,
        )
        on_log(f"已将更新后的省内数据复制回原目录：{copied_output}")
