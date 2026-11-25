# 夜间噪声证据与巡查资源复议

面向城市夜间经济开放后的噪声扰民处置：在持续导入投诉、传感器读数、气象、
活动时段、巡查结果与告知/整改安排的基础上，区分记录机会差异与真实环境变化，
输出**可解释的风险区间**、有保留的干预效果评估、可追溯的巡查协调方案。

## 核心原则

- **无观察 ≠ 无噪声**：没有读数的槽位是观察缺口，明确低于阈值的读数是
  证据，二者严格分开；离线、维护、部分覆盖各自独立表达。
- **区间而非总分**：风险与干预效果都给 `[lower, upper]` 区间和成熟度说明，
  证据不足时保留不确定范围，不用单一总分替代。
- **记录机会可解释**：投诉去重为事件，并与传感器覆盖率、巡查频次并列展示，
  避免把"记录得多"误判为"更严重"。
- **历史依据不可改写**：已确认/已完成的巡查和告知冻结当时依据快照，
  新读数只影响未来建议；只有未确认安排可重新评估。
- **全程可追溯**：每条建议可追到采用的事实、观察缺口、边界/活动版本和
  证据快照；原始记录带哈希，支持防篡改核对。

## 目录结构

```
data/                     合成业务资料（由 tools/generate_data.py 生成）
docs/                     数据字典与系统设计
src/night_noise_response/ 领域包（观察/风险/干预/调度/审计/流水线）
tests/                    基线契约与全量业务规则测试
tools/generate_data.py    合成资料生成脚本（业务包不导入）
run_report.py             命令行入口
```

## 快速开始

```bash
# 生成/重建合成资料
python3 -I tools/generate_data.py

# 运行测试与编译检查
python3 -m unittest discover -s tests -v
python3 -m compileall src tests tools

# 生成端到端报告（风险区间、干预效果、规划、冲突、快照）
python3 -I run_report.py --data data \
    --as-of 2026-10-07T12:00 --target-night 2026-10-07 \
    --control N-02 --output report.json

# 追查一条建议的完整依据链
python3 -I run_report.py --data data --trace rec-new-001

# 查回一次边界更正影响到的窗口、投诉与对照范围
python3 -I run_report.py --data data --correction sv-n01-v2
```

资料字段语义见 [docs/data-dictionary.md](docs/data-dictionary.md)，
模块设计与语义取舍见 [docs/design.md](docs/design.md)。

## 资料性质

所有资料均为本地可复现的合成记录（`source_kind = synthetic`），
不含真实个人信息；缺失、部分质量和待处理状态都是资料含义的一部分，
加载层不会静默填充或改写原始记录。
