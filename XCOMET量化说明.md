# XCOMET 本地混合 INT8 模型

本文记录 XCOMET-XL 量化；当时对照评分的 Kiwi 保持原版。之后已完成独立的 [COMETKiwi 量化与评分保真验证](COMETKiwi量化说明.md)，源码现在可以自动选择两个已验证的量化包。源检查点均保留，未覆盖。

## 产物与配方

- 目录：`data/comet-models/xcomet-xl-int8/`。
- 权重：`weights.pt`，4,476,961,130 字节（4.477 GB）。原版为 13,943,384,935 字节，减少约 67.9%。
- 完整包约 4.50 GB，包含分词器、配置、清单及本地许可说明。
- 编码层 1、2、8、10（从 0 开始）和所有输出头保留 FP32。
- 其余线性层采用逐通道动态 INT8；嵌入层采用逐行 weight-only INT8，共转换 195 个模块。
- 这是已测试的混合候选，不表示这四层是通用最优保护集合，也不表示相对原版无质量变化。

## 加载和使用

当前客户端入口默认使用双量化 CPU 模式，要求完整模型包、通过验证的 `manifest.json` 和匹配的 `verified.json`；缺失时明确报错，不下载原始权重。
缓存身份包含量化清单摘要，原版和量化版分数不会混用。工作进程读取模型包时校验文件 SHA-256。
加载器仅在 meta 设备上创建大编码器结构，再加载打包参数；不读取、下载或临时完整加载原始 FP32 检查点。

已同步本工作区 `tools/comet/app/` 的两个加载脚本。重启源码启动的阅读器后生效：

```powershell
.venv\Scripts\python.exe launcher.py
```

2026-11-17 已重建并替换本机根目录 `ReadMaster.exe`，默认进入双量化线路，没有发布 GitHub Release。
`scripts/build_comet_runtime.py` 已纳入新加载模块；现有打包脚本仍不自动分发模型权重。

显式回到两个原版模型：

```powershell
.venv\Scripts\python.exe launcher.py --comet-mode fp32
```

`READMASTER_XCOMET_MODEL` 在量化模式下必须指向已验证的对应模型清单；不再需要覆盖时，删除该环境变量。本 INT8 格式只支持 CPU/fbgemm；使用 CUDA 原版需显式选 `fp32`。

## 验证证据

- 54 项相关单元测试通过，覆盖验证标记、显式覆盖、CUDA 选择、缓存隔离、线程恢复、路径约束和既有质量逻辑。
- 在独立进程、空 Hugging Face 缓存下，经实际 worker 加载模型包。
- 32 条先前敏感性样本：保存前与重载后的混合精度结果最大分数差为 0，错误位置及严重程度完全一致。
- 加载到完成 32 条预测约 46.45 秒；这是本机一次测试，不是应用端到端延迟承诺。
- 进程峰值工作集为 8,409,309,184 字节，峰值 pagefile/commit 指标为 10,568,839,168 字节。不能把 4.50 GB 包大小当成最低内存需求，也不能据此保证任意 8 GB 或 16 GB 电脑可流畅运行。
- 验证文件：`.build/xcomet-quantized-validation/report.json`；实际阅读器后端接入记录：`.build/xcomet-quantized-validation/backend.json`。

上述等价性是“导出前的混合精度模型 vs 导出后重载”，不是“量化版 vs 原版”。先前敏感性分析中，混合版在 32 条中有 21 条的错误片段发生变化，包含边界、严重程度及新增标记。
之后完成了单篇 8 页论文的“原版/量化版接入同一审校大模型”对照，报告见 `.build/xcomet-paper-comparison/REPORT.md`；该实验仍固定使用原版 Kiwi。尚未完成独立人工标注大样本，不得将本产物标成无损量化。

## 复现

```powershell
.venv-comet\Scripts\python.exe scripts/quantize_xcomet.py
.venv-comet\Scripts\python.exe scripts/verify_quantized_xcomet.py
```

导出脚本拒绝覆盖已有清单。重载验证需要先前敏感性实验的样本和混合精度输出；详见 `scripts/analyze_xcomet_sensitivity.py`。
运行环境固定为 PyTorch 2.6.0+cpu、unbabel-comet 2.2.7。文件及配方摘要保存在模型清单内。
INT8 推理固定使用与敏感性实验一致的 6 个 CPU 线程，调用结束恢复原线程数；便携运行时默认 24 线程曾产生约 0.014 的评分差异，故不能把线程配置视为无关参数。缓存标识包含这一运行配方。

源模型为 Unbabel/XCOMET-XL，revision `6a123c5e8e6dccab25e5fcffa3c8b417abadb462`，SHA-256 为 `b644a48a1163ca7f8d3bcf237816eea7ecf368ecdbd6eb16ea27fef218fcd716`。
量化不改变原模型许可条件；本次产物留在本地，不包含账号凭据或论文样本。
