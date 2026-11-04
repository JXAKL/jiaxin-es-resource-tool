# 夹心的ES资源工具

当前版本：**2.1.24**。面向 Windows 的资源整理桌面工具，使用 Python、Tk / CustomTkinter 和 Playwright。

## 下载使用

无需配置 Python。打开 **[版本下载页](https://github.com/JXAKL/jiaxin-es-resource-tool/releases/latest)**，在 Assets 中选择：

- `Jiaxin-ES-Resource-Tool-2.1.24-Setup.exe`：中文安装向导，可创建桌面快捷方式。
- `Jiaxin-ES-Resource-Tool-2.1.24-Portable.zip`：完整解压后运行文件夹内的 `夹心的ES资源工具.exe`。

适用于 Windows 10/11 64 位。两种发行包均附带 Python、浏览器和播放组件，不含原用户配置、登录会话或素材数据库。首次使用需设置自己的素材目录，并按需要登录网站。不要下载 GitHub 自动生成的 Source code 来代替可运行发行包。

安装器安装到当前用户目录，卸载保留个人数据。安装版和免安装版均沿用 `%LOCALAPPDATA%\EroScriptsOrganizer` 保存设置。当前发行 EXE 未做代码签名。

## 功能

- 作者归类：根据 ES 帖子标题、正文和附件证据匹配作者，预览后归类。
- 本地配对：名称、元数据与时长配对，支持多轴脚本组、播放核对、整组复制或移动。
- 重复整理：Funscript 数据查重、视频时长与名称候选、完整文件查重、子文件夹组数整理。
- 链接采集与搜索刮削：按 ES 作者、标签或视频作者提取资源，支持排除标签、付费范围及预览。
- 资源补齐：分析作者目录的缺失资源，生成对应帖子与下载链接报告。
- 资源综合整理：多个来源关联配套，并按作者帖子首次发布日期归档到年月目录。
- 资源下载：统一资源队列、站点会话、画质与限速设置，支持多种视频和网盘来源。

详细说明已内置到程序右下角“使用说明”，也可按 F1。每次只显示当前模块及相关联动，不再额外生成外置说明书。

## 开发运行

建议使用带 Tk 的 Windows x64 Python 3.14。安装 requirements.txt 后，以 pythonw launch.pyw 启动。联网功能需要 Playwright 浏览器运行库；播放核对需要 mpv。构建和便携自检入口分别为 build_release.py、verify_release.py。

本仓库是源码，不包含预装的 Python、Chromium、Node 或 mpv 可执行文件。便携构建需预先准备 .build-assets 中的对应运行库及第三方许可；下载模块的已打包 JavaScript 网络库和许可位于 organizer/downloads/js/vendor。

## 项目结构

- app.py、launch.pyw：应用与窗口启动入口。
- organizer/：匹配、文件操作、采集、下载和界面模块。
- organizer/manual_content.py：随程序编译的分模块使用说明。
- tests/：测试与本地验证工具。
- tools/：开发辅助工具。
- assets/：应用图标与品牌图片。

运行数据、浏览器登录会话、缓存、素材文件、操作报告和打包运行库均不纳入仓库。使用时请保留自己的文件备份、操作记录和站点访问权限。

## 测试

可运行 `python -m unittest discover -s tests -q`。需要浏览器、媒体播放器或实际窗口的集成检查，须先准备相应本地依赖；部分性能探针面向已有本地索引，运行前请阅读脚本参数。

第三方代码的许可声明保留在对应目录。此仓库不改变第三方组件原有许可条件。
