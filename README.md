# lark-weekly — 飞书周报自动化服务

基于 [lark-cli](https://github.com/larksuite/cli) 与 [pi coding agent](https://www.npmjs.com/package/@earendil-works/pi-coding-agent) 的飞书周报自动化服务:
定时提醒 → 催交 @未提交 → AI 汇总 → 管理员卡片确认 → 邮件发送,并以智能体形式响应私聊与 @机器人 消息。

## 功能流程

| 时间(默认) | 动作 | 实现 |
|---|---|---|
| 周五 10:00 | 群内发送周报填写提醒(附表单链接) | `jobs/notify.py` |
| 周五 17:00 | 统计未提交成员,群里逐人 @ 催交;全部交齐则发表扬 | `jobs/remind.py` |
| 周五 18:00 | 拉取本周全部周报,pi(deepseek-v4-flash-vision-exp)生成汇总,以**卡片**发给管理员 | `jobs/summarize.py` |
| 管理员点卡片「确认发送邮件」 | 汇总转 HTML,经飞书邮箱发送给配置的收件人 | `jobs/mailer.py` |
| 随时 | 私聊或群里 @机器人 → pi 智能回复(按会话保持记忆,支持 /new 重置、/help 说明) | `events/handlers.py` |

## 架构

```
Docker 容器 (python:3.12-slim + node22)
├── APScheduler        三个 cron 任务(表达式存于飞书配置表,可在线修改)
├── lark-cli event     两个事件订阅子进程(im 消息 / 卡片按钮,WebSocket 长连接)
│     └── 就绪标记 + stdin EOF 优雅退出 + 崩溃自动重启(前置条件不满足时 5 分钟长退避)
├── pi -p (deepseek)   汇总(无工具) / 智能回复(带 bash 工具,可驱动 lark-cli)
└── 状态目录 /data/state   digest-{week}.md / pending-{week}.json / digest html
```

配置引导:容器只需两个环境变量(LARK_BASE_TOKEN / LARK_CONFIG_TABLE_ID),
群聊、管理员、邮箱、cron、模型等**全部业务配置存在飞书多维表格「配置表」里**,改表即生效(约 1 分钟内重载)。

## 项目结构

```
app/
├── main.py           服务入口:调度 + 事件消费 + 优雅退出
├── cli.py            手动触发/调试命令
├── settings.py       环境变量
├── lark.py           lark-cli 子进程封装(JSON envelope、记录归一化、发消息)
├── config_store.py   飞书配置表读写(带 TTL 缓存)
├── weekly.py         周次/提交状态计算
├── pi_agent.py       pi 无头模式封装(汇总/智能回复)
├── cards.py          确认卡片 JSON
├── jobs/             notify / remind / summarize / mailer
└── events/           consumer(子进程管理)/ handlers(事件路由)
agent_home/AGENTS.md  pi 智能回复的工作说明(lark-cli 速查 + 安全规则)
```

## 已创建的飞书资源

- **Base「周报自动化」**: https://qcn3ne12v38y.feishu.cn/base/TilUbvHGHadbYdsKnH6c7Zppn5c
  - 表「周报记录」(`tblB08hGNMmn55ZF`):提交人(人员)、本周完成、下周计划、问题与协调、提交时间
  - 表「配置表」(`tblbL5oCb3ILSc1I`):**表头即配置项、第一行即值**(一行式,参考「配置示例」)

### 配置表列说明

| 表头(列) | 内部含义 | 备注 |
|---|---|---|
| 管理员 | admin_open_id | 人员字段,取其 open_id |
| 群聊 | group_chat_id | 群字段,取其 chat_id |
| 发送到 / 抄送 | email_to / email_cc | 邮箱,逗号分隔 |
| 提醒时间 / 催交时间 / 汇总时间 | notify_cron / remind_cron / summarize_cron | cron 表达式 |
| 时区 | timezone | 变更需重启 |
| 智能回复 | agent_enabled | 复选框,控制私聊/@机器人 回复 |
| 模型服务商 / 模型 | pi_provider / pi_model | deepseek / deepseek-v4-flash-vision-exp |
| 邮件主题前缀 / 周报称呼 | 文案 | |
| 表单链接 / 记录表ID | form_url / reports_table_id | 建表时自动回填,勿改 |

## 部署(服务器)

1. 服务器上准备 lark-cli 凭据(三个目录,来自已完成 `config init` + `auth login` 的机器):
   - `~/.lark-cli/`(配置与缓存)
   - `~/.local/share/lark-cli/`(加密凭据:app secret / token / master.key)
   - `~/.pi/agent/`(pi 的 auth.json 等,内含 deepseek API key)
2. 上传本项目目录,在 `.env` 里确认 `LARK_BASE_TOKEN` / `LARK_CONFIG_TABLE_ID`。
3. 启动:

```bash
docker compose up -d --build
docker compose logs -f
```

4. **人工步骤**:在飞书开发者后台为应用开通 `card.action.trigger` 回调
   (服务日志会输出开通入口 URL;未开通前卡片确认按钮不生效,其余功能不受影响)。
5. 填写配置表的 `group_chat_id` 与 `email_to`,把机器人加入目标群。

## 官方 Skills 同步

lark-cli 内置 24+ 个官方 Agent Skills(im/base/docs/mail/event…),已全量导出到 pi 的全局技能目录
`~/.pi/agent/skills/lark-*`(随 `~/.pi/agent` 挂载进容器),智能回复时可按需加载:

```bash
python3 scripts/sync_lark_skills.py              # 同步到 ~/.pi/agent/skills
python3 scripts/sync_lark_skills.py /tmp/skills  # 或指定目录
```

脚本随 CLI 版本更新重新拉取(内容编译在 lark-cli 二进制里);少数非文档资源(如 lark-whiteboard/elements)CLI 未内嵌,会以警告列出,属预期。
容器内可用 `/skill:lark-im` 方式验证:`docker exec lark-weekly pi -p --no-session "/skill:lark-im 推荐的发消息命令"`。

## 手动调试

```bash
# 本地(建了 .venv)
.venv/bin/python -m app.cli config            # 查看生效配置
.venv/bin/python -m app.cli notify --dry-run  # 预览提醒文案
.venv/bin/python -m app.cli remind --dry-run  # 预览未提交名单
.venv/bin/python -m app.cli summarize --dry-run --week 2026-W36
.venv/bin/python -m app.cli mail --week 2026-W36 --dry-run
.venv/bin/python -m app.cli ask "我最近有什么日程"   # 测试智能体
.venv/bin/python -m app.cli events            # 只跑事件消费(前台)

# 容器内同理
docker compose run --rm --entrypoint python lark-weekly -m app.cli config
```

## 安全说明

- 邮件以授权用户(user 身份)的飞书邮箱发出,`mail +send` 默认存草稿,仅管理员点确认后才真正发送。
- 智能回复的 pi 进程带 bash 工具但受 `agent_home/AGENTS.md` 约束:不群发、不泄密、写操作须与请求直接相关。
- 事件处理做了 event_id 去重、非管理员卡片点击忽略、bot 自身消息过滤。
- 凭据目录(.lark-cli / .local/share/lark-cli / .pi)请勿提交仓库;`.env` 已在 `.gitignore`。
