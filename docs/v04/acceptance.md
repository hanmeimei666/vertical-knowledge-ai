# V0.4 合成工程验收

日期 2026-11-23。开发分支 `feat/pre-ingestion-readiness`。V0.3 收尾提交 `3359c2d` 独立保留在 [Draft PR #1](https://github.com/hanmeimei666/vertical-knowledge-ai/pull/1)。本阶段在 [叠加 Draft PR #2](https://github.com/hanmeimei666/vertical-knowledge-ai/pull/2) 交付。先前阶段实现 `78c7352`，本地工作台和 CI 提交 `2e8f34f`。验收修订的远端头及 CI 以 [当前 PR 检查](https://github.com/hanmeimei666/vertical-knowledge-ai/pull/2/checks) 为准。

| 能力 | 交付与验证状态 |
|---|---|
| 文件登记、权限、撤回 | 已实现。分离授权流水、原件内容版本、哈希和有效期，许可默认缺项拒绝。使用合成原件验证篡改、撤回、关联缓存、独立权限和来源重复。 |
| 原生与多模态解析 | 已验证 16 类生成样本。PDF 41 页故障恢复回归，文档/表格/图片定位，FFmpeg 视频、音轨、关键帧和时间线，有带原时码的合成 WebVTT。 |
| OCR、ASR、语义与模型 | 合成识别和向量、确定性模型流水已验证。可选真实 Tesseract 适配器已提供。真实引擎、真语音、真实知识检索性能、模型内容质量与专家审校等待后续验证。 |
| 知识与证据 | 已实现评审状态、版本、撤回、合并、来源位置校验、关系扩展和冲突提示。训练人群与教学学习者独立。 |
| 生产与审核 | 三条课程线共用证据 ID，均生成 Markdown、HTML、JSON 和独立 HTML 课件。足球合成示例再生成两个片段的回放及映射。子进程模型、超时恢复、幂等、白名单、审核、下载和撤回通过专项。 |
| 演示 | 已在 Edge 回环入口实际生成足球课件和测试剪辑，打开课件并提交 locator 获得反馈。HTTP 测试验证生产、批准、撤回、重置、跨来源请求拒绝与权限状态。 |
| 兼容运维 | 旧 66 项测试保留。原有课程和知识结构核对显示 14 个来源、32 张卡、24 课次，共 48 课时。隔离库备份/还原通过。 |
| 真实资料接入 | 隔离 staging 操作员接口已实现并用程序生成字节验证，普通启动入口关闭。真实资料、授权证明和专业审核由负责人在后续阶段确认。 |

## 验证读数

- 本地 Python 3.11 完整开发依赖环境，87 项 unittest 通过，其中 V0.4 增加 21 项。基线冒烟、历史课程结构检查和跟踪文件检查皆通过。
- 36 条合成检索查询，Recall@5、Hit@5 和 MRR@5 均为 1.000，误召回 0、权限泄露 0。计分对有答案用例，另单独检查空答案误召回。
- 完整合成报告位于本机 `data/v04-smoke/2cf32c58a1be4e598559756e436d7e74/report.json`。体能、运动康复防护、足球运行 ID 分别为 `2073eeef000443be8a7c6e7278e0e310`、`aed006e8c0114da7bfdba6426497683b`、`6bb01b74fb274965ad9351523de087dc`。三组均为 draft_ready。报告包含输入指纹、解析阶段、源定位、证据、蓝图和全部输出哈希。
- 云端矩阵保留 Linux 核心、Windows 核心、Linux 可选依赖三组。独立叠加 PR 的 [2e8f34f 三组检查](https://github.com/hanmeimei666/vertical-knowledge-ai/actions/runs/45150861201) 已全通过。后续提交沿同一工作流复验。
- Windows 全量回归曾复现未读完有界 POST 请求体时连接被重置。服务先读限长请求体、再核对不可伪造的会话和来源，回环 HTTP 安全回归及全量回归随后通过。

[本地运行与复现](operation.md) · [架构、对象与工具](architecture.md) · [起始工程差距](engineering-audit.md)

## 最终独立复核（2026-11-23）

实现基线799fbc8f169f783a8794ec5a2878a69e98794dcc；[Push CI 45152426975](https://github.com/hanmeimei666/vertical-knowledge-ai/actions/runs/45152426975)及[PR CI 45152436559](https://github.com/hanmeimei666/vertical-knowledge-ai/actions/runs/45152436559)均success。保持叠加Draft #2→feat/general-sports-platform-v03，不改main或Tag。

本轮再次实际运行：87项unittest通过（12.312秒）、smoke_readiness.py --full通过、smoke_platform.py通过、knowledge.py verify和check_tracked_files.py通过。日志保存在本地data/v04-audit/validation.txt。完整报告data/v04-smoke/8e38ffb425294ae8b36c573bdc56cf25/report.json，16类输入与解析阶段齐全，三条课程线Run依次064f3f7fd07b40dc81c029d9ffff9f99、7b31a609e9754455b2d8b152b9d42713、a59e9c7cd5ed4fb99b5ff4ba25215bc5，均draft_ready；36查询三项指标1.0，误召回和泄露0，仅代表合成工程测试。

浏览器独立复核：以python scripts/readiness_demo.py --full --no-browser --port 8770启动，实际选择足球线、拼接视频并生产Run 474e6c33f55548108bc3571e6ff05d52，页面列出lesson.md/html/json、activity.html、clip.mp4和timeline.json。独立课件提交locator返回“The locator identifies the source coordinate.”，不是静态截图演示。

当前定义卡和协作入口已更新到V0.4，能力清单按五种状态分列。本次状态补录作为后续文档提交正常Push；最新HEAD、远端与PR头SHA及其CI在交付消息和本地data/v04-audit核验，不将报告自引用SHA写成自身提交。
