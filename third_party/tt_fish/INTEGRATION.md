# tt_fish 来源与本地改动

来源：https://github.com/zz11-m/tt_fish
分支：zz11-m-patch-1
提交：c1263f7e84b77e47974f76ed733772dc6c6c0646
Copyright (C) 2026 zz11-m and contributors.
许可：GPL-3.0-only，完整 LICENSE、NOTICE.md 和上游 README.md 随源码保留。

本项目参考并复用了 tt_fish 的源码，不只是界面上的链接。main.py 增加嵌入标签页入口与互斥启动回调；fisher/config.py 将可写数据移至本助手 data/fishing，并将默认模式设为仅观察。config.example.json 同步默认观察；fisher/packaging.py 增加本说明，并移除上游清单中存在、仓库快照中实际缺失的四个打包元数据路径。其余识别、黄条时序、窗口截图和空格输入流程沿用上游。

本地适配不代表原作者背书。游戏截图素材、依赖的权利归其各自权利人。关闭助手会停止钓鱼线程并释放按键；活动任务互斥，F9可停止。

## 当前后台适配（2026-11-18）
活动名称为“钓鱼助手”。当前主界面不再加载上述原版前台UI，改由外层 fishing_adb.py 复用 fisher.vision.Detector 和 fisher.controller.Controller 及其相位模块；截图与触摸通过外层 adb_backend.py，新增截图时标和输入前复核。上游前台UI仅作源码保留。活动共用任务和F9停止，原前台空格输入不用于当前活动。
