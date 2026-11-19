欢迎安装 ReadMaster

包含：Windows x64 客户端、独立 Python/PDF/COMET 运行环境、Codex CLI、量化 COMETKiwi 和 XCOMET、WebView2 离线安装程序。

Windows 10/11 x64。评分使用 CPU，无需安装 CUDA 或 Docker。双模型权重约 5.52 GB，运行内存另计；建议 32 GB 内存。安装器会检查安装所需磁盘空间，下载缓存也需要额外空间。

安装完成后打开 Models，登录自己的 Codex 账号或填写 API。无需登录 Hugging Face。翻译服务仍需联网。

如果系统缺少 Visual C++ x64 运行库，安装器会从微软官方下载约 19 MB 的安装程序，校验后启动；Windows 可能要求管理员确认。此文件不随本软件分发。完全离线安装需事先装好该运行库，或将安装说明指定的微软官方 VC_redist.x64.exe 放在 Full EXE 同目录。

文献与设置保存在安装目录 data/。更新安装保留它们，卸载也不会主动删除之后创建的文献与设置。不要安装到源码工作目录或覆盖他人的便携客户端。

只分发非商业许可允许的模型用途。量化评分可能有偏差，关键结论、数值及公式请核对原文。
