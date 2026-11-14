# 问答 Pipeline

默认配置为 `hybrid`（`stoa/pipeline.py` 中的 `PROFILES`）。每次 LLM 调用都会记入 `logs/verda_calls.jsonl`。

```mermaid
flowchart TD
    A["图纸 + 问题<br/>(任意语言)"] --> B["① 识别文件类型<br/>inspect_file · 纯代码"]

    B -->|"PNG / JPG 栅格图"| V
    B -->|"IFC / IFC 渲染的 PDF"| R
    B -->|"DXF / 带 DXF 的 PDF"| R
    B -->|"带文字层的 PDF"| R

    R["② 路由<br/>DeepSeek 选工具 + 填参数"] --> T{"工具类型"}

    T -->|"终结工具<br/>ifc_count · ifc_sum_quantity · ifc_space_area<br/>dxf_count_symbols · dxf_room_area · dxf_total_area …"| D["③ 确定性计算<br/>答案 + 证据<br/>(GlobalId / DXF 句柄 / 文本位置)"]
    T -->|"信息工具<br/>ifc_list_elements · ifc_model_facts · dxf_room_list"| C1["④ DeepSeek 根据表格写答案"]
    T -->|"pdf_page_text<br/>(门窗表 / 做法表)"| C2["④ Kimi 根据表格写答案"]
    T -->|"没有合适工具"| V

    V["⑤ 视觉<br/>Kimi 看图 · reasoning_effort=low"]
    V -.->|"实验中，未接入"| CC["CubiCasa5K 模型<br/>门 / 窗 / 马桶 / 水槽 / 浴缸 / 桑拿<br/>本地 GPU · 每张图一次 · 缓存"]

    C1 -->|"最多 4 轮，最后一轮强制作答"| OUT
    C2 --> OUT
    D --> OUT
    V --> OUT
    CC -.-> OUT

    OUT["答案 + 证据 + 方法 + 是否确定性<br/>→ UI 在图纸上高亮证据"]

    classDef code fill:#e8f5e9,stroke:#2e7d32
    classDef cheap fill:#e3f2fd,stroke:#1565c0
    classDef strong fill:#fff3e0,stroke:#ef6c00
    classDef exp fill:#f5f5f5,stroke:#9e9e9e,stroke-dasharray: 4 3
    class B,D code
    class R,C1 cheap
    class C2,V strong
    class CC exp
```

图例：绿色为纯代码（免费、可验证），蓝色为 DeepSeek（便宜），橙色为 Kimi（强，但贵约 13 倍），灰色虚线为实验中。

## 每一步给 UI 的数据

| 步骤 | 输出 | UI 可以展示 |
|---|---|---|
| ① 识别文件 | 文件类型、可用工具 | 文件卡片，比如"IFC 模型，3 层，412 个构件" |
| ② 路由 | 工具名 + 参数（`Result.calls`） | "调用了 `ifc_space_area(space_name='ESIK (9-2)')`" |
| ③ 确定性计算 | `Answer.value / unit / method / evidence[]` | 在图纸或 3D 中高亮 evidence：IFC GlobalId、DXF 实体、PDF 文本框 `bbox` |
| ④ 写答案 | 答案 + 一句话说明引用了哪些行 | 引用的表格行 |
| ⑤ 视觉 | 答案 + 模型说明，`deterministic=False` | 低置信度标记，加"请在图上核对" |
| CubiCasa（计划中） | 每个门、窗、洁具的 `bbox` | 逐个框出来，人点一下就能核对计数 |

## 最新评测（同一批 200 题，seed 0）

| 配置 | 数值题 | 文字题 | 每 1000 题成本 |
|---|---|---|---|
| 只用 DeepSeek | 70% | 46% | $0.78 |
| 只用 Kimi | 70% | 50% | $15.45 |
| hybrid（视觉用默认推理强度） | 77% | 54% | $30.98 |
| **hybrid（视觉 `low`，当前默认）** | **79%** | **58%** | $9.51 |

详细数据和端点结论见 `VERDA_LOG.md`。
