# 来源与许可说明

本项目为塔塔冒险队活动助手，包含遗迹探险、围猎萨满及钓鱼活动。三项活动均使用ADB截图和触摸；钓鱼复用tt_fish的识别与相位预测模块。

开发过程中参考、借鉴了 [zz11-m/tt_fish](https://github.com/zz11-m/tt_fish) 的活动脚本思路，感谢 zz11-m 及贡献者。原项目版权声明为 `Copyright (C) 2026 zz11-m and contributors.`，采用 GPL-3.0-only，原项目说明见其 [NOTICE.md](https://github.com/zz11-m/tt_fish/blob/zz11-m-patch-1/NOTICE.md)。此处保留来源及参考项目的版权信息，不代表本仓库全部代码由原作者编写或获得其背书。

本仓库开发的遗迹探险流程、20项图鉴管理和 ADB 后台操作源代码及项目文档以 GPL-3.0-only 发布。分发程序时应保留许可及来源说明，并按 GPL 条款提供对应源代码。

`assets/` 中的局部游戏截图来自游戏界面，仅用于功能识别。相关游戏美术、名称及商标的权利归各自权利人；不属于代码许可证的授权范围。第三方依赖适用各自许可证。

本项目不是游戏、MuMu 或 tt_fish 的官方产品，不保证识别准确率、持续运行或适配所有分辨率。首次使用请通过观察模式核对画面和点击位置。

## 钓鱼源码整合

钓鱼标签参考并复用了 tt_fish 的源码（https://github.com/zz11-m/tt_fish），Copyright (C) 2026 zz11-m and contributors.，GPL-3.0-only。快照分支 zz11-m-patch-1，提交 c1263f7e84b77e47974f76ed733772dc6c6c0646。

本包在 third_party/tt_fish 保留对应源码、模板、测试、原 README、LICENSE、NOTICE 及 INTEGRATION.md。改动包括统一ADB界面和触摸适配、截图时标、发送前时机复核、默认仅观察与源文件清单适配；钓鱼识别和相位预测复用原项目。前台UI及Windows空格控制保留为上游源码，不由本整合版运行。上游声明、链接不代表原作者对本整合版背书。分发本修改版请保留来源、改动说明、GPL许可证及对应源码。
