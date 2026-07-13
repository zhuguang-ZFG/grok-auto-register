# Databricks Trial Pipeline

独立于 Grok CPA 的 Express 试用注册 / 号池 / OpenAI 兼容代理。

> **声明：** 仅用于自动化流程研究与个人学习。批量注册与试用额度使用可能违反 [Databricks Terms](https://www.databricks.com/legal/terms)。操作者自负合规责任。本工具**不会**绕过短信验证或硬 CAPTCHA。

设计见：`docs/superpowers/specs/2026-07-13-databricks-pipeline-design.md`

## Express trial vs Free Edition

| | Express free trial | Free Edition |
|--|-------------------|--------------|
| 目标 | 本流水线默认 | 不作为 $400 目标 |
| 额度 | 约 14 天、宣传可至 ~$400 | 学习向配额 |
| 入口 | `try-databricks` / Express | Free Edition 单独入口 |

## 快速开始

```bash
# 列表
python -m databricks_pipeline list

# 手工导入 host+token 并 probe（不经浏览器）
python -m databricks_pipeline import-manual \
  --host https://dbc-xxxx.cloud.databricks.com \
  --token dapi... \
  --email you@domain

# 全自动注册（依赖 config.json 邮箱 + 代理 + DrissionPage/Chrome）
python -m databricks_pipeline register --count 1

# 重探测
python -m databricks_pipeline probe --all

# OpenAI 兼容代理
python -m databricks_pipeline proxy
# http://127.0.0.1:8320/v1  Authorization: Bearer sk-local-databricks-pool
```

## 配置

在 `config.json` 增加 `databricks` 段（完整默认见 `databricks_pipeline/config.py`）：

```json
{
  "databricks": {
    "enabled": true,
    "register_count": 1,
    "concurrent_count": 1,
    "max_per_day": 5,
    "min_interval_sec": 120,
    "auth_dir": "databricks_auths",
    "proxy_port": 8320,
    "proxy_api_key": "sk-local-databricks-pool",
    "probe_models": [
      "databricks-qwen35-122b-a10b",
      "databricks-gpt-oss-120b",
      "databricks-gemma-3-12b"
    ]
  }
}
```

邮箱默认复用顶层 `email_provider` / Cloudflare / Cloud Mail 配置。  
**不会**写入 `cpa_auths/`。

## 人机闸门

检测到手机号或硬 CAPTCHA 时：

- 凭证 `status=needs_human`
- 截图到 `screenshots/databricks/`
- **不重试硬冲**

## 模型别名

社区帖中的 `system.ai.*` 在 `databricks_pipeline/models_catalog.yaml` 映射到官方 endpoint 名（如 `databricks-qwen35-122b-a10b`）。

## Kimi Code CLI

```toml
# 示例：C:/Users/zhugu/.kimi-code/config.toml 片段
[[providers]]
name = "databricks-local"
type = "openai"
base_url = "http://127.0.0.1:8320/v1"
api_key = "sk-local-databricks-pool"

[[models]]
provider = "databricks-local"
model = "databricks-qwen35-122b-a10b"
# 或别名 system.ai.qwen35-122b-a10b
```

（字段名以你本机 kimi-code 文档为准，核心是 OpenAI base_url + key。）

## 默认风控参数

- 并发 1  
- 日上限 5  
- 注册间隔 ≥ 120s  

## 故障

| 现象 | 处理 |
|------|------|
| 选择器失效 | 看截图，改 `databricks_pipeline/selectors.yaml` |
| PAT 找不到 | 手工生成后 `import-manual` |
| probe 全失败 | 区域未开 Foundation Model / 额度 / 权限 |
| 日上限 | 等 UTC 日切或改 `max_per_day` |
