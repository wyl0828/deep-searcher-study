# DeepSearcher Study 文档

本目录存放 DeepSearcher Study 的项目文档，文档站点由 MkDocs 构建。该项目是
[DeepSearcher](https://github.com/zilliztech/deep-searcher) 的独立 Apache-2.0 衍生项目；来源、许可与
维护边界请参见仓库根目录的 `UPSTREAM.md`。

## 环境准备

1. 安装 MkDocs 及所需插件：

```bash
pip install mkdocs mkdocs-material mkdocs-jupyter pymdown-extensions
```

2. 克隆本仓库：

```bash
git clone https://github.com/wyl0828/deep-searcher-study.git
cd deep-searcher-study
```

## 本地开发

在本地启动文档站点：

```bash
mkdocs serve
```

命令会在 http://127.0.0.1:8000/ 启动本地服务，可通过浏览器预览文档。

## 构建

构建静态站点：

```bash
mkdocs build
```

生成的静态文件位于 `site` 目录。

## 部署

代码推送到主分支后，GitHub Actions 会自动部署文档站点。
