'use client';

import { useMemo, useState } from 'react';
import ReactECharts from 'echarts-for-react';
import useSWR from 'swr';
import MacroPanel from './MacroPanel';
import VixPanel from './VixPanel';
import FactorPanel from './FactorPanel';

const API = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://127.0.0.1:8000';
const fetcher = (url: string) => fetch(url).then(async res => { if (!res.ok) throw new Error(`请求失败 (${res.status})`); return res.json(); });
type Item = Record<string, number | string | null>;
type Pool = { date: string; stocks: Item[]; industry_summary: { industry: string; count: number }[] };
const format = (v: unknown) => Number(v || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
const diffClass = (v: number) => v >= 0 ? 'up' : 'down';

function LineChart({ rows, field, color, name }: { rows: Item[]; field: string; color: string; name: string }) {
  const filtered = field === 'wind_micro_amount' ? rows.filter(row => Number(row[field]) > 0) : rows;
  return <ReactECharts style={{ height: '100%' }} option={{
    tooltip: { trigger: 'axis', backgroundColor: '#0f172a', borderColor: '#334155', textStyle: { color: '#f8fafc' } },
    grid: { left: 52, right: 28, top: 18, bottom: 62 }, xAxis: { type: 'category', data: filtered.map(x => x.trade_date), boundaryGap: false, axisLine: { lineStyle: { color: '#334155' } }, axisLabel: { color: '#94a3b8', fontSize: 10 } },
    yAxis: { type: 'value', scale: true, splitLine: { lineStyle: { color: '#1e293b' } }, axisLabel: { color: '#64748b' } },
    dataZoom: [
      { type: 'inside', xAxisIndex: 0, start: 65, end: 100, zoomOnMouseWheel: true, moveOnMouseWheel: true, moveOnMouseMove: true },
      { type: 'slider', xAxisIndex: 0, start: 65, end: 100, bottom: 12, height: 24, borderColor: '#475569', fillerColor: `${color}44`, handleStyle: { color }, textStyle: { color: '#94a3b8' } }
    ],
    series: [{ name, type: 'line', smooth: true, showSymbol: false, data: filtered.map(x => x[field]), itemStyle: { color }, lineStyle: { width: 2 }, areaStyle: { color: `${color}30` } }]
  }} />;
}

function MetricCard({ title, date, value, diff, rows, field, color, detail, wide = false }: { title: string; date: string; value: number; diff: number; rows: Item[]; field: string; color: string; detail?: string; wide?: boolean }) {
  const [open, setOpen] = useState(false);
  const high = useMemo(() => rows.reduce((best, row) => Number(row[field]) > Number(best[field]) ? row : best, rows[0] || {} as Item), [rows, field]);
  return <section className={`card ${wide ? 'wide' : ''} ${open ? 'expanded' : ''}`}>
    <div className="card-top"><span style={{ color }}>{title}</span><button className="button" onClick={() => setOpen(!open)}>{open ? '收起曲线' : '查看曲线'}</button></div>
    <div className="date">{date || '--'}</div><div className="number">{value ? format(value) : '--'} <span className="unit">亿</span></div>
    <div className={`diff ${diffClass(diff)}`}>较上日 {diff >= 0 ? '+' : ''}{format(diff)} 亿</div>
    <div className="hint"><span>{detail || '近一年历史数据'}</span><span>最高 {high ? format(high[field]) : '--'} ({String(high?.trade_date || '--')})</span></div>
    {open && <div className="chart"><p className="zoom-hint">滚轮缩放 / 拖动底部滑条查看历史区间</p><LineChart rows={rows} field={field} color={color} name={title} /></div>}
  </section>;
}

export default function Dashboard() {
  const [view, setView] = useState<'emotion' | 'price' | 'macro' | 'vix' | 'factor'>('emotion'); const [poolType, setPoolType] = useState<'limit_up' | 'limit_down'>('limit_up');
  const [industry, setIndustry] = useState<string | null>(null); const [index, setIndex] = useState('000001.SH');
  const overview = useSWR(`${API}/api/turnover/overview`, fetcher, { refreshInterval: 30000 });
  const history = useSWR(`${API}/api/turnover/history?days=365`, fetcher); const margin = useSWR(`${API}/api/margin`, fetcher);
  const up = useSWR(`${API}/api/limit_stocks?limit_type=limit_up`, fetcher, { refreshInterval: 30000 }); const down = useSWR(`${API}/api/limit_stocks?limit_type=limit_down`, fetcher, { refreshInterval: 30000 });
  const kline = useSWR(`${API}/api/index/kline?code=${index}`, fetcher);
  const treasury = useSWR(`${API}/api/macro/treasury?days=1095`, fetcher); const usdcnh = useSWR(`${API}/api/macro/price?symbol=USDCNH.FXCM&days=1095`, fetcher); const dollar = useSWR(`${API}/api/macro/price?symbol=USDOLLAR.FXCM&days=1095`, fetcher); const gold = useSWR(`${API}/api/macro/price?symbol=XAUUSD.FXCM&days=1095`, fetcher);
  const data = overview.data?.data || {}; const historyRows: Item[] = history.data?.list || []; const marginRows: Item[] = margin.data || [];
  const latestMargin = marginRows.at(-1) || {}; const prevMargin = marginRows.at(-2) || {}; const currentPool: Pool = (poolType === 'limit_up' ? up.data : down.data) || { date: '', stocks: [], industry_summary: [] };
  const selectedStocks = industry ? currentPool.stocks.filter(stock => stock.industry === industry) : currentPool.stocks;
  const refresh = () => [overview, history, margin, up, down, kline, treasury, usdcnh, dollar, gold].forEach(request => request.mutate());
  const errors = [overview.error, history.error, margin.error, up.error, down.error, kline.error, treasury.error, usdcnh.error, dollar.error, gold.error].filter(Boolean);
  const indices = [['000001.SH', '上证指数'], ['000688.SH', '科创50'], ['000015.SH', '红利指数'], ['399102.SZ', '创业板综'], ['8841423.WI', 'Wind微盘']];
  const treemap = { backgroundColor: 'transparent', tooltip: { formatter: '{b}: {c} 家', backgroundColor: '#0f172a', borderColor: '#334155', textStyle: { color: '#f8fafc' } }, series: [{ type: 'treemap', nodeClick: false, selectedMode: false, roam: false, breadcrumb: { show: false }, upperLabel: { show: false }, data: currentPool.industry_summary.filter(x => x.count > 0).map((x, position) => ({ name: x.industry, value: x.count, itemStyle: { color: poolType === 'limit_up' ? ['#be123c', '#e11d48', '#f43f5e'][position % 3] : ['#047857', '#059669', '#10b981'][position % 3], borderColor: industry === x.industry ? '#38bdf8' : '#020617', borderWidth: industry === x.industry ? 3 : 2, gapWidth: 2 } })), label: { show: true, formatter: '{b}\n{c}家', color: '#fff', fontSize: 11 }, emphasis: { disabled: true }, levels: [{ itemStyle: { borderColor: '#020617', borderWidth: 2, gapWidth: 2 } }] }] };
  const kRows: Item[] = kline.data?.list || []; const kOption = { tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } }, legend: { data: ['K线', '成交量'], textStyle: { color: '#94a3b8' } }, grid: [{ left: 55, right: 20, top: 40, height: '58%' }, { left: 55, right: 20, top: '75%', height: '15%' }], xAxis: [{ type: 'category', data: kRows.map(x => x.trade_date), axisLabel: { color: '#64748b' } }, { type: 'category', gridIndex: 1, data: kRows.map(x => x.trade_date), axisLabel: { show: false } }], yAxis: [{ scale: true, splitLine: { lineStyle: { color: '#1e293b' } }, axisLabel: { color: '#94a3b8' } }, { gridIndex: 1, scale: true, axisLabel: { show: false }, splitLine: { show: false } }], dataZoom: [{ type: 'inside', xAxisIndex: [0, 1], start: 55, end: 100 }, { type: 'slider', xAxisIndex: [0, 1], bottom: 8, height: 18 }], series: [{ name: 'K线', type: 'candlestick', data: kRows.map(x => [x.open, x.close, x.low, x.high]), itemStyle: { color: '#f43f5e', color0: '#10b981', borderColor: '#f43f5e', borderColor0: '#10b981' } }, { name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: kRows.map(x => ({ value: x.volume, itemStyle: { color: Number(x.close) >= Number(x.open) ? '#f43f5e' : '#10b981' } })) }] };
  return <main className="dashboard"><header className="header"><div><h1 className="title">A 股市场监控终端</h1><p className="subtitle">数据基准日：{currentPool.date || data.standard_date || '等待采集数据'}</p></div><div className="tabs"><button className={`button ${view === 'emotion' ? 'active' : ''}`} onClick={() => setView('emotion')}>市场情绪</button><button className={`button ${view === 'price' ? 'active' : ''}`} onClick={() => setView('price')}>价格走势 K 线</button><button className={`button ${view === 'vix' ? 'active' : ''}`} onClick={() => setView('vix')}>波动率走势</button><button className={`button ${view === 'factor' ? 'active' : ''}`} onClick={() => setView('factor')}>因子选股</button><button className={`button ${view === 'macro' ? 'active' : ''}`} onClick={() => setView('macro')}>全球宏观</button><button className="button" onClick={refresh}>刷新数据</button></div></header>
    {errors.length > 0 && <div className="error">部分数据暂不可用。请确认后端服务与采集器正在运行。</div>}
    {view === 'emotion' ? <div className="layout"><div className="metrics"><div className="metric-grid">
      <MetricCard wide title="融资融券余额" date={String(latestMargin.trade_date || '')} value={Number(latestMargin.rzrqye)} diff={Number(latestMargin.rzrqye) - Number(prevMargin.rzrqye)} rows={marginRows} field="rzrqye" color="#f59e0b" detail="数据源：交易所 / Tushare" />
      <MetricCard wide title="两市总成交额" date={String(data.standard_date || '')} value={Number(data.total_amount)} diff={Number(data.total_diff)} rows={historyRows} field="total_amount" color="#3b82f6" detail={`沪 ${format(data.sh_amount)} 亿 ｜ 深 ${format(data.sz_amount)} 亿`} />
      <MetricCard title="创业板综成交额" date={String(data.standard_date || '')} value={Number(data.cyb_amount)} diff={Number(data.cyb_diff)} rows={historyRows} field="cyb_amount" color="#06b6d4" />
      <MetricCard title="科创50成交额" date={String(data.standard_date || '')} value={Number(data.kc50_amount)} diff={Number(data.kc50_diff)} rows={historyRows} field="kc50_amount" color="#a855f7" />
      <MetricCard title="红利指数成交额" date={String(data.standard_date || '')} value={Number(data.hl_amount)} diff={Number(data.hl_diff)} rows={historyRows} field="hl_amount" color="#f59e0b" />
      <MetricCard title="Wind 微盘成交额" date={String(data.wind_micro_date || '')} value={Number(data.wind_micro_amount)} diff={Number(data.wind_micro_diff)} rows={historyRows} field="wind_micro_amount" color="#94a3b8" />
    </div></div><section className="pool"><div className="pool-head"><button className={`button ${poolType === 'limit_up' ? 'active' : ''}`} onClick={() => { setPoolType('limit_up'); setIndustry(null); }}>涨停股池 ({up.data?.stocks?.length || 0})</button><button className={`button ${poolType === 'limit_down' ? 'active' : ''}`} onClick={() => { setPoolType('limit_down'); setIndustry(null); }}>跌停股池 ({down.data?.stocks?.length || 0})</button>{industry && <button className="filter" onClick={() => setIndustry(null)}>取消行业筛选：{industry}</button>}</div>
      <div className="treemap"><ReactECharts option={treemap} notMerge style={{ height: '100%' }} onEvents={{ click: (p: { name?: string }) => p.name && setIndustry(industry === p.name ? null : p.name) }} /></div>
      <div className="table-wrap"><table><thead><tr><th>代码</th><th>名称</th><th>最新价</th><th>涨跌幅</th><th>连板</th><th>行业</th><th>首封时间</th></tr></thead><tbody>{selectedStocks.map((stock, i) => { const stockPage = `https://stockpage.10jqka.com.cn/${stock.stock_code}/`; return <tr key={`${stock.stock_code}-${i}`}><td><a href={stockPage} target="_blank" rel="noreferrer">{stock.stock_code}</a></td><td><a href={stockPage} target="_blank" rel="noreferrer">{stock.stock_name}</a></td><td>{format(stock.last_price)}</td><td className={poolType === 'limit_up' ? 'up' : 'down'}>{Number(stock.change_pct) > 0 ? '+' : ''}{format(stock.change_pct)}%</td><td>{stock.limit_num} 连板</td><td>{stock.industry}</td><td>{stock.first_limit_time || '--'}</td></tr>; })}{!selectedStocks.length && <tr><td colSpan={7} className="empty">暂无相关股票数据</td></tr>}</tbody></table></div>
    </section></div> : view === 'price' ? <section className="price"><div className="index-tabs">{indices.map(([code, name]) => <button key={code} className={`button ${index === code ? 'active' : ''}`} onClick={() => setIndex(code)}>{name}</button>)}</div><div className="price-chart"><ReactECharts option={kOption} style={{ height: '100%' }} /></div></section> : view === 'vix' ? <VixPanel /> : view === 'factor' ? <FactorPanel /> : <MacroPanel treasury={treasury.data?.list || []} usdcnh={usdcnh.data?.list || []} dollar={dollar.data?.list || []} gold={gold.data?.list || []} />}</main>;
}
