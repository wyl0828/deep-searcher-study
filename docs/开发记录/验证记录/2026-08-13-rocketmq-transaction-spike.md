# RocketMQ 事务入库技术验证

## 范围

本次仅验证 Ragent 对应的事务消息能力能否由 Apache RocketMQ 官方 Python 5.x 客户端稳定实现，
不引入 Outbox、自定义消息状态表或新的任务平台。

## 固定版本与环境

- Ragent 参考提交：`020e5c3de7d6`
- Apache RocketMQ Clients 源码：`3a8ec615c08a18d93adf564a64de37afd0cdc270`
- PyPI 客户端：`rocketmq-python-client==5.1.1`
- Broker/Proxy：官方镜像 `apache/rocketmq:5.3.2`
- Topic：预先创建为 `TRANSACTION` 类型
- 两个 SimpleConsumer：共享同一 Consumer Group

## 真实验证结果

| 场景 | 结果 |
| --- | --- |
| Half Message + 本地 Commit | 消息可被 Consumer 消费 |
| 本地 Rollback | 消息在观察窗口内不可见 |
| 不 ACK | `delivery_attempt` 从 1 增加到 2 |
| 重试后 ACK | 后续观察窗口不再投递 |
| Broker 事务回查 | `TransactionChecker.check` 被真实触发并 Commit |
| 长耗时处理 | 中途续租可见期后，第二 Consumer 未并发取得同一消息 |

SDK 要求消费不可见期至少 10 秒。正式配置必须覆盖一次正常处理时间；Worker 在处理期间按半个
可见期主动续租。客户端第一次源码隔离构建暴露构建期依赖声明缺陷，因此产品依赖使用已发布的
PyPI 5.1.1 包，不直接依赖 Git 源码目录。

## 一致性边界

事务消息只保护：

```text
Document/IngestJob queued -> processing
+
文档处理消息投递
```

它不保护对象上传、Consumer 内解析/Embedding/Milvus 写入，也不构成端到端 Exactly Once。
Broker 回查以 `job_id` 查询持久化任务状态；完成或死信任务收到重复消息时直接 ACK。
