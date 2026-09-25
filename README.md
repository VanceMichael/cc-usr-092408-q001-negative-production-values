# q001 水产养殖服务

本项目是水产养殖管理后端，维护塘口、养殖批次、投苗、投喂、水质、用药、成本、销售与周期分析数据。业务数据保存在 SQLite 文件中，HTTP 接口由 FastAPI 提供。

## 测试命令

```bash
python3 -m unittest discover -s tests -v
```

## 编译与构建命令

```bash
python3 -m compileall -q backend/app
```

## 启动命令

```bash
cd backend
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

启动后可访问 `/health` 检查服务状态。开发环境不得提交真实账号、连接凭据或生产数据。

## 数量约束

塘口面积与水深、投苗数量与重量、投喂量、检测读数、用药剂量、成本、销售重量与单价在创建、修改、批量导入时共用 `app/validation.py` 的同一套规则：

| 分类 | 含义 | 字段示例 |
| --- | --- | --- |
| `positive` | 必须大于 0 | 塘口面积/水深、投喂量、成本金额、销售重量/单价 |
| `positive_int` | 必须为大于 0 的整数 | 投苗数量（尾） |
| `non_negative` | 允许为零、可缺省，但不得为负 | 水质检测读数、投苗总重量、成本数量/单价、销售总金额 |

冲正分录允许与签署值符号相反（恢复到零），该场景仅由复核工作流独占使用。校验失败统一返回 HTTP 422：

```json
{"code": "quantity_constraint", "message": "数量约束校验失败",
 "errors": [{"field": "area", "rule": "positive", "message": "area 必须大于0"}]}
```

批量导入（`POST /api/<资源>/import/`）任一行非法则整批不落库，返回 `code=batch_import_invalid` 与逐行 `index/errors`。

## 签署冻结与复核工作流

* `POST /api/batches/{id}/close/` 关闭即签署，签署后普通增改删返回 409（`code=batch_signed`），只能冲正。
* 服务启动与 `POST /api/reviews/scan/` 会扫描旧库异常值，按“来源 + 实体行 + 字段”幂等生成复核记录（`data_reviews`），原始异常值保留不删，并记录发现时是否已进入结算。
* 未进入结算：`POST /api/reviews/{id}/corrections/` 拟单 → `POST /api/corrections/{id}/approve/` 批准。批准产生新版本行（旧行 `superseded_by_id` 留痕；塘口为原地升版本以保持批次关联）。
* 已签署批次：`POST /api/reversals/` 登记冲正事实（原值 + 冲正值 = 净额 0）。同一事实只能冲正一次：幂等键重试返回原事实，并发/异键重复冲正返回 409（`code=reversal_exists`）。
* 更正批准与批次关闭并发时，若批次先签署，批准转为 `conflict`（`code=batch_signed_concurrently`），必须改走冲正。

## 有效版本、追溯与周期分析

详情、`/api/analysis/traceability/{id}/` 与 `/api/analysis/cycle/{id}/` 共享 `app/effective.py` 的同一有效版本：只读取未被替代的最新版本，并对签署事实套用冲正净额。周期分析按数据版本指纹持久化到 `analysis_snapshots`，数据未变化时（含服务重启后）直接重放同一快照；更正或冲正改变版本后自动重算，`?refresh=true` 可强制重算。响应中的 `data_version` 在分析与追溯间一致。

## 老库升级

启动时 `bootstrap()` 幂等建表，并通过 `ALTER TABLE ADD COLUMN` 为旧库补齐 `version/superseded_by_id/void_reason`（业务表）与 `signed_at/closed_by`（批次表），可安全重复执行。
