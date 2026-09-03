# skill-auto 使用说明

这份说明面向第一次使用 `skill-auto` 的同学。核心流程是：先启动 LazyMind，再准备一个 Skill YAML 清单，最后运行测试命令并查看 `reports/` 里的结果。

## 1. 前置工作

### 启动 LazyMind

先启动 LazyMind 的 Docker 服务，并确认网页端可访问：

```text
http://127.0.0.1:8090
```

建议先手动打开网页，确认可以登录并进入聊天页面。默认本地账号通常是：

```text
账号：admin
密码：admin
```

如果你的 LazyMind 地址、账号或密码不同，后续命令里要改成自己的配置。

### 准备 Python 和 Codex CLI

需要 Python 3.9 或以上：

```bash
python3 --version
```

demo 阶段会使用本机 Codex CLI 生成测试用例，并对 LazyMind 的真实回复做语义评测。请先安装 Codex CLI 并登录：

```bash
codex login
codex doctor
codex --help
```

如果只跑安装检查或低成本 smoke，可以暂时不使用 Codex 生成用例；完整 demo 流程建议提前准备好 Codex CLI。

### 获取项目

```bash
git clone https://github.com/chenzhex/skill-auto.git
cd skill-auto
python3 -m unittest discover -s tests -p 'test_*.py'
```

看到测试通过，说明本地环境基本正常。

## 2. 使用步骤

### 第一步：准备 YAML 清单

Skill 测试清单建议放在 `manifests/` 目录，例如：

```text
manifests/my-skills.yaml
```

最小格式如下，`name` 和 `link` 必填：

```yaml
schema_version: 1
batch_id: my-skill-batch

skills:
  - name: weather
    link: https://skillhub.cn/skills/clawhub_steipete/weather

  - name: humanizer
    link: https://skillhub.cn/skills/user_ab5ae6ee/unclecheng-reduce-ai-perception-v2
```

如果某个 Skill 需要 API key 或 token，写在该 Skill 的 `env` 字段里：

```yaml
skills:
  - name: wechat-cover
    link: https://skillhub.cn/skills/user_8d36cde0/wechat-cover
    env:
      REDFOX_API_KEY: your_redfox_api_key_here
```

如果想固定测试文案，可以写 `case`；不写时，demo 阶段会让 Codex 根据 Skill 功能自动生成：

```yaml
skills:
  - name: weather
    link: https://skillhub.cn/skills/clawhub_steipete/weather
    case: 请使用 weather Skill 查询上海明天的天气，并给出通勤穿衣建议。
```

多个用例可以用 `test_cases`：

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

### 第二步：运行推荐的三阶段测试

推荐直接运行完整 pipeline：

```bash
python3 -m skill_auto pipeline \
  --manifest manifests/my-skills.yaml \
  --base-url http://127.0.0.1:8090
```

pipeline 会自动分三阶段：

- `01-install`：下载、安装和静态检查，不跑 chat，成本低。
- `02-smoke`：只对安装通过的 Skill 做一次轻量对话测试。
- `03-demo`：只对 smoke 通过的候选 Skill 做完整 demo 测试和语义评测。

也可以单独跑某个阶段：

```bash
# 只做安装检查
python3 -m skill_auto test \
  --manifest manifests/my-skills.yaml \
  --runner api \
  --base-url http://127.0.0.1:8090 \
  --mode install \
  --run-chat false

# 只做 smoke
python3 -m skill_auto test \
  --manifest manifests/my-skills.yaml \
  --runner api \
  --base-url http://127.0.0.1:8090 \
  --mode smoke \
  --attempts 1 \
  --max-response-chars 500

# 只做 demo
python3 -m skill_auto test \
  --manifest manifests/my-skills.yaml \
  --runner api \
  --base-url http://127.0.0.1:8090 \
  --mode demo \
  --attempts 2 \
  --max-response-chars 0 \
  --demo-case-generator codex \
  --demo-case-batch-size 5
```

### 第三步：查看结果

不传 `--out` 时，结果会自动保存到带时间戳的目录：

```text
reports/<manifest-name>-pipeline-YYYYMMDD-HHMMSS/
```

完整 pipeline 的结果结构：

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

优先看这些文件：

- `summary.md`：本轮测试简表。
- `skill_trials.jsonl`：最完整的机器记录。
- `bad_cases.yaml`：失败 Skill 和失败原因。
- `bugs.md`：按问题整理的失败说明。
- `onboarding_candidates.yaml`：建议进入接入流程的候选 Skill。

常用结果字段：

```text
skill_trigger_status       是否确认触发 Skill
skill_execution_status     success / degraded / blocked / failed / not_tested
failure_category           失败分类
semantic_eval_status       demo 阶段语义评测状态
recommendation             onboard / hold / reject 等建议
```

## 3. 注意事项

- 先启动 LazyMind Docker，再运行 `skill-auto`。
- 跑测试前先打开 `http://127.0.0.1:8090`，确认网页端能正常登录和聊天。
- 需要账号密码的定时任务命令会显式接收 `--username`、`--password`；这些信息会写入本机生成的调度脚本，别提交或分享。
- 需要 API key 的 Skill，统一写入 YAML 的 `env` 字段，不再使用 `env.sh`。
- 不要把真实 key 写进 README、代码、提交记录或公开 YAML；分享前改成 `your_xxx_here`。
- 大批量测试建议使用 `pipeline`，不要直接对 100 个 Skill 全量 demo。
- demo 阶段会消耗 Codex token，因为会生成 case 并做语义评测。
- 遇到 LazyMind 模型限流时，框架会退避重试；重试仍失败会记录为 `model_rate_limited_passed`，不直接算 Skill 自身失败。
- `reports/`、`logs/`、`schedules/`、`.DS_Store`、Python 缓存都是运行产物，不建议提交。
- 正式接入 LazyMind 前，先看测试报告，再按 [`docs/SKILL_ONBOARDING_GUIDE.md`](SKILL_ONBOARDING_GUIDE.md) 操作。

