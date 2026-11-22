# V0.3 CLI与迁移
在项目根执行：
```powershell
python scripts/platform_cli.py tools
python scripts/platform_cli.py tools --capability .pdf
python scripts/platform_cli.py health ocr
python scripts/smoke_platform.py
python -m unittest discover -s tests -v
python scripts/knowledge.py verify
```
模拟全链路报告在data/v03-smoke/<uuid>/report.json，不读取历史真实课程。工具目录会按当前依赖显示状态。

实际对象入口（本轮仅synthetic）：
```powershell
python scripts/platform_cli.py extract data/generated-manifest.json --key ex-001
python scripts/platform_cli.py put TaxonomyDimension data/dimension.json
python scripts/platform_cli.py put TaxonomyTerm data/term.json
python scripts/platform_cli.py put KnowledgeUnit data/unit.json --confirmed
python scripts/platform_cli.py query Synthetic --filters '{"domain":["alpha","beta"]}' --mode AND
python scripts/platform_cli.py run data/request.json --key production-001
python scripts/platform_cli.py resume <run-id>
```
输入遵循schemas/v03。清单synthetic=true、sensitive=none且六项权利明确；并不是给真实资料加synthetic标志便获授权。本轮真实内容建设禁止，完成后停止。

旧流程使用--config config/runtime.legacy.json，历史课程检查knowledge.py verify。不要在本轮执行旧seed导入真实内容。平台API复用本机Bearer只读serve.py：有V0.3对象时用共享检索及相应指纹；纯旧库维持旧方式，以免旧任务被错误失效。

显式迁移python scripts/platform_migrate.py <副本库路径>。主库本轮只审计备份，不就地试验；副本旧数据迁移通过测试。程序rollback(backup,new_target)禁止覆盖任何已有文件；回滚后切换独立配置路径。默认单写者，无跨进程业务锁或生产RBAC。
