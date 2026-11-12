# 到梦空间助手

在 Windows 电脑上，通过 USB 连接 Android 手机，辅助处理到梦空间活动的补签、录取和学分发放。

[下载 Windows 版](https://github.com/2286057864a-web/daomeng-assistant/releases/latest) · [使用说明](docs/使用说明.md) · [源码运行与打包](docs/开发说明.md)

![软件界面](docs/images/main.png)

## 功能

- **活动扫描**：读取活动管理列表，显示需要处理的活动；读到第一个已完结活动时停止。
- **补签**：按年级勾选，或导入 Excel 名单处理；年级支持手动填写。
- **录取**：按 Excel 名单演练或正式录取。
- **学分**：导入姓名、学号名单，设置分值，演练或正式发放。
- **工作结果**：作业结束后查看结果，并通过“导出工作名单”保存 Excel 工作表。
- **连续任务**：同一个窗口可继续处理下一项任务。
- **诊断与反馈**：导出诊断包用于排查问题。

界面使用 Python 标准库 tkinter/ttk，字体使用系统的微软雅黑 UI。当前显示“补签、录取、学分”三个页签；本地比对实现保留在源码中，默认隐藏。

## 下载使用

1. 从 [Releases](https://github.com/2286057864a-web/daomeng-assistant/releases/latest) 下载 Windows 程序压缩包。
2. **完整解压**后打开“到梦空间助手.exe”，保留同目录的 `_internal` 文件夹。
3. 手机开启 USB 调试，连接电脑并确认授权；登录到梦空间中具备对应活动管理权限的账号。
4. 点击“扫描活动列表”，选择活动，再执行对应操作。

正式录取、补签提交和学分发放会修改手机端数据，执行前核对活动和人员。手机端出现结果不明时，应先人工核对，不要重复提交。

## 源码快速启动

使用 Windows 和 Python 3.13。先从 [Android 官方](https://developer.android.com/tools/releases/platform-tools)下载 Windows Platform Tools，将 `platform-tools` 文件夹放在项目根目录。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe gui.py
```

离线回归检查：

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests
```

这些测试使用假设备和临时文件，不执行手机端提交。源码回归通过不等于任意手机或平台版本都已通过真机验收。

## 致谢与项目来源

感谢原项目作者与贡献者的分享，让实用的想法得以延续。

参考项目：[tangent2pi/daomeng](https://github.com/tangent2pi/daomeng)。本仓库发布的是当前 tkinter 桌面助手，与该参考项目及已有 fork 分开维护。

感谢南华大学易班工作站技术部、行政部，以及资环安团委在团学工作中的付出。也感谢每一位为团学工作付出时间与心力的同学。

第三方组件及图标说明见 [NOTICE.md](NOTICE.md)。

