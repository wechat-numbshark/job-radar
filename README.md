**语言 / Language / 言語:** [简体中文](README.md) · [English](README.en.md) · [日本語](README.ja.md)

# Job Radar / 求职雷达

从公开招聘来源汇总职位，按可配置的岗位、地区、签证与薪资偏好筛选，并可选用 OpenRouter 做匹配分析。网页默认只在本机开放。

## 先看演示

![三个虚构职位的求职雷达演示](docs/demo-jobs.png)

需要 Python 3.11+。`pip install -r requirements.txt` 后执行 `python scripts/seed_demo.py`，再运行 `python api/app.py`，打开 `http://127.0.0.1:8077`。演示中的求职者“林知遥”和三个职位全部虚构，不会调用招聘网站、模型或飞书。

## 使用自己的偏好

复制 `.env.example` 为 `.env`，将 `config/profile.example.json` 复制到 `config/profile.json` 并填写自己的资料，再设 `JOB_RADAR_PROFILE=config/profile.json`。编辑 `config/sources.yaml` 中的岗位关键词、薪资门槛和来源。个人资料及本地数据库都被 Git 忽略。

手动采集入口是 `python scripts/collect_once.py`；评分、分析与推送入口是 `python run_full.py`；`python main.py` 启动定时调度。配置 `OPENROUTER_API_KEY` 后才使用 AI，配置 `FEISHU_WEBHOOK_JOBS` 后才推送。运行前请检查 `.env` 中的 `DRY_RUN`。

## 数据来源与边界

包含 RSS、Greenhouse、Lever、Remotive、Remote OK、Arbeitnow 和 LinkedIn 访客页面采集器。LinkedIn 默认关闭；只有显式设置 `ENABLE_LINKEDIN=true` 才会使用访客页面采集。该页面结构易变，可能限制抓取；请遵守各站条款和速率限制。模型对签证赞助与薪资的判断只是提示，申请前应查看雇主原始职位页。默认网页绑定 `127.0.0.1`；如要公网使用，先加认证。

MIT License。本项目不隶属于任何招聘平台。

## 语言

网页右下角可切换中文、English、日本語；主导航、筛选和常用状态会随之切换。来自外部系统的标题、原文和诊断详情保留来源语言。
