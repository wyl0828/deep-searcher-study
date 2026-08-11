# ADR 0009：Document Version Family

## 状态

Accepted，首版实现于 2026-08-11。

## 问题

业务日期只能说明一份文档何时发布、生效或失效，不能说明两份资料是否属于同一制度的不同版本。
如果把最终 Evidence 中所有文档放在一起比较，“2026 销售政策”可能错误地让“2025 差旅制度”的
回答被判为旧版；如果仅按文件名或标题相似度自动归组，又会把模型猜测变成权限与答案策略的依据。

## 决策

引入显式 `version_family` 和 `version_family_source`。系列标识经 NFKC、大小写折叠、空白/连字符
归一化后保存，最长 128 字符；仅接受字母数字和 `-_.:`，不保存空值。可信来源固定为
`user_declared`、`connector`、`admin_verified`，未知字段和不可信来源在进入索引前拒绝。

字段贯穿产品 Document、上传/治理 API、持久 Worker、Core Loader、Chunk、Collection Manifest、
RetrievalResult、Citation 快照与 Trust Provenance。修改已就绪文档的系列会创建新的持久化入库任务，
确保产品库、向量元数据和 Manifest 不出现静默分叉。迁移 `20260811_0013` 为已有数据库增加可空字段，
旧文档不会被猜测性回填。

Freshness 的 `latest_effective/latest_published` 要求一个 Claim 引用的 Evidence 恰好属于一个可信系列：

- 没有系列：`FRESHNESS_VERSION_FAMILY_MISSING`，进入 unknown；
- 引用多个系列：`FRESHNESS_VERSION_FAMILY_AMBIGUOUS`，进入 unknown；
- 单一系列：只比较最终 Evidence 快照内相同系列的知识库 Evidence；
- 其他系列：不参与排序，也不构成时效覆盖缺失。

`current` 只检查被引用资料自身是否有效，不要求系列；普通事实问题不执行系列规则。

## 安全与可观测性

系列身份定义比较宇宙，但不进入用户问题或模型推断。Grounding Prompt 只接收经过白名单验证的属性，
并明确禁止猜测系列。Provenance Builder 2.3 保存系列身份的 SHA-256 指纹和 bound 状态，不暴露系列
原值；系列变化会改变 Evidence snapshot 与总摘要。Trust Metric 1.6 分别输出知识库 Evidence 的系列
绑定数量、分母和覆盖率，前端 Trust 谱系展示本次绑定数量。

Trust Consistency Gold v1.6 用系列缺失、无关新系列、引用系列歧义三类新增样本固定 fail-closed 与
隔离语义；Trust Provenance 的 23 项不变量固定系列去密和变化检测。

## 已知边界

- 系列由用户或连接器声明；首版不自动判断“这两份文档是不是同一制度”。
- 该规则只证明最终 Evidence 快照内同系列最新，不证明检索召回或连接器同步完整。
- 一个文件只能声明一个系列；包含多套独立制度的合订文档应在连接器或解析层拆分。
- Knowledge Health 后续需要检测同系列重复版本、时间重叠、断代、冲突和孤立文档。
