#!/usr/bin/env python3
"""对正在运行的本机服务做五页同源验收（真实 HTTP）。

用法：
    python tests/live_five_surface_check.py [http://127.0.0.1:10608]

会直接用种子违约态笼建一笔待复核意图，再走复核通过入册，逐页核对
罚没页 / 核验页 / 入笼页 / 对账页 / 审计页是否同源。仅用于本机夹具验收。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:10608").rstrip("/")
CAGE_CODE = "CG-P-EAST-001"


def _pick_cage(tok: str) -> dict:
    """优先用种子违约态笼；种子笼已罚没时另开一只专用笼，保证夹具可重复跑。"""
    _, cages = call("GET", "/api/cages", tok)
    for c in cages["items"]:
        if c["cage_code"] == CAGE_CODE and c["status"] in ("已冻结", "核验违约"):
            return c
    for c in cages["items"]:
        if c["status"] in ("已冻结", "核验违约"):
            return c
    stamp = int(time.time())
    party = f"P-FIVESURF-{stamp}"
    code, _ = call(
        "POST",
        "/api/bonds",
        tok,
        {"party_key": party, "party_name": "五页同源夹具", "initial_deposit": 50000},
    )
    assert code == 200
    code, cage = call(
        "POST",
        "/api/cages/freeze",
        tok,
        {
            "party_key": party,
            "contract_no": f"HT-FIVESURF-{stamp}",
            "amount": 50000,
            "beneficiary": "五页同源受益人",
            "cage_code": f"CG-FIVESURF-{stamp}",
        },
    )
    assert code == 200, cage
    return cage


def call(method: str, path: str, token: str | None = None, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "null")


def main() -> int:
    code, login = call("POST", "/api/login", body={"username": "admin", "password": "123456"})
    assert code == 200, login
    tok = login["token"]

    code, fate = call("GET", "/api/forfeit-fate", tok)
    assert code == 200, fate
    print("命运说明:", fate["path"], fate["locked_on"], fate["note"]["path_name"])

    cage = _pick_cage(tok)
    cage_code = cage["cage_code"]
    if cage["status"] == "已冻结":
        code, att = call(
            "POST",
            "/api/attestations",
            tok,
            {"cage_id": cage["id"], "outcome": "违约", "evidence": "五页同源夹具"},
        )
        assert code == 200, att
        print("核验违约:", att["outcome"])
    else:
        print("笼状态:", cage["status"])

    code, cands = call("GET", "/api/cages", tok)
    cage = next(c for c in cands["items"] if c["id"] == cage["id"])
    before_avail = cage["available"]
    before_frozen = cage["available"]  # 同额冻结

    code, intent = call(
        "POST", "/api/forfeits", tok, {"cage_id": cage["id"], "reason": "五页同源夹具"}
    )
    assert code == 200, intent
    print("建意图:", intent["intent_ref"], intent["status"], "金额", intent["amount"])

    # 1 罚没页
    _, intents = call("GET", "/api/forfeit-intents", tok)
    mine = [i for i in intents["items"] if i["id"] == intent["id"]][0]
    assert mine["status"] == "待复核"
    _, formal0 = call("GET", "/api/forfeits", tok)
    assert not [f for f in formal0["items"] if f["cage_id"] == cage["id"]]
    print("罚没页: 意图待复核，正式册零行 ✓")

    # 2 核验页
    _, atts = call("GET", "/api/attestations", tok)
    a = [x for x in atts["items"] if x["cage_id"] == cage["id"]][0]
    assert a["pending_intents"] == 1 and a["formal_forfeits"] == 0, a
    print("核验页: 待复核意图 1 / 正式罚没 0 ✓")

    # 3 入笼页
    _, cs = call("GET", "/api/cages", tok)
    c = [x for x in cs["items"] if x["id"] == cage["id"]][0]
    assert c["pending_intents"] == 1 and c["available"] == before_avail, c
    print("入笼页: 笼可用未动", c["available"], "/ 待复核意图", c["pending_intents"], "✓")

    # 4 对账页
    _, rec = call("GET", f"/api/reconcile/{cage_code}", tok)
    assert rec["balanced"] is True and rec["forfeited"] == 0, rec
    assert rec["pending_intent_amount"] == before_avail, rec
    print("对账页: 平衡", rec["balanced"], "/ 待复核不计入", rec["pending_intent_amount"], "✓")

    # 5 审计页
    _, chain = call("GET", "/api/audit/forfeit-chain", tok)
    row = [i for i in chain["items"] if i["id"] == intent["id"]][0]
    assert row["status"] == "待复核" and row["consistent"] is True, row
    assert chain["summary"]["fate_drift"] == 0
    print("审计页: 链路同源，偏航 0 ✓")

    # 复核通过：五页同额同态
    code, rev = call(
        "POST",
        f"/api/forfeit-intents/{intent['id']}/review",
        tok,
        {"decision": "通过", "note": "五页同源复核通过"},
    )
    assert code == 200, rev
    fid, amt = rev["forfeit"]["id"], rev["forfeit"]["amount"]
    print("复核通过入册:", rev["forfeit"]["forfeit_ref"], amt)

    _, formal = call("GET", "/api/forfeits", tok)
    assert [f["id"] for f in formal["items"] if f["cage_id"] == cage["id"]] == [fid]
    _, atts = call("GET", "/api/attestations", tok)
    a = [x for x in atts["items"] if x["cage_id"] == cage["id"]][0]
    assert a["formal_forfeits"] == 1 and a["pending_intents"] == 0
    _, cs = call("GET", "/api/cages", tok)
    c = [x for x in cs["items"] if x["id"] == cage["id"]][0]
    assert c["formal_forfeits"] == 1 and c["available"] == round(before_avail - amt, 6)
    _, rec = call("GET", f"/api/reconcile/{cage_code}", tok)
    assert rec["balanced"] is True and rec["forfeited"] == amt
    assert rec["account_frozen"] == round(before_frozen - amt, 6)
    _, chain = call("GET", "/api/audit/forfeit-chain", tok)
    row = [i for i in chain["items"] if i["id"] == intent["id"]][0]
    assert row["forfeit_id"] == fid and row["consistent"] is True
    print("五页同态: 罚没/核验/入笼/对账/审计 同额同源 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
