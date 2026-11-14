# 图纸问答流程

> **默认配置：`hybrid`**
>
> DeepSeek 选择工具；Gemini 4 Hydrogen 读取 CubiCasa 图纸；Kimi 处理 PDF 表格与其他视觉问题。
>
> 视觉推理统一使用 `low`，输出上限为 4096 tokens。

## 总体流程

```mermaid
%%{init: {"theme": "base", "flowchart": {"curve": "basis", "nodeSpacing": 28, "rankSpacing": 44}, "themeVariables": {"fontFamily": "Arial, Microsoft YaHei, sans-serif", "fontSize": "14px", "lineColor": "#94a3b8"}}}%%
flowchart TB
    subgraph INPUT["输入与识别"]
        direction TB
        A(["图纸 + 自然语言问题"])
        B{"识别文件与关联数据<br/>inspect_file · 无模型调用"}
        A --> B
    end

    subgraph STRUCTURED["结构化数据问答"]
        direction TB
        R["DeepSeek 路由<br/>选择工具 · 填写参数"]
        T{"工具返回什么？"}
        D["代码计算答案<br/>计数 · 面积 · 工程量求和"]
        I["代码提取资料<br/>构件属性 · 模型概况 · 房间清单"]
        C1["DeepSeek 组织答案<br/>根据工具结果归纳与计算"]
        P["代码提取 PDF 内容<br/>表格行 · 图例 · 说明文字"]
        C2["Kimi 组织答案<br/>查表与文字理解"]
        R --> T
        T -->|"直接给出结果"| D
        T -->|"IFC / DXF 资料"| I
        T -->|"PDF 表格或文字"| P
        I --> C1
        P --> C2
    end

    subgraph VISION["视觉问答"]
        direction TB
        S{"图纸来源"}
        G["Gemini 4 Hydrogen<br/>CubiCasa 整图理解与计数"]
        K["Kimi<br/>其他栅格图与视觉回退"]
        S -->|"CubiCasa"| G
        S -->|"其他来源"| K
    end

    B -->|"IFC / DXF / PDF 文字层"| R
    B -->|"PNG / JPG / 扫描件"| S
    T -->|"需要看图"| K

    D --> OUT
    C1 --> OUT
    C2 --> OUT
    G --> OUT
    K --> OUT
    OUT(["返回结果<br/>答案 · 单位 · 方法 · 证据 · 确定性标记"])

    classDef input fill:#f1f5f9,stroke:#94a3b8,color:#334155,stroke-width:1.5px
    classDef code fill:#ecfdf5,stroke:#10b981,color:#065f46,stroke-width:1.5px
    classDef deepseek fill:#eff6ff,stroke:#3b82f6,color:#1e40af,stroke-width:1.5px
    classDef model fill:#f5f3ff,stroke:#8b5cf6,color:#5b21b6,stroke-width:1.5px
    classDef decision fill:#fffbeb,stroke:#f59e0b,color:#92400e,stroke-width:1.5px
    classDef output fill:#0f172a,stroke:#0f172a,color:#ffffff,stroke-width:2px

    class A input
    class B,D,I,P code
    class R,C1 deepseek
    class G,K,C2 model
    class T,S decision
    class OUT output
    style INPUT fill:#f8fafc,stroke:#e2e8f0
    style STRUCTURED fill:#f8fafc,stroke:#e2e8f0
    style VISION fill:#faf8ff,stroke:#e9d5ff
```

**阅读方式：** 绿色表示代码提取与计算，蓝色表示 DeepSeek，紫色表示 Gemini 或 Kimi，深色节点汇总最终结果。

能由单个终结工具直接回答的问题，首轮即可返回。信息工具的结果会交还模型；最多运行 4 轮，最后一轮要求模型作答。

## 结果与证据

| 环节 | 返回的数据 | 可用于界面的展示 |
| :--- | :--- | :--- |
| 文件识别 | 文件类型、关联 IFC / DXF、可用工具 | 图纸信息卡片 |
| 工具调用 | 工具名与参数：`Result.calls` | 展示调用过程与查询条件 |
| 代码计算 | `value`、`unit`、`method`、`evidence[]` | 展示数值、计算方法及来源对象 |
| 资料归纳 | 答案、解释、引用的证据 | 展示引用的构件或表格行 |
| 视觉回答 | 模型估计与页面说明；`deterministic=False` | 标记为待核对，提示人工检查 |

证据可包含 IFC `GlobalId`、DXF 实体句柄、PDF 文本或表格位置。界面可利用已有坐标与对象映射高亮证据；当前整图视觉回答只提供页面级说明。

## 模型配置与评测

### CubiCasa 同题对照

同一批 **30 题，seed 1024**；整图输入、`low` 推理强度、4096 token 上限。

| 视觉模型 | 正确数 | 准确率 | 30 题估算费用 |
| :--- | ---: | ---: | ---: |
| Kimi | 17 / 30 | 56.7% | $0.153496 |
| **Gemini 4 Hydrogen · 当前默认** | **25 / 30** | **83.3%** | **$0.155036** |

这是小样本对照，结果不代表全数据集准确率。

---

配置定义见 [`stoa/pipeline.py`](stoa/pipeline.py)；端点决策与实验记录见 [`VERDA_LOG.md`](VERDA_LOG.md)。所有模型调用记录在 `logs/verda_calls.jsonl`。
