# Windows 完整安装包

本次按非商业用途分发完整量化模型，用户无需 Hugging Face 登录。两个固定源版本的实际 LICENSE 都是 CC BY-NC-SA 4.0，允许附条件复制、分享及改编；审核记录、原始模型卡与完整许可见 `licenses/models/`。仓库元数据只显示自动访问确认，未发现额外禁止再分发条款。商业服务不在本次许可判断范围内。

InfoXLM 的 HF 模型卡没有许可字段，因此不能以该卡单独证明 MIT；其官方 UniLM 项目说明与 XLM-R 使用相同词表、分词器和结构，包内保留 Microsoft UniLM 与 Facebook fairseq 的 MIT 版权声明。XLM-R XL 模型卡标注 MIT。模型量化配方和各文件 SHA-256 保留；原始 FP32 权重不随安装包重复提供。

## 用户如何安装

发布后从 Release 下载 `ReadMaster-Setup-0.5.0.1-Windows-x64.exe`，点击“下载并安装”。下载器会获取固定版本的完整安装器和分包，校验全部 SHA-256 后启动安装向导。下载器在自动重试时支持 HTTP 续传。安装向导退出后，或下载取消、失败后，下载器会自动清理本次版本的缓存文件。默认安装前建议预留 20 GB，安装后的程序和模型约占 9.1 GB。

离线使用：将 `ReadMaster-Full-0.5.0.1-Windows-x64.exe` 和所有同名前缀 `.bin` 分包放在同一目录，直接运行 Full EXE。不必先运行在线下载器。源码 ZIP 和审计 JSON 不是安装必需文件。

完全离线的前提是系统已有 Visual C++ v14 x64 运行库。缺失时安装器自动从微软固定 HTTPS 地址下载约 19 MB 文件，校验 SHA-256 后启动微软安装程序，可能出现 Windows 管理员提示。离线机器也可以先在别处从 `release-manifest.json` 的 `microsoft_vc_runtime.resolved_url` 下载该官方文件，将其以 `VC_redist.x64.exe` 放在 Full EXE 同目录；哈希必须与该清单一致。此微软安装文件不随 ReadMaster Release 再分发，因为微软将其再分发权限定于获许可的 Visual Studio 用户，而本次未确认这项资格。

默认安装到当前用户的应用目录，不需要手工安装 Python、COMET、BabelDOC 或 Docker。检测不到 WebView2 时使用微软签名的随包离线安装器。桌面快捷方式可选，开始菜单提供入口。Windows 10/11 x64，建议 32 GB 内存；尚未对所有硬件配置做性能验收。

用户在 Models 页面配置自己的翻译服务。模型评分本地运行，翻译/审校调用服务会联网。卸载移除程序、随包运行时及模型，保留运行后产生的文献和账号设置；如需完全清除个人数据，由用户另行管理。

## 构建与发布

`scripts/build_installer.py` 使用 Inno Setup 7.1.1、系统 .NET Framework C# 编译器及已准备的便携运行环境。工具链位于 `.build/installer-toolchain/`，不随源码包提供。先准备并检查模型许可及正式签名的 WebView2 x64 离线安装器，再运行构建。构建拒绝静默覆盖现有 payload；复核后可用 `--compile-only` 重新编译同一 staging。修改 payload 后须重新生成清单，不可只改公开安装附件。

输出 `output/ReadMaster-0.5.0.1/`，包括在线入口、完整离线安装器、每个不超过 1.5 GB 的分包、源码 ZIP、文件清单、分发清单和 SHA256SUMS。应用源码与 AGPL 第三方对应源码随安装内容提供。源码导出采用允许列表；模型包仅包含 manifest 列出的文件、精简验证标记和额外分发声明，不包含 HF 缓存、账号或论文。

在线入口固定指向 `https://github.com/leocql5184-dev/ReadMaster/releases/download/v0.5.0.1/`，在此 Release 正式发布前不能从远端下载；可以使用同目录离线附件验证。发布时上传同一构建的全部附件，文件名保持不变，不要混用不同构建的 EXE 和分包。发布是独立动作，本次构建不自动上传。

未配备发布者代码签名证书，所以 ReadMaster 自己的安装器/下载器未签名；SHA-256 校验不等于 Windows 发布者签名。微软 WebView2 安装器和 Inno 工具链已校验原始 Authenticode 签名。本机隔离目录安装和卸载测试不等同于全新 Windows VM 测试，测试范围以交付报告为准。

运行库依据：[微软 VC++ v14 下载及再分发说明](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist?view=msvc-170)。构建时还须准备 `.build/installer-toolchain/vcredist-download.json` 和从其中微软官方 URL 获取的参考安装文件，以固定校验值；仅 URL 与哈希编入安装器，不包含此二进制。`scripts/verify_installer.py` 对实际安装、离线双模型推理、升级和卸载执行集成验证；`scripts/verify_installer_downloads.py` 验证实际下载代码的续传、损坏拒绝和取消行为。
