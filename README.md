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
| 业务 LOC | **≥2900**（py+前端，不含 vue.global / Docker / README；自检约 2984） |
| 后端模块 | db / auth / locks / bonds_svc / cages_svc / attest_svc / release_svc / forfeit_svc / forfeit_fate / reconcile_svc / audit_svc / topup_svc / split_svc |
| 前端视图 | 保证金户 / 入笼冻结 / 履约核验 / 解笼拨付 / 罚没册 / 对账互证 / 审计争用 / 增补拆笼空桩（8 页） |
| 空桩 | topup（增补入笼）、split（拆笼分拨）——前后端均有 |

## 主链路

开户入金 → 按合约入笼冻结（笼代）→ 履约核验通过 → 解笼拨付到收款方 / 违约建待复核罚没意图 → 复核通过入册扣笼 → 笼代余额与拨付+罚没对账

## 罚没复核命运（已锁死）

**本仓只走乙路径：复核通过后入册扣款。** 完整说明见 `docs/forfeit_review_fate.md`。

| 环节 | 笼面 / 账户 / 正式罚没册 |
|------|--------------------------|
| 核验违约 | 只置「核验违约」，零改 |
| 罚没写口 `POST /api/forfeits` | 只建「待复核」意图，`cages.available` 与 `bond_accounts.frozen/balance` 零改，正式册零增 |
| 复核通过 `POST /api/forfeit-intents/{id}/review` | 同一事务写正式罚没行并扣笼 |
| 复核驳回 | 意图置「已驳回」作废，三处零改，不自动退款 |

甲路径（核验即直入正式罚没册并扣笼）与第三路径均不采用；偏离命运说明的请求记审计「罚没偏航」并返回 409 拒写。

## 互斥切口候选

- 解笼失败整笔回滚 vs 影子拨付后晋升
- 罚没立即入册 vs 影子罚没待复核晋升 **（已选后者：乙路径，见上）**
- 增补同笼扩代 vs 新笼桥接双写
- 拆笼整笔事务切分 vs 影子子笼后晋升

## 罚没复核验收

```powershell
python -m pytest -q                 # 含三条先红后绿验收测
python tests/accident_probe.py --expect-fixed          # 直罚事故夹具：应无事故
python tests/accident_probe.py --tree <历史树> --expect-accident  # 历史树：应复现事故
python tests/live_five_surface_check.py http://127.0.0.1:10608   # 五页同源（需先起服务）
```

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
