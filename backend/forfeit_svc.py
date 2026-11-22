"""罚没：乙路径——影子罚没待复核晋升（口径锁死见 docs/罚没复核纪要.md）。

登记岗 POST /api/forfeits 只落影子罚没单（待复核），账实零动；
复核岗（非登记人）POST /api/forfeit-orders/{id}/review：
  通过 → 同一事务写正式罚没册、扣笼可用、减账户冻结与余额、记审计，影子晋升；
  驳回 / 晋升失败 → 影子作废，正式罚没册与笼面零改。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit
from doctrine import (
    FORFEIT_MODE,
    doctrine_payload,
    guard_forfeit_mode,
    guard_review_not_self,
)
from locks import release, require_lock
from models import ForfeitIn, ForfeitReviewIn

router = APIRouter(tags=["forfeit"])

FORFEITABLE = ("核验违约", "部分罚没")


@router.get("/api/forfeits/doctrine")
def get_doctrine(authorization: AuthHeader = None):
    """锁定口径运行源：罚没页与核验页文案由此渲染。"""
    require_user(authorization)
    return doctrine_payload()


@router.get("/api/forfeits")
def list_forfeits(authorization: AuthHeader = None):
    """正式罚没册（只含已罚没；影子单不入册）。"""
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT f.*, c.cage_code, c.contract_no
               FROM forfeits f
               JOIN cages c ON c.id=f.cage_id
               ORDER BY f.id DESC LIMIT 200"""
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.get("/api/forfeit-orders")
def list_forfeit_orders(authorization: AuthHeader = None, status: str | None = None):
    """影子罚没单（待复核 / 已晋升 / 已作废）。"""
    require_user(authorization)
    with connect() as conn:
        sql = """SELECT o.*, c.cage_code, c.contract_no
                 FROM forfeit_orders o
                 JOIN cages c ON c.id=o.cage_id"""
        params: tuple = ()
        if status:
            sql += " WHERE o.status=?"
            params = (status,)
        sql += " ORDER BY o.id DESC LIMIT 200"
        rows = conn.execute(sql, params).fetchall()
        return {"items": [dict(r) for r in rows]}


def _aligned_breach_att(conn, cage_id: int, gen: int):
    return conn.execute(
        """SELECT * FROM attestations
           WHERE cage_id=? AND attest_gen=? AND outcome='违约'
           ORDER BY id DESC LIMIT 1""",
        (cage_id, gen),
    ).fetchone()


@router.post("/api/forfeits")
def create_shadow_forfeit(body: ForfeitIn, authorization: AuthHeader = None):
    """登记影子罚没单（待复核）：只占住意图，笼面与账户零改。"""
    user = require_writer(authorization)
    guard_forfeit_mode(user, body.mode, f"cage:{body.cage_id}")
    lock_key = f"cage:{body.cage_id}"
    with connect() as conn:
        require_lock(conn, lock_key, user)
        try:
            cage = conn.execute(
                "SELECT * FROM cages WHERE id=?", (body.cage_id,)
            ).fetchone()
            if not cage:
                raise HTTPException(404, "笼不存在")
            if cage["status"] not in FORFEITABLE:
                raise HTTPException(409, "仅核验违约的笼可登记影子罚没")
            gen = int(cage["generation"])
            if not _aligned_breach_att(conn, body.cage_id, gen):
                raise HTTPException(409, "缺少与笼代对齐的违约核验")
            avail = float(cage["available"])
            amount = float(body.amount) if body.amount is not None else avail
            if amount <= 0:
                raise HTTPException(400, "罚没金额须大于 0")
            if amount > avail + 1e-9:
                raise HTTPException(409, "罚没金额超过笼可用额")
            pending = conn.execute(
                """SELECT id FROM forfeit_orders
                   WHERE cage_id=? AND status='待复核'""",
                (body.cage_id,),
            ).fetchone()
            if pending:
                raise HTTPException(409, "该笼已有待复核影子罚没单，一笼一影")
            now = now_iso()
            ref = f"FSH-{cage['cage_code']}-g{gen}-{now.replace(':', '').replace('-', '').replace('T', '')[-6:]}"
            cur = conn.execute(
                """INSERT INTO forfeit_orders(cage_id,amount,reason,cage_generation,
                   status,order_ref,created_by,created_at)
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
                "forfeit_order_create",
                cage["cage_code"],
                {
                    "order_id": cur.lastrowid,
                    "amount": amount,
                    "reason": body.reason,
                    "cage_generation": gen,
                    "order_ref": ref,
                    "mode": FORFEIT_MODE,
                },
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM forfeit_orders WHERE id=?", (cur.lastrowid,)
            ).fetchone()
            return dict(row)
        finally:
            release(conn, lock_key, user)
            conn.commit()


def _promotable(conn, cage, order) -> str | None:
    """晋升前一致性校验；返回 None 表示可晋升，否则返回失败原因。"""
    if not cage:
        return "笼不存在"
    if cage["status"] not in FORFEITABLE:
        return f"笼状态已漂移为 {cage['status']}"
    gen = int(cage["generation"])
    if gen != int(order["cage_generation"]):
        return f"笼代已漂移为 {gen}"
    if not _aligned_breach_att(conn, int(order["cage_id"]), gen):
        return "缺少与笼代对齐的违约核验"
    if float(order["amount"]) > float(cage["available"]) + 1e-9:
        return "笼可用额不足"
    return None


def _void_order(conn, user: str, order, note: str, why: str) -> None:
    now = now_iso()
    conn.execute(
        """UPDATE forfeit_orders
           SET status='已作废', reviewed_by=?, reviewed_at=?, review_note=?
           WHERE id=?""",
        (user, now, note, int(order["id"])),
    )
    write_audit(
        conn,
        user,
        "forfeit_order_void",
        order["order_ref"],
        {"order_id": int(order["id"]), "note": note, "why": why},
    )


@router.post("/api/forfeit-orders/{order_id}/review")
def review_forfeit_order(
    order_id: int, body: ForfeitReviewIn, authorization: AuthHeader = None
):
    """复核：通过则原子晋升；驳回或晋升失败则影子作废，账实零改。"""
    user = require_writer(authorization)
    guard_forfeit_mode(user, body.mode, f"forfeit-order:{order_id}")
    if body.decision not in ("通过", "驳回"):
        raise HTTPException(400, "decision 须为通过或驳回")
    with connect() as conn:
        order = conn.execute(
            "SELECT * FROM forfeit_orders WHERE id=?", (order_id,)
        ).fetchone()
        if not order:
            raise HTTPException(404, "影子罚没单不存在")
        guard_review_not_self(user, order["created_by"], order["order_ref"])
        if order["status"] != "待复核":
            raise HTTPException(409, "影子单已了结，禁止复核")
        lock_key = f"cage:{int(order['cage_id'])}"
        require_lock(conn, lock_key, user)
        try:
            now = now_iso()
            if body.decision == "驳回":
                _void_order(conn, user, order, body.note, "复核驳回")
                conn.commit()
                return dict(
                    conn.execute(
                        "SELECT * FROM forfeit_orders WHERE id=?", (order_id,)
                    ).fetchone()
                )
            # 通过 → 晋升前校验，失败则影子作废、正式册与笼面零改
            cage = conn.execute(
                "SELECT * FROM cages WHERE id=?", (int(order["cage_id"]),)
            ).fetchone()
            why = _promotable(conn, cage, order)
            if why:
                _void_order(conn, user, order, body.note, f"晋升失败：{why}")
                conn.commit()
                raise HTTPException(409, f"晋升失败，影子已作废，账实零改：{why}")
            # 原子晋升：同一事务写罚没册、扣笼可用、减账户冻结与余额、记审计
            amount = float(order["amount"])
            gen = int(cage["generation"])
            avail = float(cage["available"])
            ref = f"FOR-{cage['cage_code']}-g{gen}"
            cur = conn.execute(
                """INSERT INTO forfeits(cage_id,amount,reason,cage_generation,
                   status,forfeit_ref,created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    int(order["cage_id"]),
                    amount,
                    order["reason"],
                    gen,
                    "已罚没",
                    ref,
                    now,
                ),
            )
            forfeit_id = int(cur.lastrowid)
            new_avail = round(avail - amount, 6)
            new_status = "已罚没" if new_avail <= 1e-9 else "部分罚没"
            conn.execute(
                "UPDATE cages SET available=?, status=? WHERE id=?",
                (max(new_avail, 0.0), new_status, int(order["cage_id"])),
            )
            conn.execute(
                """UPDATE bond_accounts
                   SET frozen = frozen - ?, balance = balance - ?
                   WHERE party_key=?""",
                (amount, amount, cage["party_key"]),
            )
            conn.execute(
                """UPDATE forfeit_orders
                   SET status='已晋升', reviewed_by=?, reviewed_at=?,
                       review_note=?, forfeit_id=?
                   WHERE id=?""",
                (user, now, body.note, forfeit_id, order_id),
            )
            write_audit(
                conn,
                user,
                "forfeit_promote",
                cage["cage_code"],
                {
                    "order_id": order_id,
                    "forfeit_id": forfeit_id,
                    "amount": amount,
                    "cage_generation": gen,
                    "order_ref": order["order_ref"],
                    "forfeit_ref": ref,
                    "mode": FORFEIT_MODE,
                },
            )
            write_audit(
                conn,
                user,
                "cage_forfeit",
                cage["cage_code"],
                {
                    "forfeit_id": forfeit_id,
                    "amount": amount,
                    "reason": order["reason"],
                    "cage_generation": gen,
                    "forfeit_ref": ref,
                },
            )
            conn.commit()
            return dict(
                conn.execute(
                    "SELECT * FROM forfeit_orders WHERE id=?", (order_id,)
                ).fetchone()
            )
        finally:
            release(conn, lock_key, user)
            conn.commit()
