#!/usr/bin/env python3
"""假绿事故夹具：种子违约态笼上的「不可撤回直罚」复现。

夹具在本机文件库跑通：
    python scripts/repro_false_green.py                 # 落 data/false_green_repro.db
    python scripts/repro_false_green.py --db /tmp/x.db  # 指定其它本机文件库

不碰 data/app.db（库路径不变）。夹具流程：
  1. 以 fixtures/breach_cages.json 种子违约态笼（CG-BREACH-001，核验违约）；
  2. 复核岗未签字，登记岗直接 POST /api/forfeits；
  3. 核对笼面、账户、正式罚没册、影子单、对账：
     - 金额已出笼且无任何复核痕 → 对账仍绿 = 假绿事故（INCIDENT-REPRODUCED）；
     - 直罚被拦成影子单、账实未动 → 已修复（BLOCKED-BY-SHADOW-REVIEW）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import db  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="假绿事故夹具：不可撤回直罚复现")
    ap.add_argument("--db", default=str(ROOT / "data" / "false_green_repro.db"))
    args = ap.parse_args()
    target = Path(args.db)
    if target.exists():
        target.unlink()
    db.DB_PATH = target
    db.init_db()

    from fastapi.testclient import TestClient

    from app import app

    c = TestClient(app)
    tok = c.post(
        "/api/login", json={"username": "admin", "password": "123456"}
    ).json()["token"]
    h = {"Authorization": "Bearer " + tok}

    cages = c.get("/api/cages?status=核验违约", headers=h).json()["items"]
    cage = next(x for x in cages if x["cage_code"] == "CG-BREACH-001")
    acct0 = c.get(f"/api/bonds/{cage['party_key']}", headers=h).json()["account"]
    print(f"[夹具] 库文件 {target}")
    print(
        f"[夹具] 违约态笼 {cage['cage_code']} 可用 {cage['available']}；"
        f"账户冻结 {acct0['frozen']} 余额 {acct0['balance']}"
    )

    # 复核岗未签字，直接罚没
    r = c.post(
        "/api/forfeits",
        headers=h,
        json={"cage_id": cage["id"], "reason": "假绿事故复现：复核未签字直罚"},
    )
    print(f"[夹具] POST /api/forfeits → {r.status_code} {r.json()}")

    cage1 = c.get(f"/api/cages/{cage['id']}", headers=h).json()["cage"]
    acct1 = c.get(f"/api/bonds/{cage['party_key']}", headers=h).json()["account"]
    recon = c.get(f"/api/reconcile/{cage['cage_code']}", headers=h).json()
    formal = [
        f
        for f in c.get("/api/forfeits", headers=h).json()["items"]
        if f["cage_id"] == cage["id"]
    ]
    orders_resp = c.get("/api/forfeit-orders", headers=h)
    orders = (
        orders_resp.json().get("items", []) if orders_resp.status_code == 200 else []
    )
    reviewed = [
        o for o in orders if o.get("cage_id") == cage["id"] and o.get("reviewed_by")
    ]

    money_moved = (
        abs(float(cage1["available"]) - float(cage["available"])) > 1e-9
        or abs(float(acct1["frozen"]) - float(acct0["frozen"])) > 1e-9
    )
    if r.status_code == 200 and money_moved and not reviewed:
        print("【假绿】事故复现：复核岗未签字，金额已出笼，正式罚没册已记账：")
        print(
            f"【假绿】笼可用 {cage['available']} → {cage1['available']}；"
            f"账户冻结 {acct0['frozen']} → {acct1['frozen']}；"
            f"正式罚没 {len(formal)} 笔，复核痕 0 条"
        )
        print(f"【假绿】对账仍平衡={recon['balanced']}（账面绿色掩盖流程缺口，出事难撤回）")
        print("VERDICT: INCIDENT-REPRODUCED")
    elif r.status_code == 200 and not money_moved:
        body = r.json()
        print(
            f"【已修复】直罚被拦成影子单 {body.get('order_ref')} "
            f"状态={body.get('status')}；笼面与账户零改，对账平衡={recon['balanced']}，"
            "待复核岗签字后才原子晋升。"
        )
        print("VERDICT: BLOCKED-BY-SHADOW-REVIEW")
    else:
        print(f"【已修复】直罚被拒：{r.status_code} {r.json()}")
        print("VERDICT: BLOCKED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
