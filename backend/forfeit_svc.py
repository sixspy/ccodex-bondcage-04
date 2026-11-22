"""罚没（乙路径）：核验违约后只建待复核意图，复核通过才入册扣款。

命运口径锁死在 `forfeit_fate`：复核办结前笼可用与账户冻结零改；复核通过
才在同一事务里写正式罚没册并扣笼；复核驳回则意图作废、正式零增。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit
from forfeit_fate import guard_fate
from locks import release, try_acquire
from models import ForfeitIn, ForfeitReviewIn

router = APIRouter(tags=["forfeit"])

PENDING = "待复核"
APPROVED = "已通过"
REJECTED = "已驳回"
DECISION_PASS = "通过"
DECISION_REJECT = "驳回"

_PASS_WORDS = {"通过", "同意", "批准", "approve", "approved", "pass"}
_REJECT_WORDS = {"驳回", "否决", "拒绝", "reject", "rejected", "deny"}


def _acquire(conn, lock_key: str, holder: str) -> None:
    """抢笼锁；后到只落争用痕并拒写业务（争用记录须先提交才能被看见）。"""
    if try_acquire(conn, lock_key, holder):
        return
    conn.commit()
    raise HTTPException(409, "争用：后到只记争用，不写业务")


def _normalize_decision(raw: str) -> str:
    value = (raw or "").strip().lower()
    if value in _PASS_WORDS:
        return DECISION_PASS
    if value in _REJECT_WORDS:
        return DECISION_REJECT
    raise HTTPException(400, "复核决定须为通过或驳回")


def _cage_or_404(conn, cage_id: int):
    cage = conn.execute("SELECT * FROM cages WHERE id=?", (cage_id,)).fetchone()
    if not cage:
        raise HTTPException(404, "笼不存在")
    return cage


def _violation_attestation(conn, cage_id: int, gen: int):
    att = conn.execute(
        """SELECT * FROM attestations
           WHERE cage_id=? AND attest_gen=? AND outcome='违约'
           ORDER BY id DESC LIMIT 1""",
        (cage_id, gen),
    ).fetchone()
    if not att:
        raise HTTPException(409, "缺少与笼代对齐的违约核验")
    return att


def _amount_for(conn, cage, requested: float | None) -> float:
    avail = float(cage["available"])
    amount = float(requested) if requested is not None else avail
    if amount <= 0:
        raise HTTPException(400, "罚没金额须大于 0")
    if amount > avail + 1e-9:
        raise HTTPException(409, "罚没金额超过笼可用额")
    return amount


def _intent_out(conn, intent_id: int) -> dict:
    row = conn.execute(
        """SELECT i.*, c.cage_code, c.contract_no
           FROM forfeit_intents i JOIN cages c ON c.id=i.cage_id
           WHERE i.id=?""",
        (intent_id,),
    ).fetchone()
    return dict(row) if row else {}


@router.get("/api/forfeit-fate")
def get_fate(authorization: AuthHeader = None):
    """展示当日锁死的罚没复核命运说明。"""
    require_user(authorization)
    with connect() as conn:
        row = conn.execute("SELECT * FROM forfeit_fate_note WHERE id=1").fetchone()
        if not row:
            raise HTTPException(503, "命运说明未锁死，禁止罚没写口")
        import json

        return {
            "path": row["path"],
            "locked_on": row["locked_on"],
            "locked": True,
            "note": json.loads(row["note"]),
        }


@router.get("/api/forfeits")
def list_forfeits(authorization: AuthHeader = None):
    """正式罚没册：只列复核通过且已扣笼的正式行。"""
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT f.*, c.cage_code, c.contract_no
               FROM forfeits f
               JOIN cages c ON c.id=f.cage_id
               WHERE f.status='已罚没'
               ORDER BY f.id DESC LIMIT 200"""
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.get("/api/forfeit-intents")
def list_intents(authorization: AuthHeader = None):
    """待复核/已办结意图：不影响笼面与账户。"""
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT i.*, c.cage_code, c.contract_no
               FROM forfeit_intents i
               JOIN cages c ON c.id=i.cage_id
               ORDER BY i.id DESC LIMIT 200"""
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.post("/api/forfeits")
def do_forfeit(body: ForfeitIn, authorization: AuthHeader = None):
    """罚没写口（乙路径）：只建待复核意图，笼面与账户冻结零改。"""
    user = require_writer(authorization)
    lock_key = f"cage:{body.cage_id}"
    with connect() as conn:
        guard_fate(
            conn,
            user,
            "forfeit_create_intent",
            f"cage:{body.cage_id}",
            body.fate_path,
        )
        _acquire(conn, lock_key, user)
        try:
            cage = _cage_or_404(conn, body.cage_id)
            if cage["status"] != "核验违约":
                raise HTTPException(409, "仅核验违约的笼可建罚没意图")
            gen = int(cage["generation"])
            _violation_attestation(conn, body.cage_id, gen)
            amount = _amount_for(conn, cage, body.amount)
            pending = conn.execute(
                """SELECT id FROM forfeit_intents
                   WHERE cage_id=? AND cage_generation=? AND status=?""",
                (body.cage_id, gen, PENDING),
            ).fetchone()
            if pending:
                raise HTTPException(409, "本笼代已有待复核罚没意图，禁止重复建单")
            seq = conn.execute(
                "SELECT COUNT(*) c FROM forfeit_intents WHERE cage_id=?",
                (body.cage_id,),
            ).fetchone()["c"]
            now = now_iso()
            intent_ref = f"FORI-{cage['cage_code']}-g{gen}-{seq + 1:02d}"
            cur = conn.execute(
                """INSERT INTO forfeit_intents(
                     cage_id,amount,reason,cage_generation,status,intent_ref,
                     requested_by,created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    body.cage_id,
                    amount,
                    body.reason or "履约违约",
                    gen,
                    PENDING,
                    intent_ref,
                    user,
                    now,
                ),
            )
            write_audit(
                conn,
                user,
                "forfeit_intent",
                cage["cage_code"],
                {
                    "intent_id": cur.lastrowid,
                    "intent_ref": intent_ref,
                    "amount": amount,
                    "reason": body.reason,
                    "cage_generation": gen,
                    "fund_fate": "待复核：笼面与账户冻结零改",
                    "fate_path": "乙",
                },
            )
            conn.commit()
            return _intent_out(conn, cur.lastrowid)
        except HTTPException:
            conn.rollback()
            raise
        finally:
            release(conn, lock_key, user)
            conn.commit()


@router.post("/api/forfeit-intents/{intent_id}/review")
def review_intent(
    intent_id: int, body: ForfeitReviewIn, authorization: AuthHeader = None
):
    """复核办结：通过才入册扣款；驳回只作废意图，正式零增。"""
    user = require_writer(authorization)
    with connect() as conn:
        guard_fate(
            conn,
            user,
            "forfeit_review",
            f"intent:{intent_id}",
            body.fate_path,
        )
        intent = conn.execute(
            "SELECT * FROM forfeit_intents WHERE id=?", (intent_id,)
        ).fetchone()
        if not intent:
            raise HTTPException(404, "罚没意图不存在")
        decision = _normalize_decision(body.decision)
        lock_key = f"cage:{intent['cage_id']}"
        _acquire(conn, lock_key, user)
        try:
            intent = conn.execute(
                "SELECT * FROM forfeit_intents WHERE id=?", (intent_id,)
            ).fetchone()
            if intent["status"] != PENDING:
                raise HTTPException(409, "该罚没意图已办结，禁止重复复核")
            cage = _cage_or_404(conn, intent["cage_id"])
            gen = int(cage["generation"])
            if gen != int(intent["cage_generation"]):
                raise HTTPException(409, "笼代已变更，意图失效")
            now = now_iso()
            note = body.note or ""

            if decision == DECISION_REJECT:
                conn.execute(
                    """UPDATE forfeit_intents
                       SET status=?, reviewed_by=?, review_decision=?, review_note=?,
                           reviewed_at=?
                       WHERE id=?""",
                    (REJECTED, user, DECISION_REJECT, note, now, intent_id),
                )
                write_audit(
                    conn,
                    user,
                    "forfeit_review_reject",
                    intent["intent_ref"],
                    {
                        "intent_id": intent_id,
                        "amount": float(intent["amount"]),
                        "review_note": note,
                        "fund_fate": "驳回：意图作废，笼面/账户/正式册零改",
                    },
                )
                conn.commit()
                return {"intent": _intent_out(conn, intent_id), "forfeit": None}

            # 复核通过：锁内复核条件后同一事务入册扣款。
            if cage["status"] not in ("核验违约",):
                raise HTTPException(409, "笼状态不再允许罚没")
            _violation_attestation(conn, intent["cage_id"], gen)
            amount = _amount_for(conn, cage, float(intent["amount"]))
            avail = float(cage["available"])
            ref = f"FOR-{cage['cage_code']}-g{gen}"
            cur = conn.execute(
                """INSERT INTO forfeits(
                     cage_id,amount,reason,cage_generation,status,forfeit_ref,
                     created_at,intent_id,reviewed_by,review_note)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    intent["cage_id"],
                    amount,
                    intent["reason"] or "履约违约",
                    gen,
                    "已罚没",
                    ref,
                    now,
                    intent_id,
                    user,
                    note,
                ),
            )
            new_avail = round(avail - amount, 6)
            new_status = "已罚没" if new_avail <= 1e-9 else "部分罚没"
            conn.execute(
                "UPDATE cages SET available=?, status=? WHERE id=?",
                (max(new_avail, 0.0), new_status, intent["cage_id"]),
            )
            conn.execute(
                """UPDATE bond_accounts
                   SET frozen = frozen - ?, balance = balance - ?
                   WHERE party_key=?""",
                (amount, amount, cage["party_key"]),
            )
            conn.execute(
                """UPDATE forfeit_intents
                   SET status=?, reviewed_by=?, review_decision=?, review_note=?,
                       reviewed_at=?, forfeit_id=?
                   WHERE id=?""",
                (
                    APPROVED,
                    user,
                    DECISION_PASS,
                    note,
                    now,
                    cur.lastrowid,
                    intent_id,
                ),
            )
            write_audit(
                conn,
                user,
                "cage_forfeit",
                cage["cage_code"],
                {
                    "forfeit_id": cur.lastrowid,
                    "intent_id": intent_id,
                    "intent_ref": intent["intent_ref"],
                    "amount": amount,
                    "reason": intent["reason"],
                    "cage_generation": gen,
                    "forfeit_ref": ref,
                    "reviewed_by": user,
                    "review_note": note,
                    "fate_path": "乙",
                },
            )
            conn.commit()
            forfeit = conn.execute(
                "SELECT * FROM forfeits WHERE id=?", (cur.lastrowid,)
            ).fetchone()
            return {
                "intent": _intent_out(conn, intent_id),
                "forfeit": dict(forfeit),
            }
        except HTTPException:
            conn.rollback()
            raise
        finally:
            release(conn, lock_key, user)
            conn.commit()
