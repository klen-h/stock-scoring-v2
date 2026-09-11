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
    try:
        r = requests.post(
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
