from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .database import SessionLocal, bootstrap
from .routers import ponds, batches, stocking, feeding, water_quality, medication, costs, harvest, analysis, reviews
from .review_workflow import WorkflowError, scan_anomalies
from .validation import BatchValidationError, QuantityViolation


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 建表 + 老库幂等迁移；扫描旧库异常值生成复核记录（扫描本身幂等，
    # 服务重启后复核进度继续，不产生重复记录）。
    bootstrap()
    db = SessionLocal()
    try:
        scan_anomalies(db, source="startup_scan")
    finally:
        db.close()
    yield


app = FastAPI(
    title="水产养殖管理系统",
    description="支持塘口管理、投苗记录、日常管理、成本核算、出塘销售、养殖周期分析与异常数据复核",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(QuantityViolation)
async def quantity_violation_handler(request: Request, exc: QuantityViolation):
    return JSONResponse(
        status_code=422,
        content={
            "code": "quantity_constraint",
            "message": "数量约束校验失败",
            "errors": exc.errors,
        },
    )


@app.exception_handler(BatchValidationError)
async def batch_validation_handler(request: Request, exc: BatchValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "code": "batch_import_invalid",
            "message": "批量导入存在非法行，整批未写入",
            "errors": exc.row_errors,
        },
    )


@app.exception_handler(WorkflowError)
async def workflow_error_handler(request: Request, exc: WorkflowError):
    return JSONResponse(status_code=409, content={"code": exc.code, "message": exc.message})


@app.exception_handler(RequestValidationError)
async def request_validation_handler(request: Request, exc: RequestValidationError):
    # 与业务数量校验一致的稳定字段错误信封
    errors = []
    for err in exc.errors():
        loc = [str(part) for part in err.get("loc", ()) if part != "body"]
        errors.append({
            "field": ".".join(loc) if loc else "body",
            "rule": err.get("type", "invalid"),
            "message": err.get("msg", "参数不合法"),
        })
    return JSONResponse(
        status_code=422,
        content={
            "code": "request_validation",
            "message": "请求参数校验失败",
            "errors": errors,
        },
    )


app.include_router(ponds.router)
app.include_router(batches.router)
app.include_router(stocking.router)
app.include_router(feeding.router)
app.include_router(water_quality.router)
app.include_router(medication.router)
app.include_router(costs.router)
app.include_router(harvest.router)
app.include_router(analysis.router)
app.include_router(reviews.router)
app.include_router(reviews.corrections_router)
app.include_router(reviews.reversals_router)

@app.get("/")
def root():
    return {
        "message": "欢迎使用水产养殖管理系统API",
        "docs": "/docs",
        "version": "2.0.0"
    }

@app.get("/health")
def health_check():
    return {"status": "healthy"}
