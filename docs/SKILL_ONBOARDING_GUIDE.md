# Skill 接入说明

本文档是 `skill-auto` 的 Skill 接入执行手册。`skill-auto` 负责测试、记录和筛选候选 Skill；正式接入 LazyMind 时，不再默认使用 `skill-auto onboard --apply` 自动写入，而是由 Codex 按本文档人工修改 LazyMind 仓库并验证。

参考依据：

- LazyMind 真实配置：`skills/builtin-sources.yaml`
- LazyMind 真实 Featured 示例：`skills/featured/gaokao-volunteer-advisor/`、`skills/featured/ima-skills/`、`skills/featured/ui-new/`
- 产品说明：`PRODUCT_SKILLS_GUIDE.md`

## 1. 两种接入方式

| 接入方式 | 用户看到的位置 | LazyMind 配置入口 | 是否需要素材 | 典型用途 |
| --- | --- | --- | --- | --- |
| 内置可安装 Skill | 普通技能广场 | `skills/builtin-sources.yaml` | 不需要 | 通过测试后作为普通 Skill 供用户安装 |
| Featured 精选能力 | 首页精选能力、案例广场 | `skills/featured/<id>/featured.yaml` | 通常需要 | 运营展示、案例传播、点击试一试自动安装并绑定 |

注意：

- `builtin` 表示产品离线包中包含该 Skill，不表示每个用户账号已安装。
- 用户第一次点击普通安装或 Featured 试一试时，才会把 ZIP 解压写入个人 Skill 版本库。
- 同一个 `source_url` 不能同时出现在普通 `skills/builtin-sources.yaml` 和 Featured-only 来源里，否则构建会报 `source ... cannot be both a market Skill and a featured-only Skill`。
- 普通 GitHub 仓库页、`/tree/` 页、`/blob/` 页不能作为产品下载源。产品构建器期待 SkillHub 页面或 ZIP 直链。

## 2. 接入前必须看测试结果

正式接入前，先用 `skill-auto` 跑三阶段测试：

```bash
python3 -m skill_auto pipeline \
  --manifest manifests/<batch>.yaml \
  --base-url http://127.0.0.1:8090
```

重点看：

```text
reports/<batch>-pipeline-YYYYMMDD-HHMMSS/
  01-install/summary.md
  02-smoke/summary.md
  03-demo/summary.md
  03-demo/skill_trials.jsonl
  03-demo/onboarding_candidates.yaml
  03-demo/bad_cases.yaml
```

推荐接入标准：

- `preflight_status=pass`
- `install_status=pass`
- `skill_trigger_status=confirmed`
- `skill_execution_status=success`，或可解释、可接受的 `degraded`
- `failure_category` 不是 `missing_api_key`、`blocked_by_env`、`lazymind_unauthorized`、`model_rate_limited`、`source_unavailable`、`install_failed`

`degraded` 需要人工判断。例如 `dev-expert` 这类方法论/诊断型 Skill，脚本失败但最终完成高质量诊断，可以考虑普通内置；但不建议直接做 Featured。`agent-browser` 这类核心能力依赖浏览器脚本，脚本失败且没有真实浏览结果时，不应接入。

## 3. 内置可安装 Skill 接入

适用：让 Skill 出现在普通技能广场，用户点击安装后使用。

### 3.1 只改这些文件

通常只修改：

```text
/Users/chenzhe1/Public/WorkDir/LazyMind/skills/builtin-sources.yaml
/Users/chenzhe1/Public/WorkDir/LazyMind/skills/builtin-skills.lock.json
```

只检查、不提交：

```text
/Users/chenzhe1/Public/WorkDir/LazyMind/skills/.runtime/
```

不要因为内置接入去改：

- `skills/featured/`
- `frontend/`
- `backend/`
- `docker-compose.yml`
- 用户个人 Skill 数据

### 3.2 远程 Skill 写到 `skills:`

真实仓库结构类似：

```yaml
schema_version: 1
patch_catalog: patches/catalog.yaml
bundled_skills:
  -
    uid: bsk_01JZ7Q3YF6Q2Z4HM9V8K7D1R3P
    path: research/deep-research
    category: research
    version: 1.0.0
    provider: LazyMind
skills:
  -
    source_url: 'https://skillhub.cn/skills/user_290ac21c/find-skill-skillhub'
    category: search
    provider: SkillHub
```

新增 SkillHub 或 ZIP 远程源时，只在 `skills:` 下追加：

```yaml
  -
    source_url: 'https://skillhub.cn/skills/clawhub_steipete/weather'
    category: work
    provider: LazyMind
```

不要把远程 Skill 写进 `bundled_skills:`。`bundled_skills:` 只用于仓库内已经存在的本地 Skill 目录，例如 `skills/research/deep-research`、`skills/chat/humanizer`。

### 3.3 source 支持范围

可以直接接入：

- SkillHub 页面链接，例如 `https://skillhub.cn/skills/clawhub_steipete/weather`
- 公开 ZIP 直链
- GitHub Release ZIP asset URL
- GitHub codeload/archive ZIP，前提是 ZIP 解压后根目录或唯一包装目录下有 `SKILL.md`

不要直接接入：

- `https://github.com/<owner>/<repo>`
- `https://github.com/<owner>/<repo>/tree/<branch>/<subdir>`
- `https://github.com/<owner>/<repo>/blob/<branch>/<file>`
- 需要登录、Cookie、Authorization Header 的链接

如果 Skill 只存在于 GitHub 子目录，先让对方提供只包含该 Skill 的 ZIP，或自己制作公开 ZIP，再接入 ZIP 链接。

### 3.4 构建与验证

在 LazyMind 仓库根目录运行：

```bash
make skills-build
```

如果只想跑构建器：

```bash
cd /Users/chenzhe1/Public/WorkDir/LazyMind/backend/core
GOCACHE=/private/tmp/lazymind-gocache go run ./cmd/builtin-skill-bundle \
  --sources ../../skills/builtin-sources.yaml \
  --lock ../../skills/builtin-skills.lock.json \
  --cache ../../skills/.runtime/cache \
  --output ../../skills/.runtime/builtin-skills \
  --featured-sources ../../skills/featured \
  --featured-output ../../skills/.runtime/featured-skills
```

成功后检查：

```bash
rg -n "<skill-name>|<source-url>" \
  /Users/chenzhe1/Public/WorkDir/LazyMind/skills/builtin-skills.lock.json \
  /Users/chenzhe1/Public/WorkDir/LazyMind/skills/.runtime/builtin-skills/catalog.json
```

验收点：

- 构建命令成功。
- `skills/builtin-skills.lock.json` 中出现该 Skill。
- `skills/.runtime/builtin-skills/catalog.json` 中出现该 Skill。
- 普通技能广场可安装该 Skill。
- 安装后 chat 回复里能看到 Skill 被显式加载。

提交前看变更范围：

```bash
git -C /Users/chenzhe1/Public/WorkDir/LazyMind status --short -- \
  skills/builtin-sources.yaml \
  skills/builtin-skills.lock.json \
  skills/.runtime
```

正常只提交 `builtin-sources.yaml` 和 `builtin-skills.lock.json`。

## 4. Featured 精选能力接入

适用：运营展示、首页精选、案例广场、点击试一试自动安装并绑定 Skill。

Featured 的要求比普通内置高。只有经过安全性、可安装性、核心流程和效果验证的 Skill 才能推荐。

### 4.1 只改这些文件

通常新增或修改：

```text
/Users/chenzhe1/Public/WorkDir/LazyMind/skills/featured/<featured-id>/featured.yaml
/Users/chenzhe1/Public/WorkDir/LazyMind/skills/featured/<featured-id>/assets/*
/Users/chenzhe1/Public/WorkDir/LazyMind/skills/featured/<featured-id>/locales/en-US.yaml
/Users/chenzhe1/Public/WorkDir/LazyMind/skills/builtin-skills.lock.json
```

通常不要修改：

```text
/Users/chenzhe1/Public/WorkDir/LazyMind/skills/builtin-sources.yaml
```

原因：Featured 的 `skill.source_url` 会由构建器自动纳入离线 Skill 包。如果同一个链接也写进普通 `skills:`，会触发重复来源错误。

只检查、不提交：

```text
/Users/chenzhe1/Public/WorkDir/LazyMind/skills/.runtime/
```

### 4.2 真实 Featured 目录结构

真实示例通常长这样：

```text
skills/featured/ima-skills/
├── featured.yaml
├── locales/
│   └── en-US.yaml
└── assets/
    ├── cover.png
    ├── skillhub-note.png
    ├── note-search-summary.html
    └── workbuddy-trial-note.png
```

或者：

```text
skills/featured/ui-new/
├── featured.yaml
├── locales/
│   └── en-US.yaml
└── assets/
    ├── cover.png
    ├── ai-ticket-landing.html
    ├── ai-learning-home.html
    └── crm-customer-design-spec.html
```

规则：

- 目录名必须与 `featured.yaml` 的 `id` 一致。
- `assets.cover.file` 一般是 `assets/cover.png`。
- 结果资产可以是 PNG/WebP，也可以是 HTML。
- 已注册的资产必须被 `presentation.card.cover_asset` 或某个 task 的 `result.image_asset/html_asset` 引用。
- `locales/en-US.yaml` 可选，但真实可运营案例通常会补。

### 4.3 `featured.yaml` 最小结构

```yaml
schema_version: 2
id: my-featured-skill
type: work
version: 1.0.0
status: published
default_locale: zh-CN
provider: SkillHub

skill:
  source_url: https://skillhub.cn/skills/example/my-skill
  category: work
  required_version: 1.0.0

placement:
  home: true
  gallery: true
  order: 100

classification:
  category: 通用工作
  tags:
    - Skill
    - 自动化

assets:
  cover:
    file: assets/cover.png
    role: cover

presentation:
  card:
    title: 能力标题
    description: 一句话说明这个能力能帮用户完成什么任务
    output_type: report
    output_label: 交付结果
    cover_asset: cover
    result_summary: 输出摘要、建议和可执行下一步
  detail:
    title: 能力标题
    description: 说明适用场景、输入材料和最终产出。
    attachment_hint: 可选输入材料

tasks:
  - id: core-demo
    selector:
      title: 核心示例
      description: 完成一个可复现的核心流程
      output_label: 交付结果
    launch:
      prompt_short: 帮我完成一个核心示例任务。
      prompt: 请使用 xxx Skill 完成一个具体、可验收的任务。
    replay:
      steps:
        - title: 读取 Skill
          description: 解析 Skill 指令与必要引用
        - title: 执行核心流程
          description: 按示例任务完成主要工作
        - title: 生成结果
          description: 输出可复现的展示结果
    result:
      template: generic_report_v1
      eyebrow: 交付结果
      title: 示例结果标题
      summary: 说明最终产出的价值
      highlights:
        - 关键结果一
        - 关键结果二
        - 关键结果三
```

### 4.4 字段选择规则

`type`：

- `chat`：快速问答、咨询、诊断、连续对话。
- `work`：新建任务、报告、文档、网页、图片、表格、会议纪要等交付型能力。

`output_type` 只能使用：

```text
report, dashboard, slides, document, images, web, meeting, table
```

`result.template` 常用三种：

- `generic_report_v1`：报告、摘要、写作、方案、会议纪要。可配 `image_asset`。
- `html_preview_v1`：交互 HTML 结果预览。真实案例中 `gaokao-volunteer-advisor`、`ui-new`、`ima-skills` 都有使用。
- `product_report_v1`：指标卡 + 双栏内容，适合产品方案、运营分析、复杂规划。

`version` 与 `skill.required_version`：

- `version` 是 Featured 自身版本。
- `skill.required_version` 是 Skill 包版本。
- 两者可以不同，例如真实 `ima-skills` 中 Featured 是 `1.1.0`，但 Skill required version 是 `1.1.9`。

### 4.5 locale 文件注意事项

`locales/en-US.yaml` 结构必须与默认语言对齐：

- 文件名 `en-US.yaml` 对应 `locale: en-US`。
- Task 数量、顺序、ID 必须一致。
- 每个 Task 的 `result.template` 必须一致。
- `output_type` 必须一致。
- `cover_asset`、`image_asset`、`html_asset` 引用同一套 asset id。
- 只翻译文案，不改变结构。

### 4.6 Featured 构建与验证

先跑 Featured schema 和素材检查：

```bash
cd /Users/chenzhe1/Public/WorkDir/LazyMind
make featured-check
```

如果新增或修改了 `skill.source_url`、`required_version` 或资产，再跑：

```bash
make skills-build
```

检查：

```bash
rg -n "<featured-id>|<source-url>" \
  /Users/chenzhe1/Public/WorkDir/LazyMind/skills/.runtime/featured-skills/catalog.json \
  /Users/chenzhe1/Public/WorkDir/LazyMind/skills/builtin-skills.lock.json
```

提交前看变更范围：

```bash
git -C /Users/chenzhe1/Public/WorkDir/LazyMind status --short -- \
  skills/featured/<featured-id> \
  skills/builtin-sources.yaml \
  skills/builtin-skills.lock.json \
  skills/.runtime
```

正常提交：

- `skills/featured/<featured-id>/featured.yaml`
- `skills/featured/<featured-id>/locales/en-US.yaml`，如果有
- `skills/featured/<featured-id>/assets/*`
- `skills/builtin-skills.lock.json`

正常不提交：

- `skills/.runtime/`
- `skills/builtin-sources.yaml`，除非这次还明确要求它也进入普通技能广场

## 5. Featured 三个真实风格案例

下面三个案例按 LazyMind 现有真实 Featured 风格整理。实际接入时要替换 `id`、`source_url`、版本、分类、素材和文案。

### 5.1 轻工具类：天气出行助手

```yaml
schema_version: 2
id: weather-assistant
type: chat
version: 1.0.0
status: published
default_locale: zh-CN
provider: SkillHub

skill:
  source_url: https://skillhub.cn/skills/clawhub_steipete/weather
  category: work
  required_version: 1.0.0

placement:
  home: true
  gallery: true
  order: 120

classification:
  category: 生活助手
  tags:
    - 天气
    - 出行
    - 穿衣建议

assets:
  cover:
    file: assets/cover.png
    role: cover

presentation:
  card:
    title: 天气出行助手
    description: 查询城市天气，并给出穿衣、通勤和户外活动建议
    output_type: report
    output_label: 天气建议
    cover_asset: cover
    result_summary: 输出天气概况、风险提醒和行动建议
  detail:
    title: 天气出行助手
    description: 根据城市和日期查询天气信息，辅助安排出行、穿衣和活动。
    attachment_hint: 城市、日期或出行计划

tasks:
  - id: weekend-plan
    selector:
      title: 周末出行建议
      description: 查询未来两天天气并生成行动建议
      output_label: 天气建议
    launch:
      prompt_short: 查询北京周末天气并给出出行建议。
      prompt: 请使用 weather Skill 查询北京未来两天的天气，并给出穿衣、通勤和户外活动建议。
    replay:
      steps:
        - title: 确认城市和日期
          description: 识别用户要查询的地点和时间范围
        - title: 获取天气信息
          description: 查询温度、天气状况、风力和降雨风险
        - title: 生成建议
          description: 输出穿衣、通勤和户外活动安排
    result:
      template: generic_report_v1
      eyebrow: 天气建议
      title: 北京周末出行建议
      summary: 汇总天气概况、穿衣建议和活动风险提醒
      highlights:
        - 未来两天天气概况
        - 穿衣和通勤建议
        - 户外活动风险提醒
```

### 5.2 多任务知识管理类：IMA 知识库

真实案例参考 `skills/featured/ima-skills/featured.yaml`。这种类型适合展示一个 Skill 的多个核心能力，通常配 3 个任务、多个结果资产和英文 locale。

```yaml
schema_version: 2
id: ima-skills
type: work
version: 1.1.0
status: published
default_locale: zh-CN
provider: 腾讯 IMA

skill:
  source_url: https://skillhub.cn/skills/ima-skills
  required_version: 1.1.9

placement:
  home: true
  gallery: true
  order: 23

classification:
  category: 知识管理
  tags:
    - IMA
    - 知识库
    - 网页收藏
    - 摘要笔记

assets:
  cover:
    file: assets/cover.png
    role: cover
  note_preview:
    file: assets/skillhub-note.png
    role: result
  note_search_summary:
    file: assets/note-search-summary.html
    role: result

presentation:
  card:
    title: IMA 知识管理
    description: 覆盖网页入库、笔记检索总结、新建并追加笔记的 IMA 案例
    output_type: report
    output_label: IMA 知识管理结果
    cover_asset: cover
    result_summary: 查看 SkillHub 网页入库和 AI 办公自动化检索总结
  detail:
    title: IMA 知识库与笔记操作案例
    description: 通过网页入库和笔记检索总结案例，展示 IMA 知识库和笔记的读写、检索、摘要与归档能力。
    attachment_hint: 网页链接、搜索关键词、目标知识库、笔记标题、分类方向和摘要要求

tasks:
  - id: skillhub-note
    selector:
      title: SkillHub 文章入库摘要
      description: 收藏网页到 IMA，并生成可长期检索的摘要笔记
      output_label: 摘要笔记
    launch:
      prompt_short: 请把这篇网页加入我的 IMA 知识库，并为它生成一条便于以后检索的摘要笔记。
      prompt: 请把这篇网页加入我的 IMA 知识库，并为它生成一条便于以后检索的摘要笔记：https://cloud.tencent.com/developer/article/2697255。要求摘要里包含文章主题、核心观点、适合谁看、后续可检索关键词，并把它归到“AI 工具 / SkillHub / 知识管理”方向。
    replay:
      steps:
        - title: 识别跨模块任务
          description: 判断任务包含网页入知识库和创建摘要笔记两部分
        - title: 加入知识库
          description: 调用 IMA 接口收录网页 URL
        - title: 创建摘要笔记
          description: 写入主题、核心观点、适合人群和检索关键词
    result:
      template: generic_report_v1
      eyebrow: IMA 知识管理案例
      title: SkillHub 文章摘要笔记
      summary: 网页已加入 IMA 知识库，并生成可检索摘要。
      image_asset: note_preview
      highlights:
        - 网页已归档到个人知识库
        - 摘要按指定分类组织
        - 后续可通过关键词检索

  - id: ai-office-note-search
    selector:
      title: AI 办公自动化笔记检索
      description: 搜索 IMA 笔记，提炼观点、工具、场景和待办
      output_label: 结构化摘要
    launch:
      prompt_short: 请在我的 IMA 笔记里搜索“AI 办公自动化”相关内容。
      prompt: 请在我的 IMA 笔记里搜索“AI 办公自动化”相关内容，找出最相关的 5 条笔记，并总结关键结论、相关笔记和可行动建议。
    replay:
      steps:
        - title: 加载笔记检索模块
          description: 确认标题和正文检索接口方式
        - title: 扩展关键词搜索
          description: 扩展 AI、自动化、办公、工作流和提效关键词
        - title: 输出结构化摘要
          description: 按关键结论、相关笔记、可行动建议整理结果
    result:
      template: html_preview_v1
      eyebrow: IMA 笔记检索案例
      title: AI 办公自动化相关笔记结构化摘要
      summary: 展示检索命中、反复观点、工具名称、适用场景和后续行动。
      html_asset: note_search_summary
```

### 5.3 多 HTML 结果类：UI 设计

真实案例参考 `skills/featured/ui-new/featured.yaml`。这种类型适合 UI、网页、可视化、设计类 Skill，重点是每个任务都有可预览 HTML 资产。

```yaml
assets:
  cover:
    file: assets/cover.png
    role: cover
  landing_demo:
    file: assets/ai-ticket-landing.html
    role: result
  learning_home_demo:
    file: assets/ai-learning-home.html
    role: result

presentation:
  card:
    title: UI 设计
    description: 覆盖 SaaS 落地页、移动 App 首页和桌面后台改版的 UI 案例
    output_type: web
    output_label: UI HTML 原型
    cover_asset: cover
    result_summary: 查看多个真实设计产出

tasks:
  - id: ai-ticket-landing
    selector:
      title: AI 客服工单分析落地页
      description: 为中小企业 SaaS 产品生成专业可信的完整落地页
      output_label: 落地页 HTML
    launch:
      prompt_short: 我准备做一个面向中小企业的 AI 客服工单分析 SaaS 产品，请帮我设计一个落地页 UI 方案。
      prompt: 我准备做一个面向中小企业的“AI 客服工单分析”SaaS 产品，请帮我设计一个落地页 UI 方案。页面需要包含首屏、核心功能、使用流程、客户价值、价格方案和转化按钮，整体要专业可信，不要太花哨。
    replay:
      steps:
        - title: 梳理产品定位
          description: 明确目标用户、核心功能和转化目标
        - title: 规划页面模块
          description: 组织首屏、功能、流程、价值和价格模块
        - title: 生成完整页面
          description: 输出可直接预览的单页 HTML
    result:
      template: html_preview_v1
      eyebrow: SaaS 落地页案例
      title: AI 客服工单智能分析平台
      summary: 完整 HTML 落地页，包含 Hero、核心功能、使用流程、客户价值、价格方案和转化按钮。
      html_asset: landing_demo
```

## 6. 运营质量要求

Featured 不是“把 Skill 放上去”就结束，必须能作为展示案例使用：

- `prompt` 要像真实用户需求，不要写“完成一个核心示例任务”。
- `prompt` 不依赖本地文件、localhost、私有网页、用户账号状态或缺失 API key。
- `replay.steps` 要对应真实执行过程，不要写空泛流程。
- `result.summary/highlights` 要能解释产出价值。
- HTML 结果资产要能独立预览，不依赖外网脚本或本地服务。
- 图片资产要清晰，封面不要只放纯文字或无关抽象图。
- 多任务 Featured 建议覆盖 2 到 4 个典型场景，真实 `gaokao-volunteer-advisor` 有 4 个任务，`ima-skills` 和 `ui-new` 有 3 个任务。

## 7. 常见错误与处理

| 现象 | 常见原因 | 处理方式 |
| --- | --- | --- |
| 下载后不是 ZIP | 配了 GitHub 页面或登录页 | 改成 SkillHub 页面或公开 ZIP 直链 |
| `skill package must contain SKILL.md` | ZIP 结构不符合要求 | 重新打包，只保留根目录或一层包装目录下的 `SKILL.md` |
| `source ... cannot be both...` | 同一源同时进普通广场和 Featured-only | 从 `builtin-sources.yaml` 删除重复 source，或不要做 Featured-only |
| `required version ... got ...` | `skill.required_version` 与包内版本不一致 | 修改 Skill 包版本或 Featured 要求版本 |
| `field ... not found` | Featured YAML 拼写错误或使用旧字段 | 按 schema v2 修正 |
| `id ... must match directory` | 目录名和 `id` 不一致 | 统一目录名和 `id` |
| 图片 404 | 没跑构建或素材未放在 `assets/` | 跑 `make skills-build`，检查 `.runtime/featured-skills/assets` |
| asset 未使用 | 注册了但没有被引用 | 删除 asset 或在 card/result 中引用 |
| locale 校验失败 | Task ID、顺序或模板不一致 | 复制默认结构后只翻译文案 |
| Skill 能触发但脚本失败 | 运行环境缺依赖、权限或脚本路径问题 | 普通内置可视情况接入，Featured 默认不推荐 |

## 8. Codex 接入时的输入格式

内置 Skill：

```text
按内置 Skill 方式接入：
name: weather
source_url: https://skillhub.cn/skills/clawhub_steipete/weather
category: work
provider: LazyMind
```

精选能力：

```text
按精选能力方式接入：
name: ima-skills
source_url: https://skillhub.cn/skills/ima-skills
featured_id: ima-skills
type: work
version: 1.1.0
required_version: 1.1.9
provider: 腾讯 IMA
category: 知识管理
title: IMA 知识管理
description: 覆盖网页入库、笔记检索总结、新建并追加笔记的 IMA 案例
tasks:
  - id: skillhub-note
    title: SkillHub 文章入库摘要
    prompt: 请把这篇网页加入我的 IMA 知识库，并为它生成一条便于以后检索的摘要笔记：https://cloud.tencent.com/developer/article/2697255。
```

如果没有提供 Featured 文案或素材，Codex 可以根据 `skill-auto` demo 报告、`SKILL.md` 和真实执行结果补齐，但必须先说明假设，并保证配置能通过 `make featured-check` 和 `make skills-build`。
