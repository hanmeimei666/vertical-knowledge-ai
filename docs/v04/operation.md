# 本地运行与复现

## 一键启动

Windows 双击 `start-demo.cmd`，或在仓库根执行以下命令。浏览器默认打开 `http://127.0.0.1:8766`。Linux 可执行 `sh start-demo.sh`。默认只用 Python 3.11 标准库。按 Ctrl+C 关闭。

```powershell
.\start-demo.cmd
python -m pip install -r requirements-ci-optional.txt
.\start-demo.cmd --full
```

`--full` 启用可选媒体处理。工作台的来源页能查看状态和指纹，资料处理页选择测试样本查看解析阶段和原坐标。可检索 gold 或 amber，选择三条课程线之一，用 shared 产生课件，完整模式下还可拼接文件。任务区可打开展示 HTML，也可审核和撤回。独立 activity.html 中的练习可输入 locator 查看反馈。

“重新生成演示库”切换到隔离的新合成工作区。每次启动/重置均使用独立 UUID，不覆盖旧库。关闭服务后可以按需清理 `data/v04-demo` 的历史合成运行目录。旧课程、历史知识和私有原件始终在独立目录。

## 验证命令

```powershell
python -m unittest discover -s tests -v
python scripts/smoke_platform.py
python scripts/smoke_readiness.py
python scripts/smoke_readiness.py --full
python scripts/knowledge.py verify
python scripts/check_tracked_files.py
```

完整冒烟把输入、哈希、Run ID、EvidencePack、蓝图、输出路径和哈希放在命令打印的 `data/v04-smoke/<UUID>/report.json`。测试源和运行输出保留在该 UUID 目录内。

## 操作者与后续接入

合成数据生成函数与授权登记是两个操作。扩展 ParserRegistry 或 ModelProvider 只需受信任本地代码。打开真实工作区前，由负责人确定第一批来源范围、可信授权记录和专家审查流程，使用隔离库逐份登记，并监督专业任务的准确率、适用性和内容质量。公网模型、真实素材的批量入口和外发发布目前未启用。
