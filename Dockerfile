FROM python:3.12-slim

WORKDIR /app

# 依赖均有 manylinux 预编译 wheel，无需 gcc 等编译工具，去除以减小镜像体积
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 仅安装生产依赖（测试/开发依赖不进入镜像）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.ustc.edu.cn/pypi/simple/ --trusted-host mirrors.ustc.edu.cn

# 复制项目代码
COPY . .

# 运行时数据与日志目录（由 docker-compose 卷挂载持久化）
RUN mkdir -p /app/data /app/logs

EXPOSE 9095

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9095"]
