# NovalClean · 小说 TXT 校准净化

把自己手上的盗版/转载 TXT 小说丢进来，自动去掉站点的广告水印、碎片话术、异常空格，
把章节标题规范化、把被写坏的章号复位，最后给出「成品 + 审计明细 + 校准报告」。

网页端：拖拽或点选 txt → 看结果表 → 下载成品/审计/zip/报告。
命令行：一个目录里的 txt 批量跑一遍。

> 本项目只提供清洗引擎，仓库里**不含任何真实小说文本**；`samples/sample.txt` 是脱敏样例。

---

## 快速开始

### 1. 直接用已发布的镜像（最省事）

镜像由 CI 在 main 分支上构建并推送到 Docker Hub：

```bash
docker pull gift95/novelclean:latest
docker run -d -p 8000:8000 -v "$PWD/work:/data/work" --name novel-txt-cleaner gift95/novelclean:latest
```

也可以拉某个提交对应的版本（标签是 7 位 commit sha）：`gift95/novelclean:sha-xxxxxxx`。

### 2. 本地直接跑（Python 3.10+）

```bash
pip install -r requirements.txt
python app.py            # 默认 http://127.0.0.1:8000
```

### 3. 自己构建镜像

```bash
docker build -t novel-txt-cleaner .
docker run -d -p 8000:8000 -v "$PWD/work:/data/work" --name novel-txt-cleaner novel-txt-cleaner
```

### 4. docker compose

```bash
docker compose up -d --build     # 本地构建
docker compose up -d             # 优先用已 pull 下来的 gift95/novelclean:latest
```

打开 `http://localhost:8000` 即可。健康检查走 `GET /healthz`（Dockerfile 里已配 `HEALTHCHECK`）。

---

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `PORT` | `8000` | 仅 `python app.py` 直跑时生效；gunicorn 用 `-b` 指定 |
| `WORK_DIR` | `<项目>/work` | 上传与产物的临时目录，容器内默认 `/data/work` |
| `MAX_FILE_BYTES` | `33554432`（32 MB） | 单个文件上限 |
| `MAX_TOTAL_BYTES` | `134217728`（128 MB） | 单次请求总量上限，同时也是 Flask `MAX_CONTENT_LENGTH` |
| `MAX_FILES` | `20` | 单次最多文件数 |
| `JOB_TTL` | `7200`（2 小时） | 任务目录保留时长，到时自动清理 |

## HTTP 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 单页界面（拖拽/多选、进度、结果表、下载入口） |
| GET | `/healthz` | 健康检查，返回 `ok` |
| POST | `/api/clean` | `multipart/form-data`，字段名 `files` 可重复；返回 JSON（`token` / `results` / `zip_url` / `report_url`） |
| GET | `/api/download/<token>/<name>` | 下载单本成品或审计（`.txt` 以附件形式下载） |
| GET | `/api/zip/<token>` | 打包下载本次全部产物 |

返回的 `token` 是 32 位 hex，下载链接只在这个 `token` 的目录里找文件；任务过期返回 404。

## 命令行

```bash
python cli.py 某目录 -o 输出目录      # 目录里所有 .txt 批量处理
python cli.py 单本.txt -o 输出目录
python cli.py 某目录 -o 输出目录 --quiet   # 只打一行汇总
```

每本产出两个文件：

- `书名_校对版.txt` —— 净化后的正文（保留原编码形态：UTF-8 输入保留 BOM 与否，GB18030 统一转 UTF-8）
- `书名_审计.txt` —— 逐条记录「删了什么 / 剥了什么 / 复位了哪个章号」，便于人工复核

## 引擎做了什么

`cleaner/` 是纯函数层，没有文件 IO，Web、CLI、测试共用同一套逻辑：

| 步骤 | 内容 |
|---|---|
| 1 | 编码识别（UTF-8 / GB18030）→ 去 BOM |
| 2 | 去广告：站点横幅、群号 URL、短水印话术（`请关注`、`分享地址失效`…）、碎片前缀、句中内嵌广告 |
| 3 | 章节标题识别与规范化：`第N章` / `N.标题` / `第N节`、编号补「第」、标记统一成「章」、中文数字转阿拉伯数字、标题去重号 |
| 3.5 | 伪章节整章删除：源站在真章前插的「正文全是水印」的同号假章 |
| 4 | 编号复位：仅在有 `nxt - L == prev + 1` 证据时动手（块长恰好补缺口，或整块平移到文件末尾） |
| 5 | 正文格式化：全角缩进补齐、清理水印空格、行尾数字尾巴、重复句号、行尾误用开引号 |
| 6 | 组装 + 校验：残留推广 / 残留空格 / 非规范缩进 / 重号 / 缺号 / 空章节 / 引号不平衡 |

**判定原则（改动引擎时请守住）**

- 标题行永不参与广告判定。
- 真实句子里夹的推广数字尾巴、拟声留白、长省略号、相邻重复段、原文错别字，一律保留。
- 章号没有证据不动：缺号只在审计里记录，不补号、不猜号。
- 水印判定用「固定话术 + 句内无标点 + 不超过 24 字」，不看空格密度（按密度删会误删正文）。

## 测试

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
```

覆盖：广告/水印/碎片各形态（含字符间插符号、句中广告、伪章节、横幅）、标题规范化、编号复位、
编码与 BOM、CLI 单文件/目录、Web 接口（`/api/clean`、下载、zip、上限 413）。

## 目录结构

```
app.py                  Flask 应用（单页 + 5 个接口）
cli.py                  命令行批处理
cleaner/rules.py        常量与判定函数（无 IO）
cleaner/pipeline.py     流水线：decode / clean_text / to_output_bytes / process
templates/index.html    单页界面
tests/                  pytest（含 Web 冒烟与 CLI）
samples/sample.txt      脱敏样例
Dockerfile               python:3.12-slim + gunicorn
docker-compose.yml      一键起服务
.github/workflows/ci.yml 3.10/3.12 矩阵跑测试 + 镜像构建
```

## 部署注意

`Dockerfile` 用的是 **单 worker + 4 threads**（`gunicorn -w 1 --threads 4`）。
产物写在 `WORK_DIR` 下的任务目录里，多 worker 会让「生成产物的进程」和「处理下载的进程」不是同一个，
下载链接就会落到没有该目录的进程上（表现为 404）。要横向扩容请在前面加共享存储，或改成单 worker 多实例 + 粘性会话。

## 免责

本工具用于清洗**你合法持有的**文本（自己录入、自己购买、公有领域作品等）。
请勿用它处理你没有权利使用的版权内容。
