from __future__ import annotations

import json
import sys
from pathlib import Path


PUBLIC_APP_ROOT = Path(__file__).resolve().parents[1] / "miniapp-public"
TEXT_SUFFIXES = {".js", ".json", ".wxml", ".wxss"}
FORBIDDEN_TERMS = {
    "A股",
    "K线",
    "买入",
    "买点",
    "卖出",
    "卖点",
    "做T",
    "个股",
    "主线龙头",
    "仓位",
    "止损",
    "止盈",
    "涨停",
    "目标价",
    "证券",
    "荐股",
    "股票",
    "股价",
    "行情",
    "量化",
    "选股",
    "预测走势",
}
FORBIDDEN_API_PATHS = {
    "/alerts",
    "/market",
    "/paper",
    "/positions",
    "/recommendations",
    "/screener",
    "/stocks",
}
EXPECTED_TITLE = "个人复盘助手"


def collect_violations(root: Path = PUBLIC_APP_ROOT) -> list[str]:
    violations: list[str] = []
    if not root.is_dir():
        return [f"公开版目录不存在：{root}"]

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        relative = path.relative_to(root)
        text = path.read_text(encoding="utf-8")
        for term in sorted(FORBIDDEN_TERMS):
            if term.lower() in text.lower():
                violations.append(f"{relative}: 包含不适合公开提审版的内容“{term}”")
        for api_path in sorted(FORBIDDEN_API_PATHS):
            if api_path in text:
                violations.append(f"{relative}: 引用了公开版禁用接口“{api_path}”")

    app_json_path = root / "app.json"
    try:
        app_json = json.loads(app_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        violations.append(f"app.json 无法读取：{exc}")
    else:
        title = str(app_json.get("window", {}).get("navigationBarTitleText") or "")
        if title != EXPECTED_TITLE:
            violations.append(f"app.json 标题应为“{EXPECTED_TITLE}”，当前为“{title}”")

    cloud_config = (root / "cloud.config.js").read_text(encoding="utf-8")
    if cloud_config.count("service: 'fupanbiji'") != 3:
        violations.append("开发版、体验版和正式版必须全部连接隔离服务 fupanbiji")
    if "gupiaoxiaochengxu" in cloud_config:
        violations.append("公开版不得连接个人自用研究服务")
    return violations


def main() -> int:
    violations = collect_violations()
    if violations:
        print("公开小程序合规检查未通过：")
        for violation in violations:
            print(f"- {violation}")
        return 1
    print("公开小程序合规检查通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
