# 小念 XiaoNian 部署模式

小念默认是"本地优先"的个人助理，但架构同时预留了服务器/企业部署路径（商业化）。

## 模式 A：本地个人版（默认，比赛演示用）
- 在用户自己的电脑上运行 daemon，直接调用本机算力，数据全本地。
- 启动：`python run.py` → 浏览器打开 `http://127.0.0.1:8765`。
- 适合：隐私优先的个人用户；比赛现场用自己的电脑演示。

## 模式 B：服务器/企业版（商业化预留）
- 把同一套 daemon 部署到服务器（容器化），多用户通过 Web 访问，按 `user_id` 隔离
  记忆与画像。
- 记忆层：把 Mem0 的向量库从本地 Qdrant 切换为 Qdrant/PG-vector 服务（改 `memory/store.py`
  的 `vector_store` 配置即可，无需改业务逻辑）。
- 模型层：多 provider 抽象不变，企业可换成自有/合规的 OpenAI 兼容网关。
- 安全：保留两步确认 + PII 脱敏 + 审计日志；企业可叠加 SSO / RBAC。

### 容器化（示意）
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 8765
CMD ["python", "run.py"]
```
```bash
docker build -t xiaonian .
docker run -p 8765:8765 --env-file .env xiaonian
```

## 模式 C：生态版（跨厂商比赛复用）
- 把"能力"做成技能（Agent Skills 开放标准），可接入小米米家 / 荣耀 / 华为等生态：
  各家终端能力封装为 Skill，小念核心不变。
- 模型可切到小米 MiMo（`XIAONIAN_PROVIDER=mimo`），作为小米生态加分项。

## 环境要求
- Python ≥ 3.10（推荐 3.12；Mem0 运行期需要 3.10+）。
- 见 `requirements.txt`。
