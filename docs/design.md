# 系统设计与使用说明

本系统在原始合成资料之上建设证据核算、风险区间、干预评估、资源协调与
审计追溯能力。核心立场：**记录机会的差异不能被当成环境恶化，证据不足时
保留不确定范围，而不是用一个总分替代。**

## 1. 模块分工

| 模块 | 职责 |
| --- | --- |
| `clock.py` | 跨午夜夜间窗口、槽位对齐、夜键归属、区间重叠 |
| `model.py` | 显式状态枚举与不可变值对象 |
| `ingestion.py` | 资料加载、契约校验、引用完整性、记录哈希 |
| `observations.py` | 读数对齐槽位、缺口/证据分列、成熟度、超标率区间 |
| `complaints.py` | 投诉去重归并、撤回过滤、版本化边界归属 |
| `versions.py` | 边界与活动版本更正、影响窗口与对照范围查回 |
| `risk_ranges.py` | 可解释风险区间、风险带、记录机会偏差汇总 |
| `intervention.py` | 干预前后区间评估、对照差分、回访对比 |
| `scheduling.py` | 冻结/可重评拆分、资质时段路程约束、冲突检测 |
| `audit.py` | 证据快照、防篡改、建议追查链、回访对账 |
| `pipeline.py` | 端到端流水线与 CLI |

## 2. 观察机会与"无观察 ≠ 无噪声"

每个地点-夜固定 8 个槽位，槽位状态为六者之一：

- 明确证据：`exceeded`、`observed_below`；
- 观察缺口：`not_observed`、`partial`、`maintenance`、`offline`。

超标率给出区间而非点值：

- 下界 = 明确超标槽位 / 总槽位；
- 上界 =（明确超标 + 全部观察缺口）/ 总槽位。

明确低于阈值的读数永远不会进入上界，因此"达标证据"与"缺失"被严格分开。
窗口成熟度只看有效槽位占比是否达到 `maturity_min_valid_ratio`；不成熟或
窗口未结束的风险一律标记 `provisional`。

## 3. 投诉去重与记录机会偏差

重复投诉按时间窗归并为事件，风险同时报告**去重事件数**与**原始投诉条数**，
避免把同一事件的多次描述当成多起问题。`opportunity_bias_summary` 并列展示
各地点的传感器覆盖率、观察缺口、原始投诉量与已安排巡查次数，显式暴露
"投诉多的街区往往传感器更密、巡查更频繁"这一记录机会差异。

## 4. 风险区间而非总分

每个地点-夜输出 `[lower, upper]` 与风险带（high/moderate/low/unknown），
不产生跨地点可比的单一总分。`unknown` 表示没有有效证据（可能整夜无观察），
调度上视为"需要现场核实"，优先级高于 moderate、低于证据充分的 high。

## 5. 干预效果只在成熟且可比的窗口内解释

- 仅纳入成熟夜间窗口，并按气象桶（默认 dry）限制可比性；雨夜或缺证据夜
  会带原因列入 `excluded_nights`。
- 效果为区间 `post - pre`：上界仍为负才判 `reduced`，下界仍为正判
  `increased`，跨 0 判 `unclear`，没有合格窗口判 `insufficient`。
- 可指定对照地点做差分（DiD）；对照不成熟时不强行给结论。
- 回访数据进入后与原解释**并列**比较，原结论不被改写。

## 6. 资源协调与冲突

- `confirmed/completed` 安排冻结，保留 `basis_snapshot_id`；只有未来的
  `proposed` 安排可重新评估，过期的 proposed 退出本轮。
- 新建议按风险带排序，校验队伍资质、地点巡查时段、夜间窗口与路程；
  无可行资源时建议标记 `feasible = false` 并给出 blocker，而不是静默丢弃。
- 队伍或设备双重占用形成冲突，报告中可直接看到受影响的操作与建议。

## 7. 审计链与防篡改

- 每条原始记录在加载时计算规范化 SHA-256；规划时刻生成只读 `Snapshot`，
  快照自身也带摘要，任何字段改动都会使摘要校验失败。
- 检测磁盘资料篡改需把**历史快照的 record_hashes 作为外部基线**
  （`verify_against_snapshot`）；只用当次加载哈希无法发现外部改动。
- `trace_recommendation` 从一条建议追到风险事实、观察缺口、相关操作/投诉/
  边界版本与所用快照。

## 8. 运行方式

```bash
# 重新生成合成资料（仅写 data/，幂等）
python3 -I tools/generate_data.py

# 基线与全量测试
python3 -m unittest discover -s tests -v
python3 -m compileall src tests tools

# 端到端报告
python3 -I run_report.py --data data \
    --as-of 2026-10-07T12:00 --target-night 2026-10-07 \
    --control N-02 --output report.json

# 追查某条建议
python3 -I run_report.py --data data --trace rec-new-001

# 查回某次边界更正的影响范围
python3 -I run_report.py --data data --correction sv-n01-v2
```

> 隔离模式（`-I`）会忽略 `PYTHONPATH`，因此请通过仓库根目录的
> `run_report.py` 运行，它会自行把 `src/` 加入搜索路径。

## 9. 资料重新生成的稳定性

`tools/generate_data.py` 是唯一写数据的脚本，业务包不导入它。原始记录
（c-001..c-003、s-001..s-003、o-001..o-002 与 sites 初始字段）的取值与
语义在重新生成后保持不变，新记录以追加标识进入资料包。
