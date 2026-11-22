# 保证金笼

> 遵守 `ops/底座生成前必读_效率与技术栈.md`（≥2000 行像产品）。  
> FastAPI + Vue 3 + SQLite；`docker compose up -d --build` 一键起。  
> **禁止同构换皮**：主链路不是「占位→确认入册」也不是「入轨→裁定赔付」也不是「业务窗裁切物化」。

## 骨架一句话

保证金先入笼冻结，履约核验通过才解笼拨付，违约进罚没册，笼代与拨付罚没对账。

## 基本信息

| 项 | 值 |
|----|-----|
| 产品名 | 保证金笼 |
| 目录 | `11-bondcage` |
| 接口 | http://127.0.0.1:11314 |
| 库 | `data/app.db` |
| 账号 | admin / clerk / viewer ，密码均为 123456（viewer 只读） |

## 厚度

| 项 | 内容 |
|----|------|
| 业务 LOC | **≥2900**（py+前端，不含 vue.global / Docker / README；自检约 3540） |
| 后端模块 | db / auth / locks / doctrine / bonds_svc / cages_svc / attest_svc / release_svc / forfeit_svc / reconcile_svc / audit_svc / topup_svc / split_svc |
| 前端视图 | 保证金户 / 入笼冻结 / 履约核验 / 解笼拨付 / 罚没册 / 对账互证 / 审计争用 / 增补拆笼空桩（8 页） |
| 空桩 | topup（增补入笼）、split（拆笼分拨）——前后端均有 |

## 主链路

开户入金 → 按合约入笼冻结（笼代）→ 履约核验通过 → 解笼拨付到收款方 / 违约罚没 → 笼代余额与拨付+罚没对账

## 互斥切口候选

- 解笼失败整笔回滚 vs 影子拨付后晋升
- 罚没立即入册 vs 影子罚没待复核晋升 —— **已锁定：影子罚没待复核晋升**（乙路径，2026-11-22 当日锁死，见 `docs/罚没复核纪要.md` 与 `backend/doctrine.py`；异口径请求记「罚没偏航」并拒写）
- 增补同笼扩代 vs 新笼桥接双写
- 拆笼整笔事务切分 vs 影子子笼后晋升

## 罚没复核（乙路径）

登记岗 `POST /api/forfeits` 只落影子罚没单（待复核），账实零动；复核岗（非登记人）
`POST /api/forfeit-orders/{id}/review`：通过则同一事务晋升（写罚没册+扣笼可用+
减账户冻结与余额+记审计），驳回或晋升失败则影子作废、正式册与笼面零改。
种子违约态笼（`fixtures/breach_cages.json`）与假绿事故夹具
（`scripts/repro_false_green.py`，在本机文件库跑通）可复现「复核未签字直罚」事故；
罚没/核验/入笼/对账/审计五页账实同源。

## 启动

```powershell
cd projects/11-bondcage
docker compose up -d --build
```

浏览器：http://127.0.0.1:11314/

本机开发：

```powershell
python -m pip install -r requirements.txt
python -m uvicorn app:app --app-dir backend --host 127.0.0.1 --port 10608
python -m pytest -q
```
