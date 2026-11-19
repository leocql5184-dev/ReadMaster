# GitHub 发布评估（2026-11-17）

结论：当前代码可作为实验性阅读器发布源码；使用导出脚本生成的干净快照建立新仓库。尚不具备“别人只需克隆仓库，就能直接运行完整双量化翻译流程”的交付条件。模型分发与首次安装体验是剩余障碍，不是源码大小。

## 本次客户端交付

- `launcher.py` 和新构建的 `ReadMaster.exe` 默认进入双量化 CPU 线路。Kiwi 和 XCOMET 分别读取 `data/comet-models/cometkiwi-int8/manifest.json`、`data/comet-models/xcomet-xl-int8/manifest.json`。
- 必须存在完整文件及摘要匹配的通过验证标记；缺包、格式不符或 CUDA 冲突明确报错，不自动下载原版。实际加载时校验各文件哈希。保留 `--comet-mode fp32` 显式回退；`auto` 显式恢复旧选择方式。
- 根目录 EXE 和 `_internal` 已更新，旧版备份位于 `backups/client-before-quantized-20261117-195130/`。用户文库及账号配置未迁移或覆盖。
- 执行 179 项自动测试，171 项通过、8 项依赖外部运行服务或论文测试集而跳过。首次沙箱运行的 3 个本地 HTTP 测试被套接字权限阻止，放开本地连接后全套通过。
- 新 EXE 的入口诊断确认 `frozen=true`、`mode=quantized` 和两份本地 manifest；无模型的隔离构建目录返回缺包失败，未回退原版。
- 实际桌面窗口、健康接口、主页及正常退出已通过隔离文库验证，不调用外部翻译服务。
- 便携 worker 真实加载两份量化模型：6 条 Kiwi 回归样本最大分差 0，1 条 XCOMET 分数与错误跨度回归一致，缓存命中通过。这里对照的是此前已验证的量化结果，不是宣称量化与 FP32 相同。
- 本机证据：`.build/client-model-check-installed.json`、`.build/client-desktop-smoke.json`、`.build/client-tests-unrestricted.log`、`.build/kiwi-quantization/backend.json`。这些本地验证产物不随源码导出。

## 源码与隐私检查

导出命令：`.venv\Scripts\python.exe scripts/prepare_github_source.py`。产物是 `output/github-source-日期时间/`、对应 ZIP 和 `.audit.json`；审计文件记录文件数、体积、最大文件与 ZIP SHA-256。

导出按允许列表选择源码、测试、README、量化说明、许可证和界面资源；排除 `data/`、论文与译文、实验产物、权重、运行环境、账号会话、私有备忘、备份及 `.git`。常见凭据、私钥和个人主目录路径扫描通过；已把实验脚本的个人桌面路径改成 `READMASTER_TEST_PDF` 环境变量。该扫描不等价于完整安全审计。

另外将源码导出到隔离目录，在现有 Python 依赖环境下运行其中的 179 项测试：169 项通过，10 项因外部运行服务或未分发的论文测试集而跳过。修正了两项检查点单元测试对本机 BabelDOC 模板安装的隐式依赖，仍保留保存、恢复及失败后检查点断言。此验证不等同于在全新 Windows 上安装全部依赖。ZIP 完整性、内容清单及 SHA-256 已核对，源码约 1.07 MB、压缩后约 0.40 MB，最大文件小于 100 KB。

现有 Git 可达历史检查了 84 个 blob，最大 36,235 字节，没有命中本次令牌或私钥模式；三个历史浏览器测试文件仍含个人目录路径。工作树中的清理不会删除历史内容。因此推荐从干净快照建立新的公开仓库，不直接推送当前 `.git`；本次没有改写或删除开发历史。

## 大小与模型许可

| 交付物 | 当前判断 |
| --- | --- |
| 源码快照 | 可放普通 Git 仓库；保留 AGPL-3.0 及第三方声明 |
| 量化 Kiwi 权重 | 1,042,019,544 字节，不能直接提交普通 Git |
| 量化 XCOMET 权重 | 4,476,961,130 字节，不能直接提交普通 Git |
| 双量化权重合计 | 5,518,980,674 字节；另需 tokenizer、配置与运行环境 |
| 便携客户端 | 本机已更新，但本次没有制作或发布面向外部用户的完整安装包 |

GitHub 普通 Git 阻止超过 100 MiB 的文件。[官方文件限制](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)。Release 每个附件必须小于 2 GiB；XCOMET 即使单独作为原始权重附件也超过限制，需要分卷、分片或其他托管方式。[官方 Release 限制](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)。

两个源模型卡都标注 CC BY-NC-SA 4.0，量化不会取消原许可；源码的 AGPL-3.0 也不代替权重许可。[Kiwi 模型卡](https://huggingface.co/Unbabel/wmt22-cometkiwi-da)、[XCOMET 模型卡](https://huggingface.co/Unbabel/XCOMET-XL)。CC 许可允许在满足条款的情况下分享、改编，要求署名、提供许可链接、标明修改、非商业及相同方式共享；不能仅因 gated 下载就断言禁止再分发。[CC 许可说明](https://creativecommons.org/licenses/by-nc-sa/4.0/)。XCOMET 官方对商业服务另要求联系 Unbabel 授权。正式托管量化包前仍需核实接受过的具体访问条件，以及随包 tokenizer/config 的声明；本次未将权重分发视为已完成。

## 新用户目前的实际体验

源码克隆不会带上 COMET、BabelDOC 的独立运行环境、模型权重、Codex 登录或 API 配置。量化模型目前没有公共下载地址，导出与验证脚本还依赖本地敏感性分析产物。新用户要么取得完整且许可合规的量化包，要么接受官方模型条件后显式选择原版线路。README 已说明这点。

面向非开发者提供“下载即用”需要另行完成：合规的量化包托管与完整性验证、安装/下载入口、干净 Windows 环境的首次使用测试，以及完整便携分发物的第三方声明和对应源码核对。本次不冒充已完成这些发布工作。

## 质量定位

此前单篇 56 页材料的配对实验中，880 个可翻译句块有 873 个逐字相同、7 个不同；存在局部遗漏，且两条路线共有跨句块修订问题。两份定稿均未通过当时的全文验收门槛。此结果支持继续使用量化路线探索，但不能宣称无损量化、自动保证科研翻译正确或生产级质量。公开项目应保留人工核对提示，也不应随源码上传用户论文或完整译文作为演示数据。

本次仅完成本机修改、验证和发布准备，没有创建远程仓库、推送提交或发布 Release。
