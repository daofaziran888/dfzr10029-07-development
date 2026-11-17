"""字段契约接口：把来源 schema 变更包编译为可审计接入契约，覆盖导入、编译、核对与审核动作。"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, EntryPayload, PageResult
from app.services.field_contract import FieldContractService

router = APIRouter(prefix="/api/field_contract", tags=["字段契约"])

service = FieldContractService()

LIST_FIELDS = ["契约编号", "数据集编号", "数据集名称", "契约版本", "来源规则", "字段数量", "负责人", "契约状态"]
STATUSES = ["草稿", "待审核", "已发布", "已拒绝"]


def query_filters(request: Request) -> dict[str, str]:
    """只接受当前模块声明过的字段，忽略分页等保留参数。"""
    return {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in LIST_FIELDS and value.strip()
    }


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按契约编号检索"),
    status: str | None = Query(default=None, description="草稿、待审核、已发布、已拒绝"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    """按页面字段、契约编号关键字与状态过滤字段契约列表。"""
    items, total = service.list_entries(
        filters=query_filters(request), keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries(
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    """下载当前筛选条件下的 UTF-8 CSV；必须声明在 /{entry_id} 之前。"""
    items, _ = service.list_entries(
        filters=query_filters(request), keyword=keyword, status=status, page=1, size=1000000)
    output = io.StringIO()
    output.write("﻿")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *LIST_FIELDS, "status"])
    for item in items:
        row = []
        for value in [item.get("id"), *(item.get(field, "") for field in LIST_FIELDS), item.get("status", "")]:
            text = "" if value is None else str(value)
            row.append("'" + text if text.startswith(("=", "+", "-", "@")) else text)
        writer.writerow(row)
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="field_contract.csv"'})


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条字段契约明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"字段契约 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: dict[str, object]) -> ActionResult:
    """登记一条字段契约，缺字段或数据集已有契约时说明原因而不是静默丢弃。"""
    entry, errors = service.create_entry(payload)
    if errors:
        return ActionResult(ok=False, message="；".join(errors))
    return ActionResult(ok=True, message="字段契约已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    """对单条字段契约执行提交审核、接受变更、拒绝变更；不允许的动作会被拦下并说明原因。"""
    entry, message = service.run_action(entry_id, payload.action.strip())
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/{entry_id}/packages", response_model=dict)
def list_packages(entry_id: int) -> dict:
    """列出契约收到的来源 schema 变更包（含待编译与已编译）。"""
    if service.get_entry(entry_id) is None:
        raise HTTPException(status_code=404, detail=f"字段契约 {entry_id} 不存在或已归档")
    return {"items": service.list_packages(entry_id)}


@router.post("/{entry_id}/packages", response_model=ActionResult)
def import_package(entry_id: int, payload: dict[str, object]) -> ActionResult:
    """导入来源 schema 变更包：登记字段签名，等待按声明来源规则编译。"""
    package, message = service.import_package(entry_id, payload)
    if package is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=package)


@router.post("/{entry_id}/compile", response_model=ActionResult)
def compile_contract(entry_id: int, payload: dict[str, object]) -> ActionResult:
    """按声明来源规则编译待编译变更包；基准版本不一致时本次编译不生效。"""
    try:
        base_version = int(payload.get("基准版本"))
    except (TypeError, ValueError):
        return ActionResult(ok=False, message="缺少必填字段：基准版本（当前契约版本号）")
    entry, message, snapshot = service.compile_contract(entry_id, base_version)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry={"契约": entry, "快照": snapshot})


@router.get("/{entry_id}/snapshots", response_model=dict)
def list_snapshots(entry_id: int) -> dict:
    """查询契约的字段契约快照列表。"""
    if service.get_entry(entry_id) is None:
        raise HTTPException(status_code=404, detail=f"字段契约 {entry_id} 不存在或已归档")
    return {"items": service.list_snapshots(entry_id)}


@router.get("/{entry_id}/snapshots/{snapshot_id}", response_model=dict)
def get_snapshot(entry_id: int, snapshot_id: int) -> dict:
    """读取字段契约快照，并回到快照的关联来源记录。"""
    snapshot, message = service.get_snapshot(entry_id, snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=message)
    return snapshot


@router.get("/{entry_id}/diff", response_model=dict)
def diff_snapshot(entry_id: int, snapshot_id: int = Query(description="要核对的字段契约快照 id")) -> dict:
    """读取字段契约快照，与主版本逐项核对字段签名差异。"""
    result, message = service.diff_snapshot(entry_id, snapshot_id)
    if result is None:
        raise HTTPException(status_code=404, detail=message)
    return result


@router.get("/{entry_id}/affected_templates", response_model=dict)
def affected_templates(entry_id: int) -> dict:
    """跨模块读取查询模板，列出引用了未发布或已变化字段、需要停用的模板。"""
    if service.get_entry(entry_id) is None:
        raise HTTPException(status_code=404, detail=f"字段契约 {entry_id} 不存在或已归档")
    items, _ = service.affected_templates(entry_id)
    return {"items": items}
