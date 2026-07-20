from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_quant.notify import send_webhook
from stock_quant.recommender import HORIZON_LABELS, scan_recommendations
from stock_quant.review import refresh_recommendation_outcomes
from stock_quant.settings import STRATEGY_VERSION
from stock_quant.storage import (
    create_run,
    save_capital_hotspots,
    save_market_snapshot,
    save_recommendations,
    update_run,
)


def build_markdown(frame) -> str:
    lines = ["## A股每日量化推荐", f"生成日期：{date.today():%Y年%m月%d日}", ""]
    if frame.empty:
        lines.append("今日没有满足条件的推荐。")
        return "\n".join(lines)

    for horizon in ("short", "mid", "long"):
        data = frame[frame["horizon"] == horizon].sort_values("score", ascending=False)
        if data.empty:
            continue
        lines.append(f"### {HORIZON_LABELS[horizon]}")
        for _, row in data.iterrows():
            topics = row.get("topic_text", "")
            topic_text = f"，题材：{topics}" if topics else ""
            lines.append(
                f"- {row['symbol']} {row['name']}：{row['priority']}，评分 {row['score']:.1f}，"
                f"买点 {row['buy_zone_low']:.2f}-{row['buy_zone_high']:.2f}，"
                f"止损 {row['stop_loss']:.2f}，目标 {row['take_profit_1']:.2f}/{row['take_profit_2']:.2f}{topic_text}"
            )
        lines.append("")

    lines.append("量化结果仅作研究和风控参考，不承诺收益。")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成、保存并推送每日A股量化推荐。")
    parser.add_argument("--scan-size", type=int, default=int(os.getenv("SCAN_SIZE", "50")), help="K线复核候选数")
    parser.add_argument("--top-n", type=int, default=min(10, int(os.getenv("TOP_N", "10"))), help="每个周期推荐上限")
    parser.add_argument(
        "--min-turnover",
        type=float,
        default=float(os.getenv("MIN_TURNOVER", "50000000")),
        help="最低成交额，单位为元",
    )
    parser.add_argument("--provider", default=os.getenv("PUSH_PROVIDER", "企业微信"), help="推送通道")
    parser.add_argument("--webhook-url", default=os.getenv("PUSH_WEBHOOK_URL", ""), help="官方机器人地址")
    parser.add_argument(
        "--ignore-proxy",
        action=argparse.BooleanOptionalAction,
        default=os.getenv("IGNORE_PROXY", "false").lower() in {"1", "true", "yes", "on"},
        help="是否忽略系统代理",
    )
    parser.add_argument("--no-send", action="store_true", help="只生成和保存，不发送推送")
    args = parser.parse_args()

    run_id = create_run(
        trade_date=date.today().strftime("%Y-%m-%d"),
        scan_size=args.scan_size,
        top_n=args.top_n,
        min_turnover=args.min_turnover,
        strategy_version=STRATEGY_VERSION,
        parameters={
            "K线复核候选数": args.scan_size,
            "每周期推荐上限": args.top_n,
            "最低成交额": args.min_turnover,
            "忽略系统代理": args.ignore_proxy,
        },
    )
    try:
        frame, _context, errors = scan_recommendations(
            run_id=run_id,
            scan_size=args.scan_size,
            top_n=args.top_n,
            min_turnover=args.min_turnover,
            ignore_proxy=args.ignore_proxy,
        )
        save_recommendations(frame.to_dict("records"))
        save_capital_hotspots(run_id, _context.capital_hotspots)
        save_market_snapshot(run_id, _context.breadth, _context.global_summary, _context.global_indices)
        update_run(run_id, "done", f"生成 {len(frame)} 条推荐；数据源提示 {len(errors)} 条")
        _reviewed, review_errors = refresh_recommendation_outcomes(
            ignore_proxy=args.ignore_proxy,
        )
        errors.extend(review_errors)
    except Exception as exc:
        update_run(run_id, "failed", str(exc))
        print(f"扫描失败：{exc}", file=sys.stderr)
        return 2

    markdown = build_markdown(frame)
    print(markdown)

    if args.no_send:
        return 0

    ok, message = send_webhook(args.provider, args.webhook_url, markdown)
    print(f"推送结果：{'成功' if ok else '失败'}，{message}")
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
