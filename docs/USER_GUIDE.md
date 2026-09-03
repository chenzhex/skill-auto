# skill-auto 使用说明

这份说明面向第一次拿到 `skill-auto` 的使用者。目标是：启动 LazyMind 后，用一个 YAML 清单批量测试 Skill，并在 `reports/` 目录下查看结果。

## 1. 需要提前准备

### 1.1 启动 LazyMind

先在本机启动 LazyMind 的 Docker 服务，并确认网页端可以访问：

```text
http://127.0.0.1:8090
```

建议先手动打开网页，确认可以登录并进入聊天页面。默认测试命令会使用：

```text
账号：admin
密码：admin
```

如果你的 LazyMind 地址、账号或密码不同，运行命令时需要改成自己的值。

### 1.2 安装 Python

需要 Python 3.9 或以上：

```bash
python3 --version
```

### 1.3 安装并登录 Codex CLI

`skill-auto` 在 demo 阶段会用本机 Codex CLI 做两件事：

- 根据 Skill 的 `SKILL.md` 批量生成 demo 用例。
- 对 LazyMind 的真实回复做语义级评测。

请先安装 Codex CLI，并完成登录：

```bash
codex login
codex doctor
```

确认可用：

```bash
codex --help
```

如果只想做低成本的安装检查或 smoke 检查，可以暂时不用 Codex 生成用例：

```bash
--demo-case-generator static
```

但推荐完整 demo 流程前先准备好 Codex CLI。

## 2. 获取项目

```bash
git clone git@github.com:chenzhex/skill-auto.git
cd skill-auto
```

如果使用 HTTPS：

```bash
git clone https://github.com/chenzhex/skill-auto.git
cd skill-auto
```

安装为可编辑项目是可选的。不安装也可以用 `python3 -m skill_auto` 运行。

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

看到测试通过，说明本地环境基本正常。

## 3. YAML 清单放在哪里

Skill 测试清单建议放在：

```text
manifests/
```

例如：

```text
manifests/my-skills.yaml
```

最小格式如下：

```yaml
schema_version: 1
batch_id: my-skill-batch

skills:
  - name: weather
    link: https://skillhub.cn/skills/clawhub_steipete/weather

  - name: humanizer
    link: https://skillhub.cn/skills/user_ab5ae6ee/unclecheng-reduce-ai-perception-v2
```

`name` 和 `link` 是必填字段。

## 4. 带 API Key 的 Skill 怎么写

如果某个 Skill 需要 API key 或 token，把环境变量写在这个 Skill 自己的 `env` 字段里：

```yaml
schema_version: 1
batch_id: api-key-skills

skills:
  - name: wechat-cover
    link: https://skillhub.cn/skills/user_8d36cde0/wechat-cover
    env:
      REDFOX_API_KEY: your_redfox_api_key_here
```

多个环境变量也写在同一个 `env` 下：

```yaml
skills:
  - name: summarize
    link: https://skillhub.cn/skills/clawhub_paudyyin/summarize
    env:
      OPENAI_API_KEY: your_openai_api_key_here
      OPENAI_BASE_URL: https://api.example.com/v1
      OPENAI_USE_CHAT_COMPLETIONS: "1"
      SUMMARIZE_MODEL: openai/example-model
```

注意：

- 不再使用 `env.sh`。
- 不要把真实 key 提交到 GitHub。
- 分享 YAML 前，把真实 key 改成 `your_xxx_here` 这类占位符。
- `env` 是可选字段，不需要 key 的 Skill 可以不写。

## 5. 自己指定测试用例

如果不想让 Codex 自动生成 case，可以在 YAML 里写 `case`：

```yaml
skills:
  - name: weather
    link: https://skillhub.cn/skills/clawhub_steipete/weather
    case: 请使用 weather Skill 查询上海明天的天气，并给出通勤穿衣建议。
```

有多个用例时用 `test_cases`：

```yaml
skills:
  - name: humanizer
    link: https://skillhub.cn/skills/user_ab5ae6ee/unclecheng-reduce-ai-perception-v2
    test_cases:
      - id: core-flow
        prompt: 请使用 humanizer Skill，把这段公众号开头改得更像真人作者写的。
      - id: business-style
        prompt: 请使用 humanizer Skill，把这段产品介绍改得更自然可信。
```

没有写 `case` 或 `test_cases` 时，demo 阶段会用 Codex 根据 Skill 功能自动生成用例。

## 6. 推荐运行方式：三阶段 pipeline

最推荐用一条命令跑完整三阶段：

```bash
python3 -m skill_auto pipeline \
  --manifest manifests/my-skills.yaml \
  --base-url http://127.0.0.1:8090
```

三阶段含义：

- `01-install`：安装和静态检查，不跑 chat，成本低。
- `02-smoke`：只对安装通过的 Skill 做一次轻量聊天测试。
- `03-demo`：只对 smoke 通过的候选 Skill 做更完整的 demo 测试和语义评测。

如果 LazyMind 账号密码不是 `admin/admin`，目前需要先在网页端登录并保持会话可用；调度任务才显式接收 `--username`、`--password`。批量 API 测试时也可以通过 LazyMind 本地会话或相关 `SKILL_AUTO_*` 认证环境变量接入，具体取决于本地 LazyMind 部署方式。

## 7. 单独运行某一阶段

只做安装检查：

```bash
python3 -m skill_auto test \
  --manifest manifests/my-skills.yaml \
  --runner api \
  --base-url http://127.0.0.1:8090 \
  --mode install \
  --run-chat false
```

只做 smoke：

```bash
python3 -m skill_auto test \
  --manifest manifests/my-skills.yaml \
  --runner api \
  --base-url http://127.0.0.1:8090 \
  --mode smoke \
  --attempts 1 \
  --max-response-chars 500 \
  --between-skill-delay 10
```

只做 demo：

```bash
python3 -m skill_auto test \
  --manifest manifests/my-skills.yaml \
  --runner api \
  --base-url http://127.0.0.1:8090 \
  --mode demo \
  --attempts 2 \
  --max-response-chars 0 \
  --demo-case-generator codex \
  --demo-case-batch-size 5 \
  --between-skill-delay 30
```

## 8. 结果在哪里看

不传 `--out` 时，结果会自动保存到带时间戳的目录：

```text
reports/<manifest-name>-pipeline-YYYYMMDD-HHMMSS/
```

完整 pipeline 的结构类似：

```text
reports/my-skills-pipeline-YYYYMMDD-HHMMSS/
  01-install/
    summary.md
    skill_trials.jsonl
    install_passed.yaml
  02-smoke/
    summary.md
    skill_trials.jsonl
    smoke_passed.yaml
  03-demo/
    summary.md
    skill_trials.jsonl
    onboarding_candidates.yaml
    bad_cases.yaml
    bugs.md
    logs/
```

优先看：

- `summary.md`：本轮结果简表。
- `skill_trials.jsonl`：最完整的机器记录。
- `bad_cases.yaml`：失败 Skill 和失败原因。
- `bugs.md`：按 bug 形式整理的失败说明。
- `onboarding_candidates.yaml`：建议接入的 Skill 候选。

核心字段：

```text
skill_trigger_status       是否确认触发 Skill
skill_execution_status     success / degraded / blocked / failed / not_tested
failure_category           失败分类
semantic_eval_status       demo 阶段语义评测状态
recommendation             onboard / hold / reject 等建议
```

## 9. 定时执行

macOS 上可以创建一次性 `launchd` 定时任务：

```bash
python3 -m skill_auto schedule \
  --at "14:30" \
  --manifest manifests/my-skills.yaml \
  --base-url http://127.0.0.1:8090 \
  --username admin \
  --password admin \
  --attempts 2 \
  --install-launchd
```

注意：

- `--username` 和 `--password` 会写入本机生成的调度脚本。
- 不要把 `schedules/` 目录提交或发给别人。
- 当前仓库 `.gitignore` 已默认忽略 `schedules/`。

## 10. 常见注意事项

- 先启动 LazyMind Docker，再跑 `skill-auto`。
- 先打开 `http://127.0.0.1:8090`，确认网页端能正常登录和聊天。
- 大批量测试时建议用 `pipeline`，不要直接对 100 个 Skill 全量 demo。
- 遇到模型限流时，框架会退避重试；仍然限流会记录为 `model_rate_limited_passed`，不直接算 Skill 失败。
- demo 阶段可能消耗 Codex token，因为会生成 case 并做语义评测。
- 对于 100 条级别的批量测试，建议先跑 install，再跑 smoke，最后只对候选跑 demo。
- `reports/` 是运行结果，不建议提交到 GitHub。
- `logs/`、`schedules/`、`.DS_Store`、Python 缓存都不建议提交。
- 需要 API key 的 Skill，统一写入 YAML 的 `env` 字段，不要写进 README、代码或提交记录。

