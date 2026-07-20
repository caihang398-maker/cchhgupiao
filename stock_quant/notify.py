from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import requests

from .settings import REPORTS_DIR


PROVIDER_HOSTS = {
    "企业微信": {"qyapi.weixin.qq.com"},
    "钉钉": {"oapi.dingtalk.com"},
    "飞书": {"open.feishu.cn", "open.larksuite.com"},
}


def _canonical_provider(provider: str) -> str | None:
    value = provider.strip().lower()
    aliases = {
        "企业微信": "企业微信",
        "wecom": "企业微信",
        "wechat_work": "企业微信",
        "钉钉": "钉钉",
        "dingtalk": "钉钉",
        "飞书": "飞书",
        "feishu": "飞书",
        "lark": "飞书",
    }
    return aliases.get(value)


def validate_webhook_url(provider: str, webhook_url: str) -> tuple[bool, str]:
    canonical = _canonical_provider(provider)
    if canonical is None:
        return False, f"暂不支持的推送类型：{provider}"
    raw = webhook_url.strip()
    if not raw:
        return False, "机器人地址不能为空"
    parsed = urlparse(raw)
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        return False, "机器人地址必须是官方HTTPS地址"
    allowed_hosts = set(PROVIDER_HOSTS[canonical])
    extra_hosts = {
        host.strip().lower()
        for host in os.getenv("PUSH_ALLOWED_HOSTS", "").split(",")
        if host.strip()
    }
    allowed_hosts.update(extra_hosts)
    if parsed.hostname.lower() not in allowed_hosts:
        return False, f"机器人地址域名与所选通道不匹配：{parsed.hostname}"
    return True, canonical


def mask_webhook_url(webhook_url: str) -> str:
    raw = str(webhook_url or "").strip()
    if not raw:
        return "-"
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.hostname:
        return "地址已配置"
    return f"{parsed.scheme}://{parsed.hostname}/...（密钥已隐藏）"


def format_markdown_report(candidates: pd.DataFrame, title: str = "A股量化候选") -> str:
    now = datetime.now().strftime("%Y年%m月%d日 %H:%M")
    lines = [f"## {title}", f"生成时间：{now}", ""]

    if candidates.empty:
        lines.append("今日没有满足条件的候选。")
        return "\n".join(lines)

    keep = candidates.head(10)
    for _, row in keep.iterrows():
        lines.append(
            f"- **{row['代码']} {row['名称']}**：评分 {row['评分']}，{row['级别']}，"
            f"买点 {row['买点下沿']}-{row['买点上沿']}，止损 {row['止损']}，目标 {row['目标一']}/{row['目标二']}"
        )
    lines.append("")
    lines.append("仅作量化研究和风险管理参考，不构成收益承诺。")
    return "\n".join(lines)


def send_webhook(provider: str, webhook_url: str, markdown: str) -> tuple[bool, str]:
    valid, canonical_or_error = validate_webhook_url(provider, webhook_url)
    if not valid:
        return False, canonical_or_error
    canonical = canonical_or_error

    if canonical == "企业微信":
        payload = {"msgtype": "markdown", "markdown": {"content": markdown}}
    elif canonical == "钉钉":
        payload = {"msgtype": "markdown", "markdown": {"title": "A股量化候选", "text": markdown}}
    elif canonical == "飞书":
        payload = {"msg_type": "text", "content": {"text": markdown}}

    try:
        response = requests.post(webhook_url, json=payload, timeout=15)
        response.raise_for_status()
    except requests.Timeout:
        return False, "推送失败：通知平台响应超时"
    except requests.RequestException:
        return False, "推送失败：无法连接通知平台或平台返回HTTP错误"

    try:
        result = response.json()
    except ValueError:
        result = {}
    error_code = result.get("errcode", result.get("code", result.get("StatusCode", 0)))
    if error_code not in (None, 0, "0"):
        message = result.get("errmsg") or result.get("msg") or result.get("StatusMessage") or "平台拒绝了推送"
        return False, f"推送失败：{message}"
    return True, "推送成功"


def save_report(candidates: pd.DataFrame, output_dir: str | Path = REPORTS_DIR) -> tuple[Path, Path]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = directory / f"量化候选_{stamp}.csv"
    md_path = directory / f"量化候选_{stamp}.md"
    candidates.to_csv(csv_path, index=False, encoding="utf-8-sig")
    md_path.write_text(format_markdown_report(candidates), encoding="utf-8")
    return csv_path, md_path
