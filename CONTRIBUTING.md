# 持续开发、审查与发布规范

从本轮起所有开发阶段遵守此流程。main保持已验收稳定状态，单个阶段使用独立feature分支。不得强推、重置他人成果或将功能代码直接提交main。

## 阶段流程

1. 核查status、branch -vv、remote、提交差异和远端；有未提交修改先审查归属。
2. 从已批准基线创建feature分支，例如feat/asr-timestamps；相邻修订按阶段独立记录。
3. 完成一个可验证小任务即Commit，写清问题/变化/验收；建议feat(parser)、test、docs、ci等前缀。
4. 通过本地相关检查、全部回归、跟踪文件检查后及时正常Push，建立upstream；已有远端分支先fetch比较，禁止force。
5. 创建或更新指向main的Draft PR，注明数据/迁移、兼容、测试、限制；每个Push自动运行CI。未推送只能称本地交付。
6. 核查GitHub远端头SHA与本地一致，以及该SHA的全部CI；失败时修复并提交新Commit，不跳过或冒称通过。
7. 负责人审查、测试通过并批准后才能合并main。Draft PR与历史分支保留，不自动合并或删除。
8. 正式阶段完成后，在已批准合并的main提交创建版本Tag；开发分支通过测试不等于已发布。

## V0.3检查

```powershell
python scripts/check_tracked_files.py
python -m unittest discover -s tests -v
python scripts/smoke_platform.py
python scripts/knowledge.py verify
```

CI对所有Push、指向main的PR和手动触发运行：Linux/Windows核心测试，以及Linux轻量可选PDF/图片/Schema测试。核心依赖文件为空（标准库），可选依赖锁定在requirements-ci-optional.txt。无大型模型、OCR/ASR依赖或真实API调用。不得用continue-on-error掩盖失败。不上传data/outputs等测试产物到Actions artifacts。

跟踪文件脚本仅识别高确定性的凭证和禁止路径，不能替代差异人工复核；原件/个人信息/授权未知素材/秘密/本地配置/运行库/生成物不得提交。

## v0.3.0发布门槛（本轮不执行）

待Draft PR审查、CI通过、负责人批准并合并main后，fetch远端并检查merge SHA；重新验收合并后的main，确认v0.3.0尚不存在且未指向别处，再在该main SHA创建附注Tag并正常推送，禁止移动已发布Tag。发布记录填写：main Commit SHA、PR链接、CI链接及通过结果、交付/迁移和限制。保留开发分支与PR历史。

本轮只建立规则与审查入口，尚未配置GitHub分支保护/Ruleset的服务端强制限制；不将文档约定宣传为已强制执行。所有开发报告必须写分支、实现Commit、最新远端SHA、PR、CI与发布状态。自引用报告不能包含自身Commit SHA，使用git rev-parse HEAD及PR头SHA核对最新修订。
