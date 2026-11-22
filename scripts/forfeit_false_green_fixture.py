#!/usr/bin/env python3
"""假绿事故夹具：种子违约态笼 CG-P-RISK-901 上的罚没命运演练。

在本机文件库 data/app.db 上跑通（库路径不变）：

    python3 scripts/forfeit_false_green_fixture.py
    python3 scripts/forfeit_false_green_fixture.py --expect accident
    python3 scripts/forfeit_false_green_fixture.py --expect guarded

事故剧本（旧直罚行为）：核验违约后一点罚没，同一提交直写正式罚没册并
扣笼减冻，复核岗还没签字钱已出笼；对账公式 available+拨付+罚没=总额
依然平衡 —— 绿是假的：复核未办、资金已不可撤回。

新行为（乙路径·复核前笼面零扣）：直罚探针被记「罚没偏航」并 409 拒写；
普通罚没只落待复核意图，复核通过前笼面与账户冻结零改。

夹具幂等：开场与收尾都会把 CG-P-RISK-901 / P-RISK 复位到种子违约态，
可反复在本机文件库上跑。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from app import app  # noqa: E402
from db import DB_PATH, connect, init_db, now_iso  # noqa: E402

FIXTURE_CAGE = "CG-P-RISK-901"
FIXTURE_PARTY = "P-RISK"
FIXTURE_AMOUNT = 60000.0
FIXTURE_BALANCE = 200000.0


def login(c: TestClient, user: str) -> dict:
    tok = c.post("/api/login", json={"username": user, "password": "123456"}).json()[
        "token"
    ]
    return {"Authorization": "Bearer " + tok}


def table_exists(conn, name: str) -> bool:
    return (
        conn.execute(
            "SELECT COUNT(*) c FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()["c"]
        > 0
    )


def reset_fixture_state(c: TestClient, h: dict) -> None:
    """把夹具笼与夹具户复位到种子违约态；复位动作本身落审计。"""
    with connect() as conn:
        cage = conn.execute(
            "SELECT id FROM cages WHERE cage_code=?", (FIXTURE_CAGE,)
        ).fetchone()
        if not cage:
            raise SystemExit(f"种子违约态笼 {FIXTURE_CAGE} 不存在，请先 init_db")
        cid = cage["id"]
        conn.execute("DELETE FROM forfeits WHERE cage_id=?", (cid,))
        if table_exists(conn, "forfeit_intents"):
            conn.execute("DELETE FROM forfeit_intents WHERE cage_id=?", (cid,))
        conn.execute(
            "UPDATE cages SET available=?, status='核验违约' WHERE id=?",
            (FIXTURE_AMOUNT, cid),
        )
        conn.execute(
            "UPDATE bond_accounts SET balance=?, frozen=? WHERE party_key=?",
            (FIXTURE_BALANCE, FIXTURE_AMOUNT, FIXTURE_PARTY),
        )
        conn.execute(
            """INSERT INTO audit_trail(actor,action,target,payload,created_at)
               VALUES (?,?,?,?,?)""",
            (
                "fixture",
                "fixture_reset",
                FIXTURE_CAGE,
                json.dumps({"note": "假绿事故夹具复位到种子违约态"}, ensure_ascii=False),
                now_iso(),
            ),
        )
        conn.commit()


def snapshot(c: TestClient, h: dict) -> dict:
    cages = c.get("/api/cages", headers=h).json()["items"]
    cage = [x for x in cages if x["cage_code"] == FIXTURE_CAGE][0]
    acct = c.get(f"/api/bonds/{FIXTURE_PARTY}", headers=h).json()["account"]
    forfeits = [
        f
        for f in c.get("/api/forfeits", headers=h).json()["items"]
        if f["cage_code"] == FIXTURE_CAGE
    ]
    r_intents = c.get("/api/forfeit-intents", headers=h)
    intents = (
        [i for i in r_intents.json()["items"] if i["cage_code"] == FIXTURE_CAGE]
        if r_intents.status_code == 200
        else None
    )
    recon = c.get(f"/api/reconcile/{FIXTURE_CAGE}", headers=h).json()
    audits = c.get("/api/audit?limit=500", headers=h).json()["items"]
    deviations = [
        a for a in audits if a["action"] == "罚没偏航" and a["target"] == FIXTURE_CAGE
    ]
    reviews = [
        a
        for a in audits
        if a["action"] in ("forfeit_promote", "forfeit_void")
        and a["target"] == FIXTURE_CAGE
    ]
    return {
        "cage_status": cage["status"],
        "cage_available": cage["available"],
        "account_frozen": acct["frozen"],
        "account_balance": acct["balance"],
        "formal_forfeits": len(forfeits),
        "forfeit_intents": intents,
        "reconcile_balanced": recon["balanced"],
        "reconcile_forfeited": recon["forfeited"],
        "deviation_audit_count": len(deviations),
        "review_audit_count": len(reviews),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect", choices=["accident", "guarded"], default=None)
    args = ap.parse_args()

    init_db()
    c = TestClient(app)
    h_admin = login(c, "admin")
    h_clerk = login(c, "clerk")

    reset_fixture_state(c, h_admin)
    before = snapshot(c, h_admin)
    assert before["cage_status"] == "核验违约", before
    assert before["cage_available"] == FIXTURE_AMOUNT, before
    assert before["formal_forfeits"] == 0, before

    report = {"db": str(DB_PATH), "cage": FIXTURE_CAGE, "before": before}

    # 探针 A：旧世界语义的直罚 —— 要求绕过复核直进罚没册
    probe_direct = c.post(
        "/api/forfeits",
        headers=h_admin,
        json={
            "cage_id": next(
                x["id"]
                for x in c.get("/api/cages", headers=h_admin).json()["items"]
                if x["cage_code"] == FIXTURE_CAGE
            ),
            "amount": FIXTURE_AMOUNT,
            "reason": "夹具演练：要求直进罚没册",
            "mode": "直进罚没册",
        },
    )
    mid = snapshot(c, h_admin)
    report["probe_direct"] = {
        "status_code": probe_direct.status_code,
        "body": probe_direct.json(),
    }
    report["after_direct"] = mid

    money_moved = mid["cage_available"] != before["cage_available"] or mid[
        "account_frozen"
    ] != before["account_frozen"]

    if probe_direct.status_code == 200 and money_moved:
        # 旧世界：直罚成功，钱已出笼
        world = "accident"
        report["false_green_evidence"] = {
            "复核记录条数": mid["review_audit_count"],
            "正式罚没行数": mid["formal_forfeits"],
            "笼内可用_罚后": mid["cage_available"],
            "账户冻结_罚后": mid["account_frozen"],
            "对账平衡": mid["reconcile_balanced"],
            "结论": (
                "复核岗未签字（复核记录 0 条），金额已从笼里抽走并直进正式罚没册；"
                "对账公式依然平衡（绿），但绿是假的：罚没不可撤回，出事只能认栽。"
            ),
        }
    elif probe_direct.status_code == 409 and mid["deviation_audit_count"] >= 1:
        # 新世界：偏航被拒写；再验证普通提交只落意图、笼面零改
        world = "guarded"
        cage_id = next(
            x["id"]
            for x in c.get("/api/cages", headers=h_admin).json()["items"]
            if x["cage_code"] == FIXTURE_CAGE
        )
        probe_intent = c.post(
            "/api/forfeits",
            headers=h_admin,
            json={"cage_id": cage_id, "amount": 20000, "reason": "夹具演练：普通提交"},
        )
        after_intent = snapshot(c, h_admin)
        report["probe_intent"] = {
            "status_code": probe_intent.status_code,
            "body": probe_intent.json(),
        }
        report["after_intent"] = after_intent
        report["guarded_evidence"] = {
            "直罚探针": "409 拒写，审计已记「罚没偏航」",
            "意图状态": probe_intent.json().get("status"),
            "笼面零改": after_intent["cage_available"] == before["cage_available"],
            "冻结零改": after_intent["account_frozen"] == before["account_frozen"],
            "正式册零改": after_intent["formal_forfeits"] == 0,
        }
        # 复核分人：clerk 驳回，意图作废，正式册与笼面始终零改
        intent_id = probe_intent.json()["id"]
        rv = c.post(
            f"/api/forfeit-intents/{intent_id}/review",
            headers=h_clerk,
            json={"decision": "驳回", "note": "夹具演练：复核驳回"},
        )
        after_void = snapshot(c, h_clerk)
        report["probe_review_void"] = {"status_code": rv.status_code}
        report["after_void"] = after_void
        report["guarded_evidence"]["驳回后正式册零改"] = (
            after_void["formal_forfeits"] == 0
        )
        report["guarded_evidence"]["驳回后笼面零改"] = (
            after_void["cage_available"] == before["cage_available"]
        )
    else:
        world = "unknown"
        report["error"] = "既未复现事故也未看到偏航拒写，行为无法归类"

    # 收尾复位，保持本机文件库干净、夹具可重跑
    reset_fixture_state(c, h_admin)
    report["reset_ok"] = snapshot(c, h_admin)["cage_available"] == FIXTURE_AMOUNT
    report["world"] = world

    banner = {
        "accident": "!! 假绿事故复现：复核未办、钱已出笼、对账假绿 !!",
        "guarded": "✓ 乙路径生效：直罚记偏航拒写，复核前笼面零扣",
        "unknown": "? 行为无法归类",
    }[world]
    print("=" * 72)
    print(banner)
    print("=" * 72)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))

    if args.expect and args.expect != world:
        print(f"\nEXPECT {args.expect} BUT GOT {world}", file=sys.stderr)
        return 1
    return 0 if world != "unknown" else 2


if __name__ == "__main__":
    raise SystemExit(main())
