'use client';

import React from 'react';
import ReactECharts from 'echarts-for-react';
import useSWR from 'swr';
import styles from './FactorPanel.module.css';

// Use the local FastAPI port only while developing locally.  The deployed
// dashboard uses same-origin /api requests through the reverse proxy.
const API = process.env.NEXT_PUBLIC_API_BASE_URL ||
  (typeof window !== 'undefined' && ['localhost', '127.0.0.1'].includes(window.location.hostname)
    ? 'http://127.0.0.1:8000'
    : '');
const fetcher = (url: string) => fetch(url).then(async response => {
  if (!response.ok) throw new Error(`请求失败 (${response.status})`);
  return response.json();
});
const factors = [
  ['market_cap', '市值因子', '按总市值由高至低排名'],
  ['dividend_yield', '纯红利因子', '三年平均股息率由高至低排名'],
  ['ebitda_cagr', 'EBITDA 增速', '三年 EBITDA 年复合增速由高至低排名'],
] as const;
type FactorName = typeof factors[number][0];
const format = (value: unknown, digits = 2) => value === null || value === undefined ? '--' : Number(value).toLocaleString('zh-CN', { maximumFractionDigits: digits });

export default function FactorPanel() {
  const [page, setPage] = React.useState<'pool' | 'performance'>('pool');
  const [factor, setFactor] = React.useState<FactorName>('market_cap');
  const [rankInput, setRankInput] = React.useState('100');
  const [rank, setRank] = React.useState(100);
  const [industry, setIndustry] = React.useState<string | null>(null);
  // New samples arrive while the first full-universe backfill is running.
  // Keep the page live so visitors do not have to reload it manually.
  const pool = useSWR(`${API}/api/factors/pool?factor=${factor}&rank=${rank}`, fetcher, { refreshInterval: 30_000, revalidateOnFocus: true });
  const performance = useSWR(`${API}/api/factors/performance?factor=${factor}&days=1095`, fetcher, { refreshInterval: 30_000, revalidateOnFocus: true });
  const current = factors.find(([key]) => key === factor) || factors[0];
  const stocks = pool.data?.stocks || [];
  const filteredStocks = industry ? stocks.filter((stock: Record<string, string>) => stock.industry === industry) : stocks;
  const rows = performance.data?.list || [];
  const progress = pool.data?.progress || performance.data?.progress;
  const available = Number(pool.data?.universe_count || 0);
  const sourceReady = factor === 'market_cap' ? progress?.universe || 0 : factor === 'dividend_yield' ? progress?.dividend_ready || 0 : progress?.financial_ready || 0;
  const sourceLabel = factor === 'market_cap' ? '沪深非 ST 股票' : factor === 'dividend_yield' ? '分红资料' : '财务资料';
  const currentStatus = `已展示 ${available.toLocaleString('zh-CN')} 只可用样本 · ${sourceLabel} ${Number(sourceReady).toLocaleString('zh-CN')}/${Number(progress?.universe || 0).toLocaleString('zh-CN')} 只`;
  const submitRank = (event: React.FormEvent) => {
    event.preventDefault();
    const value = Math.trunc(Number(rankInput));
    if (Number.isFinite(value) && value !== 0) setRank(Math.max(-5000, Math.min(5000, value)));
  };
  const treemap = {
    backgroundColor: 'transparent',
    tooltip: { formatter: '{b}: {c} 家', backgroundColor: '#0f172a', borderColor: '#334155', textStyle: { color: '#f8fafc' } },
    series: [{ type: 'treemap', nodeClick: false, selectedMode: false, roam: false, breadcrumb: { show: false }, upperLabel: { show: false },
      data: (pool.data?.industry_summary || []).filter((item: { count: number }) => item.count > 0).map((item: { industry: string; count: number }, index: number) => ({
        name: item.industry, value: item.count,
        itemStyle: { color: ['#1d4ed8', '#0e7490', '#4f46e5', '#7c3aed', '#047857', '#b45309'][index % 6], borderColor: industry === item.industry ? '#f8fafc' : '#020617', borderWidth: industry === item.industry ? 3 : 2, gapWidth: 2 },
      })), label: { show: true, formatter: '{b}\n{c}家', color: '#fff', fontSize: 11 }, emphasis: { disabled: true }, levels: [{ itemStyle: { borderColor: '#020617', borderWidth: 2, gapWidth: 2 } }],
    }],
  };
  const performanceOption = {
    animation: false,
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross', label: { backgroundColor: '#334155' } }, backgroundColor: '#0f172a', borderColor: '#334155', textStyle: { color: '#f8fafc' }, formatter: (items: { axisValue: string; dataIndex: number; data: number }[]) => {
      const item = items[0]; const row = rows[item?.dataIndex];
      return !item || !row ? '' : `${item.axisValue}<br/><b>累计收益：${format(item.data)}%</b><br/>当日组合收益：${format(row.daily_return_pct)}%<br/>前一日选股：${row.signal_date}<br/>有效持仓：${row.holding_count} 只`;
    } },
    grid: { left: 58, right: 26, top: 24, bottom: 66 },
    xAxis: { type: 'category', data: rows.map((row: { trade_date: string }) => row.trade_date), boundaryGap: false, axisLine: { lineStyle: { color: '#334155' } }, axisLabel: { color: '#94a3b8', fontSize: 10 } },
    yAxis: { type: 'value', scale: true, name: '累计收益 (%)', nameTextStyle: { color: '#94a3b8' }, splitLine: { lineStyle: { color: '#1e293b' } }, axisLabel: { color: '#94a3b8', formatter: '{value}%' } },
    dataZoom: [{ type: 'inside', xAxisIndex: 0, start: 0, end: 100, zoomOnMouseWheel: true, moveOnMouseWheel: true, moveOnMouseMove: true }, { type: 'slider', xAxisIndex: 0, start: 0, end: 100, bottom: 12, height: 22, borderColor: '#475569', fillerColor: '#38bdf844', handleStyle: { color: '#38bdf8' }, textStyle: { color: '#94a3b8' } }],
    series: [{ name: '累计收益', type: 'line', showSymbol: false, data: rows.map((row: { nav: number }) => (Number(row.nav) - 1) * 100), lineStyle: { color: '#38bdf8', width: 2.2 }, areaStyle: { color: '#38bdf822' } }],
  };
  return <section className={styles.panel}>
    <div className={styles.topbar}><div className={styles.pageTabs}><button className={`${styles.pageButton} ${page === 'pool' ? styles.active : ''}`} onClick={() => setPage('pool')}>因子股票池</button><button className={`${styles.pageButton} ${page === 'performance' ? styles.active : ''}`} onClick={() => setPage('performance')}>因子收益率走势</button></div><span className={styles.status}>{currentStatus}</span></div>
    <div className={styles.factorTabs}>{factors.map(([key, label, detail]) => <button key={key} className={`${styles.factorButton} ${factor === key ? styles.active : ''}`} title={detail} onClick={() => { setFactor(key); setIndustry(null); }}>{label}</button>)}</div>
    <header className={styles.heading}><h2>{current[1]}</h2>{page === 'pool' && <form className={styles.rankForm} onSubmit={submitRank}><label>排名<input aria-label="因子排名筛选" value={rankInput} type="number" min="-5000" max="5000" step="1" onChange={event => setRankInput(event.target.value)} /></label><button type="submit">应用</button></form>}</header>
    {page === 'pool' ? <>
      <div className={styles.filterLine}>{industry ? <button onClick={() => setIndustry(null)}>取消行业：{industry}</button> : <span />}<b>当前 {filteredStocks.length} / {stocks.length} 只</b></div>
      <div className={styles.treemap}><ReactECharts option={treemap} notMerge style={{ height: '100%' }} onEvents={{ click: (params: { name?: string }) => params.name && setIndustry(industry === params.name ? null : params.name) }} /></div>
      <div className={styles.tableWrap}><table><thead><tr><th>股票代码</th><th>股票名称</th><th>最新收盘价</th><th>最新涨跌幅</th><th>市值排名</th><th>所属行业</th></tr></thead><tbody>
        {filteredStocks.map((stock: Record<string, number | string>) => <tr key={String(stock.stock_code)}><td><a href={`https://stockpage.10jqka.com.cn/${stock.stock_code}/`} target="_blank" rel="noreferrer">{stock.stock_code}</a></td><td>{stock.stock_name}</td><td>{format(stock.close)}</td><td className={Number(stock.pct_chg) >= 0 ? styles.up : styles.down}>{Number(stock.pct_chg) > 0 ? '+' : ''}{format(stock.pct_chg)}%</td><td>{stock.market_cap_rank ? `#${format(stock.market_cap_rank, 0)}` : '--'}</td><td>{stock.industry || '其他'}</td></tr>)}
        {!filteredStocks.length && <tr><td colSpan={6} className={styles.empty}>暂未形成可用样本；{sourceLabel}已回补 {Number(sourceReady).toLocaleString('zh-CN')}/{Number(progress?.universe || 0).toLocaleString('zh-CN')} 只，新的可用股票会自动显示。</td></tr>}
      </tbody></table></div>
    </> : <>
      <div className={styles.chartHead}><b>近三年等权组合累计收益</b></div>
      <div className={styles.chart}><ReactECharts option={performanceOption} notMerge style={{ height: '100%' }} /></div>
      {!rows.length && <div className={styles.pending}>收益序列会随已回补的行情和因子样本逐步生成，页面会每 30 秒自动刷新。</div>}
    </>}
  </section>;
}
