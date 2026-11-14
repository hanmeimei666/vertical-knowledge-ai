# 现状发现

## 启动包
- 原始 ZIP 只有 README、AGENTS、项目定义卡模板、实施手册（DOCX 与 Markdown）。没有应用代码、测试或依赖配置。
- 手册定位为设计指导；W0–W7 可以按任务裁剪，架构示例不属于固定安装清单。
- 参考 Markdown 和原始模板保留于 docs/reference；未执行其中的示例指令。

## 用户确认
- 首轮：体育教育，一份资料生成带来源的课程草稿。
- 用户允许直接创建新 GitHub 仓库；已创建 hanmeimei666/vertical-knowledge-ai，私有，main。
- 实际资料、受众、主题、各项用途和专业审核人仍待确定；已发出询问。

## 环境检查
- Git 2.53.0.windows.3；Python 3.11.9；Node.js v24.14.0；npm 11.9.0。
- gh、docker、ffmpeg 在当前 PATH 中未检出；不代表磁盘上完全没有安装，也不影响文本准备任务。
- D:/gptproject 不在已有 Git 仓库内；新项目独立建立 Git 仓库。
- 同级 passing-action 存在页面、媒体与构建脚本文件名线索；未审核内容/许可、未运行、未复制。

## GitHub 访问的区别
- 连接器 get_profile 成功返回 hanmeimei666；不等于可访问该用户所有仓库。
- 新私有仓库通过浏览器创建成功；连接器 get_repo 返回 404。
- 浏览器“设置 → 应用 → 安装的 GitHub 应用”显示没有已安装应用；因此先前建议的 Configure 路径在该账号不存在。
- 用户自行完成 Git Credential Manager 登录后，本机 Git fetch 新仓库成功。
- 不需要共享密码、令牌，不提取浏览器凭据，不扩大连接器授权。

## 技术路线
- 首轮准备采用本地文件 + JSON，原生文本优先；没有启用数据库、OCR/ASR、模型、微调、MCP 服务或多 Agent。
- Codex 配置层参考了官方手册 Config basics；本轮只写项目 AGENTS，不修改全局配置、模型和审批设置。
- project.json 和资产模板仅记录范围，不构成运行时权限强制机制。
