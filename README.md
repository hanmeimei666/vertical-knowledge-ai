# 体育教育垂类大模型 V0.3 · Sports Education Vertical LLM

## V0.4 本地工作台

Windows 打开 `start-demo.cmd`，Linux 运行 `sh start-demo.sh`。演示使用程序生成的样本，可查看来源授权、运行检索、生成三条课程线的合成课件和审核结果。完整媒体模式和验证命令见 [本地运行与复现](docs/v04/operation.md)，对象、工具与权限见 [V0.4 架构](docs/v04/architecture.md)。
覆盖开放人群、专项、体育专业与场景的共享知识和数字资产底座。青少年体能、足球与运动康复并行准备，体能可先行半步；48课时仅历史验证样本，正式课时待专家论证。

本轮只建设通用工程基础，使用程序生成的非专业测试数据。不新增真实知识/课程/儿童或健康信息，不训练、不下载大模型、不调用付费API、不部署公开网站。

## 许可状态

本仓库当前尚未声明开源许可证，也未提供LICENSE文件。公开可见不代表已经授权第三方复制、修改、分发或复用。后续许可策略由项目负责人决定；本轮不自行选择MIT、Apache或其他许可证。

## 配置和运行
```powershell
python scripts/platform_cli.py tools
python scripts/smoke_platform.py
python -m unittest discover -s tests -v
```
默认config/runtime.json为共享体育工作空间，runtime.legacy.json保留旧配置。SQLite记录/修订/审计和可恢复任务沿用旧实现，独立新库不覆盖历史数据。V0.3在feat/general-sports-platform-v03开发分支审查，正式main与v0.3.0发布须负责人批准。

[持续开发与发布规范](CONTRIBUTING.md)：独立分支、小任务Commit、重要节点Push、Draft PR与逐Push CI。CI区分Linux/Windows标准库核心测试及轻量可选依赖测试；不安装OCR/ASR模型。云端同步及当前审查状态见[交付报告](docs/v03/云端同步交付报告.md)。

## 已实现与限制
- 版本化对象Schema、多维词条及方向关系、同知识跨领域共享、AND/OR条件检索、源版本/权限EvidencePack。
- 工具定义/发现/能力过滤、依赖健康检查、输入输出校验、进程超时、幂等日志、代码适配器允许列表。
- TXT/MD/DOCX、HTML/CSV/JSON、PPTX/XLSX原生结构；PDF依赖pypdf逐页文本；图片依赖Pillow仅元数据，WAV仅元数据。复杂版式、图片语义、OCR/ASR与视频未实现。
- 单一可恢复流程、通用Request/蓝图/Artifact，保留Markdown/HTML/JSON导出及指定审核门禁；真实模型未验证。
- OCR、ASR、FFmpeg、语义检索与MCP工具为planned，不能把注册接口当成功能。没有多用户生产部署或专业适用性自动判断。

## 设计与验收
[架构及对象](docs/v03/架构与数据对象.md) · [多维分类](docs/v03/多维分类与共享检索.md) · [工具规范](docs/v03/工具注册与调用.md) · [CLI及迁移](docs/v03/CLI与迁移.md) · [参考依据](docs/v03/架构参考依据.md) · [验收报告](docs/v03/验收报告.md)

schemas/v03保存契约，scripts/platform_*为新接口；旧pipeline/workflow/import_resources/model_adapter继续保留。[旧课程](courses/youth-conditioning/青少年体能训练核心课程_48课时.md)与[知识卡](knowledge/youth-conditioning/知识卡.md)仅历史样本，内容不修改。旧检验：python scripts/knowledge.py verify。

原件/解析/运行库/备份/输出保留data或outputs并由Git忽略。授权分解析、检索、改编、再分发、训练、外发及细分处理；存储权不推导OCR/ASR。当前Schema/模型/标准映射均非专业内容已确认或已完成外部互操作的声明。
