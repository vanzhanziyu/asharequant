# A 股行情监控面板

本项目由 Python 数据服务和 Next.js 前端组成，提供两市成交额、两融余额、涨跌停股池、行业热力图、指数 K 线、全球宏观、ETF 标准 VIX 与因子选股看板。

## 配置与数据边界

将 Tushare Token 保存在 `backend/.env`，内容为 `TUSHARE_TOKEN=...`。该文件、SQLite 数据库、缓存和运行日志均已排除在 Git 之外；云端服务器维护独立的 `.env` 和市场数据，不应被代码部署覆盖。

## 快速运行

在 PyCharm Terminal 或 macOS Terminal 中，进入项目目录后执行一条命令：

```bash
bash start_dashboard.sh
```

该命令会同时启动 API、市场数据采集器、Wind 微盘指数采集器和前端，并转入后台运行；关闭 Terminal 后服务仍会继续。浏览器打开 http://localhost:3000 。日志位于 `.runtime-logs/`。

停止全部服务：

```bash
bash stop_dashboard.sh
```

重启全部服务：

```bash
bash restart_dashboard.sh
```

## 手动运行

如需分别启动服务，可按以下方式运行：

```bash
cd backend
pip install -r requirements.txt
python collector.py --once
python wind_collector.py --once
python vix_collector.py --history  # 首次回补近三年 VIX，可中断后续跑
python main.py
```

3. 新开一个终端：

```bash
cd frontend
npm install
npm run dev
```

4. 浏览器打开 http://localhost:3000 。API 文档在 http://127.0.0.1:8000/docs 。

## 常驻采集

首次归档完成后，分别执行 `python collector.py`、`python wind_collector.py`、`python vix_collector.py` 与 `python factor_collector.py`，即可按交易时间自动更新。因子采集器会分批回补近三年行情、分红和 EBITDA 数据，完成后自动生成选股快照与收益率序列。VIX 采集器会在交易日 15:30 后每半小时尝试刷新最新交易日；首次运行时会用 Tushare 回补近三年数据。
