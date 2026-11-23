# V0.4 架构、对象与操作接口

## 运行链路

独立 SQLite 工作区复用 V0.3 的 records、revisions、runs、audit 和迁移/定点还原。V0.3 入口及历史课程仍使用原配置。V0.4 的合成工作区由程序生成，从旧课程库隔离。

源登记 → 独立授权 → ParserRegistry → ParseRevision → KnowledgeUnit 候选与复核 → EvidencePack → ModelProvider → CourseBlueprint → 本地 Artifact → 操作员审核。通知、外部发布、训练和外部模型由当前工作区禁用。

| 对象 | 持久化及主要字段 |
|---|---|
| Source | ReadinessSource，标题、提供者、类型、负责人、敏感等级、创建和登记时间、原路径、内容版本、SHA-256 |
| SourceAuthorization | 独立记录，操作者、关联证明、所绑定内容版本与 SHA-256、授权域、有效期和状态，保留版本历史 |
| SourceSpan | ParseRevision 标识 + source/revision/segment/locator，定位原页、块、单元格、区域、时码，带解析方法及内容哈希 |
| ParseRun、ParseRevision、SpanCorrection | 有序 stage 清单、已完成段和错误。机器提取、专项 OCR/ASR、以及人工修订独立留存 |
| KnowledgeUnit | ReadinessUnit，目标、前置条件、风险、学习者、训练适用人群、领域、场景、能力、证据引用、有效期、候选与审核状态 |
| KnowledgeRelation、TrainingUnit、MediaAsset | 独立关系与内容，TrainingUnit 引用共享知识，MediaAsset 引用固定源和时长，组合从有权限的媒介生成 |
| Request、EvidencePack、CourseBlueprint | Run 步骤内持久化。模型计划有结构校验，Agent 使用可信检索证据和固定工具集 |
| Artifact、Run、Audit | 文件名、路径、SHA-256、审核状态；Run 持久保存请求、所用知识版本、时间线、错误和审核；Audit 记录阶段与版本操作 |

## 本地接口

- `Readiness.generate_source` 生成受信样本后进入待审。证明与每一项许可由受信任本地操作员调用 `record_grant` 单独登记。拒绝未知、过期、撤回、未授权、哈希变更或版本不一致。
- `readiness_parse.parse` 根据扩展名和已注册阶段调用适配器。许可依据数据库，后续复用已完成的阶段，失败显式 retry。解析使用同一原件，媒体文件只写入输出目录。
- `correct_span`、`save_unit`、`review_unit`、`merge_units` 和 `relate` 维持单元与来源的证据关系。有效审批取决于当前证据状态。
- `readiness_retrieval.search` 支持 AND/OR、多维过滤、版本、审核状态、授权文集上的词项排名、向量与混合召回、受控重排、关系扩展和冲突标记。
- `readiness_agent.produce` 以结构化请求和幂等键启动。阶段包括 evidence_ready、blueprint_ready、draft_ready，失败保留检查点。可替换 ModelProvider 在子进程运行并设超时。模型返回知识 ID 和工具名称，越权工具或引用直接拒绝。
- `review`、`publish` 与 `artifact` 复核文件哈希及当前证据权限；发布输出局限于本地内部状态。浏览器提供生产、审核和撤回及可点击工件。

## 权限语义

|许可|用途|
|---|---|
|internal_parse|本地解析及解析结果回读|
|retrieve|查证据和引用|
|adapt|内容编排与改写，连同 retrieve 校验|
|redistribute|分发许可，独立最终发布限制仍生效|
|model_training / external_model_processing|独立许可字段，当前系统不执行对应任务|
|ocr / asr / video_processing|专项许可，与解析共同校验，派生证据继承必要专项权限|

状态包含 pending_review、admitted、quarantined、rejected、revoked、expired。授权变更与原件版本分别留痕。HTTP 不接收自定义路径、授权或审核人属性。演示身份局限于回环服务中的合成库，状态页对不可引用的知识隐藏正文。Python 操作员入口受本机文件和进程信任边界约束。已经被用户手工复制的离线文件仍由操作员管理。

## 解析兼容及边界

PDF 正文给出页号与页面元信息，DOCX 定位块及表行，PPTX 用幻灯片和 XML 元素序号，XLSX/CSV 使用单元格，HTML/TXT/Markdown 和 JSON 使用行或指针。支持 PNG/JPEG、WAV/MP3/M4A、MP4/MOV。Pillow 和 pypdf 可选，FFmpeg 允许系统程序或可选依赖中的本地二进制。

图像条纹检测、音频音调识别与课程模型均为有明确标签的合成链路。Tesseract TSV 是可选本地适配器。FFmpeg 执行真实音轨、关键帧、剪辑与拼接。媒体时间线以毫秒提供 source 路径对应，允许查看解析层的音频段和识别坐标。输入文件限 50 MB，Office 解包有大小、条目和路径限制，演示导出的处理段最长 60 秒。解析恢复粒度是已登记的处理阶段。复杂版式和 OCR/ASR 专业准确率待真实评测。
