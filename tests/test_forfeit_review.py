"""罚没可复核改造（乙路径：影子罚没待复核晋升，口径见 docs/罚没复核纪要.md）。

种子违约态笼来自 fixtures/breach_cages.json（假绿事故夹具）。
"""
import pytest
from fastapi.testclient import TestClient

from app import app
from db import init_db


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


def _breach_cage(c: TestClient, h: dict, code="CG-BREACH-001") -> dict:
    cages = c.get("/api/cages", headers=h).json()["items"]
    return next(x for x in cages if x["cage_code"] == code)


def _account(c: TestClient, h: dict, party_key: str) -> dict:
    return c.get(f"/api/bonds/{party_key}", headers=h).json()["account"]


def test_forfeit_must_be_reviewable_shadow_first():
    """罚没不可复核失败测：直罚必须被拦成影子单，复核签字前账实零动。"""
    c = TestClient(app)
    h_admin = _auth(c, "admin")
    h_clerk = _auth(c, "clerk")
    cage = _breach_cage(c, h_admin)
    acct0 = _account(c, h_admin, cage["party_key"])

    r = c.post(
        "/api/forfeits",
        headers=h_clerk,
        json={"cage_id": cage["id"], "reason": "逾期未履约"},
    )
    assert r.status_code == 200
    order = r.json()
    # 只落影子罚没单，不得直接进正式罚没册
    assert order["status"] == "待复核"
    # 复核签字前：笼面可用不动
    cage1 = c.get(f"/api/cages/{cage['id']}", headers=h_admin).json()["cage"]
    assert cage1["available"] == cage["available"]
    # 账户冻结与余额不动
    acct1 = _account(c, h_admin, cage["party_key"])
    assert acct1["frozen"] == acct0["frozen"]
    assert acct1["balance"] == acct0["balance"]
    # 正式罚没册为零
    formal = c.get("/api/forfeits", headers=h_admin).json()["items"]
    assert not [f for f in formal if f["cage_id"] == cage["id"]]
    # 对账仍平衡（影子不计入已罚没）
    recon = c.get(f"/api/reconcile/{cage['cage_code']}", headers=h_admin).json()
    assert recon["balanced"] is True


def test_two_officers_same_cage_lock_contention():
    """双人同罚争用失败测：后到只留争用痕，不写任何业务行。"""
    c = TestClient(app)
    h_admin = _auth(c, "admin")
    h_clerk = _auth(c, "clerk")
    cage = _breach_cage(c, h_admin)
    lock_key = f"cage:{cage['id']}"

    # admin 先抢到笼锁
    assert (
        c.post("/api/locks/acquire", headers=h_admin, json={"lock_key": lock_key})
        .status_code
        == 200
    )
    # clerk 同罚同一只笼：只能吃到 409
    r = c.post(
        "/api/forfeits",
        headers=h_clerk,
        json={"cage_id": cage["id"], "reason": "争用测试"},
    )
    assert r.status_code == 409
    # 后到必须留下争用痕（业务回滚也不得丢痕）
    cont = c.get("/api/contention", headers=h_admin).json()["items"]
    assert any(
        x["lock_key"] == lock_key and x["actor"] == "clerk" for x in cont
    ), "后到争用痕丢失"
    # 后到不得写任何业务行：无影子单、无正式罚没
    orders_resp = c.get("/api/forfeit-orders", headers=h_admin)
    assert orders_resp.status_code == 200
    assert not [o for o in orders_resp.json()["items"] if o["cage_id"] == cage["id"]]
    formal = c.get("/api/forfeits", headers=h_admin).json()["items"]
    assert not [f for f in formal if f["cage_id"] == cage["id"]]


def _file_shadow(c: TestClient, h: dict, cage_id: int, **kw) -> dict:
    payload = {"cage_id": cage_id, "reason": kw.pop("reason", "逾期未履约")}
    payload.update(kw)
    r = c.post("/api/forfeits", headers=h, json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def test_shadow_review_promote_atomic():
    """复核通过 → 同一事务晋升：罚没册+扣笼+减账户+审计，一步不缺。"""
    c = TestClient(app)
    h_admin = _auth(c, "admin")
    h_clerk = _auth(c, "clerk")
    cage = _breach_cage(c, h_admin)
    acct0 = _account(c, h_admin, cage["party_key"])

    order = _file_shadow(c, h_clerk, cage["id"])
    # 复核岗（非登记人）通过 → 原子晋升
    rv = c.post(
        f"/api/forfeit-orders/{order['id']}/review",
        headers=h_admin,
        json={"decision": "通过", "note": "证据属实，同意罚没"},
    )
    assert rv.status_code == 200
    promoted = rv.json()
    assert promoted["status"] == "已晋升"
    assert promoted["reviewed_by"] == "admin"
    assert promoted["forfeit_id"]

    # 正式罚没册：整额已罚没
    formal = [
        f
        for f in c.get("/api/forfeits", headers=h_admin).json()["items"]
        if f["cage_id"] == cage["id"]
    ]
    assert len(formal) == 1
    assert formal[0]["status"] == "已罚没"
    assert formal[0]["amount"] == cage["available"]
    # 笼面：可用清零、状态已罚没
    cage1 = c.get(f"/api/cages/{cage['id']}", headers=h_admin).json()["cage"]
    assert cage1["available"] == 0
    assert cage1["status"] == "已罚没"
    # 账户：冻结与余额同步减
    acct1 = _account(c, h_admin, cage["party_key"])
    assert acct1["frozen"] == acct0["frozen"] - formal[0]["amount"]
    assert acct1["balance"] == acct0["balance"] - formal[0]["amount"]
    # 审计链完整：登记 → 晋升
    actions = {
        x["action"]
        for x in c.get("/api/audit/actions", headers=h_admin).json()["items"]
    }
    assert {"forfeit_order_create", "forfeit_promote", "cage_forfeit"} <= actions
    # 账实同源：对账平衡、当事人互证成立
    recon = c.get(f"/api/reconcile/{cage['cage_code']}", headers=h_admin).json()
    assert recon["balanced"] is True
    proof = c.get(f"/api/proof/{cage['party_key']}", headers=h_admin).json()
    assert proof["frozen_matches_live"] is True


def test_shadow_partial_amount_and_reject_voids_zero_change():
    """部分额影子单；复核驳回 → 影子作废，正式册与笼面零改。"""
    c = TestClient(app)
    h_admin = _auth(c, "admin")
    h_clerk = _auth(c, "clerk")
    cage = _breach_cage(c, h_admin, "CG-BREACH-002")
    acct0 = _account(c, h_admin, cage["party_key"])

    order = _file_shadow(c, h_clerk, cage["id"], amount=30000)
    assert order["amount"] == 30000
    rv = c.post(
        f"/api/forfeit-orders/{order['id']}/review",
        headers=h_admin,
        json={"decision": "驳回", "note": "证据不足"},
    )
    assert rv.status_code == 200
    assert rv.json()["status"] == "已作废"
    # 正式册与笼面零改
    formal = c.get("/api/forfeits", headers=h_admin).json()["items"]
    assert not [f for f in formal if f["cage_id"] == cage["id"]]
    cage1 = c.get(f"/api/cages/{cage['id']}", headers=h_admin).json()["cage"]
    assert cage1["available"] == cage["available"]
    assert cage1["status"] == "核验违约"
    acct1 = _account(c, h_admin, cage["party_key"])
    assert acct1["frozen"] == acct0["frozen"]
    assert acct1["balance"] == acct0["balance"]
    # 审计留作废痕
    actions = {
        x["action"]
        for x in c.get("/api/audit/actions", headers=h_admin).json()["items"]
    }
    assert "forfeit_order_void" in actions
    recon = c.get(f"/api/reconcile/{cage['cage_code']}", headers=h_admin).json()
    assert recon["balanced"] is True


def test_deviation_foreign_mode_and_self_review():
    """偏离纪要：异口径请求与复核不换人，都记罚没偏航并拒写。"""
    c = TestClient(app)
    h_admin = _auth(c, "admin")
    h_clerk = _auth(c, "clerk")
    cage = _breach_cage(c, h_admin)

    # 甲路径口径混用 → 罚没偏航 + 拒写
    r = c.post(
        "/api/forfeits",
        headers=h_clerk,
        json={
            "cage_id": cage["id"],
            "reason": "混用口径",
            "mode": "立即入罚没册整笔提交",
        },
    )
    assert r.status_code == 409
    # 登记影子后，登记人自己复核 → 罚没偏航 + 拒写，影子仍待复核
    order = _file_shadow(c, h_clerk, cage["id"])
    rv = c.post(
        f"/api/forfeit-orders/{order['id']}/review",
        headers=h_clerk,
        json={"decision": "通过", "note": "自己批自己"},
    )
    assert rv.status_code == 409
    orders = c.get("/api/forfeit-orders", headers=h_admin).json()["items"]
    mine = next(o for o in orders if o["id"] == order["id"])
    assert mine["status"] == "待复核"
    # 两笔偏航都落审计
    audits = c.get("/api/audit?limit=200", headers=h_admin).json()["items"]
    deviations = [a for a in audits if a["action"] == "罚没偏航"]
    assert len(deviations) >= 2
    # 账实零改
    formal = c.get("/api/forfeits", headers=h_admin).json()["items"]
    assert not [f for f in formal if f["cage_id"] == cage["id"]]
    recon = c.get(f"/api/reconcile/{cage['cage_code']}", headers=h_admin).json()
    assert recon["balanced"] is True


def test_one_pending_shadow_per_cage():
    """一笼一影：已有待复核影子单的笼不得重复登记。"""
    c = TestClient(app)
    h_admin = _auth(c, "admin")
    h_clerk = _auth(c, "clerk")
    cage = _breach_cage(c, h_admin)
    _file_shadow(c, h_clerk, cage["id"], amount=50000)
    r = c.post(
        "/api/forfeits",
        headers=h_admin,
        json={"cage_id": cage["id"], "amount": 20000, "reason": "重复登记"},
    )
    assert r.status_code == 409
    orders = c.get("/api/forfeit-orders", headers=h_admin).json()["items"]
    pending = [
        o for o in orders if o["cage_id"] == cage["id"] and o["status"] == "待复核"
    ]
    assert len(pending) == 1


def test_promote_failure_voids_shadow_zero_change():
    """晋升失败（笼面已漂移）→ 影子作废，正式册与笼面零改。"""
    c = TestClient(app)
    h_admin = _auth(c, "admin")
    h_clerk = _auth(c, "clerk")
    cage = _breach_cage(c, h_admin, "CG-BREACH-002")
    order = _file_shadow(c, h_clerk, cage["id"])  # 全额 90000
    # 笼面漂移：可用额被外部改小（事故场景）
    from db import connect

    with connect() as conn:
        conn.execute("UPDATE cages SET available=1000 WHERE id=?", (cage["id"],))
        conn.commit()
    rv = c.post(
        f"/api/forfeit-orders/{order['id']}/review",
        headers=h_admin,
        json={"decision": "通过", "note": "同意"},
    )
    assert rv.status_code == 409
    orders = c.get("/api/forfeit-orders", headers=h_admin).json()["items"]
    mine = next(o for o in orders if o["id"] == order["id"])
    assert mine["status"] == "已作废"
    # 正式册零改、笼面保持漂移后的样子（不被晋升污染）
    formal = c.get("/api/forfeits", headers=h_admin).json()["items"]
    assert not [f for f in formal if f["cage_id"] == cage["id"]]
    cage1 = c.get(f"/api/cages/{cage['id']}", headers=h_admin).json()["cage"]
    assert cage1["available"] == 1000


def test_five_pages_same_source():
    """五页同源：罚没/核验/入笼/对账/审计读同一库，口径一致。"""
    c = TestClient(app)
    h_admin = _auth(c, "admin")
    h_clerk = _auth(c, "clerk")
    cage = _breach_cage(c, h_admin)

    # 口径同源：页面从接口读锁定纪要
    doctrine = c.get("/api/forfeits/doctrine", headers=h_admin).json()
    assert doctrine["mode"] == "影子罚没待复核晋升"
    assert doctrine["locked"] is True

    # 登记 → 晋升一条，再看五页
    order = _file_shadow(c, h_clerk, cage["id"], amount=60000)
    rv = c.post(
        f"/api/forfeit-orders/{order['id']}/review",
        headers=h_admin,
        json={"decision": "通过", "note": "属实"},
    )
    assert rv.status_code == 200

    # 罚没页：正式册与影子单同接口可查
    formal = [
        f
        for f in c.get("/api/forfeits", headers=h_admin).json()["items"]
        if f["cage_id"] == cage["id"]
    ]
    assert len(formal) == 1 and formal[0]["amount"] == 60000
    # 入笼页：笼详情同时带正式罚没与影子单
    detail = c.get(f"/api/cages/{cage['id']}", headers=h_admin).json()
    assert len(detail["forfeits"]) == 1
    assert any(o["status"] == "已晋升" for o in detail["forfeit_orders"])
    # 核验页：核验列表同源带出违约核验
    atts = c.get("/api/attestations", headers=h_admin).json()["items"]
    assert any(a["cage_id"] == cage["id"] and a["outcome"] == "违约" for a in atts)
    # 对账页：罚没合计与正式册一致，公式平衡
    recon = c.get(f"/api/reconcile/{cage['cage_code']}", headers=h_admin).json()
    assert recon["forfeited"] == 60000
    assert recon["balanced"] is True
    # 审计页：登记、晋升、笼扣三步痕齐全
    audits = c.get("/api/audit?limit=200", headers=h_admin).json()["items"]
    acts = [a["action"] for a in audits if str(cage["cage_code"]) in (a["target"] or "")]
    assert "forfeit_order_create" in acts
    assert "forfeit_promote" in acts
    assert "cage_forfeit" in acts
    # 仪表盘已罚没合计与正式册同源
    dash = c.get("/api/dashboard", headers=h_admin).json()
    assert dash["forfeited_total"] == 60000
