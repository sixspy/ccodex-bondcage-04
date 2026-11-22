"""对账互证：笼面可用 + 已拨付 + 已罚没 须等于笼面金额（同代口径）。

同源口径：只有复核通过的正式罚没行才进 `forfeited`；待复核意图不计入任何
一侧（既不算已罚没，也不算可用），因此复核未办结时笼面必须保持平衡。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user
from db import connect

router = APIRouter(tags=["reconcile"])


def _path_audit(conn) -> dict:
    """同源核验：正式罚没行必须都来自复核通过的意图，不得混入直罚行。"""
    total = conn.execute("SELECT COUNT(*) c FROM forfeits").fetchone()["c"]
    unreviewed = conn.execute(
        """SELECT COUNT(*) c FROM forfeits
           WHERE intent_id IS NULL OR reviewed_by IS NULL"""
    ).fetchone()["c"]
    pending = conn.execute(
        "SELECT COUNT(*) c FROM forfeit_intents WHERE status='待复核'"
    ).fetchone()["c"]
    return {
        "fate_path": "乙",
        "formal_rows": int(total),
        "unreviewed_formal_rows": int(unreviewed),
        "pending_intents": int(pending),
        "same_source": int(unreviewed) == 0,
    }


def _reconcile_cage(conn, cage) -> dict:
    cage_id = cage["id"]
    gen = int(cage["generation"])
    amount = float(cage["amount"])
    available = float(cage["available"])
    released = conn.execute(
        """SELECT COALESCE(SUM(amount),0) s FROM releases
           WHERE cage_id=? AND status='已拨付' AND cage_generation=?""",
        (cage_id, gen),
    ).fetchone()["s"]
    forfeited = conn.execute(
        """SELECT COALESCE(SUM(amount),0) s FROM forfeits
           WHERE cage_id=? AND status='已罚没' AND cage_generation=?""",
        (cage_id, gen),
    ).fetchone()["s"]
    pending_rows = conn.execute(
        """SELECT COALESCE(SUM(amount),0) s, COUNT(*) c FROM forfeit_intents
           WHERE cage_id=? AND cage_generation=? AND status='待复核'""",
        (cage_id, gen),
    ).fetchone()
    pending = float(pending_rows["s"] or 0)
    accounted = round(float(available) + float(released) + float(forfeited), 6)
    ok = abs(accounted - amount) < 1e-6
    # 账户侧：该当事人冻结额应覆盖所有仍冻结/核验中的笼可用
    party = cage["party_key"]
    acct = conn.execute(
        "SELECT balance, frozen FROM bond_accounts WHERE party_key=?",
        (party,),
    ).fetchone()
    live_frozen = conn.execute(
        """SELECT COALESCE(SUM(available),0) s FROM cages
           WHERE party_key=? AND status IN ('已冻结','核验通过','核验违约','部分解笼','部分罚没')""",
        (party,),
    ).fetchone()["s"]
    return {
        "cage_id": cage_id,
        "cage_code": cage["cage_code"],
        "party_key": party,
        "contract_no": cage["contract_no"],
        "generation": gen,
        "status": cage["status"],
        "amount": amount,
        "available": available,
        "released": float(released),
        "forfeited": float(forfeited),
        "pending_intent_amount": pending,
        "pending_intent_count": int(pending_rows["c"] or 0),
        "accounted": accounted,
        "balanced": ok,
        "gap": round(amount - accounted, 6),
        "account_balance": float(acct["balance"]) if acct else None,
        "account_frozen": float(acct["frozen"]) if acct else None,
        "live_cage_available": float(live_frozen),
        "proof": {
            "formula": "available + released + forfeited == amount（同笼代）",
            "left": accounted,
            "right": amount,
            "pending_not_counted": pending,
        },
    }


@router.get("/api/reconcile")
def reconcile_all(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        cages = conn.execute("SELECT * FROM cages ORDER BY id").fetchall()
        items = [_reconcile_cage(conn, c) for c in cages]
        bad = [i for i in items if not i["balanced"]]
        return {
            "items": items,
            "summary": {
                "total": len(items),
                "balanced": len(items) - len(bad),
                "unbalanced": len(bad),
            },
            "path_audit": _path_audit(conn),
        }


@router.get("/api/reconcile/{cage_code}")
def reconcile_one(cage_code: str, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        cage = conn.execute(
            "SELECT * FROM cages WHERE cage_code=?", (cage_code,)
        ).fetchone()
        if not cage:
            raise HTTPException(404, "笼不存在")
        return _reconcile_cage(conn, cage)


@router.get("/api/proof/{party_key}")
def party_proof(party_key: str, authorization: AuthHeader = None):
    """当事人级互证：账户冻结 vs 活笼可用合计。"""
    require_user(authorization)
    with connect() as conn:
        acct = conn.execute(
            "SELECT * FROM bond_accounts WHERE party_key=?", (party_key,)
        ).fetchone()
        if not acct:
            raise HTTPException(404, "保证金户不存在")
        live = conn.execute(
            """SELECT COALESCE(SUM(available),0) s FROM cages
               WHERE party_key=? AND status IN
               ('已冻结','核验通过','核验违约','部分解笼','部分罚没')""",
            (party_key,),
        ).fetchone()["s"]
        released = conn.execute(
            """SELECT COALESCE(SUM(r.amount),0) s FROM releases r
               JOIN cages c ON c.id=r.cage_id
               WHERE c.party_key=? AND r.status='已拨付'""",
            (party_key,),
        ).fetchone()["s"]
        forfeited = conn.execute(
            """SELECT COALESCE(SUM(f.amount),0) s FROM forfeits f
               JOIN cages c ON c.id=f.cage_id
               WHERE c.party_key=? AND f.status='已罚没'""",
            (party_key,),
        ).fetchone()["s"]
        frozen = float(acct["frozen"])
        live_f = float(live)
        pending = conn.execute(
            """SELECT COALESCE(SUM(i.amount),0) s FROM forfeit_intents i
               JOIN cages c ON c.id=i.cage_id
               WHERE c.party_key=? AND i.status='待复核'""",
            (party_key,),
        ).fetchone()["s"]
        return {
            "party_key": party_key,
            "account": dict(acct),
            "live_cage_available": live_f,
            "pending_intent_amount": float(pending or 0),
            "released_total": float(released),
            "forfeited_total": float(forfeited),
            "frozen_matches_live": abs(frozen - live_f) < 1e-6,
            "proof": {
                "formula": "account.frozen == sum(live cage available)，待复核意图不改冻结",
                "left": frozen,
                "right": live_f,
                "pending_not_counted": float(pending or 0),
            },
        }
