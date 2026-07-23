# 🌐 部署

本指南介绍如何将 DeepSearcher 部署为 Web 服务。

## ⚙️ 配置模块

可以通过修改配置文件设置各项参数：

```yaml
# config.yaml - https://github.com/zilliztech/deep-searcher/blob/main/config.yaml
llm:
  provider: "OpenAI"
  api_key: "your_openai_api_key_here"
  # 其他配置项……
```

> **重要：** 请在 YAML 文件的 `llm` 部分设置 `OPENAI_API_KEY`。

## 🚀 启动服务

主程序会启动 FastAPI 服务，默认地址为 `localhost:8000`：

```shell
python main.py
```

启动成功后，终端会显示服务正在运行。

## 🔍 通过浏览器访问

1. 在浏览器中打开 [http://localhost:8000/docs](http://localhost:8000/docs)
2. Swagger UI 会列出所有可用接口
3. 点击接口中的“Try it out”按钮
4. 填写必要参数并执行请求

通过这份交互式接口文档，可以直接测试 DeepSearcher 的 API 功能。

## 🐳 使用 Docker 部署

也可以使用 Docker 简化环境配置和管理。

### 构建 Docker 镜像

在项目根目录执行：

```shell
docker build -t deepsearcher:latest .
```

该命令使用当前目录的 Dockerfile 构建镜像，并将其标记为 `deepsearcher:latest`。

### 运行容器

```shell
docker run -p 8000:8000 \
  -e OPENAI_API_KEY=your_openai_api_key \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  -v $(pwd)/deepsearcher/config.yaml:/app/deepsearcher/config.yaml \
  deepsearcher:latest
```

该命令会：

- 将容器的 8000 端口映射到主机的 8000 端口
- 设置 `OPENAI_API_KEY` 环境变量
- 挂载本地 `data`、`logs` 和配置文件
- 运行 `deepsearcher:latest` 镜像

> **注意：** 请把 `your_openai_api_key` 替换为实际的 OpenAI API Key；如果使用其他模型，也要设置相应的环境变量。
