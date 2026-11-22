"""罚没：乙路径——复核前笼面零扣（docs/罚没复核命运说明.md，2026-11-22 锁死）。

- 罚没提交只落「待复核意图」：笼面 available、笼状态、账户冻结/余额零改，
  正式罚没册零写；
- 复核通过：同一事务原子写正式罚没册 + 扣笼 + 减账户冻结/余额 + 意图晋升，
  失败整笔同滚；
- 复核驳回：意图作废，正式册与笼面始终零改；
- 复核岗与提交人分人，复核不得改金额；
- 任何直进罚没册的写企图记「罚没偏航」并 409 拒写；
- 同笼抢锁，后到只留争用痕；同笼同时刻至多一条待复核意图。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import FATE_SPEC, connect, now_iso, write_audit
from locks import release, require_lock
from models import ForfeitIn, ReviewIn

router = APIRouter(tags=["forfeit"])

REVIEW_DECISIONS = {"通过", "驳回"}
FORFEITABLE_STATUS = {"核验违约", "部分罚没"}


def _deviation(conn, user: str, target: str, detail: str) -> HTTPException:
    """偏离命运说明：记「罚没偏航」（独立提交，审计必留），业务零写后 409。"""
    write_audit(
        conn,
        user,
        "罚没偏航",
        target,
        {"detail": detail, "fate": f"{FATE_SPEC['path']}·{FATE_SPEC['name']}"},
    )
    conn.commit()
    return HTTPException(
        409,
        f"偏离罚没复核命运说明（{FATE_SPEC['path']}·{FATE_SPEC['name']}）："
        f"{detail}，已记罚没偏航并拒写",
    )


def _cage_code_or(conn, cage_id: int) -> str:
    row = conn.execute(
        "SELECT cage_code FROM cages WHERE id=?", (cage_id,)
    ).fetchone()
    return row["cage_code"] if row else f"cage:{cage_id}"


@router.get("/api/forfeits")
def list_forfeits(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT f.*, c.cage_code, c.contract_no
               FROM forfeits f
               JOIN cages c ON c.id=f.cage_id
               ORDER BY f.id DESC LIMIT 200"""
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.get("/api/forfeit-intents")
def list_forfeit_intents(authorization: AuthHeader = None, status: str | None = None):
    require_user(authorization)
    with connect() as conn:
        sql = """SELECT i.*, c.cage_code, c.contract_no
                 FROM forfeit_intents i
                 JOIN cages c ON c.id=i.cage_id"""
        params: tuple = ()
        if status:
            sql += " WHERE i.status=?"
            params = (status,)
        sql += " ORDER BY i.id DESC LIMIT 200"
        rows = conn.execute(sql, params).fetchall()
        return {"items": [dict(r) for r in rows], "fate": FATE_SPEC}


@router.post("/api/forfeits")
def submit_forfeit_intent(body: ForfeitIn, authorization: AuthHeader = None):
    """乙路径：罚没提交只落待复核意图，笼面与账户零改。"""
    user = require_writer(authorization)
    with connect() as conn:
        if body.mode != "待复核意图":
            raise _deviation(
                conn,
                user,
                _cage_code_or(conn, body.cage_id),
                f"罚没提交模式「{body.mode}」企图绕过复核直进罚没册",
            )
        lock_key = f"cage:{body.cage_id}"
        try:
            require_lock(conn, lock_key, user)
            cage = conn.execute(
                "SELECT * FROM cages WHERE id=?", (body.cage_id,)
            ).fetchone()
            if not cage:
                raise HTTPException(404, "笼不存在")
            if cage["status"] not in FORFEITABLE_STATUS:
                raise HTTPException(409, "仅核验违约的笼可提交罚没意图")
            att = conn.execute(
                """SELECT * FROM attestations
                   WHERE cage_id=? AND attest_gen=? AND outcome='违约'
                   ORDER BY id DESC LIMIT 1""",
                (body.cage_id, int(cage["generation"])),
            ).fetchone()
            if not att:
                raise HTTPException(409, "缺少与笼代对齐的违约核验")
            avail = float(cage["available"])
            amount = float(body.amount) if body.amount is not None else avail
            if amount <= 0:
                raise HTTPException(400, "罚没金额须大于 0")
            if amount > avail + 1e-9:
                raise HTTPException(409, "罚没金额超过笼可用额")
            pending = conn.execute(
                """SELECT id FROM forfeit_intents
                   WHERE cage_id=? AND status='待复核'""",
                (body.cage_id,),
            ).fetchone()
            if pending:
                raise HTTPException(409, "本笼已有待复核罚没意图，禁止同笼并罚")
            now = now_iso()
            gen = int(cage["generation"])
            seq = conn.execute(
                "SELECT COUNT(*) c FROM forfeit_intents WHERE cage_id=?",
                (body.cage_id,),
            ).fetchone()["c"] + 1
            ref = f"FI-{cage['cage_code']}-g{gen}-{seq}"
            cur = conn.execute(
                """INSERT INTO forfeit_intents(cage_id,amount,reason,cage_generation,
                   status,intent_ref,officer,created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    body.cage_id,
                    amount,
                    body.reason or "履约违约",
                    gen,
                    "待复核",
                    ref,
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
                    "amount": amount,
                    "reason": body.reason,
                    "cage_generation": gen,
                    "intent_ref": ref,
                    "fate": f"{FATE_SPEC['path']}·{FATE_SPEC['name']}",
                    "cage_available_unchanged": avail,
                },
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM forfeit_intents WHERE id=?", (cur.lastrowid,)
            ).fetchone()
            return dict(row)
        finally:
            release(conn, lock_key, user)
            conn.commit()


@router.post("/api/forfeit-intents/{intent_id}/review")
def review_forfeit_intent(intent_id: int, body: ReviewIn, authorization: AuthHeader = None):
    """复核办结：通过=原子入册扣款；驳回=意图作废且册与笼面零改。"""
    user = require_writer(authorization)
    if body.decision not in REVIEW_DECISIONS:
        raise HTTPException(400, "decision 须为通过或驳回")
    with connect() as conn:
        intent = conn.execute(
            """SELECT i.*, c.cage_code FROM forfeit_intents i
               JOIN cages c ON c.id=i.cage_id WHERE i.id=?""",
            (intent_id,),
        ).fetchone()
        if not intent:
            raise HTTPException(404, "罚没意图不存在")
        target = intent["cage_code"]
        if body.amount is not None:
            raise _deviation(conn, user, target, "复核夹带改金额，命运金额以意图为准")
        lock_key = f"cage:{intent['cage_id']}"
        try:
            require_lock(conn, lock_key, user)
            if intent["status"] != "待复核":
                raise HTTPException(409, "意图已办结，禁止重复复核")
            if intent["officer"] == user:
                raise HTTPException(409, "复核岗须与提交人分人")
            now = now_iso()
            try:
                if body.decision == "驳回":
                    conn.execute(
                        """UPDATE forfeit_intents
                           SET status='已作废', reviewer=?, review_note=?, reviewed_at=?
                           WHERE id=?""",
                        (user, body.note or "", now, intent_id),
                    )
                    write_audit(
                        conn,
                        user,
                        "forfeit_void",
                        target,
                        {
                            "intent_id": intent_id,
                            "intent_ref": intent["intent_ref"],
                            "amount": float(intent["amount"]),
                            "note": body.note or "",
                            "formal_ledger_untouched": True,
                            "cage_unchanged": True,
                        },
                    )
                else:
                    cage = conn.execute(
                        "SELECT * FROM cages WHERE id=?", (intent["cage_id"],)
                    ).fetchone()
                    gen = int(cage["generation"])
                    if gen != int(intent["cage_generation"]):
                        raise HTTPException(409, "笼代已变，意图失效，请另起意图")
                    avail = float(cage["available"])
                    amount = float(intent["amount"])
                    if amount > avail + 1e-9:
                        raise HTTPException(409, "复核时笼可用额不足，禁止晋升")
                    ref = f"FOR-{cage['cage_code']}-g{gen}-i{intent_id}"
                    cur = conn.execute(
                        """INSERT INTO forfeits(cage_id,amount,reason,cage_generation,
                           status,forfeit_ref,created_at)
                           VALUES (?,?,?,?,?,?,?)""",
                        (
                            intent["cage_id"],
                            amount,
                            intent["reason"],
                            gen,
                            "已罚没",
                            ref,
                            now,
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
                           SET status='已晋升', reviewer=?, review_note=?, reviewed_at=?,
                               forfeit_id=?
                           WHERE id=?""",
                        (user, body.note or "", now, cur.lastrowid, intent_id),
                    )
                    write_audit(
                        conn,
                        user,
                        "forfeit_promote",
                        target,
                        {
                            "intent_id": intent_id,
                            "intent_ref": intent["intent_ref"],
                            "forfeit_id": cur.lastrowid,
                            "forfeit_ref": ref,
                            "amount": amount,
                            "note": body.note or "",
                            "atomic": "入册+扣笼+减冻同事务，失败同滚",
                        },
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            row = conn.execute(
                "SELECT * FROM forfeit_intents WHERE id=?", (intent_id,)
            ).fetchone()
            return dict(row)
        finally:
            release(conn, lock_key, user)
            conn.commit()
