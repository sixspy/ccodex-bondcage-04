"""罚没复核路径：三条先红后绿的验收测（乙路径）。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import app
from db import connect, init_db


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", tmp_path / "t.db")
    init_db()
    yield


def _auth(c: TestClient, user: str = "admin") -> dict:
    tok = c.post(
        "/api/login", json={"username": user, "password": "123456"}
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}


def _violation_cage(c: TestClient, h: dict) -> dict:
    """取种子违约态笼：对已冻结的种子笼核验违约。"""
    cages = c.get("/api/cages", headers=h).json()["items"]
    cage = next(x for x in cages if x["cage_code"] == "CG-P-EAST-001")
    r = c.post(
        "/api/attestations",
        headers=h,
        json={"cage_id": cage["id"], "outcome": "违约", "evidence": "验收不合格"},
    )
    assert r.status_code == 200, r.text
    return c.get(f"/api/cages/{cage['id']}", headers=h).json()["cage"]


def _snapshot(c: TestClient, h: dict, cage: dict) -> dict:
    detail = c.get(f"/api/cages/{cage['id']}", headers=h).json()
    acct = c.get(f"/api/bonds/{cage['party_key']}", headers=h).json()["account"]
    formal = [
        f
        for f in c.get("/api/forfeits", headers=h).json()["items"]
        if f["cage_id"] == cage["id"]
    ]
    return {
        "cage_status": detail["cage"]["status"],
        "cage_available": detail["cage"]["available"],
        "account_balance": acct["balance"],
        "account_frozen": acct["frozen"],
        "formal_rows": len(formal),
        "formal_amount": sum(float(f["amount"]) for f in formal),
    }


def test_forfeit_must_be_reviewable_not_direct():
    """核验违约后打罚没写口：只能建待复核意图，不得直写正式罚没册。"""
    c = TestClient(app)
    h = _auth(c, "admin")
    cage = _violation_cage(c, h)
    before = _snapshot(c, h, cage)

    r = c.post(
        "/api/forfeits",
        headers=h,
        json={"cage_id": cage["id"], "reason": "逾期未履约"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "待复核", body
    assert body["intent_ref"].startswith("FORI-"), body
    assert body["cage_id"] == cage["id"]

    # 正式罚没册不得出现该笼行；资金留在笼内。
    after = _snapshot(c, h, cage)
    assert after["formal_rows"] == 0
    assert after["formal_amount"] == 0
    assert after["cage_available"] == before["cage_available"]
    assert after["account_frozen"] == before["account_frozen"]
    assert after["account_balance"] == before["account_balance"]

    intents = c.get("/api/forfeit-intents", headers=h).json()["items"]
    mine = [i for i in intents if i["cage_id"] == cage["id"]]
    assert len(mine) == 1, intents
    assert mine[0]["status"] == "待复核"
    assert mine[0]["id"] == body["id"]


def test_rejected_review_leaves_cage_and_account_untouched():
    """复核未过则意图作废：笼可用与账户冻结/余额零改，正式罚没零增。"""
    c = TestClient(app)
    h = _auth(c, "admin")
    cage = _violation_cage(c, h)
    before = _snapshot(c, h, cage)

    intent = c.post(
        "/api/forfeits",
        headers=h,
        json={"cage_id": cage["id"], "reason": "证据待复核"},
    ).json()
    r = c.post(
        f"/api/forfeit-intents/{intent['id']}/review",
        headers=h,
        json={"decision": "驳回", "note": "证据不足，退回补证"},
    )
    assert r.status_code == 200, r.text
    reviewed = r.json()["intent"]
    assert reviewed["status"] == "已驳回", reviewed
    assert reviewed["review_note"] == "证据不足，退回补证"
    assert reviewed["reviewed_by"] == "admin"

    after = _snapshot(c, h, cage)
    assert after["cage_available"] == before["cage_available"]
    assert after["account_frozen"] == before["account_frozen"]
    assert after["account_balance"] == before["account_balance"]
    assert after["formal_rows"] == 0
    assert after["formal_amount"] == 0

    intents = c.get("/api/forfeit-intents", headers=h).json()["items"]
    mine = [i for i in intents if i["cage_id"] == cage["id"]]
    assert len(mine) == 1
    assert mine[0]["status"] == "已驳回"


def test_two_writers_forfeit_same_cage_contend_for_cage_lock():
    """两人同罚一只笼：后到抢不到笼锁，只留争用痕，不写意图不写正式行。"""
    c = TestClient(app)
    h_admin = _auth(c, "admin")
    h_clerk = _auth(c, "clerk")
    cage = _violation_cage(c, h_admin)
    lock_key = f"cage:{cage['id']}"

    # 第一人处于罚没事务中（持笼锁）。
    r = c.post("/api/locks/acquire", headers=h_admin, json={"lock_key": lock_key})
    assert r.status_code == 200, r.text

    # 第二人同罚：只记争用，不写业务。
    r = c.post(
        "/api/forfeits",
        headers=h_clerk,
        json={"cage_id": cage["id"], "reason": "后到者争用"},
    )
    assert r.status_code == 409, r.text
    assert "争用" in r.json()["detail"]

    cont = c.get("/api/contention", headers=h_admin).json()["items"]
    assert any(
        x["lock_key"] == lock_key
        and x["actor"] == "clerk"
        and x["action"] == "争用"
        for x in cont
    ), cont

    intents = c.get("/api/forfeit-intents", headers=h_admin).json()["items"]
    assert not [i for i in intents if i["cage_id"] == cage["id"]]
    formal = c.get("/api/forfeits", headers=h_admin).json()["items"]
    assert not [f for f in formal if f["cage_id"] == cage["id"]]

    # 争用后笼面未动。
    detail = c.get(f"/api/cages/{cage['id']}", headers=h_admin).json()["cage"]
    assert detail["available"] == cage["available"]
    assert detail["status"] == "核验违约"


def test_fate_deviation_is_refused_and_audited():
    """偏离命运说明（甲路径/第三路）记罚没偏航并拒写。"""
    c = TestClient(app)
    h = _auth(c, "admin")
    cage = _violation_cage(c, h)
    before = _snapshot(c, h, cage)

    r = c.post(
        "/api/forfeits",
        headers=h,
        json={"cage_id": cage["id"], "reason": "甲路径试探", "fate_path": "甲"},
    )
    assert r.status_code == 409, r.text
    assert "罚没偏航" in r.json()["detail"]

    after = _snapshot(c, h, cage)
    assert after == before
    assert not c.get("/api/forfeit-intents", headers=h).json()["items"]

    audits = c.get("/api/audit?limit=200", headers=h).json()["items"]
    drift = [a for a in audits if a["action"] == "罚没偏航"]
    assert drift, audits
    assert "甲" in drift[0]["payload"]


def test_five_surfaces_share_same_forfeit_source():
    """罚没/核验/入笼/对账/审计五面须同源：通过前零扣、通过后同额入册。"""
    c = TestClient(app)
    h = _auth(c, "admin")
    cage = _violation_cage(c, h)
    before = _snapshot(c, h, cage)

    intent = c.post(
        "/api/forfeits",
        headers=h,
        json={"cage_id": cage["id"], "reason": "同源验收"},
    ).json()

    # 1) 罚没面：意图待复核，正式册零行。
    intents = c.get("/api/forfeit-intents", headers=h).json()["items"]
    assert [i["status"] for i in intents if i["cage_id"] == cage["id"]] == ["待复核"]
    assert not c.get("/api/forfeits", headers=h).json()["items"]

    # 2) 核验面：核验行标注待复核意图 1、正式罚没 0。
    att = [
        a
        for a in c.get("/api/attestations", headers=h).json()["items"]
        if a["cage_id"] == cage["id"]
    ][0]
    assert att["pending_intents"] == 1
    assert att["formal_forfeits"] == 0

    # 3) 入笼面：笼可查待复核意图数/金额，笼可用未动。
    cages = c.get("/api/cages", headers=h).json()["items"]
    mine = [x for x in cages if x["id"] == cage["id"]][0]
    assert mine["pending_intents"] == 1
    assert mine["available"] == before["cage_available"]

    # 4) 对账面：待复核不计入，仍平衡。
    recon = c.get(f"/api/reconcile/{cage['cage_code']}", headers=h).json()
    assert recon["balanced"] is True
    assert recon["pending_intent_amount"] == before["cage_available"]
    assert recon["forfeited"] == 0

    # 5) 审计面：意图链路一致，无偏航。
    chain = c.get("/api/audit/forfeit-chain", headers=h).json()
    row = [i for i in chain["items"] if i["id"] == intent["id"]][0]
    assert row["status"] == "待复核"
    assert row["consistent"] is True
    assert chain["summary"]["fate_drift"] == 0

    # 复核通过后五面同步出现同一笔正式罚没。
    review = c.post(
        f"/api/forfeit-intents/{intent['id']}/review",
        headers=h,
        json={"decision": "通过", "note": "同源验收通过"},
    )
    assert review.status_code == 200
    forfeit_id = review.json()["forfeit"]["id"]
    amount = review.json()["forfeit"]["amount"]

    formal = c.get("/api/forfeits", headers=h).json()["items"]
    assert [f["id"] for f in formal if f["cage_id"] == cage["id"]] == [forfeit_id]

    att = [
        a
        for a in c.get("/api/attestations", headers=h).json()["items"]
        if a["cage_id"] == cage["id"]
    ][0]
    assert att["pending_intents"] == 0
    assert att["formal_forfeits"] == 1

    mine = [
        x
        for x in c.get("/api/cages", headers=h).json()["items"]
        if x["id"] == cage["id"]
    ][0]
    assert mine["pending_intents"] == 0
    assert mine["formal_forfeits"] == 1

    recon = c.get(f"/api/reconcile/{cage['cage_code']}", headers=h).json()
    assert recon["balanced"] is True
    assert recon["forfeited"] == amount
    assert recon["pending_intent_amount"] == 0
    assert recon["account_frozen"] == before["account_frozen"] - amount

    chain = c.get("/api/audit/forfeit-chain", headers=h).json()
    row = [i for i in chain["items"] if i["id"] == intent["id"]][0]
    assert row["status"] == "已通过"
    assert row["forfeit_status"] == "已罚没"
    assert row["forfeit_id"] == forfeit_id
    assert row["consistent"] is True
