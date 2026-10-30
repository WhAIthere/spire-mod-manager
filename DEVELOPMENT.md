# 源码运行与修改

推荐 Windows 10/11、64 位 Python 3.11。最低 Python 3.11（文件校验使用 hashlib.file_digest）。普通用户下载 EXE，无需安装 Python。

## 一次性准备

在项目目录打开 PowerShell，执行：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe web_app.py
```

不必激活虚拟环境，因此无需更改 PowerShell 脚本执行策略。Windows 需要 Microsoft Edge WebView2 Runtime；若初始化提示缺少 WebView2，请安装微软官方 Runtime 后重试。

## 修改入口

- frontend/index.html、styles.css、app.js：界面和交互。
- manager_model.py、core.py：列表、标签、开关和游戏配置适配。
- web_api.py、web_jobs.py：前后端接口、刷新和后台任务。
- versions.py、release_store.py：可选缓存和增量备份包。
- steam_account.py：Steam Key 的 DPAPI 本机加密存储。

源码默认沿用已打包程序的 data。如果想独立试验，先指定单独的数据目录；不要在真实游戏配置上测试应用或恢复。

```powershell
$env:SPIRE_MANAGER_DATA = "$PWD\dev-data"
.\.venv\Scripts\python.exe web_app.py
```

测试使用临时目录，不需要填写 Steam Key：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -p "test_*.py"
```

## 构建 EXE

```powershell
py -3.11 -m venv .build-venv
.\.build-venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.build-venv\Scripts\python.exe build_exe.py
```

EXE 使用 onedir 方式，必须与 _internal 一起分发。普通构建用于本机更新，会保留、并可能初始化个人 data；不要压缩正在使用的 dist 目录上传。

公开发布使用独立的纯净构建与白名单源码导出：

```powershell
.\.build-venv\Scripts\python.exe build_exe.py --public
.\.build-venv\Scripts\python.exe public_release.py
```

纯净程序生成在 build/public-package，源码和程序 ZIP、SHA256SUMS 生成在 build/public-release/v版本号，不读取个人 data。相同版本输出目录已存在时会拒绝覆盖；需要重做时先将该目录移走。依赖许可证从构建虚拟环境收集，不随源码复制整个环境。

首次公开版本说明见 [docs/RELEASE_NOTES.md](docs/RELEASE_NOTES.md)。
