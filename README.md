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

## 生产数据数量约束

所有数量字段在**创建、修改、批量导入**三个入口执行同一套规则（见
`backend/app/validation.py`），新库另有数据库层 CHECK 兜底：

- **必须大于零（positive）**：塘口面积/水深、投苗单重、成本单价、销售单价等；零与负数一律拒绝。
- **允许为零（non_negative）**：水质检测读数（溶解氧、氨氮、透明度、pH 等）允许 0，拒绝负数，pH 限制 0–14、水温 0–50℃。
- **可撤销后归零（voidable）**：投苗数量/总重量、投喂量、用药剂量、成本金额/数量、销售重量/金额；直接写入必须 >0，只有已批准的冲正事实可使其归零。

写入失败统一返回稳定字段错误信封：

```json
{"error": {"code": "validation_error", "message": "…",
           "fields": [{"field": "items[1].quantity", "code": "negative_value", "message": "…"}]}}
```

批量导入（各资源 `POST .../bulk/`）为原子操作：任一条目不合法整批拒绝，
字段路径形如 `items[i].field`。

## 批次签署、异常复核与冲正

- `POST /api/batches/{id}/sign/` 签署（月末结算冻结）批次；冻结后事实记录
  不能直接增删改（409 `state_conflict`），只能通过复核流程处理。
- 服务启动时（及 `POST /api/data-reviews/rescan/`）扫描旧库历史异常值，
  **不删除异常行**，而是按来源（legacy_scan/manual）与影响指标生成复核记录，
  扫描进度持久化、重启幂等。
- `POST /api/data-reviews/{id}/decision/` 批准或驳回。
- `POST /api/data-reviews/{id}/resolve/`：
  - 未进入结算（批次未签署）→ **更正**，直接改为正确值；
  - 已签署批次 → **冲正**，底表事实保留不动，追加冲正流水把有效值归零
    （整笔销售作废时量、价、额联动归零），周期分析按冲正后口径计算。
- 并发防护：复核与处置流水一一对应，配合条件认领与批次 `freeze_version`
  乐观版本，更正与批次关闭、或两笔更正并发时只有一笔成功，重复冲正返回
  409 `duplicate_reversal`；支持 `idempotency_key` 安全重试。

## 有效版本与重启重放

详情、追溯（`/api/analysis/traceability/...`）与周期分析
（`/api/analysis/cycle/...`）共享同一有效版本：更正呈现新值，冲正呈现 0，
底表原值始终保留可审计。周期分析结果按批次版本存入 `analysis_snapshots`，
版本未变时重启后直接重放（响应中 `replayed=true`），任何写入/更正/冲正都会
推进版本并触发下次重算。
