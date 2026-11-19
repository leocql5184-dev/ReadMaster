# COMETKiwi 本地混合 INT8 模型

已完成本机导出、固定译文评分对照与源码接入。保留原版检查点，未覆盖，也没有重新生成译文。

## 体积与配方

| 项目 | 大小 |
|---|---:|
| 原版 Kiwi 权重 | 2,260,734,705 字节（2.261 GB） |
| 量化 Kiwi 权重 | 1,042,019,544 字节（1.042 GB） |
| 完整 Kiwi 包 | 约 1.064 GB |
| 两个量化模型权重合计 | 5,518,980,674 字节（5.519 GB） |

Kiwi 权重减少约 53.9%。GB 按十进制；模型权重体积不等于运行内存，也不包含阅读器及 Python 运行时。

目录：`data/comet-models/cometkiwi-int8/`。采用 `protect_top12_heads`：保留编码层 8、9、10、11、12、13、14、15、16、17、19、20（从 0 开始）和评分头为 FP32；其余线性层为逐通道动态 INT8，嵌入层为逐行 weight-only INT8。LayerNorm、偏置等未声明量化的部分保持浮点。

不是直接把 XCOMET 的保护层套给 Kiwi。32 条既有诊断样本中，全线性层和嵌入层 INT8 的平均绝对分差为 0.02332、最大 0.08601；所选配方分别降为 0.00606、0.02089，诊断集没有 0.7 阈值翻转。另测试了仅嵌入层、评分头及保护 2/4/6/8/16 层等组合。选择后再查看固定论文集结果；这些保护层不保证对其他语种或文体同样最优。

## 固定论文评分验证

使用先前 8 页论文的初译和两份 XCOMET 修订稿，每份 200 个单元，去重后 225 对；与 32 条配方选择样本无完全相同的原文/译文对。不是 600 个独立样本。

| 固定译文 | 原版 Kiwi 均分 | INT8 Kiwi 均分 |
|---|---:|---:|
| 初译 | 0.775670 | 0.771689 |
| 原版 XCOMET 修订稿 | 0.781034 | 0.776989 |
| INT8 XCOMET 修订稿 | 0.778292 | 0.774471 |

去重集平均绝对分差 **0.00663**，95% 绝对分差不超过 **0.01869**，最大 **0.03529**，Spearman 相关系数 **0.99233**。

只有 **1/225** 对跨过 0.7：`p003_b066_001` 从 **0.69821** 到 **0.70138**。该文本在初译和 INT8 XCOMET 修订稿中重复出现，不能算两条独立翻转。0.5 阈值判断无翻转；三份稿件的全文数值验收结论均仍为未通过。

这是评分器保真测试，分数下降不表示固定译文变差。之后评分变化可能影响进入 XCOMET、修订和验收的片段；本次没有重跑两模型同时量化后的完整翻译/审校链，也没有人工金标准，不能称为无损。

接入前设定本地工程容差：MAE ≤ 0.01、P95 ≤ 0.03、最大绝对分差 ≤ 0.06、跨 0.7 比例 ≤ 5%。全部通过；这些是工程选择，不是通用质量标准。原有 0.7/0.5/97% 验收规则没有调整。

## 加载与回退

当前客户端入口默认使用双量化 CPU 模式，要求两份完整模型包和摘要匹配、通过验证的 `verified.json`。缺失时明确报错，不回退下载原始权重。启动：

```powershell
.venv\Scripts\python.exe launcher.py
```

显式切回两个原版模型：

```powershell
.venv\Scripts\python.exe launcher.py --comet-mode fp32
```

如果之前设置过单模型覆盖，恢复默认双量化前清除当前会话覆盖：

```powershell
Remove-Item Env:READMASTER_KIWI_MODEL -ErrorAction SilentlyContinue
Remove-Item Env:READMASTER_XCOMET_MODEL -ErrorAction SilentlyContinue
Remove-Item Env:READMASTER_COMET_MODE -ErrorAction SilentlyContinue
```

量化格式限 CPU/fbgemm，量化模式下 CUDA 配置会报错；需要 CUDA 原版时显式选 `--comet-mode fp32`。缓存包含模型清单 SHA-256 和固定 6 线程运行配方，不复用原版分数；损坏或缺少验证标记不会启用。加载时验证各文件校验和；无需读取原始 FP32 权重。

2026-11-17 已重建并替换本机根目录 `ReadMaster.exe`，默认进入双量化线路；已同步 `tools/comet/app/` 加载脚本。权重没有发布到 GitHub，也不自动随源码仓库或打包脚本分发。此前固定译文验证之后完成的双模型全流程对照属于另一项实验，详见 [发布评估](GITHUB发布评估.md)。

## 运行验证与复现

- 独立进程、空 HF 缓存，只加载 `cometkiwi-int8/weights.pt`，没有读取原版权重。32 条保存前/重载后最大分差为 0。
- 本机一次 257 对（32 诊断 + 225 论文）的运行，原版/量化峰值工作集约 5.09/2.16 GB，峰值 pagefile/commit 指标约 7.35/3.94 GB。不能将其当作任意机器的最低内存要求；双模型共存还需要额外内存。
- 原版/量化加载、校验和预测合计约 41.31/31.61 秒，不是纯推理加速比，也不是多用户并发测试。
- 54 项相关单元测试通过，覆盖双模型选择、显式覆盖、CUDA、验证标记、缓存隔离、线程恢复和既有质量逻辑。
- 实际便携 worker、两个量化模型共存、Kiwi 缓存命中和 XCOMET 回归结果见 `.build/kiwi-quantization/backend.json`。
- 完整证据：`.build/kiwi-quantization/REPORT.md`、`comparison.html`、`scores.csv`、`validation.json`、`sensitivity.json`。

```powershell
.venv-comet\Scripts\python.exe scripts/analyze_kiwi_quantization.py
.venv-comet\Scripts\python.exe scripts/analyze_kiwi_quantization.py --extend
.venv-comet\Scripts\python.exe scripts/quantize_kiwi.py --recipe protect_top12_heads
.venv-comet\Scripts\python.exe scripts/verify_quantized_kiwi.py
.venv\Scripts\python.exe scripts/verify_kiwi_backend.py
```

导出拒绝覆盖已有清单。复现依赖本地诊断样本和先前固定论文实验数据，它们没有包含在公开源码或模型包内。原版与量化版均使用 PyTorch 2.6.0+cpu、unbabel-comet 2.2.7、batch_size=1、6 个 CPU 线程。

原版来源：[Unbabel/wmt22-cometkiwi-da](https://huggingface.co/Unbabel/wmt22-cometkiwi-da)，revision `1ad785194e391eebc6c53e2d0776cada8f83179a`，SHA-256 `4f357aa38b0737dcd502f166238c99711ff3419d7b5c8cdf9cde08525a8e7858`。量化不改变原模型许可条件；模型包包含来源和修改说明，不含论文或凭据。
