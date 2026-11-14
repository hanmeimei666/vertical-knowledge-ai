# vertical-knowledge-ai

垂直领域知识应用启动仓库。首轮目标由用户确定为：**体育教育，一份资料生成带来源的课程草稿**。

当前处于项目准备阶段；尚未实现资料解析、检索或模型生成服务，也未宣称训练了自有模型。

## 从这里开始

1. 阅读 [项目定义卡](PROJECT_BRIEF.md)，区分已确认、建议和未知项。
2. 按 [启动配置与推进步骤](docs/启动配置与推进步骤.md) 的 W0–W6 推进。
3. 运行 `python scripts/check_readiness.py`，核查项目骨架和本地工具。
4. 选定一份资料，填写 `config/asset_manifest.example.json` 的实际副本；逐项确定用途范围。
5. 先验证“来源登记 → 原生文本解析 → 引用回查 → 课程草稿 → 人工审核”。

## 本地检查

```powershell
Set-Location 'D:\gptproject\vertical-knowledge-ai'
python scripts/check_readiness.py
```

此脚本只检查准备状态，不安装软件、不调用模型、不上传资料。`SETUP_READY` 不代表业务链路验收通过。
首轮准备仅依赖 Python 标准库和 Git；Node.js 是已存在的可选工具。

## 目录

- `config/`：项目范围、样本登记模板；配置仅用于记录，不是已实现的权限系统。
- `docs/`：启动流程、ADR、参考手册。
- `scripts/`：准备状态检查。
- `data/raw/`、`data/derived/`、`outputs/`：真实输入与生成物，默认被 Git 忽略。
- `AGENTS.md`：本项目开发协作规则。

原始启动包是设计参考；其中架构、模型、数据库和专业应用示例不等于本仓库已具备的能力。
