# K12 母号 / 教育域名 调研笔记（合法边界）

> 更新：2026-07-14  
> 目的：理解社区讨论中的「k12.xx.us / 虚拟学校」线索，**只做技术与合规边界梳理**。  
> **不做、也不协助**：伪造成绩单/监护证明/免疫卡、虚假 SheerID 教师认证、伪造入学材料。

## 1. 社区帖在说什么（摘要）

Linux.do「开发调优」帖（Lx720s，约 7/11）观察：

1. 美国部分 **州级 K12 命名空间** 仍出现在活着的 Teachers 工作区后缀里，例如 `@k12.nd.us`（North Dakota）。
2. 其它常见形态：`k12.ca.us` / `k12.tx.us` / `k12.fl.us` 等（州代码）。
3. 联想到 [K12 enrollment portal](https://enrollmentportal.k12.com/)（Stride/K12 Inc. 虚拟/在线学校入学流程），并推测入学后可能拿到学校体系账号/邮箱，再去 ChatGPT Teachers 验证。
4. 帖内还讨论用生图模型伪造入学材料——**此路径违法且本仓库明确拒绝实现**。

## 2. 域名技术事实（可核验）

| 事实 | 说明 |
|------|------|
| `k12.<state>.us` 是历史 **US 地理 TLD 下的学校分支** | 见 [RFC 1480 / US Domain](https://www.rfc-editor.org/rfc/rfc1480.html)、[NTIA 对 usTLD 的说明](https://www.ntia.gov/sites/default/files/publications/sectione_0.pdf) |
| 典型委托 | 学区/学校：`<school>.<district>.k12.<st>.us` 或类似层级，**不是**任何人都能买的开放 gTLD |
| 邮箱 ≠ 买域名 | 即便控制某个 DNS 名，**收信**还要 MX + 身份提供方；Teachers 还要 **SheerID 教师身份** |
| ChatGPT for Teachers | 官方：验证的美国 K–12 **教育工作者**，至约 2027-06；入口 [OpenAI 说明](https://openai.com/index/chatgpt-for-teachers/)、[Help Center](https://help.openai.com/en/articles/12844995-chatgpt-for-teachers) |

结论：社区把「废弃教育域名 / 虚拟学校入学 / 教师验证」串成一条链，**逻辑上相关，但每一步都有独立门槛**，没有「注册一个 k12.xx.us 就自动变母号」的公开稳解。

## 3. 和当前本机池的关系

| 现状 | 含义 |
|------|------|
| 已导入 ~8 万 **共享 K12 快照** | 有 `plan_type=k12`，**无 refresh_token**，窗口约至 7/23 |
| workspace | `fc4f8db5-72cd-44cb-ae0d-fef1370a16c8` |
| hotmail free 自注册 | 有 RT，但 **request 进共享 workspace 同域 401**；accept 可假成功 |
| 可持续补 K12 | 需要：**母号邀请权** 或 **真教师/同域邮箱 + SheerID** 或 **带 RT 的 K12 导出** |

域名线索若要「母号」，本质是：**合法拿到可验证的教师/学区身份**，而不是批量伪造材料。

## 4. 可行 vs 不可行（本仓库立场）

### 可做（工程）

- 继续榨干现有共享 K12（监控、SQLite、网关加固）— 见 `K12_POOL_HARDEN.md`
- 母号 session 到手后走 `scripts/k12_mother_invite.py`（invite → accept → **硬校验 plan_type**）
- 新货用 `k12_rt_import.py inspect`，只收 **K12+RT**
- 调研公开的州域名命名规范、官方 Teachers 流程文档（本文件）

### 不做（红线）

- 生成/提交假成绩单、居住证明、免疫卡等入学材料  
- 绕过/批量欺诈 SheerID 教师认证  
- 盗用真实学区域名或未授权注册/劫持教育 DNS  

## 5. 若只做「合法启发」的下一步清单

1. **官方路径**：真实 K-12 教职邮箱 → `chatgpt.com/k12-verification` → SheerID → 建/进 workspace → 导出带 RT 的 OAuth。  
2. **母号路径**：已有 Teachers 管理权 → 邀请子号（同策略邮箱更稳）→ accept + check。  
3. **域名路径（仅研究）**：弄清 `k12.<st>.us` 由谁委托、二级如何申请——通常是 **学区/州教育网络**，不是 Namecheap 开盒。  
4. **虚拟学校路径**：即便真实入学拿到学校账号，是否给教师 Teachers 权益、是否给可邀请的 workspace，**必须以官方身份规则为准**，不能默认等于母号。

## 6. 与 SQLite 迁移

大号池性能项与域名研究正交：优先把 `accounts.json`（~230MB）迁到 `STORAGE_BACKEND=sqlite`，降低启动与写放大。脚本：`scripts/k12_migrate_sqlite.py`。
