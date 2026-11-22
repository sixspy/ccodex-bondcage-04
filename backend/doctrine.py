"""罚没复核纪要锁死口径（2026-11-22 当日锁死，见 docs/罚没复核纪要.md）。

乙路径：影子罚没待复核晋升。任何请求携带异口径 → 记审计「罚没偏航」并拒写；
复核与登记同人 → 同样记偏航。偏航痕独立提交，业务回滚不丢痕。
"""
from __future__ import annotations

from fastapi import HTTPException

from db import connect, write_audit

FORFEIT_MODE = "影子罚没待复核晋升"  # 乙路径
REJECTED_MODE = "立即入罚没册整笔提交"  # 甲路径（弃用，不得混用）
MINUTES_DOC = "docs/罚没复核纪要.md"
LOCKED_AT = "2026-11-22"
DEVIATION_ACTION = "罚没偏航"


def doctrine_payload() -> dict:
    """五页同源的口径运行源：页面文案由此渲染，不得各自硬编码。"""
    return {
        "mode": FORFEIT_MODE,
        "rejected_mode": REJECTED_MODE,
        "minutes": MINUTES_DOC,
        "locked": True,
        "locked_at": LOCKED_AT,
        "text": (
            f"口径已锁死（{LOCKED_AT}）：{FORFEIT_MODE}。登记岗只落影子罚没单（待复核），"
            "复核岗换人签字后同一事务晋升：写罚没册、扣笼可用、减账户冻结与余额、记审计；"
            "驳回或晋升失败则影子作废，正式册与笼面零改。偏离纪要记罚没偏航并拒写。"
        ),
    }


def write_committed_audit(actor: str, action: str, target: str, payload: dict) -> None:
    """独立连接立即提交的审计痕：调用方业务事务随后回滚也不丢。"""
    with connect() as conn:
        write_audit(conn, actor, action, target, payload)
        conn.commit()


def guard_forfeit_mode(actor: str, mode: str | None, target: str) -> None:
    """请求携带非锁定口径 → 记罚没偏航并拒写（409）。"""
    if mode is None or mode == FORFEIT_MODE:
        return
    write_committed_audit(
        actor,
        DEVIATION_ACTION,
        target,
        {
            "requested_mode": mode,
            "locked_mode": FORFEIT_MODE,
            "minutes": MINUTES_DOC,
            "locked_at": LOCKED_AT,
        },
    )
    raise HTTPException(409, "罚没偏航：请求口径偏离已锁死纪要，拒写")


def guard_review_not_self(actor: str, created_by: str, target: str) -> None:
    """复核与登记同人 → 记罚没偏航并拒写（409），影子单保持待复核。"""
    if actor != created_by:
        return
    write_committed_audit(
        actor,
        DEVIATION_ACTION,
        target,
        {
            "reason": "复核与登记同人，违反换人复核",
            "locked_mode": FORFEIT_MODE,
            "minutes": MINUTES_DOC,
        },
    )
    raise HTTPException(409, "罚没偏航：复核岗须与登记岗换人")
