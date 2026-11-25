# 资料说明（合成数据）

所有记录均为本地流程演练用的合成资料，不含真实个人信息，并由
`source_info.json` 标记 `source_kind = synthetic`。字段含义以 JSON 字段名
和实际记录为准。加载层不静默填充缺失值，也不改写原始记录。

## 文件总览

| 文件 | 内容 | 关键标识 |
| --- | --- | --- |
| `source_info.json` | 资料包来源与模式版本 | `source_kind` |
| `sites.json` | 地点、行政区、当前边界版本、地点巡查可接受时段 | `site_id` |
| `site_versions.json` | 地点边界版本与汇水分区，含更正版本 | `version_id` |
| `activities.json` | 夜间活动时段版本与限时播放干预 | `activity_id` + `version` |
| `weather.json` | 逐地点逐夜气象，`bucket` 为 dry/wet | `weather_id` |
| `sensors.json` | 传感器读数与质量状态 | `reading_id` |
| `complaints.json` | 投诉原始记录（含撤回、重复、分区归属） | `complaint_id` |
| `operations.json` | 巡查/告知/回访安排及结果 | `operation_id` |
| `resources.json` | 队伍与设备、资质、驻点 | `resource_id` |
| `travel_times.json` | 地点间路程（分钟）矩阵 | — |
| `policy.json` | 阈值、夜间时段、成熟度与风险带参数 | `policy_id` |

## 时间与夜间窗口

- 夜间窗口固定为 `22:00` 至次日 `06:00`，按 `slot_minutes`（60）切为 8 个槽位。
- 凌晨时间归属前一天开始的夜间窗口，例如 `2026-10-04T01:00` 属于
  `2026-10-03` 夜。
- 活动时段 `window_start/window_end` 同样可能跨午夜。

## 传感器读数质量状态（`sensors.json: quality`）

质量状态是一等语义，绝不与"没有记录"混同：

- `valid`：有效读数，必须携带数值 `db`；`db >= threshold_db` 判超标，
  否则是**明确低于阈值的证据**。
- `partial`：部分质量，可携带参考数值，但不单独判定超标，计入观察缺口。
- `maintenance`：维护中，无 `db`，计入观察缺口。
- `offline`：短时离线，无 `db`，计入观察缺口。
- 该槽位**没有任何读数记录**时状态为 `not_observed`，同样是观察缺口，
  但不会伪造读数标识。"无观察"不等于"没有噪声"。

## 投诉状态与归属（`complaints.json`）

- `status = received` 参与事件归并；`withdrawn` 保留记录但不形成事件。
- 同一地点、同一夜间窗口内，相邻投诉间隔不超过
  `policy.cluster_window_minutes` 即归并为同一扰民事件，事件保留全部
  原始 `complaint_id` 以便查回。
- `recorded_site_id` 是录入系统的地点；系统按投诉时刻有效的边界版本，
  用 `zone_id` 重算归属地点。原始 `site_id` 与 `recorded_site_id` 不被改写。

## 边界与活动版本

- `site_versions.json` 用 `effective_from/effective_to` 表达版本有效期，
  `is_correction = true` 标记勘误版本，并记录 `correction_reason`。
- `activities.json` 中 `activity_type = intervention` 表示限时播放等干预
  措施；普通活动为 `night_market`。时段更正同样带版本与原因。
- 更正发生后，受影响的夜间窗口、投诉归属和可比较对照范围可通过
  `correction_impact` 查回。

## 巡查安排（`operations.json`）

- `kind`：`patrol` 巡查、`notice` 居民告知、`revisit` 回访。
- `status`：
  - `proposed`：尚未确认，仅当其仍在未来时可被重新评估；
  - `confirmed`：已确认，冻结，保留当时依据；
  - `completed`：已完成，冻结并保存 `result` 与 `basis_snapshot_id`；
  - `cancelled`：取消，不参与排期。
- `basis_snapshot_id` 指向该安排决策时刻的证据快照；新读数只能改变未来
  建议，不能改写已确认/已完成安排的当时依据。
- `linked_operation_id` 把回访关联回被回访的原巡查。

## 资源、资质与冲突

- 队伍（`resource_type = team`）携带资质列表，设备（`equipment`）无资质。
- 同一 `staff` 或 `equipment` 在重叠时段被两个地点占用即构成冲突，
  系统会指出冲突的操作、共享资源与时间窗，并标明哪些建议因此无法兑现。
- 地点可在 `sites.json` 用 `patrol_window_start/end` 限定可接受到场时段；
  排期还校验队伍资质、夜间时段和地点间路程。

## 策略参数（`policy.json`）

- `threshold_db`：超标阈值；`maturity_min_valid_ratio`：窗口成熟所需的
  有效槽位占比（默认 0.75）。
- `risk_*_weight`：各类观察缺口在风险上界中的折算权重。
- `band_*`：风险带分界；风险以区间 `[lower, upper]` 表达，不产生单一总分。
