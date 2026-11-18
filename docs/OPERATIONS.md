# 运行维护

主服务端口与Compose保持一致。数据保存在records命名卷，应用代码不需要写入权限。

```sh
docker compose logs --tail=80 app
docker compose restart app
docker compose exec -T app pytest -q
docker compose down --remove-orphans
```

健康检查GET /health。失败输入返回原因码，结构性错误422，过大文件413，陈旧复核409。
文件内容最大4MB。批次先计算再原子保存，失败不留半条记录。证据CSV按原始行号定位。

## 发布到达日库区日账

`POST /api/ledger/publish`，请求体 `{"run_id": "<批次>"}` 或 `{"run_id": "<批次>", "days": [...]}`。不传 `days` 时使用该批次已算出的到达日账。`GET /api/ledger` 查看已入账到达日。

- 发布前整批校验：缺测站或到达日、日期非 `YYYY-MM-DD`、数值非有限、入库或出库为负、状态未知、同站同日重复，任一命中即整批422，旧版日账保持不变，不写半条。
- 已确认（完整）到达日为续跑水位线：水位线及之前的日期保留旧版值、不重复入账，也不能用重算值改写；只续跑其后的到达日。
- 状态为“缺测”的到达日可重跑补齐，补齐后转为已确认并推进水位线；同一天始终只有一行。
- 响应的 `posted`/`skipped`/`confirmed_through` 为本次入账、沿用和已确认水位线，`confirmed` 为各测站已确认到达日。
