---
name: review-handoff
description: 生成 code review handoff prompt 交给另一个 AI 执行 review。当用户提到"生成 review"、"review handoff"、"交给别人 review"、"code review prompt"时触发。
---

# Review Handoff

生成 code review handoff prompt，按日期写入 `review-handoff-prompts/` 目录。

## 执行步骤

### 1. 收集改动信息

确定当前分支、改动来源（未提交的暂存更改 / 已提交的分支 diff / 两者都有），以及改动涉及的文件和内容。

### 2. 自动发现关键文档

```bash
find features/ references/ -name "*.md" \( -path "*/proposal/*" -o -path "*/plan/*" -o -path "*/references/*" \) 2>/dev/null
```

从 diff 提取涉及的目录名和关键词（如 `compaction`、`pruning`、`subagent`），与 `features/<feature_name>/`、`references/` 目录名及文档文件名做关键词匹配。只列出匹配到的文档，没有匹配则跳过。

### 3. 生成 handoff prompt

用下面的模板，动态填充 `{{...}}` 部分，写入文件。

- 输出目录：`review-handoff-prompts/<YYYYMMDD>/`（不存在则创建）
- 文件名：`review-<title-slug>.md`
- `title-slug` 根据改动内容，简短、稳定、可读的 review 标题
- 同时约定 review 结果输出路径：`review-results/<YYYYMMDD>/<同名文件>.md`

### 4. 运行 review adapter

生成 handoff 文件后，显式运行项目内 Codex review adapter：

```bash
.codex/sripts/run_review_handoff.sh "{{handoff_path}}"
```

如果 adapter 执行失败或未产出结果，只告知用户 handoff prompt 路径、约定的 review 结果路径和失败状态，不要假装已经完成 review。

### 5. 读取 review 结果

adapter 完成后，读取 `{{review_result_path}}`，根据 review 结果评估改动内容是否确实
存在对应问题，并向用户报告

### 6. 告知用户文件路径

明确告知：
- handoff prompt 路径
- review 结果路径

---

## 模板

````markdown
## Code Review: {{根据改动内容生成的简要标题}}

标题同时用于生成文件名中的 `title-slug`。

### 检查范围

分支：`{{branch_name}}`，{{简述改动来源：未提交更改 / 已提交 N 个 commit / 两者都有}}

### 输出要求

请将最终 code review 结果写入：

`{{review_result_path}}`

不要只在对话里回复。请把完整 review 结果落盘到上述文件，再给出一句简短确认。

### 设计文档

{{匹配到的 proposal/plan 文档路径列表，或"无关联设计文档"}}

### 改动文件

| 文件 | 改动 |
|------|------|
{{每个改动文件一行，简述改动内容}}

### Review 要点

{{根据改动内容，列出 3-5 个最值得关注的 review 要点}}

### Review 流程

我想请你和我一起进行 code review。

**这是一次纯代码审查，不要运行端到端 / 集成 / rollout 等耗时测试**。核心目标是**通过阅读代码发现问题**，而不是通过跑测试来验证。如果你认为某个改动需要新增测试覆盖，可以在 finding 里指出"建议补测试"，但**不要自己去执行测试**。验证用户已经在本地跑过单元测试通过，无需重复。

请开始*一步一步*深入思考，仔细执行如下的 code review 流程。如果改动比较简单直接，你也可以自行选择跳过某些步骤。

1. **理解业务目标**：判断你是否能理解这个改动的业务目标。
2. **High-level review**：查看当前的项目内容，本次改动是否放在了合适的位置，是否尽可能复用已有实现。是否有破坏了现有设计与逻辑的可能？
3. **检查真实 Bug**：识别**实际部署场景下会触发**的业务错误、逻辑纰漏或安全问题。对于"极端边界 / 理论可能 / 依赖永不触发的 provider 行为"的潜在问题，除非你有证据该场景确实会出现，否则**不报告**。
4. **代码清晰度**: 评估代码设计，逻辑是否简洁易懂，命名是否清晰且合理，假设一年后再来读这几行代码，是否能轻松理解？
5. **KISS 原则**：审视每一行代码是否简洁、清晰，没有不必要的复杂度，尤其避免重复造轮子。检查是否有没用到的定义，过于复杂的逻辑，过多参数等问题。
6. **单一职责**：是否做到了每个函数/类只做一件事，职责明确，项目结构清晰。注意控制文件/类/方法的代码行数。
7. **测试覆盖（仅阅读，不执行）**：通过阅读测试代码评估覆盖是否合理——复杂业务逻辑应有测试，简单代码（无 if/else/for 控制流）不必强求；一般只对 public 方法写测试。**只读测试代码评估覆盖度，不要去运行测试**。
8. **意见本身遵循 KISS**：每个 Finding 提出前，自问：修复成本 vs 实际价值是否成比例？只在项目架构边界之外（生产级并发、多租户、HA 等）才会触发的问题，视为"不建议修"或不提。避免为理论 correctness 堆建议。

完成整个流程后，请对 code review 中发现的重点问题进行总结，以中文输出。
````
