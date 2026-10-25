# -*- coding: utf-8 -*-
"""
【文件作用】重任务触发接口：把「重活」转发给 GitHub Actions，本进程不计算
================================================================================

背景（2026-09-07）：Render 免费实例 512MB，用户在页面上点「扫描战法 / 矛盾扫描 /
刷新缓存」会直接把实例打爆（崩溃重启循环）。

方案：本接口**不做任何计算**，只调用 GitHub API 触发 `daily-batch.yml` workflow，
由 Actions（4核16G）完成后写回数据库；前端之后正常读结果即可。
Render 全程只承担一次 HTTPS 出网请求（<1KB）。

依赖环境变量（Render 配置）：
  GITHUB_TOKEN  有 Actions 写权限的 PAT（repo + workflow）
  GITHUB_REPO   默认 klen-h/stock-scoring-v2
================================================================================
"""

from __future__ import annotations

import asyncio
import os

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import get_current_user

router = APIRouter()

REPO = (os.environ.get("GITHUB_REPO") or "klen-h/stock-scoring-v2").strip()
WORKFLOW = (os.environ.get("DAILY_BATCH_WORKFLOW") or "daily-batch.yml").strip()
REF = (os.environ.get("GITHUB_REF_NAME") or "main").strip()

# 与 scripts/daily_batch.py 的任务名保持一致（白名单，防止任意注入）
# ★ 2026-09-11：补齐后来迁入日批的任务——漏一个，前端「远端重算」就少一个
#   手动补跑入口（如 weekly_report 周五没跑成，周末/周一可从这里一键补）。
ALLOWED_TASKS = {
    "backfill", "mainflow", "market_snapshot", "sector_snapshot",
    "strategy_scan", "contradiction_scan", "score_snapshot", "lhb",
    "daily_report", "all",
    "market_regime", "mainforce_state", "mainline", "rank_live",
    "contradiction_report", "zz_finance", "news_snapshot", "weekly_report",
    "calendar",
    # ★ 2026-10-25（评分分市况重构 H2'/H3）：等权锚日增量 + 阴跌防御影子榜
    #   （与 daily_batch.DEFAULT_ORDER 同名；补跑入口防"漏跑一天断档"）
    "eq_anchor", "shadow_defensive",
    # ★ 2026-10-25：宽防御组名单（银行∪高股息代理∪低波）—— 方案 A 对照用；
    #   与 daily_batch.DEFAULT_ORDER 同名，漏登记则该任务无法手动补跑。
    "defensive_group",
    # ★ 2026-10-25：数据库流量归因快照（只读计数器）—— 与 daily_batch.DEFAULT_ORDER 同名
    "egress_report",
    # ★ 2026-10-26：选股事实宽表（工作台底座）—— 与 daily_batch.DEFAULT_ORDER 同名，
    #   漏登记则前端「远端重算」没有该任务的补跑入口（第三次同类遗漏，已进 checklist）
    "stock_facts_wide",
    # ★ 2026-10-25（eypai 溯源调研）：流动性三件套日度落库（两融/基差/中美利差）；
    #   与 daily_batch.DEFAULT_ORDER 同名，漏登记则无法手动补跑。
    "liquidity_watch",
    # ★ 2026-09-12：决策简报进日批（盘后 + 企微推送），补上手动补跑入口
    "trader_brief",
    # ★ 2026-09-30（P0）：数据底座断档自检 + 板块快照自愈（plate_daily_zz 断档 5 天
    #   无人发现的兜底闸）—— 与 `daily_batch.DEFAULT_ORDER` 同名任务。
    "data_gap",
    # ★ 2026-09-30：预登记验证脚本的月度复核（三个脚本原先全项目无调用方 ⇒
    #   "样本够了没人跑"）；同样与 DEFAULT_ORDER 同名。
    "verify_monthly",
}


class TriggerIn(BaseModel):
    task: str = "all"
    force: bool = True


@router.post("/trigger")
async def trigger_task(body: TriggerIn, user: dict = Depends(get_current_user)):
    """触发远端日批任务（不阻塞、不等结果）。

    返回 queued=true 表示已成功投递给 Actions；实际完成时间取决于任务量
    （战法扫描约 3-10 分钟，全量 all 约 20-30 分钟），完成后数据写库，
    前端直接刷新结果接口即可看到。

    ★ 2026-09-11 加鉴权：此接口原本**完全开放**（/api/tasks/trigger 无任何认证）——
      公网任何人 POST 一次就能消耗 Actions 额度、反复触发 Actions 运行。
      前端 axios 实例对每个请求都带 JWT（frontend/src/api/index.js 请求拦截器），
      故加 Depends(get_current_user) 不影响页面按钮（未登录用户本就看不了这些页）。
      /tasks/config 保留开放：它只暴露「是否配置了 token + 可选任务名」。
    """
    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if not token:
        raise HTTPException(
            status_code=503,
            detail="未配置 GITHUB_TOKEN：无法触发远端日批（Render 环境变量需加带 workflow 权限的 PAT）")

    task = (body.task or "all").strip()
    if task not in ALLOWED_TASKS:
        raise HTTPException(status_code=400,
                            detail=f"未知任务 {task}；可选: {sorted(ALLOWED_TASKS)}")

    url = f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches"

    def _post():
        # ★ 2026-10-26（BK-04a）：async 端点内同步 requests 会阻塞事件循环（stock.py
        #   头注释记载的同款事故——15s 超时期间所有请求停摆）⇒ 挪线程池执行。
        return requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"ref": REF, "inputs": {
                "tasks": task,
                "force": "true" if body.force else "false",
            }},
            timeout=15,
        )

    try:
        r = await asyncio.to_thread(_post)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"调用 GitHub API 失败: {e}")

    if r.status_code >= 300:
        raise HTTPException(status_code=502,
                            detail=f"触发失败 {r.status_code}: {r.text[:200]}")

    return {
        "queued": True,
        "task": task,
        "workflow": WORKFLOW,
        "repo": REPO,
        "message": f"已提交「{task}」到 GitHub Actions，完成后数据会自动入库，请稍后刷新查看",
        "actions_url": f"https://github.com/{REPO}/actions/workflows/{WORKFLOW}",
    }


@router.get("/config")
async def task_config():
    """前端据此判断是否显示「远端重算」按钮（未配置 token 时隐藏）。"""
    return {
        "enabled": bool((os.environ.get("GITHUB_TOKEN") or "").strip()),
        "repo": REPO,
        "workflow": WORKFLOW,
        "tasks": sorted(ALLOWED_TASKS),
    }
