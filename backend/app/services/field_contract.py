"""字段契约业务规则：把来源 schema 变更包编译为可审计的接入契约。

核心口径：
- 编译输入 = 来源 schema 变更包 + 当前字段契约；同名字段按声明来源规则（来源连接的优先级
  序列）裁决，而不是按变更包到达顺序，保证同一冲突下结果唯一、可复算。
- 字段签名与来源规则写入同一契约版本，并冻结为字段契约快照，快照保留业务输入，
  供字段契约接口读取后与主版本逐项核对。
- 编译动作加锁并校验基准版本，并发提交时只有一个是有效结果。
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "field_contract"
PACKAGE_TABLE = "schema_change_package"
SNAPSHOT_TABLE = "contract_snapshot"
AUDIT_TABLE = "audit"

LIST_FIELDS = ["契约编号", "数据集编号", "数据集名称", "契约版本", "来源规则", "字段数量", "负责人", "契约状态"]
REQUIRED_FIELDS = ["契约编号", "数据集编号", "数据集名称"]
STATUS_ORDER = ["草稿", "待审核", "已发布", "已拒绝"]
ACTION_RULES = {"提交审核": "待审核", "接受变更": "已发布", "拒绝变更": "已拒绝"}
NEGATIVE_ACTIONS = ["拒绝变更"]

# 编译串行化：同一时刻只允许一个编译写入契约版本，配合基准版本校验保证唯一有效结果。
_COMPILE_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _next_id(table: str) -> int:
    return max((int(row.get("id", 0)) for row in store.rows(table)), default=0) + 1


def _next_code(table: str, prefix: str) -> str:
    return f"{prefix}-{_next_id(table):04d}"


def _priority(rules: list[str], source: str) -> int:
    """来源连接在声明来源规则里的优先级，越靠前越高；未声明的来源排最后。"""
    try:
        return list(rules).index(source)
    except ValueError:
        return len(rules)


def _write_audit(action: str, target: str, detail: str) -> None:
    """把契约关键动作写进数据审计，保证接入契约可审计。"""
    rows = store.rows(AUDIT_TABLE)
    rows.append({
        "id": _next_id(AUDIT_TABLE),
        "status": "已审计",
        "pending": False,
        "abnormal": False,
        "记录编号": _next_code(AUDIT_TABLE, "AUDI"),
        "操作类型": action,
        "操作人": "接入值班",
        "操作对象": target,
        "操作时间": _now(),
        "变更内容": detail,
        "审计结果": "通过",
        "记录状态": "已登记",
    })


def _find_contract_by_dataset(dataset_code: str) -> dict[str, Any] | None:
    for row in store.rows(MODULE):
        if row.get("数据集编号") == dataset_code:
            return row
    return None


def _snapshots_of(contract_code: str) -> list[dict[str, Any]]:
    rows = [row for row in store.rows(SNAPSHOT_TABLE) if row.get("契约编号") == contract_code]
    return sorted(rows, key=lambda row: (int(row.get("契约版本", 0)), int(row.get("id", 0))))


def _packages_of(contract_code: str, status: str | None = None) -> list[dict[str, Any]]:
    rows = [row for row in store.rows(PACKAGE_TABLE) if row.get("契约编号") == contract_code]
    if status:
        rows = [row for row in rows if row.get("status") == status]
    return sorted(rows, key=lambda row: int(row.get("id", 0)))


def _normalize_signatures(raw: Any) -> list[dict[str, str]]:
    """把接口传入的字段签名规整为 {字段名, 字段类型} 列表，丢弃缺项并说明。"""
    items: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return items
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("字段名") or "").strip()
        type_name = str(item.get("字段类型") or "").strip()
        if name and type_name:
            items.append({"字段名": name, "字段类型": type_name})
    return items


class FieldContractService:
    # ---------------------------------------------------------------- 标准列表/登记/动作
    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        for field, expected in (filters or {}).items():
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            rows = [row for row in rows if keyword.casefold() in str(row.get("契约编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return [self._list_view(row) for row in rows[start:start + size]], total

    def _list_view(self, row: dict[str, Any]) -> dict[str, Any]:
        view = dict(row)
        view["来源规则"] = " > ".join(str(item) for item in row.get("来源规则", []))
        return view

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return self._list_view(row) if row else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, [f"缺少必填字段：{'、'.join(missing)}"]
        if _find_contract_by_dataset(str(values.get("数据集编号"))):
            return None, [f"数据集 {values.get('数据集编号')} 已存在字段契约"]
        rows = store.rows(MODULE)
        rules = values.get("来源规则")
        rule_list = [str(item).strip() for item in rules] if isinstance(rules, list) else [
            part.strip() for part in str(rules or "").split(">") if part.strip()]
        signatures = _normalize_signatures(values.get("字段签名"))
        entry: dict[str, Any] = {"id": _next_id(MODULE)}
        for field in LIST_FIELDS:
            if field == "来源规则":
                entry[field] = rule_list
            elif field == "契约版本":
                entry[field] = 1
            elif field == "字段数量":
                entry[field] = len(signatures)
            else:
                entry[field] = values.get(field)
        entry["字段签名"] = [
            {"字段名": sig["字段名"], "字段类型": sig["字段类型"],
             "来源连接": rule_list[0] if rule_list else ""}
            for sig in signatures
        ]
        entry["status"] = STATUS_ORDER[0]
        entry["契约状态"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return self._list_view(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"字段契约 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于字段契约可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["契约状态"] = target
        entry["pending"] = target in ("草稿", "待审核")
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        # 审核结论同步到当前版本的快照：已发布契约的字段才对查询模板生效。
        snapshots = _snapshots_of(str(entry.get("契约编号")))
        current = next((row for row in snapshots if int(row.get("契约版本", 0)) == int(entry.get("契约版本", 0))), None)
        if current is not None:
            current["status"] = target
            current["pending"] = entry["pending"]
        _write_audit(f"字段契约{action}", str(entry.get("契约编号")), f"契约版本 {entry.get('契约版本')} -> {target}")
        return self._list_view(entry), f"字段契约已{action}"

    # ---------------------------------------------------------------- 变更包导入与编译
    def import_package(self, entry_id: int, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """登记一个来源 schema 变更包：导入字段签名，等待按声明来源规则编译。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"字段契约 {entry_id} 不存在或已归档"
        source = str(payload.get("来源连接") or "").strip()
        signatures = _normalize_signatures(payload.get("字段签名"))
        if not source:
            return None, "缺少必填字段：来源连接"
        if not signatures:
            return None, "缺少有效的字段签名（需要 字段名 + 字段类型）"
        rows = store.rows(PACKAGE_TABLE)
        package = {
            "id": _next_id(PACKAGE_TABLE),
            "status": "待编译",
            "pending": True,
            "abnormal": False,
            "包编号": _next_code(PACKAGE_TABLE, "PKG"),
            "契约编号": entry.get("契约编号"),
            "来源连接": source,
            "到达时间": _now(),
            "字段签名": signatures,
        }
        rows.append(package)
        _write_audit("导入变更包", str(entry.get("契约编号")),
                     f"{package['包编号']} 来自 {source}，{len(signatures)} 个字段签名")
        return package, f"来源 schema 变更包 {package['包编号']} 已登记，等待编译"

    def compile_contract(self, entry_id: int, base_version: int) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        """按声明来源规则把待编译变更包合并进字段契约，生成新版本与字段契约快照。

        并发提交时只有基准版本与当前版本一致的那一个编译生效，其余收到版本冲突提示。
        """
        with _COMPILE_LOCK:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"字段契约 {entry_id} 不存在或已归档", None
            current_version = int(entry.get("契约版本", 0))
            if current_version != base_version:
                return None, f"契约当前版本为 {current_version}，与基准版本 {base_version} 不一致，本次编译未生效", None
            packages = _packages_of(str(entry.get("契约编号")), status="待编译")
            if not packages:
                return None, "没有待编译的来源 schema 变更包", None

            rules = [str(item) for item in entry.get("来源规则", [])]
            merged = {sig["字段名"]: dict(sig) for sig in entry.get("字段签名", [])}
            conflicts: list[dict[str, Any]] = []
            # 同一来源内部按到达时间顺序应用；跨来源的同名字段冲突由声明来源规则裁决。
            for package in sorted(packages, key=lambda row: str(row.get("到达时间", ""))):
                source = str(package.get("来源连接", ""))
                for sig in package.get("字段签名", []):
                    name, new_type = sig["字段名"], sig["字段类型"]
                    current = merged.get(name)
                    if current is None:
                        merged[name] = {"字段名": name, "字段类型": new_type, "来源连接": source}
                    elif current["字段类型"] != new_type:
                        incoming_wins = _priority(rules, source) <= _priority(rules, str(current.get("来源连接", "")))
                        if incoming_wins:
                            conflicts.append({"字段名": name, "旧类型": current["字段类型"], "新类型": new_type,
                                              "裁决来源": source, "裁决结果": "按声明来源规则采用新签名"})
                            current["字段类型"] = new_type
                            current["来源连接"] = source
                        else:
                            conflicts.append({"字段名": name, "旧类型": current["字段类型"], "新类型": new_type,
                                              "裁决来源": str(current.get("来源连接", "")),
                                              "裁决结果": "按声明来源规则保留现有签名"})

            entry["契约版本"] = current_version + 1
            entry["字段签名"] = [merged[name] for name in sorted(merged)]
            entry["字段数量"] = len(entry["字段签名"])
            entry["status"] = "待审核"
            entry["契约状态"] = "待审核"
            entry["pending"] = True

            snapshot = {
                "id": _next_id(SNAPSHOT_TABLE),
                "status": "待审核",
                "pending": True,
                "abnormal": False,
                "快照编号": _next_code(SNAPSHOT_TABLE, "SNAP"),
                "契约编号": entry.get("契约编号"),
                "契约版本": entry["契约版本"],
                # 字段签名与来源规则写入同一契约版本，一并冻结。
                "来源规则": list(rules),
                "字段签名": [dict(sig) for sig in entry["字段签名"]],
                "关联来源记录": [pkg.get("包编号") for pkg in packages],
                "业务输入": {"类型": "变更包编译", "变更包": [pkg.get("包编号") for pkg in packages],
                             "基准版本": base_version},
                "生成时间": _now(),
                "文件回执": "",
            }
            store.rows(SNAPSHOT_TABLE).append(snapshot)
            for package in packages:
                package["status"] = "已编译"
                package["pending"] = False

            _write_audit("编译字段契约", str(entry.get("契约编号")),
                         f"版本 {base_version} -> {entry['契约版本']}，合并 {len(packages)} 个变更包，"
                         f"冲突 {len(conflicts)} 处")
            result = self._list_view(entry)
            result["冲突裁决"] = conflicts
            return result, (f"字段契约已编译为版本 {entry['契约版本']}，"
                            f"快照 {snapshot['快照编号']} 待审核，冲突裁决 {len(conflicts)} 处"), snapshot

    # ---------------------------------------------------------------- 快照查询与逐项核对
    def list_packages(self, entry_id: int) -> list[dict[str, Any]]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return []
        return _packages_of(str(entry.get("契约编号")))

    def list_snapshots(self, entry_id: int) -> list[dict[str, Any]]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return []
        return _snapshots_of(str(entry.get("契约编号")))

    def get_snapshot(self, entry_id: int, snapshot_id: int) -> tuple[dict[str, Any] | None, str]:
        """查询字段契约快照，并回到快照的关联来源记录（编译它的变更包）。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"字段契约 {entry_id} 不存在或已归档"
        snapshot = next((row for row in _snapshots_of(str(entry.get("契约编号")))
                         if int(row.get("id", 0)) == snapshot_id), None)
        if snapshot is None:
            return None, f"字段契约快照 {snapshot_id} 不属于契约 {entry.get('契约编号')}"
        packages = {row.get("包编号"): row for row in store.rows(PACKAGE_TABLE)}
        view = dict(snapshot)
        view["关联来源记录明细"] = [packages[code] for code in snapshot.get("关联来源记录", []) if code in packages]
        return view, "ok"

    def diff_snapshot(self, entry_id: int, snapshot_id: int) -> tuple[dict[str, Any] | None, str]:
        """读取字段契约快照，与主版本逐项核对字段签名差异。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"字段契约 {entry_id} 不存在或已归档"
        snapshot = next((row for row in _snapshots_of(str(entry.get("契约编号")))
                         if int(row.get("id", 0)) == snapshot_id), None)
        if snapshot is None:
            return None, f"字段契约快照 {snapshot_id} 不属于契约 {entry.get('契约编号')}"
        current = {sig["字段名"]: sig for sig in entry.get("字段签名", [])}
        frozen = {sig["字段名"]: sig for sig in snapshot.get("字段签名", [])}
        items: list[dict[str, Any]] = []
        for name in sorted(set(current) | set(frozen)):
            in_current, in_frozen = name in current, name in frozen
            if in_current and in_frozen:
                same_type = current[name]["字段类型"] == frozen[name]["字段类型"]
                same_source = current[name].get("来源连接") == frozen[name].get("来源连接")
                diff = "一致" if (same_type and same_source) else ("类型变更" if not same_type else "来源变更")
            elif in_current:
                diff = "快照后新增"
            else:
                diff = "快照后移除"
            items.append({
                "字段名": name,
                "快照类型": frozen[name]["字段类型"] if in_frozen else "",
                "当前类型": current[name]["字段类型"] if in_current else "",
                "快照来源": frozen[name].get("来源连接", "") if in_frozen else "",
                "当前来源": current[name].get("来源连接", "") if in_current else "",
                "差异": diff,
            })
        changed = sum(1 for item in items if item["差异"] != "一致")
        return {
            "契约编号": entry.get("契约编号"),
            "契约版本": entry.get("契约版本"),
            "快照编号": snapshot.get("快照编号"),
            "快照版本": snapshot.get("契约版本"),
            "差异字段数": changed,
            "items": items,
        }, "ok"

    # ---------------------------------------------------------------- 跨模块读取
    def affected_templates(self, entry_id: int) -> tuple[list[dict[str, Any]], str]:
        """跨模块读取查询模板：找出引用了未发布或已变化字段、需要停用的模板。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return [], f"字段契约 {entry_id} 不存在或已归档"
        contract_code = str(entry.get("契约编号"))
        snapshots = _snapshots_of(contract_code)
        known_fields = {sig["字段名"] for row in snapshots for sig in row.get("字段签名", [])}
        known_fields |= {sig["字段名"] for sig in entry.get("字段签名", [])}
        published = next((row for row in reversed(snapshots) if row.get("status") == "已发布"), None)
        published_fields = {sig["字段名"]: sig["字段类型"] for sig in (published or {}).get("字段签名", [])}
        results: list[dict[str, Any]] = []
        for template in store.rows("query_builder"):
            statement = str(template.get("查询语句", ""))
            referenced = sorted(name for name in known_fields if name and name in statement)
            problems = []
            for name in referenced:
                if published is None:
                    problems.append({"字段名": name, "问题": "契约尚未发布，查询模板不得引用"})
                elif name not in published_fields:
                    problems.append({"字段名": name, "问题": "字段不在已发布契约中，需要停用"})
                elif published_fields[name] != next(
                        (sig["字段类型"] for sig in entry.get("字段签名", []) if sig["字段名"] == name),
                        published_fields[name]):
                    problems.append({"字段名": name, "问题": "字段类型已变化但未发布，需要停用"})
            if problems:
                results.append({
                    "模板编号": template.get("模板编号"),
                    "模板名称": template.get("模板名称"),
                    "模板状态": template.get("status"),
                    "问题字段": problems,
                })
        return results, "ok"

    # ---------------------------------------------------------------- 数据集模块联动
    def import_file_for_dataset(
        self, dataset_id: int, payload: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        """数据集导入文件：登记变更包并立即按声明来源规则编译，生成字段契约快照。"""
        dataset = store.find("dataset", dataset_id)
        if dataset is None:
            return None, f"分析数据集 {dataset_id} 不存在或已归档", None
        file_name = str(payload.get("文件名") or "").strip()
        source = str(payload.get("来源连接") or "").strip()
        signatures = _normalize_signatures(payload.get("字段签名"))
        if not file_name:
            return None, "缺少必填字段：文件名", None
        if not source:
            return None, "缺少必填字段：来源连接", None
        if not signatures:
            return None, "缺少有效的字段签名（需要 字段名 + 字段类型）", None

        with _COMPILE_LOCK:
            contract = _find_contract_by_dataset(str(dataset.get("数据集编号")))
            if contract is None:
                created, errors = self.create_entry({
                    "契约编号": _next_code(MODULE, "CONT"),
                    "数据集编号": dataset.get("数据集编号"),
                    "数据集名称": dataset.get("数据集名称") or dataset.get("数据集编号"),
                    "来源规则": [source],
                    "负责人": dataset.get("负责人") or "接入值班",
                })
                if created is None:
                    return None, "；".join(errors), None
                contract = store.find(MODULE, int(created["id"]))
            rules = [str(item) for item in contract.get("来源规则", [])]
            if source not in rules:
                rules.append(source)
                contract["来源规则"] = rules

            rows = store.rows(PACKAGE_TABLE)
            package = {
                "id": _next_id(PACKAGE_TABLE),
                "status": "待编译",
                "pending": True,
                "abnormal": False,
                "包编号": _next_code(PACKAGE_TABLE, "PKG"),
                "契约编号": contract.get("契约编号"),
                "来源连接": source,
                "到达时间": _now(),
                "字段签名": signatures,
            }
            rows.append(package)

            base_version = int(contract.get("契约版本", 0))
            merged = {sig["字段名"]: dict(sig) for sig in contract.get("字段签名", [])}
            for sig in signatures:
                name, new_type = sig["字段名"], sig["字段类型"]
                current = merged.get(name)
                if current is None:
                    merged[name] = {"字段名": name, "字段类型": new_type, "来源连接": source}
                elif current["字段类型"] != new_type and _priority(rules, source) <= _priority(
                        rules, str(current.get("来源连接", ""))):
                    current["字段类型"] = new_type
                    current["来源连接"] = source
            contract["契约版本"] = base_version + 1
            contract["字段签名"] = [merged[name] for name in sorted(merged)]
            contract["字段数量"] = len(contract["字段签名"])
            contract["status"] = "待审核"
            contract["契约状态"] = "待审核"
            contract["pending"] = True

            snapshot = {
                "id": _next_id(SNAPSHOT_TABLE),
                "status": "待审核",
                "pending": True,
                "abnormal": False,
                "快照编号": _next_code(SNAPSHOT_TABLE, "SNAP"),
                "契约编号": contract.get("契约编号"),
                "契约版本": contract["契约版本"],
                "来源规则": list(rules),
                "字段签名": [dict(sig) for sig in contract["字段签名"]],
                "关联来源记录": [package["包编号"]],
                "业务输入": {"类型": "文件导入", "文件名": file_name, "基准版本": base_version},
                "生成时间": _now(),
                "文件回执": "",
            }
            store.rows(SNAPSHOT_TABLE).append(snapshot)
            package["status"] = "已编译"
            package["pending"] = False

        _write_audit("导入文件生成快照", str(contract.get("契约编号")),
                     f"文件 {file_name} -> 快照 {snapshot['快照编号']}（版本 {contract['契约版本']}）")
        return (self._list_view(contract),
                f"导入文件 {file_name} 已生成字段契约快照 {snapshot['快照编号']}（契约版本 {contract['契约版本']}，待审核）",
                snapshot)

    def record_export_receipt(self, dataset_rows: list[dict[str, Any]], file_name: str) -> list[str]:
        """数据集导出/下载时，在相关契约的最新字段契约快照上保留文件回执。"""
        receipts: list[str] = []
        for dataset in dataset_rows:
            contract = _find_contract_by_dataset(str(dataset.get("数据集编号", "")))
            if contract is None:
                continue
            snapshots = _snapshots_of(str(contract.get("契约编号")))
            if not snapshots:
                continue
            receipt = f"RCPT-{contract.get('契约编号')}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            snapshots[-1]["文件回执"] = f"{receipt} 文件 {file_name} 于 {_now()} 导出"
            receipts.append(receipt)
        return receipts
