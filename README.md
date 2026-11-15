# vertical-knowledge-ai · 垂类知识系统

本项目主要交付垂类大模型的配置与知识生产流程。青少年体能知识库和48课时课程是首个业务样本。

## 系统设计与运行入口

- **[总流程、22项需求追踪和八板块设计](docs/系统流程设计与实现.md)**
- [本轮实现与验收报告](docs/系统流程实现验收报告.md)
- [运行配置](config/runtime.json) · [领域规则](config/domain_rules.json) · [资料准入模板](config/import_manifest.example.json)

已实现本机SQLite目录、原生资料准入、六项用途权限、证据包、规则蓝图、持久流程、三种导出、人工审核门禁、内部发布、来源撤回失效、审计、备份和鉴权只读API。默认不调用模型；Ollama/OpenAI适配接口通过契约测试，真实模型尚未验证。仅限单机可信写入操作者，非公网生产服务。

```powershell
Set-Location 'D:\gptproject\vertical-knowledge-ai'
python scripts/workflow.py seed
python scripts/workflow.py search '成熟度'
python scripts/workflow.py run '蹲与髋铰链的教学观察' --key my-first-draft
python scripts/workflow.py status
python scripts/evaluate.py
python -m unittest discover -s tests -v
```

成果路径和run_id由命令返回；审核人默认未配置，真实课程停在待审。API启动、模型可选配置、失败恢复、更新撤回与备份见八板块文档。data/outputs及实际原件、解析、运行库不会提交Git。

## 首个业务样本

首期优先构建青少年体能知识库及课程，足球和运动康复后置。课程采用用户要求的40–60课时范围内的 **48课时**：24次课，每次2课时；1课时45分钟，共36小时；理论18、实践/案例30。

这是面向成年体育教师与教练的独立核心课程。原150/200学时文件保留，不自动宣称新版等同于原等级培训或资格认定。

## 先看成果

- [48课时课程与24次课教案](courses/youth-conditioning/青少年体能训练核心课程_48课时.md)
- [原大纲调整与30项任务去向](courses/youth-conditioning/原大纲调整与任务映射.md)
- [32张带来源知识卡](knowledge/youth-conditioning/知识卡.md)
- [12条国际资料与2份本地母版来源目录](knowledge/youth-conditioning/来源目录.md)
- [首份90分钟样本教案：蹲与髋铰链](courses/youth-conditioning/L10_首份样本教案.md)
- [7份表单与八周模拟计划](courses/youth-conditioning/实训表单与八周示例.md)
- [12道情境题与评分要点](courses/youth-conditioning/情境题与评分要点.md)
- [建设与验证报告](docs/青少年体能知识库_首轮建设报告.md)

## 运行

Python 3.11+；已实现部分只用标准库，不需要安装向量库、模型或服务。

```powershell
Set-Location 'D:\gptproject\vertical-knowledge-ai'
python scripts/knowledge.py search '成熟度'
python scripts/knowledge.py search '损伤预测' --limit 3
python scripts/knowledge.py lesson L10
python scripts/knowledge.py verify
python -m unittest discover -s tests -v
```

`knowledge.py`是前轮只读编辑资产检查脚本；检索是中文双字片段/英文关键词匹配，没有自由生成问答或语义模型。它直接读取编辑JSON，不执行运行库的撤回/准入状态。**实际生产和知识服务使用workflow.py/serve.py**，由运行库执行权限和版本过滤。无候选返回 `no_evidence`；关键词结果仍需结合限制核查。

## 原始样本与复现

首轮样本目录：`D:\gptproject\vertical-knowledge-ai\data\raw\youth-conditioning`。
内含用户提供的《青少年体能训练师》岗位技能标准和教学实施方案DOCX副本。原件未修改，副本和全文解析结果默认不上传GitHub。

```powershell
python scripts/ingest_outline.py
python scripts/render_course.py
python scripts/knowledge.py verify
```

`ingest_outline.py`需要本地样本；远端克隆不含原始DOCX。渲染、检索和结构验证可仅依赖仓库内的原创归纳/配置；源指纹与合法结构坐标见 `knowledge/youth-conditioning/local_source_index.json`。本地存在原件时校验指纹，不能把结构验证当成逐条专业正确性审核。

## 当前交付边界

课程资产已实现：DOCX原生结构提取、源指纹、32卡编辑基线检索、24课结构化数据、Markdown渲染与验证。系统运行实现见前述总流程与验收报告。
已完成初稿：课程讲解要点、活动、作业、评价、表单、样本教案。
待验证：专家签审、动作示范复核、真实班级试教、学习效果与正式运营规则。
未建设：视频、自动处方、康复、足球专项、微调、云部署和实时设备功能。

国际资料只保留原创中文归纳、出处和核查状态；摘要/索引与全文核查分开记录。课程不得标记为已经专业审定或已验证有效。
