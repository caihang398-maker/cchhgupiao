from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
RELEASE_DIR = ROOT / "release"
TOP_LEVEL_FILES = {
    "app.py",
    "dashboard.py",
    "README.md",
    "requirements.txt",
    "requirements-server.txt",
}
SOURCE_DIRECTORIES = {
    ".streamlit",
    "database",
    "docs",
    "pages",
    "scripts",
    "stock_quant",
    "tests",
}
EXCLUDED_NAMES = {
    ".git",
    ".venv",
    "__pycache__",
    "artifacts",
    "data",
    "logs",
    "release",
    "reports",
    "secrets.toml",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".log", ".sqlite3"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_allowed(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    if any(part in EXCLUDED_NAMES for part in relative.parts):
        return False
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    return path.is_file()


def source_files() -> list[Path]:
    files: set[Path] = set()
    for name in TOP_LEVEL_FILES:
        candidate = ROOT / name
        if not candidate.is_file():
            raise FileNotFoundError(f"发布所需文件不存在：{candidate}")
        files.add(candidate)
    for directory_name in SOURCE_DIRECTORIES:
        directory = ROOT / directory_name
        if not directory.is_dir():
            raise FileNotFoundError(f"发布所需目录不存在：{directory}")
        files.update(path for path in directory.rglob("*") if is_allowed(path))
    return sorted(files, key=lambda item: item.relative_to(ROOT).as_posix())


def run_check(command: list[str], env: dict[str, str] | None = None) -> None:
    completed = subprocess.run(command, cwd=ROOT, env=env, check=False)
    if completed.returncode:
        raise RuntimeError("检查命令失败：" + " ".join(command))


def build(skip_tests: bool, full_app_check: bool) -> Path:
    if not skip_tests:
        run_check([sys.executable, "-m", "compileall", "-q", "app.py", "dashboard.py", "stock_quant", "pages", "scripts", "tests"])
        run_check([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
    if full_app_check:
        local_env = os.environ.copy()
        local_env["APP_ENV"] = "local"
        local_env["AUTH_ENABLED"] = "false"
        run_check([sys.executable, "scripts/release_check.py"], env=local_env)

    from stock_quant.settings import STRATEGY_VERSION

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    build_root = RELEASE_DIR / f"stock-quant-server-{timestamp}"
    archive_path = RELEASE_DIR / f"stock-quant-server-{timestamp}.zip"
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    if build_root.exists():
        shutil.rmtree(build_root)
    build_root.mkdir(parents=True)

    manifest_files: list[dict[str, object]] = []
    for source in source_files():
        relative = source.relative_to(ROOT)
        target = build_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        manifest_files.append(
            {
                "path": relative.as_posix(),
                "sha256": sha256(target),
                "size": target.stat().st_size,
            }
        )

    manifest = {
        "format_version": 1,
        "product": "A股每日量化推荐",
        "strategy_version": STRATEGY_VERSION,
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "python_version": sys.version.split()[0],
        "file_count": len(manifest_files),
        "files": manifest_files,
        "preserved_server_paths": [
            ".venv/",
            "data/",
            "logs/",
            "reports/",
            ".streamlit/secrets.toml",
        ],
    }
    manifest_path = build_root / "release_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if archive_path.exists():
        archive_path.unlink()
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(build_root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(build_root).as_posix())
    print(f"发布目录：{build_root}")
    print(f"发布压缩包：{archive_path}")
    print(f"文件数量：{len(manifest_files)}")
    return archive_path


def main() -> int:
    parser = argparse.ArgumentParser(description="构建带哈希清单的服务器发布包")
    parser.add_argument("--skip-tests", action="store_true", help="跳过编译和单元测试")
    parser.add_argument(
        "--full-app-check",
        action="store_true",
        help="额外以本地免登录模式运行完整Streamlit发布检查",
    )
    args = parser.parse_args()
    try:
        build(skip_tests=args.skip_tests, full_app_check=args.full_app_check)
    except Exception as exc:
        print(f"[失败] 构建发布包失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
