#!/usr/bin/env python3
"""罚没直罚事故夹具（不参与 pytest 收集）。

用种子违约态笼打罚没写口，检查「不可撤回直罚」事故是否出现：

- 事故态（修复前）：写口 HTTP 200、笼可用/账户冻结立刻减少、正式罚没册出现行，
  且没有 /api/forfeit-intents 复核入口 → `accident: true`。
- 修复后（乙路径）：写口只建待复核意图，笼可用/账户冻结零改、正式册零行，
  复核入口存在 → `accident: false`。

跑法：
    python tests/accident_probe.py                    # 验当前工作树（应 accident=false）
    python tests/accident_probe.py --tree /tmp/head   # 验历史树（应 accident=true）
    python tests/accident_probe.py --expect-accident  # 断言事故存在（红）
    python tests/accident_probe.py --expect-fixed     # 断言事故已消除（绿）

始终使用临时 SQLite 文件库，不动 data/app.db。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CAGE = "CG-P-WEST-VIOL-001"


def _load(tree: Path):
    sys.path.insert(0, str(tree / "backend"))
    import db  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from app import app  # noqa: PLC0415

    return db, TestClient, app


def _auth(c, user: str = "admin") -> dict:
    tok = c.post(
        "/api/login", json={"username": user, "password": "123456"}
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}


def _snapshot(c, h: dict, cage: dict) -> dict:
    detail = c.get(f"/api/cages/{cage['id']}", headers=h).json()
    account = c.get(f"/api/bonds/{cage['party_key']}", headers=h).json()["account"]
    formal = [
        f
        for f in c.get("/api/forfeits", headers=h).json()["items"]
        if f["cage_id"] == cage["id"]
    ]
    return {
        "cage_status": detail["cage"]["status"],
        "cage_available": detail["cage"]["available"],
        "account_balance": account["balance"],
        "account_frozen": account["frozen"],
        "formal_forfeit_rows": len(formal),
        "formal_forfeit_amount": sum(float(f["amount"]) for f in formal),
    }


def _ensure_violation_cage(c, h: dict, cage_code: str = DEFAULT_CAGE) -> dict:
    """取违约态笼；种子缺位（历史树）时经 API 现造一只同构违约态笼。"""
    cages = c.get("/api/cages", headers=h).json()["items"]
    found = next((x for x in cages if x["cage_code"] == cage_code), None)
    if found is None:
        party = "P-ACCIDENT"
        r = c.post(
            "/api/bonds",
            headers=h,
            json={
                "party_key": party,
                "party_name": "事故夹具当事方",
                "initial_deposit": 50000,
            },
        )
        assert r.status_code in (200, 409), r.text
        r = c.post(
            "/api/cages/freeze",
            headers=h,
            json={
                "party_key": party,
                "contract_no": "HT-ACCIDENT-1",
                "amount": 30000,
                "beneficiary": "事故夹具受益人",
                "cage_code": cage_code,
            },
        )
        assert r.status_code == 200, r.text
        found = r.json()
    if found["status"] == "已冻结":
        r = c.post(
            "/api/attestations",
            headers=h,
            json={
                "cage_id": found["id"],
                "outcome": "违约",
                "evidence": "种子违约态（事故夹具）",
            },
        )
        assert r.status_code == 200, r.text
        found = c.get(f"/api/cages/{found['id']}", headers=h).json()["cage"]
    assert found["status"] == "核验违约", found
    return found


def probe(tree: Path) -> dict:
    db, TestClient, app = _load(tree)
    with tempfile.TemporaryDirectory() as td:
        db.DB_PATH = Path(td) / "accident.db"
        db.init_db()
        c = TestClient(app)
        h = _auth(c, "admin")

        cage = _ensure_violation_cage(c, h)

        before = _snapshot(c, h, cage)
        direct = c.post(
            "/api/forfeits",
            headers=h,
            json={"cage_id": cage["id"], "reason": "事故夹具：不可撤回直罚"},
        )
        after = _snapshot(c, h, cage)
        intent_list = c.get("/api/forfeit-intents", headers=h)
        body = (
            direct.json()
            if direct.headers.get("content-type", "").startswith("application/json")
            else None
        )
        money_left = (
            after["cage_available"] < before["cage_available"]
            or after["account_frozen"] < before["account_frozen"]
        )
        return {
            "tree": str(tree),
            "cage": cage["cage_code"],
            "direct_forfeit_http": direct.status_code,
            "direct_forfeit_body_status": (body or {}).get("status"),
            "before": before,
            "after": after,
            "money_left_cage": money_left,
            "intent_list_http": intent_list.status_code,
            "accident": bool(
                direct.status_code == 200
                and money_left
                and after["formal_forfeit_rows"] == 1
                and intent_list.status_code == 404
            ),
        }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default=str(ROOT))
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--expect-accident", action="store_true")
    g.add_argument("--expect-fixed", action="store_true")
    args = ap.parse_args()

    report = probe(Path(args.tree).resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.expect_accident:
        return 0 if report["accident"] else 1
    if args.expect_fixed:
        return 0 if not report["accident"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
