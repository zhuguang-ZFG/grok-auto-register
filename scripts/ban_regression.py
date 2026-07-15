#!/usr/bin/env python
"""死号回归分析：把封禁回归到具体的可关联轴，替代"猜"。

背景（AGENTS.md / docs/STATUS.md）：xAI 疑似"按注册管道指纹批量封"，
但此前无数据可证伪——死号历史里没记注册时的出口节点/指纹。
grok_register_ttk.record_reg_metric 现在会记 email + egress + 生效 UA/vp/tz，
本脚本把 cpa_auths(_dead) 的存活/死亡状态跟这些 metric 按 email 关联，
算每个轴（域名 / 出口节点 / UA / viewport / tz）的死亡率。

两种数据源，自动降级：
  1) 域名轴：直接扫 cpa_auths / cpa_auths_dead 的 email 域名——**今天就能跑**，
     复现 "hotmail 死 97%" 结论，确认域名是不是主因。
  2) 出口/指纹轴：join reg_metrics.jsonl（需 email 字段）——历史行无 email 会跳过，
     随着新号积累而逐步完整。

只读；不改任何号、不发网络请求。

用法：
    python scripts/ban_regression.py                 # 全轴摘要
    python scripts/ban_regression.py --axis egress   # 只看出口节点
    python scripts/ban_regression.py --min-n 20       # 隐藏样本 < 20 的桶
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
try:
    from stdio_utf8 import ensure_utf8_stdio  # noqa: E402

    ensure_utf8_stdio()
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
LIVE_DIR = ROOT / "cpa_auths"
DEAD_DIR = ROOT / "cpa_auths_dead"
METRICS = ROOT / "logs" / "reg_metrics.jsonl"

# refresh_revoked = 服务端确证吊销（真死）。permission-denied 是可自愈通道闸，
# 不算"封禁"，回归时排除以免污染信号（见 AGENTS.md 判死铁律第 4 条）。
TERMINAL_REASONS = {"refresh_revoked", "missing_refresh_token"}


def _email_of(path: Path) -> str:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return str(d.get("email") or "").strip().lower()
    except Exception:
        return ""


def _dead_reason(path: Path) -> str:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        qs = d.get("quota_state") or {}
        return str(qs.get("reason") or "").strip()
    except Exception:
        return ""


def _domain(email: str) -> str:
    return email.split("@", 1)[1] if "@" in email else "(none)"


def load_status() -> dict[str, str]:
    """email -> 'live' | 'dead' (仅 terminal 死因，permission-denied 不计)。"""
    status: dict[str, str] = {}
    for p in LIVE_DIR.glob("xai-*.json"):
        em = _email_of(p)
        if em:
            status[em] = "live"
    for p in DEAD_DIR.glob("xai-*.json"):
        em = _email_of(p)
        if not em:
            continue
        # unknown.local = 历史铸造丢了真实 email，无法关联，跳过
        if em.endswith("@unknown.local"):
            continue
        if _dead_reason(p) in TERMINAL_REASONS:
            status[em] = "dead"
        else:
            # 非 terminal（如 permission-denied 软禁用）当作存活，不算封禁样本
            status.setdefault(em, "live")
    return status


def load_metric_axes() -> dict[str, dict]:
    """email -> {egress, fp_ua, fp_vp, fp_tz}（取该 email 最后一次 success/fail 行）。"""
    axes: dict[str, dict] = {}
    if not METRICS.is_file():
        return axes
    for line in METRICS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("event") not in ("success", "fail"):
            continue
        em = str(r.get("email") or "").strip().lower()
        if not em:
            continue
        axes[em] = {
            "egress": r.get("egress", ""),
            "fp_ua": r.get("fp_ua", ""),
            "fp_vp": r.get("fp_vp", ""),
            "fp_tz": r.get("fp_tz", ""),
        }
    return axes


def _short_ua(ua: str) -> str:
    if not ua or ua == "browser_default":
        return ua or "(none)"
    # 提取 "Chrome/138" + 平台 首词
    plat = "?"
    for tag in ("Windows", "Macintosh", "Linux"):
        if tag in ua:
            plat = tag
            break
    ver = ""
    if "Chrome/" in ua:
        ver = "Chrome/" + ua.split("Chrome/", 1)[1].split(".", 1)[0]
    return f"{plat} {ver}".strip()


def bucket_rates(
    status: dict[str, str], key_fn, min_n: int = 1
) -> list[tuple[str, int, int, float]]:
    """返回 [(bucket, n, dead, death_rate)] 按死亡率降序。"""
    agg: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # [n, dead]
    for em, st in status.items():
        k = key_fn(em)
        if k is None:
            continue
        agg[k][0] += 1
        if st == "dead":
            agg[k][1] += 1
    rows = []
    for k, (n, dead) in agg.items():
        if n < min_n:
            continue
        rows.append((k, n, dead, dead / n if n else 0.0))
    rows.sort(key=lambda r: (-r[3], -r[1]))
    return rows


def _print_axis(title: str, rows: list, note: str = "") -> None:
    print(f"\n=== {title} ===" + (f"  ({note})" if note else ""))
    if not rows:
        print("  (无样本——该轴的 metric 尚未积累 email)")
        return
    print(f"  {'bucket':<38} {'n':>6} {'dead':>6} {'death%':>7}")
    for k, n, dead, rate in rows[:25]:
        kk = (k[:36] + "..") if len(str(k)) > 38 else str(k)
        print(f"  {kk:<38} {n:>6} {dead:>6} {rate*100:>6.1f}%")


def main() -> int:
    ap = argparse.ArgumentParser(description="死号回归分析（域名/出口/指纹轴死亡率）")
    ap.add_argument(
        "--axis",
        choices=["domain", "egress", "ua", "viewport", "tz", "all"],
        default="all",
    )
    ap.add_argument("--min-n", type=int, default=1, help="隐藏样本数少于此值的桶")
    args = ap.parse_args()

    status = load_status()
    n_live = sum(1 for v in status.values() if v == "live")
    n_dead = sum(1 for v in status.values() if v == "dead")
    total = n_live + n_dead
    print(
        f"关联样本: {total} (live={n_live} dead={n_dead} "
        f"terminal_death_rate={n_dead/total*100:.1f}%)"
        if total
        else "无样本"
    )
    print("死因口径: refresh_revoked / missing_refresh_token（permission-denied 不计封禁）")

    # 域名轴：直接可算，不依赖 metric
    if args.axis in ("domain", "all"):
        rows = bucket_rates(status, lambda em: _domain(em), args.min_n)
        _print_axis("域名 (domain)", rows, "不依赖 metric，今天即可用")

    # 出口/指纹轴：依赖 metric email
    axes = load_metric_axes()
    linked = sum(1 for em in status if em in axes)
    if args.axis in ("egress", "ua", "viewport", "tz", "all"):
        print(
            f"\n[metric join] {linked}/{total} 个号能关联到注册 metric "
            f"({'历史行无 email，随新号积累而增长' if linked < total*0.5 else 'ok'})"
        )

    def mk(field):
        return lambda em: axes[em][field] if em in axes and axes[em].get(field) else None

    if args.axis in ("egress", "all"):
        _print_axis("出口节点 (egress)", bucket_rates(status, mk("egress"), args.min_n))
    if args.axis in ("ua", "all"):
        _print_axis(
            "生效 UA (fp_ua)",
            bucket_rates(
                status,
                lambda em: _short_ua(axes[em]["fp_ua"]) if em in axes and axes[em].get("fp_ua") else None,
                args.min_n,
            ),
        )
    if args.axis in ("viewport", "all"):
        _print_axis("视口 (fp_vp)", bucket_rates(status, mk("fp_vp"), args.min_n))
    if args.axis in ("tz", "all"):
        _print_axis("时区 (fp_tz)", bucket_rates(status, mk("fp_tz"), args.min_n))

    print(
        "\n判读: 若某轴内各桶死亡率相近 → 该轴不是封禁驱动；"
        "若某桶显著偏高 → 该轴/该值是嫌疑。域名轴已知 hotmail≫自有域(见 STATUS.md)。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
