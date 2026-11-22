"""罚没复核命运说明（锁定口径）。

本仓只认「乙路径」：核验违约只置违约态；罚没动作先建待复核意图，
笼可用与账户冻结零改；复核通过后才写正式罚没册并扣笼；复核驳回则
意图作废、正式罚没零增。

甲路径（核验即刻入正式罚没册并扣笼，复核只能追加意见）与任何第三路径
均不采用，也不得与乙路径混用。任何请求或配置偏离本说明，一律记
「罚没偏航」并拒写。
"""
from __future__ import annotations

import os
from datetime import date

from fastapi import HTTPException

from db import write_audit

FATE_PATH = "乙"
LOCKED_ON = date.today().isoformat()
FUND_FATE = (
    "复核办结前笼内资金零动：cage.available 与 bond_accounts.frozen/balance 均不改，"
    "正式罚没册零增。复核通过后才入册扣款；复核驳回则意图作废、正式零增。"
)

NOTE = {
    "title": "罚没复核命运说明",
    "path": FATE_PATH,
    "path_name": "复核通过后入册扣款",
    "locked": True,
    "locked_on": LOCKED_ON,
    "fund_fate_before_review": FUND_FATE,
    "approved": (
        "复核通过且持笼锁时，在同一事务内写正式罚没册一行，并同步扣减"
        "笼可用额、账户余额与账户冻结额。"
    ),
    "rejected": (
        "复核驳回只把意图置为已驳回，笼可用、账户余额与冻结、正式罚没册零改；"
        "不自动退款，也不补写正式罚没行。"
    ),
    "forbidden": [
        "甲路径：核验即直入正式罚没册并扣笼，复核仅追加意见",
        "任何第三路径",
        "甲、乙混用",
        "只改罚没页文案而不改服务写口",
    ],
    "deviation": "偏离本说明记「罚没偏航」并拒写。",
}


def configured_path() -> str:
    """当前进程被要求走的路径；未显式要求时按锁死的乙路径。"""
    return (os.environ.get("FORFEIT_FATE_PATH") or FATE_PATH).strip() or FATE_PATH


def guard_fate(
    conn,
    actor: str,
    action: str,
    target: str,
    requested: str | None = None,
) -> dict:
    """校验写口未偏离锁死的乙路径；偏离则记罚没偏航并拒写。"""
    requested = (requested or configured_path()).strip()
    row = conn.execute("SELECT path, locked_on FROM forfeit_fate_note WHERE id=1").fetchone()
    db_path = row["path"] if row else None
    if requested != FATE_PATH or db_path != FATE_PATH:
        write_audit(
            conn,
            actor,
            "罚没偏航",
            target,
            {
                "罚没偏航": True,
                "requested_path": requested,
                "locked_path": FATE_PATH,
                "db_locked_path": db_path,
                "locked_on": row["locked_on"] if row else None,
                "refused_action": action,
            },
        )
        conn.commit()
        raise HTTPException(
            409,
            f"罚没偏航：命运说明锁定{FATE_PATH}路径，拒绝按{requested}路径写入",
        )
    return NOTE
