"""审计只读面。"""
from __future__ import annotations

from fastapi import APIRouter

from auth import AuthHeader, require_user
from db import connect

router = APIRouter(tags=["audit"])


@router.get("/api/audit")
def list_audit(authorization: AuthHeader = None, limit: int = 100):
    require_user(authorization)
    limit = max(1, min(int(limit), 500))
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_trail ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.get("/api/audit/actions")
def list_actions(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT action, COUNT(*) c FROM audit_trail
               GROUP BY action ORDER BY c DESC"""
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.get("/api/audit/forfeit-chain")
def forfeit_chain(authorization: AuthHeader = None, limit: int = 200):
    """罚没账实同源链：意图 -> 复核 -> 正式行 -> 笼面扣减。"""
    require_user(authorization)
    limit = max(1, min(int(limit), 500))
    with connect() as conn:
        intents = conn.execute(
            """SELECT i.id, i.intent_ref, i.cage_id, c.cage_code, i.amount,
                      i.cage_generation, i.status, i.requested_by, i.reviewed_by,
                      i.review_decision, i.review_note, i.forfeit_id,
                      i.created_at, i.reviewed_at,
                      f.forfeit_ref, f.status AS forfeit_status
               FROM forfeit_intents i
               JOIN cages c ON c.id=i.cage_id
               LEFT JOIN forfeits f ON f.id=i.forfeit_id
               ORDER BY i.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        drift = conn.execute(
            """SELECT COUNT(*) c FROM audit_trail WHERE action='罚没偏航'"""
        ).fetchone()["c"]
        items = []
        for r in intents:
            row = dict(r)
            if row["status"] == "已通过":
                consistent = (
                    row["forfeit_status"] == "已罚没"
                    and row["reviewed_by"] is not None
                    and row["forfeit_id"] is not None
                )
            elif row["status"] == "已驳回":
                consistent = row["forfeit_id"] is None
            else:
                consistent = row["forfeit_id"] is None
            row["consistent"] = bool(consistent)
            items.append(row)
        bad = [i for i in items if not i["consistent"]]
        return {
            "items": items,
            "summary": {
                "total": len(items),
                "consistent": len(items) - len(bad),
                "inconsistent": len(bad),
                "fate_drift": int(drift),
                "fate_path": "乙",
            },
        }
