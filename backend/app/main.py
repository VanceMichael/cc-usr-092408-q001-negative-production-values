from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .database import engine, Base, SessionLocal
from .errors import FieldError
from .migrations import ensure_schema
from .routers import (
    ponds,
    batches,
    stocking,
    feeding,
    water_quality,
    medication,
    costs,
    harvest,
    analysis,
    reviews,
)
from .services.reviews import scan_legacy_anomalies

# 新库按模型（含 CHECK 约束）建表；旧库补齐新增列，历史异常行保留并在启动扫描登记。
Base.metadata.create_all(bind=engine)
ensure_schema(engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动扫描：旧库异常值按来源/影响形成复核记录；扫描本身幂等，重启可重放。
    db = SessionLocal()
    try:
        scan_legacy_anomalies(db)
    finally:
        db.close()
    yield


app = FastAPI(
    title="水产养殖管理系统",
    description=(
        "支持塘口管理、投苗记录、日常管理、成本核算、出塘销售、养殖周期分析，"
        "并对生产数量执行统一约束、异常复核与签署冲正。"
    ),
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


def _loc_to_field(loc) -> str:
    parts = [p for p in loc if p not in ("body", "query", "path")]
    field = ""
    for part in parts:
        if isinstance(part, int):
            field += f"[{part}]"
        else:
            field += f".{part}" if field else str(part)
    return field


@app.exception_handler(FieldError)
async def field_error_handler(_request: Request, exc: FieldError):
    return JSONResponse(status_code=exc.status_code, content=exc.to_body())


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(_request: Request, exc: RequestValidationError):
    fields = []
    for err in exc.errors():
        fields.append(
            {
                "field": _loc_to_field(err.get("loc", ())) or "_root",
                "code": err.get("type", "invalid"),
                "message": err.get("msg", "参数不合法"),
            }
        )
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "请求参数校验未通过",
                "fields": fields,
            }
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


@app.get("/")
def root():
    return {
        "message": "欢迎使用水产养殖管理系统API",
        "docs": "/docs",
        "version": "2.0.0",
    }


@app.get("/health")
def health_check():
    return {"status": "healthy"}
