"""罚没复核命运（乙路径：复核前笼面零扣）的失败测。

口径（与 docs/罚没复核命运说明.md 一致，2026-11-22 锁死）：
- 罚没提交只落「待复核意图」，笼面 available 与账户冻结/余额零改；
- 复核通过才在同一事务里原子写正式罚没册 + 扣笼 + 减账户冻结；
- 复核驳回则意图作废，正式罚没册与笼面始终零改；
- 复核岗须与提交人分人；复核不得改金额；
- 任何直进罚没册的写企图记「罚没偏航」并拒写；
- 两人同罚一只笼抢笼锁，后到只留争用痕，不写业务。

本文件两条测必须先见红（服务未实现），实现后转绿。
"""
import pytest
from fastapi.testclient import TestClient

from app import app
from db import init_db

FIXTURE_CAGE = "CG-P-RISK-901"
FIXTURE_PARTY = "P-RISK"


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", tmp_path / "t.db")
    init_db()
    yield


def _auth(c: TestClient, user="admin") -> dict:
    tok = c.post(
        "/api/login", json={"username": user, "password": "123456"}
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}


def _fixture_cage(c: TestClient, h: dict) -> dict:
    cages = c.get("/api/cages", headers=h).json()["items"]
    cage = [x for x in cages if x["cage_code"] == FIXTURE_CAGE]
    assert cage, "种子违约态笼不存在"
    return cage[0]


def _account(c: TestClient, h: dict) -> dict:
    return c.get(f"/api/bonds/{FIXTURE_PARTY}", headers=h).json()["account"]


def _intents(c: TestClient, h: dict) -> list:
    r = c.get("/api/forfeit-intents", headers=h)
    assert r.status_code == 200
    return [i for i in r.json()["items"] if i["cage_code"] == FIXTURE_CAGE]


def _formal(c: TestClient, h: dict) -> list:
    return [
        f
        for f in c.get("/api/forfeits", headers=h).json()["items"]
        if f["cage_code"] == FIXTURE_CAGE
    ]


def _audits(c: TestClient, h: dict, action: str) -> list:
    rows = c.get("/api/audit?limit=500", headers=h).json()["items"]
    return [a for a in rows if a["action"] == action and a["target"] == FIXTURE_CAGE]


def test_forfeit_intent_zero_deduction_until_review():
    """罚没不可复核即事故：复核前笼面零扣，复核通过才原子入册扣款。"""
    c = TestClient(app)
    h_admin = _auth(c, "admin")
    h_clerk = _auth(c, "clerk")
    cage = _fixture_cage(c, h_admin)
    assert cage["status"] == "核验违约"
    assert cage["available"] == 60000
    acct0 = _account(c, h_admin)
    assert acct0["frozen"] == 60000
    assert acct0["balance"] == 200000

    # 1) 罚没提交只落待复核意图，笼面与账户零改
    r = c.post(
        "/api/forfeits",
        headers=h_admin,
        json={"cage_id": cage["id"], "amount": 20000, "reason": "逾期未履约"},
    )
    assert r.status_code == 200
    intent = r.json()
    assert intent["status"] == "待复核"
    assert intent["intent_ref"]
    cage1 = _fixture_cage(c, h_admin)
    assert cage1["available"] == 60000, "复核前笼面被动了"
    assert cage1["status"] == "核验违约", "复核前笼状态被改了"
    acct1 = _account(c, h_admin)
    assert acct1["frozen"] == 60000, "复核前账户冻结被减了"
    assert acct1["balance"] == 200000, "复核前账户余额被减了"
    assert _formal(c, h_admin) == [], "复核前正式罚没册被写了"
    assert len(_intents(c, h_admin)) == 1

    # 2) 直进罚没册的写企图：记罚没偏航并拒写
    dev = c.post(
        "/api/forfeits",
        headers=h_admin,
        json={
            "cage_id": cage["id"],
            "amount": 1000,
            "reason": "想绕复核",
            "mode": "直进罚没册",
        },
    )
    assert dev.status_code == 409
    assert len(_audits(c, h_admin, "罚没偏航")) >= 1
    assert _fixture_cage(c, h_admin)["available"] == 60000

    # 3) 复核岗须与提交人分人
    same = c.post(
        f"/api/forfeit-intents/{intent['id']}/review",
        headers=h_admin,
        json={"decision": "通过", "note": "自己审自己"},
    )
    assert same.status_code == 409

    # 4) 复核不得改金额
    tamper = c.post(
        f"/api/forfeit-intents/{intent['id']}/review",
        headers=h_clerk,
        json={"decision": "通过", "note": "顺手改数", "amount": 1},
    )
    assert tamper.status_code == 409
    assert len(_audits(c, h_admin, "罚没偏航")) >= 2

    # 5) 驳回：意图作废，正式册与笼面始终零改
    void = c.post(
        f"/api/forfeit-intents/{intent['id']}/review",
        headers=h_clerk,
        json={"decision": "驳回", "note": "证据不足"},
    )
    assert void.status_code == 200
    assert _intents(c, h_admin)[0]["status"] == "已作废"
    assert _formal(c, h_admin) == []
    assert _fixture_cage(c, h_admin)["available"] == 60000
    assert _account(c, h_admin)["frozen"] == 60000

    # 6) 重新提交意图，复核通过：同一事务原子入册 + 扣笼 + 减冻结
    r2 = c.post(
        "/api/forfeits",
        headers=h_admin,
        json={"cage_id": cage["id"], "amount": 20000, "reason": "逾期未履约"},
    )
    assert r2.status_code == 200
    intent2 = r2.json()
    ok = c.post(
        f"/api/forfeit-intents/{intent2['id']}/review",
        headers=h_clerk,
        json={"decision": "通过", "note": "证据确凿"},
    )
    assert ok.status_code == 200
    formal = _formal(c, h_admin)
    assert len(formal) == 1
    assert formal[0]["status"] == "已罚没"
    assert formal[0]["amount"] == 20000
    cage2 = _fixture_cage(c, h_admin)
    assert cage2["available"] == 40000
    assert cage2["status"] == "部分罚没"
    acct2 = _account(c, h_admin)
    assert acct2["frozen"] == 40000
    assert acct2["balance"] == 180000
    promoted = [i for i in _intents(c, h_admin) if i["status"] == "已晋升"]
    assert len(promoted) == 1
    assert promoted[0]["forfeit_id"] == formal[0]["id"]

    # 7) 五页同源：对账与当事人互证皆绿（真绿）
    recon = c.get(f"/api/reconcile/{FIXTURE_CAGE}", headers=h_admin).json()
    assert recon["balanced"] is True
    assert recon["forfeited"] == 20000
    proof = c.get(f"/api/proof/{FIXTURE_PARTY}", headers=h_admin).json()
    assert proof["frozen_matches_live"] is True
    assert _audits(c, h_admin, "forfeit_intent")
    assert _audits(c, h_admin, "forfeit_promote")
    assert _audits(c, h_admin, "forfeit_void")


def test_double_forfeit_contention_only_trace():
    """两人同时罚同一只笼：抢笼锁，后到只留争用痕，不写业务。"""
    c = TestClient(app)
    h_admin = _auth(c, "admin")
    h_clerk = _auth(c, "clerk")
    cage = _fixture_cage(c, h_admin)
    lock_key = f"cage:{cage['id']}"

    # admin 的罚没在进行中（持锁），clerk 同罚此笼只能留争用痕
    assert (
        c.post("/api/locks/acquire", headers=h_admin, json={"lock_key": lock_key})
        .status_code
        == 200
    )
    late = c.post(
        "/api/forfeits",
        headers=h_clerk,
        json={"cage_id": cage["id"], "amount": 30000, "reason": "抢罚"},
    )
    assert late.status_code == 409
    cont = c.get("/api/contention", headers=h_admin).json()["items"]
    assert any(
        x["lock_key"] == lock_key and x["actor"] == "clerk" for x in cont
    ), "后到者未留争用痕"
    assert _intents(c, h_admin) == [], "后到者写了业务意图"
    assert _formal(c, h_admin) == [], "后到者写了正式罚没册"
    assert _fixture_cage(c, h_admin)["available"] == 60000

    # 锁释放后 clerk 可正常落意图；同笼已有待复核意图时第二单被拒
    assert (
        c.post("/api/locks/release", headers=h_admin, json={"lock_key": lock_key})
        .status_code
        == 200
    )
    first = c.post(
        "/api/forfeits",
        headers=h_clerk,
        json={"cage_id": cage["id"], "amount": 30000, "reason": "逾期未履约"},
    )
    assert first.status_code == 200
    assert first.json()["status"] == "待复核"
    second = c.post(
        "/api/forfeits",
        headers=h_admin,
        json={"cage_id": cage["id"], "amount": 10000, "reason": "重复罚"},
    )
    assert second.status_code == 409
    assert len(_intents(c, h_admin)) == 1
    assert _formal(c, h_admin) == []
