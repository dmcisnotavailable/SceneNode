"""旅行项目相关的安全目录校验服务。

把目录白名单校验独立成模块，避免在 API 层直接信任
用户输入的任意路径。前端传入的目录必须位于后端配置的
允许扫描根目录之内。
"""

import os

from pathlib import Path


def get_allowed_roots():
    """读取允许扫描的素材根目录列表。

    从环境变量 SCENENOTE_ALLOWED_ROOTS 读取，
    多个目录用英文分号分隔，例如：
    SCENENOTE_ALLOWED_ROOTS=D:\\Travel;E:\\Video
    """

    raw = os.getenv("SCENENOTE_ALLOWED_ROOTS", "")
    roots = []

    for item in raw.split(";"):
        item = item.strip()

        if item:
            roots.append(Path(item).resolve())

    return roots


def validate_source_directory(directory: str) -> Path:
    """校验用户输入的素材目录是否允许扫描。

    校验通过时返回规范化后的目录 Path。

    抛出的异常：
    - ValueError:      路径不存在、不是目录或未配置白名单。
    - PermissionError: 目录不在允许的根目录范围内。

    说明：该校验能阻止普通的路径越界输入，但不是
    完整的操作系统级沙箱。生产环境还需考虑目录权限
    变化、符号链接和扫描期间文件被替换等情况。
    """

    candidate = Path(directory).resolve(strict=True)

    if not candidate.is_dir():
        raise ValueError("指定路径不是目录")

    roots = get_allowed_roots()

    if not roots:
        raise ValueError("尚未配置允许扫描的素材根目录")

    allowed = False

    for root in roots:
        try:
            candidate.relative_to(root)
            allowed = True
            break
        except ValueError:
            continue

    if not allowed:
        raise PermissionError("不允许扫描该目录")

    return candidate
