# 真实资料接入前架构加固与能力底座交付记录

基于 V0.4 验收基线（`87729b5`），在独立特性分支 `feat/pre-content-architecture-hardening` 上完成了全部可在“无真实专业资料前”落地的底层工程加固与可插拔底座建设。

## 完成项清单

| 模块 | 实现内容 | 关键文件 |
| --- | --- | --- |
| 存储并发与权限分级 | SQLite 开启 `WAL` 与 `busy_timeout`，新增 `records`/`runs` 索引、`unit_fts`（FTS5）和 `unit_embeddings` 向量缓存表；支持可配置的敏感度分级权限矩阵（`none`/`internal`/`anonymized`，未知级别默认拒绝）；单请求 `verification_cache()` 将磁盘 SHA-256 校验去重为 O(1)/源 | `scripts/readiness_store.py`, `config/domain_rules.json` |
| 反向影响面与级联治理 | 新增 `impact_analysis(source_id/unit_id)` 反查全链路受影响的解析、媒体、知识单元、关系与课程运行任务，并提供带审计日志的 `invalidate_impacted_runs` 级联失效能力 | `scripts/readiness_store.py`, `scripts/readiness_demo.py`, `demo/readiness/app.js` |
| 检索加速与真实 Embedding | 接入 FTS5 词项预筛（支持中文二元切词）、SQLite 持久化向量缓存，以及标准库实现的 `OllamaEmbeddingProvider` 与 `OpenAICompatibleEmbeddingProvider`（非回环外发自动校验 `external_model_processing` 与敏感度策略） | `scripts/readiness_retrieval.py` |
| 安全红线、真实模型与富蓝图 | 将 `domain_rules.json` 的 `excluded_request_phrases` 拦截与 `limitations` 免责声明正式接入 V0.4 生产主链路；新增 `LocalOllamaModel` / `OpenAICompatibleModel` 适配器；向后兼容扩展 `PLAN_SCHEMA`（`phases`/`safety_notes`/`exercises` 多题型/`citations` 幻觉校验） | `scripts/readiness_model.py`, `scripts/readiness_agent.py` |
| 多模态健壮性 | 新增确定性文档切块 `chunk` 阶段；支持无音轨视频的关键帧提取与 FFmpeg `anullsrc` 静音补轨拼接；`tesseract_ocr` 支持中文语言包探测；新增本地 `whisper_asr`（`faster-whisper` / `whisper` CLI）适配路由 | `scripts/readiness_media.py`, `scripts/readiness_parse.py`, `scripts/readiness_agent.py` |
| 工作台与操作员 CLI | 工作台升级 `ThreadingHTTPServer` 并支持 `--workspace` 持久化工作区模式，新增 `/api/impact`、`/api/span-correct`、`/api/unit-draft` 接口与影响面视图；新增 `scripts/readiness_cli.py` 支持初始化、暂存登记、审批、解析、草拟知识单元、影响面分析与备份还原 | `scripts/readiness_demo.py`, `scripts/readiness_cli.py`, `demo/readiness/index.html`, `demo/readiness/app.js` |
| 多维度评测脚手架 | 新增 `scripts/readiness_eval.py` 覆盖检索基准、红线拒答率、无证据阻断、间接提示注入隔离、引用忠实度及大批量检索延迟/缓存命中压测 | `scripts/readiness_eval.py`, `tests/test_readiness_hardening.py` |

## 仍待下一阶段（真实资料进入后）验证
- 真实体育专业文档、扫描件和长视频上的中文 OCR/ASR 识别准确率。
- 真实本地/云端大模型与真实中文 Embedding 在真实教研题集上的专业内容质量与专家审校。
- 真实授权批次的合规录入与正式课程课时定型。
