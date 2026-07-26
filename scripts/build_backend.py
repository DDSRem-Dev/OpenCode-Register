from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

LOGGER = logging.getLogger("build_backend")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = PROJECT_ROOT / "backend"
BACKEND_ENTRY = BACKEND_ROOT / "main.py"
SIDECAR_DIRECTORY = PROJECT_ROOT / "src-tauri" / "binaries"
BUILD_ROOT = PROJECT_ROOT / "build" / "pyinstaller"
DIST_ROOT = PROJECT_ROOT / "build" / "dist"
SIDECAR_STEM = "backend"
WINDOWS_TRIPLE_MARKER = "windows"

# uvicorn 通过 import_from_string 动态加载协议、事件循环与 lifespan 实现，
# cloakbrowser 的 human 子包同样不被静态分析捕获；两者都必须整包收集。
COLLECTED_PACKAGES = ("uvicorn", "cloakbrowser")


class BuildError(RuntimeError):
    """
    冻结后端过程中的不可恢复错误
    """


def main(argv: Optional[List[str]] = None) -> int:
    """
    冻结 Python 后端并放置为 Tauri sidecar 二进制

    :param argv (List): 命令行参数，缺省时使用当前进程参数

    :return int: 进程退出码
    """

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    arguments = _parse_arguments(argv)
    try:
        target_triple = arguments.target or resolve_host_triple()
        if arguments.placeholder:
            artifact = write_sidecar_placeholder(target_triple)
        else:
            artifact = freeze_backend(target_triple, clean=arguments.clean)
    except BuildError as error:
        LOGGER.error("%s", error)
        return 1
    LOGGER.info("sidecar ready: %s", artifact)
    return 0


def resolve_host_triple() -> str:
    """
    读取当前 Rust 工具链的宿主目标三元组

    Tauri 按 `binaries/<name>-<target triple>` 定位 sidecar，因此二进制文件名后缀
    必须与 Rust 的 target triple 完全一致，不能按操作系统名自行拼装

    :return str: 形如 aarch64-apple-darwin 的目标三元组

    :raises BuildError: rustc 不可用或输出中没有 host 字段
    """

    try:
        completed = subprocess.run(
            ["rustc", "-vV"],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise BuildError("未找到 rustc，请先安装 Rust 工具链或显式传入 --target") from error
    except subprocess.CalledProcessError as error:
        raise BuildError("rustc -vV 执行失败，无法确定目标三元组") from error

    for line in completed.stdout.splitlines():
        if line.startswith("host:"):
            return line.split(":", 1)[1].strip()
    raise BuildError("rustc -vV 输出中没有 host 字段，无法确定目标三元组")


def freeze_backend(target_triple: str, clean: bool = False) -> Path:
    """
    使用 PyInstaller 把后端冻结为单文件可执行程序

    PyInstaller 不支持交叉编译，因此该函数只能产出当前机器架构的二进制；
    target_triple 仅决定产物文件名，调用方需保证在匹配的机器上构建

    :param target_triple (str): 目标三元组，决定 sidecar 文件名后缀
    :param clean (bool): 是否在构建前清理 PyInstaller 缓存

    :return Path: 已放置到 src-tauri/binaries 的 sidecar 路径

    :raises BuildError: 后端入口缺失或 PyInstaller 构建失败
    """

    if not BACKEND_ENTRY.is_file():
        raise BuildError(f"未找到后端入口 {BACKEND_ENTRY}")

    LOGGER.info("freezing backend for %s", target_triple)
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    DIST_ROOT.mkdir(parents=True, exist_ok=True)
    command = _pyinstaller_command(clean=clean)
    try:
        subprocess.run(command, check=True, cwd=PROJECT_ROOT)
    except FileNotFoundError as error:
        raise BuildError("未找到 pyinstaller，请先执行 uv sync --project backend --group dev") from error
    except subprocess.CalledProcessError as error:
        raise BuildError("PyInstaller 构建失败") from error

    return _place_sidecar(target_triple)


def write_sidecar_placeholder(target_triple: str) -> Path:
    """
    写入一个空的 sidecar 占位文件

    `tauri.conf.json` 声明了 externalBin，`tauri-build` 会在 build script 阶段校验其存在，
    因此缺少该文件时连 `cargo check` 与 `npm run tauri dev` 都无法执行。开发与 CI 用占位
    文件满足该校验，实际启动仍回落到开发期 Python 解释器

    :param target_triple (str): 目标三元组，决定 sidecar 文件名后缀

    :return Path: 占位文件路径
    """

    SIDECAR_DIRECTORY.mkdir(parents=True, exist_ok=True)
    placeholder = SIDECAR_DIRECTORY / sidecar_file_name(target_triple)
    placeholder.write_bytes(b"")
    placeholder.chmod(0o755)
    return placeholder


def sidecar_file_name(target_triple: str) -> str:
    """
    按目标三元组推导 sidecar 文件名

    :param target_triple (str): 目标三元组

    :return str: Tauri externalBin 期望的文件名
    """

    suffix = ".exe" if WINDOWS_TRIPLE_MARKER in target_triple else ""
    return f"{SIDECAR_STEM}-{target_triple}{suffix}"


def _parse_arguments(argv: Optional[List[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Freeze the OpenCode Register backend into a Tauri sidecar binary")
    parser.add_argument(
        "--target",
        default=None,
        help="Rust target triple for the produced binary; defaults to the rustc host triple",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove PyInstaller caches before building",
    )
    parser.add_argument(
        "--placeholder",
        action="store_true",
        help="Write an empty sidecar file to satisfy tauri-build without running PyInstaller",
    )
    return parser.parse_args(argv)


def _pyinstaller_command(clean: bool) -> List[str]:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--noconfirm",
        "--name",
        SIDECAR_STEM,
        "--paths",
        str(BACKEND_ROOT),
        "--distpath",
        str(DIST_ROOT),
        "--workpath",
        str(BUILD_ROOT),
        "--specpath",
        str(BUILD_ROOT),
    ]
    for package in COLLECTED_PACKAGES:
        command.extend(["--collect-submodules", package])
    if clean:
        command.append("--clean")
    command.append(str(BACKEND_ENTRY))
    return command


def _place_sidecar(target_triple: str) -> Path:
    produced = DIST_ROOT / (f"{SIDECAR_STEM}.exe" if WINDOWS_TRIPLE_MARKER in target_triple else SIDECAR_STEM)
    if not produced.is_file():
        raise BuildError(f"PyInstaller 未产出预期文件 {produced}")

    SIDECAR_DIRECTORY.mkdir(parents=True, exist_ok=True)
    destination = SIDECAR_DIRECTORY / sidecar_file_name(target_triple)
    shutil.copy2(produced, destination)
    destination.chmod(0o755)
    return destination


if __name__ == "__main__":
    raise SystemExit(main())
