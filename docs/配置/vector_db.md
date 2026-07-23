# 向量数据库配置

DeepSearcher 使用向量数据库存储和检索文档向量，从而实现高效的语义搜索。

## 📝 基本配置

```python
config.set_provider_config("vector_db", "(VectorDBName)", "(Arguments dict)")
```

当前支持 Milvus，包括 Milvus Lite 和 Zilliz Cloud。

## 🔍 Milvus 配置

```python
config.set_provider_config("vector_db", "Milvus", {"uri": "./milvus.db", "token": ""})
```

### 部署方式

??? example "使用 Milvus Lite 本地存储"

    将 `uri` 设置为本地文件（例如 `./milvus.db`），会自动使用 [Milvus Lite](https://milvus.io/docs/milvus_lite.md)，并把数据保存在该文件中。这种方式最适合开发环境和小规模数据集，不需要启动 Docker。

    ```python
    config.set_provider_config("vector_db", "Milvus", {"uri": "./milvus.db", "token": ""})
    ```

??? example "独立 Milvus 服务"

    数据量较大时，可以通过 [Docker 或 Kubernetes](https://milvus.io/docs/quickstart.md) 部署性能更强的 Milvus 服务，然后把服务地址传给 `uri`：

    ```python
    config.set_provider_config("vector_db", "Milvus", {"uri": "http://localhost:19530", "token": ""})
    ```

    还可设置 `user`、`password`、`secure` 等 Milvus 连接参数：

    ```python
    config.set_provider_config("vector_db", "Milvus", {"uri": "http://localhost:19530", "user": "<username>", "password": "<password>", "secure": True, "token": ""})
    ```

??? example "Zilliz Cloud（托管服务）"

    [Zilliz Cloud](https://zilliz.com/cloud) 提供完全托管的 Milvus 云服务。请根据控制台中的[公共访问地址和 API Key](https://docs.zilliz.com/docs/on-zilliz-cloud-console#free-cluster-details)设置 `uri` 与 `token`：

    ```python
    config.set_provider_config("vector_db", "Milvus", {
        "uri": "https://your-instance-id.api.gcp-us-west1.zillizcloud.com",
        "token": "your_api_key"
    })
    ```
