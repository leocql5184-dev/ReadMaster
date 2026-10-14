# COMET 全文评估管线

当前实现版本：`comet-document-v5`（2026-10-14）。用户最新门槛为**至少 97% 的完整评估单元严格大于 0.7，全部不低于 0.5**。0.7 不计入超过阈值的比例，0.5 是允许的最低分；比例按实际数量计算，不四舍五入放行。全文平均分不能替代通过比例与最低分；分数不是准确率。

## 工作流程

1. 初译沿用原有约 3000 字的段落批次，保留句块编号、阅读进度和暂停恢复。已完成译文会复用。
2. 全文正文都有译文后，组合完整文章的原文、译文、术语和固定 ID。保留原版的图表、公式与参考文献也进入上下文，并明确标注 preserved。
3. COMETKiwi 对完整评估单元进行真实评分。机械句号拆开的数字、缩写，以及有版面依据的跨页续句会重组；阅读器 ID 不变。超出模型输入长度时明确失败，不静默截断。
4. XCOMET 对低分单元、论文规则异常和上一轮阻塞问题定位错误跨度。当前配置的 LLM 评估 agent 接收全文及这些线索，核实后给出逐字证据与修订建议。XCOMET 自身不生成自然语言修改意见，其预测也不直接证明译文有误。
5. 翻译 agent 接收全文上下文，只修订指定 ID。修订立即保存，旧分数失效，再次评估。未变化的模型输入复用真实推理缓存。
6. 至少 97% 的单元大于 0.7、全部不低于 0.5 且无阻塞问题后，独立验收角色再次检查全文。该角色不接收上一角色的通过结论、分数或 XCOMET 建议。通过后才标记 accepted，并允许按新译文生成 PDF。

论文检查覆盖数字、单位、百分比、公式、引用、术语、否定、条件、范围、漏译、重复及跨段指代。数字规则兼容脚注、千位分隔空格、缩写后的数字和型号中的连字符。数字上标必须由原 PDF 的字形大小、基线和位置证明，才恢复为指数参与核查；不会把普通整数 103 猜成 10^3。补充的版面信息不改动原句、固定编号、译文或阅读进度。

普通修订停滞时，翻译 agent 可生成三种忠实表达，由真实 COMETKiwi 分数和论文规则筛选。选中候选仍回到完整评估与独立验收。循环、译文不变或连续三轮未改善本次最好结果时会保留 needs_review，可继续或换模型复核；不得伪造分数、降低门槛或无限重复请求。不能保证任意论文的每个正确译文都达到固定模型阈值。

## 模型与运行环境

默认模型固定为：

- `Unbabel/wmt22-cometkiwi-da`，revision `1ad785194e391eebc6c53e2d0776cada8f83179a`，权重 2,260,734,705 字节。
- `Unbabel/XCOMET-XL`，revision `6a123c5e8e6dccab25e5fcffa3c8b417abadb462`，权重 13,943,384,935 字节。

两项官方授权已完成，模型与编码器已下载。XCOMET 完整权重通过官方 SHA256 校验。模型位于 `data/comet-models`；便携运行时位于 `tools/comet`，使用 CPU PyTorch 2.6.0 和 COMET 2.2.7。两个模型分进程串行加载，避免同时占用大量内存。离线评分不需要 Hugging Face 登录；凭据和权重不进入发布 ZIP。模型许可见模型页和 THIRD-PARTY-NOTICES.md，XCOMET 权重包含非商业条件。

COMET 2.2.7 的跨度解码在句尾错误未闭合时会丢失跨度；本项目依据真实标签、置信度和原译文偏移补齐句尾，不修改权重或预测标签，并对缓存使用独立版本标记。

可用环境变量：`READMASTER_COMET_PYTHON`、`READMASTER_COMET_DEVICE`、`READMASTER_KIWI_MODEL`、`READMASTER_XCOMET_MODEL`。自定义远程模型需设置对应 `READMASTER_KIWI_REVISION` / `READMASTER_XCOMET_REVISION`。更换模型后需重新校准阈值。

## 已验证证据与剩余工作

- 140 项回归通过，无跳过；33 页阅读界面的鼠标、键盘、缩放、移动布局、暂停与进度检查通过。新比例与最低分边界均有测试。界面测试的模拟分数不作为模型推理证据：`.build/reader-compat-report.json`。
- 官方 Kiwi 的真实离线推理能区分正确否定译文与故意漏译否定的译文：`.build/real-comet-smoke/kiwi-offline.json`。
- 官方 XCOMET 的真实预测已验证错误跨度及句尾修复：`.build/real-comet-smoke/xcomet-spans-corrected.json`。
- DeepSeek 与 Codex（gpt-6-luna）分别通过真实模型评分、故意否定误译修复、独立验收和 PDF 导出的小型控制测试：`.build/comet-live-verification/report.json`、`.build/comet-codex-verification/report.json`。
- 最新 0.5.0 候选 EXE（comet-document-v5）已通过真实 DeepSeek、离线 Kiwi/XCOMET、故意否定误译修复、独立验收、修订回写、阅读更新与两种 PDF 下载验证。4 个控制句只修订故意出错的句子，最低分 0.810524，未修改其他编号与译文：`.build/quality-client-verification.json`。
- 33 页原论文通过真实全量复评、论文检查和独立验收：262 个完整评估单元中 260 个严格高于 0.7（99.2366%），最低 0.583589，满足至少 97% 高于 0.7 且全部不低于 0.5 的用户标准。评估单元会重接机械切开的缩写、小数和有几何证据的跨页句，保留 392 个阅读句块编号：`.build/comet-live-verification-full-paper/report.json`。
- 同一最终译文的中文版、左右双语版 PDF 均为 33 页，392 个句块全部对应。修复了原生图注碎片、保留小标题前缀、脚注与下标对应，以及有字形位置证据的标识符下划线顺序。已查看全部页的预览，放大核查第 3、4、27 页和双语第 3 页：`.build/pdf-final-visual`。
- 最终程序入口为 `D:\readmaster\ReadMaster.exe`，已验证正式目录启动、质量界面资源、完成状态、两种下载及重启持久化。旧文库由既有版式升级流程备份后加载；确认原 PDF、升级后的编号与原文完全一致，才同步隔离副本的已验收结果，并保留阅读位置。升级前和同步前均有备份。PDF 另存于 `output/verified-paper`，模型连接仅保存在本机加密配置中。

验证入口：`scripts/verify_reader_compat.py`、`scripts/verify_comet_workflow.py`、`scripts/verify_comet_workflow.py --provider codex --model gpt-6-luna`、`scripts/verify_quality_client.py`。完整论文测试使用 `--paper-document` 指定现有 document.json，并在隔离副本上运行。

## 科研翻译专家验收提示词

`comet-document-v6-expert` 将评估与验收的语义阻塞标准统一为有证据的实质性错误，接受合理译法，必要的不确定提醒仅为 warning。验收输入继续不包含评估分数、结论和问题列表；输出使用原有 JSON 结构，问题回流至定点修订，再进入原评分与评估循环。评分门槛不变，循环停滞时仍保留需复核状态，不无限重试或自动放行。

运行时来源是 `readmaster/review_prompts.py`，`prompts/acceptance.txt` 为含机械切分规则的完整导出副本。改动审校提示词会自动改变评估/验收缓存键；翻译缓存与原始 COMET 推理缓存继续保留。旧文献的验收记录不会被伪装成新提示词验证结果。

设计参考 [MQM 严重程度标准](https://www.themqm.org/mqm-pillars/the-mqm-scoring-models/) 和 [OpenAI 评测指导](https://developers.openai.com/api/docs/guides/evaluation-best-practices)：按含义与可用性影响区分错误和偏好，明确通过/失败条件，并用有标签的案例检查。这里借鉴审校原则，不采用完整 MQM 计分，也未引入 OpenAI 评测服务。

真实 DeepSeek 小样本记录 `.build/expert-review-live.json`：11 个正确译文无阻塞，9 类故意错误全部检出，另外两个正确译文未误报。该记录仅验证提示词样例，不代表全文准确率或 33 页论文的新耗时；上面的 v5 论文验证仍是历史结果。

## 两批并发与常驻评分进程

初译每波最多两批，同时运行；完成的批次立即按原编号保存。整波完成后交给后台单队列进行 Kiwi 评分，后续两批翻译同时启动。后台最多积压两波任务，避免无限创建评分任务。全文评估前等待预评分结束；最终仍用完整文章的单元重新汇总，缓存仅复用相同原文、译文及权重版本的分数，跨波拼接单元不同则重新计算。

多个文献共享一个评分进程；模型最多驻留两个，内存不足时先卸载，CUDA 仅驻留一个。评分串行执行，避免两个 XCOMET 实例争抢内存。进程空闲 120 秒释放，评分中暂停会终止进程；后续启动继续复用磁盘分数缓存。Codex 全局并发上限为两路，原有限容量重试和暂停保持有效。

2026-10-14 小范围实测（不是整篇论文总耗时）：

- 同一论文前四批、14276 原文字符，DeepSeek 新请求串行 136.14 秒，两批并发 76.06 秒；样本耗时减少约 44%。记录 `.build/speed-live/translation-report.json`。
- Kiwi 首次单句 26.98 秒，常驻后的另一条新译文 0.11 秒；XCOMET 首次 31.25 秒，常驻后的另一条新译文 0.41 秒。模型切换时按内存情况卸载和重载，不能把这些比值当作全文加速倍数。记录 `.build/speed-live/resident-report.json`。
- Codex 默认模型两路真实请求同时开始，分别约 11.0 秒、12.7 秒完成，编号、数字与否定保留。记录 `.build/speed-live/codex-report.json`。
- 并发重叠、暂停、同波失败保留成功译文、模型复用、低内存卸载和空闲退出均有自动测试。全文审校输入未精简，质量门槛未调整。

## 磁盘维护

保留正式模型、tools 运行环境、源码、用户文献、模型连接和正在进行的任务。2026-10-14 已清理项目内 26 处旧测试客户端、完成下载的分片和无效 incomplete 文件；清理前后磁盘空闲容量增加约 74.7 GiB。报告：`.build/cleanup-result.json`。硬链接会重复计入目录文件大小，不能把目录大小合计当作物理占用。

项目外删除必须先向用户说明具体路径。D:\rm 和 C 盘 pip 缓存的删除命令被安全策略拦截，未执行；不通过更换工具或脚本绕过该限制。
