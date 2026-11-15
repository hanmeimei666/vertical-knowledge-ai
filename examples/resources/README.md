# 导入演示资源

import-demo.txt是本项目为测试原创的文本，允许项目内部解析、引用和改编；未使用私有原件或真实儿童信息。

执行`python scripts/smoke_import.py`会将它复制到新的本地隔离目录，登记用途权限、生成候选、仅确认该演示候选并生成三格式草稿。演示自动确认仅针对这份已知原创样本，不适用于后续真实资料。

真实资源不要放进examples；请放在data/raw/incoming，并按[导入指南](../../docs/真实知识资源导入指南.md)操作。Git默认忽略data和outputs。
