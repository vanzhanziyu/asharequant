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
  ['market_cap_large', '大盘因子', 100],
  ['market_cap_micro', '微盘因子', -100],
  ['dividend_yield', '纯红利因子', 100],
  ['ebitda_cagr', 'EBITDA 增速', 100],
] as const;
type FactorName = typeof factors[number][0];
type PoolSortKey = 'close' | 'pct_chg' | 'market_cap_rank' | 'industry';
const MA_PERIODS = [5, 10, 20, 60] as const;
const MA_STORAGE_KEY = 'ashare-factor-visible-moving-averages-v1';
const FACTOR_TAB_STORAGE_KEY = 'ashare-factor-selected-tabs-v1';
const DEFAULT_VISIBLE_MAS: Record<number, boolean> = { 5: true, 10: true, 20: true, 60: true };
const format = (value: unknown, digits = 2) => value === null || value === undefined ? '--' : Number(value).toLocaleString('zh-CN', { maximumFractionDigits: digits });
const percent = (value: unknown) => value === null || value === undefined ? '--' : `${Number(value).toFixed(2)}%`;

export default function FactorPanel() {
  const [page, setPage] = React.useState<'pool' | 'performance'>('pool');
  const [factor, setFactor] = React.useState<FactorName>('market_cap_large');
  const [rankInput, setRankInput] = React.useState('100');
  const [rank, setRank] = React.useState(100);
  const [industry, setIndustry] = React.useState<string | null>(null);
  const [poolSort, setPoolSort] = React.useState<{ key: PoolSortKey | null; direction: 'asc' | 'desc' }>({ key: null, direction: 'asc' });
  const [visibleMas, setVisibleMas] = React.useState<Record<number, boolean>>(DEFAULT_VISIBLE_MAS);
  const [maPreferenceLoaded, setMaPreferenceLoaded] = React.useState(false);
  const [tabPreferenceLoaded, setTabPreferenceLoaded] = React.useState(false);
  React.useEffect(() => {
    try {
      const saved = JSON.parse(window.localStorage.getItem(FACTOR_TAB_STORAGE_KEY) || '{}');
      const savedFactor = factors.find(([key]) => key === saved.factor);
      if (saved.page === 'pool' || saved.page === 'performance') setPage(saved.page);
      if (savedFactor) {
        setFactor(savedFactor[0]);
        setRank(savedFactor[2]);
        setRankInput(String(savedFactor[2]));
      }
    } catch { /* Use the default factor page and type. */ }
    setTabPreferenceLoaded(true);
  }, []);
  React.useEffect(() => {
    if (tabPreferenceLoaded) window.localStorage.setItem(FACTOR_TAB_STORAGE_KEY, JSON.stringify({ page, factor }));
  }, [tabPreferenceLoaded, page, factor]);
  React.useEffect(() => {
    try {
      const saved = JSON.parse(window.localStorage.getItem(MA_STORAGE_KEY) || '{}');
      setVisibleMas({ ...DEFAULT_VISIBLE_MAS, ...Object.fromEntries(MA_PERIODS.map(period => [period, typeof saved[period] === 'boolean' ? saved[period] : DEFAULT_VISIBLE_MAS[period]])) });
    } catch { setVisibleMas(DEFAULT_VISIBLE_MAS); }
    setMaPreferenceLoaded(true);
  }, []);
  React.useEffect(() => {
    if (maPreferenceLoaded) window.localStorage.setItem(MA_STORAGE_KEY, JSON.stringify(visibleMas));
  }, [maPreferenceLoaded, visibleMas]);
  // New samples arrive while the first full-universe backfill is running.
  // Keep the page live so visitors do not have to reload it manually.
  const pool = useSWR(`${API}/api/factors/pool?factor=${factor}&rank=${rank}`, fetcher, { refreshInterval: 30_000, revalidateOnFocus: true });
  const performance = useSWR(`${API}/api/factors/performance?factor=${factor}&days=1095`, fetcher, { refreshInterval: 30_000, revalidateOnFocus: true });
  const current = factors.find(([key]) => key === factor) || factors[0];
  const stocks = pool.data?.stocks || [];
  const filteredStocks = industry ? stocks.filter((stock: Record<string, string>) => stock.industry === industry) : stocks;
  const sortedStocks = React.useMemo(() => {
    if (!poolSort.key) return filteredStocks;
    const key = poolSort.key;
    return [...filteredStocks].sort((left: Record<string, number | string>, right: Record<string, number | string>) => {
      const leftValue = key === 'industry' ? String(left.industry || '其他') : Number(left[key]);
      const rightValue = key === 'industry' ? String(right.industry || '其他') : Number(right[key]);
      const leftMissing = key === 'market_cap_rank' && (!Number.isFinite(Number(leftValue)) || Number(leftValue) <= 0);
      const rightMissing = key === 'market_cap_rank' && (!Number.isFinite(Number(rightValue)) || Number(rightValue) <= 0);
      if (leftMissing || rightMissing) return leftMissing === rightMissing ? 0 : leftMissing ? 1 : -1;
      const comparison = key === 'industry' ? String(leftValue).localeCompare(String(rightValue), 'zh-CN') : Number(leftValue) - Number(rightValue);
      return poolSort.direction === 'asc' ? comparison : -comparison;
    });
  }, [filteredStocks, poolSort]);
  const rows = performance.data?.list || [];
  const stats = performance.data?.stats;
  const progress = pool.data?.progress || performance.data?.progress;
  const available = Number(pool.data?.universe_count || 0);
  const sourceReady = factor.startsWith('market_cap') ? progress?.universe || 0 : factor === 'dividend_yield' ? progress?.dividend_ready || 0 : progress?.financial_ready || 0;
  const sourceLabel = factor.startsWith('market_cap') ? '沪深非 ST 股票' : factor === 'dividend_yield' ? '分红资料' : '财务资料';
  const currentStatus = `已展示 ${available.toLocaleString('zh-CN')} 只可用样本 · ${sourceLabel} ${Number(sourceReady).toLocaleString('zh-CN')}/${Number(progress?.universe || 0).toLocaleString('zh-CN')} 只`;
  const submitRank = (event: React.FormEvent) => {
    event.preventDefault();
    const value = Math.trunc(Number(rankInput));
    if (Number.isFinite(value) && value !== 0) setRank(Math.max(-5000, Math.min(5000, value)));
  };
  const togglePoolSort = (key: PoolSortKey) => setPoolSort(current => current.key === key ? { key, direction: current.direction === 'asc' ? 'desc' : 'asc' } : { key, direction: 'asc' });
  const sortIndicator = (key: PoolSortKey) => poolSort.key === key ? (poolSort.direction === 'asc' ? ' ↑' : ' ↓') : ' ↕';
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
  const netValueKline = rows.map((row: { nav: number; open_nav?: number; high_nav?: number; low_nav?: number }) => {
    const values = [row.open_nav, row.nav, row.low_nav, row.high_nav].map(value => value === null || value === undefined ? NaN : Number(value));
    return values.every(Number.isFinite) ? values.map(value => (value - 1) * 100) : '-';
  });
  const movingAverage = (days: number) => rows.map((_: unknown, index: number) => {
    if (index < days - 1) return '-';
    const total = rows.slice(index - days + 1, index + 1).reduce((sum: number, item: { nav: number }) => sum + Number(item.nav), 0);
    return (total / days - 1) * 100;
  });
  const recentZoomStart = rows.length > 120 ? Math.max(0, 100 - 120 / rows.length * 100) : 0;
  const navReturn = (value: unknown) => value === null || value === undefined || !Number.isFinite(Number(value)) ? '--' : percent((Number(value) - 1) * 100);
  const maStyles: Record<number, string> = { 5: 'rgba(250,204,21,.52)', 10: 'rgba(251,146,60,.52)', 20: 'rgba(56,189,248,.58)', 60: 'rgba(167,139,250,.58)' };
  const visibleMaPeriods = MA_PERIODS.filter(period => visibleMas[period]);
  const performanceOption = {
    animation: false,
    legend: { data: ['组合净值 K 线', ...visibleMaPeriods.map(period => `MA${period}`)], selectedMode: false, textStyle: { color: '#94a3b8' }, left: 58, top: 9, itemWidth: 22, itemHeight: 12, itemGap: 18 },
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross', label: { backgroundColor: '#334155' } }, backgroundColor: '#0f172a', borderColor: '#334155', textStyle: { color: '#f8fafc' }, formatter: (items: { axisValue: string; dataIndex: number; data: number }[]) => {
      const item = items[0]; const row = rows[item?.dataIndex];
      const cumulativeReturn = row ? (Number(row.nav) - 1) * 100 : NaN;
      return !item || !row ? '' : `${item.axisValue}<br/><b>累计收益：${Number.isFinite(cumulativeReturn) ? format(cumulativeReturn) : '--'}%</b><br/>开：${navReturn(row.open_nav)}　高：${navReturn(row.high_nav)}<br/>低：${navReturn(row.low_nav)}　收：${navReturn(row.nav)}<br/>当日组合收益：${format(row.daily_return_pct)}%<br/>前一日选股：${row.signal_date}<br/>有效持仓：${row.holding_count} 只`;
    } },
    grid: { left: 62, right: 28, top: 48, bottom: 58 },
    xAxis: { type: 'category', data: rows.map((row: { trade_date: string }) => row.trade_date), boundaryGap: false, axisLine: { lineStyle: { color: '#334155' } }, axisLabel: { color: '#94a3b8', fontSize: 10 } },
    yAxis: { type: 'value', scale: true, name: '累计收益 (%)', nameTextStyle: { color: '#94a3b8' }, splitLine: { lineStyle: { color: '#1e293b' } }, axisLabel: { color: '#94a3b8', formatter: '{value}%' } },
    dataZoom: [{ type: 'inside', xAxisIndex: 0, start: recentZoomStart, end: 100, zoomOnMouseWheel: true, moveOnMouseWheel: true, moveOnMouseMove: true }, { type: 'slider', xAxisIndex: 0, start: recentZoomStart, end: 100, bottom: 12, height: 22, borderColor: '#475569', fillerColor: '#38bdf844', handleStyle: { color: '#38bdf8' }, textStyle: { color: '#94a3b8' } }],
    series: [
      { name: '组合净值 K 线', type: 'candlestick', data: netValueKline, barMinWidth: 4, barMaxWidth: 18, itemStyle: { color: 'rgba(244,63,94,.82)', color0: 'rgba(16,185,129,.82)', borderColor: '#fb7185', borderColor0: '#34d399', borderWidth: 1.4 }, emphasis: { itemStyle: { borderWidth: 2 } } },
      ...visibleMaPeriods.map(period => ({ name: `MA${period}`, type: 'line', showSymbol: false, data: movingAverage(period), lineStyle: { color: maStyles[period], width: 1.35 }, itemStyle: { color: maStyles[period] }, z: 3 })),
    ],
  };
  return <section className={`${styles.panel} ${page === 'performance' ? styles.performancePanel : ''}`}>
    <div className={styles.topbar}><div className={styles.pageTabs}><button className={`${styles.pageButton} ${page === 'pool' ? styles.active : ''}`} onClick={() => setPage('pool')}>因子股票池</button><button className={`${styles.pageButton} ${page === 'performance' ? styles.active : ''}`} onClick={() => setPage('performance')}>因子收益率走势</button></div><span className={styles.status}>{currentStatus}</span></div>
    <div className={styles.factorTabs}>{factors.map(([key, label, defaultRank]) => <button key={key} className={`${styles.factorButton} ${factor === key ? styles.active : ''}`} onClick={() => { setFactor(key); setRank(defaultRank); setRankInput(String(defaultRank)); setIndustry(null); }}>{label}</button>)}</div>
    {page === 'pool' && <header className={styles.heading}><h2>{current[1]}</h2>{!factor.startsWith('market_cap') && <form className={styles.rankForm} onSubmit={submitRank}><label>排名<input aria-label="因子排名筛选" value={rankInput} type="number" min="-5000" max="5000" step="1" onChange={event => setRankInput(event.target.value)} /></label><button type="submit">应用</button></form>}</header>}
    {page === 'pool' ? <>
      <div className={styles.filterLine}>{industry ? <button onClick={() => setIndustry(null)}>取消行业：{industry}</button> : <span />}<b>当前 {filteredStocks.length} / {stocks.length} 只</b></div>
      <div className={styles.treemap}><ReactECharts option={treemap} notMerge style={{ height: '100%' }} onEvents={{ click: (params: { name?: string }) => params.name && setIndustry(industry === params.name ? null : params.name) }} /></div>
      <div className={styles.tableWrap}><table><thead><tr><th>股票代码</th><th>股票名称</th><th><button className={styles.sortButton} onClick={() => togglePoolSort('close')}>最新收盘价{sortIndicator('close')}</button></th><th><button className={styles.sortButton} onClick={() => togglePoolSort('pct_chg')}>最新涨跌幅{sortIndicator('pct_chg')}</button></th><th><button className={styles.sortButton} onClick={() => togglePoolSort('market_cap_rank')}>市值排名{sortIndicator('market_cap_rank')}</button></th><th><button className={styles.sortButton} onClick={() => togglePoolSort('industry')}>所属行业{sortIndicator('industry')}</button></th></tr></thead><tbody>
        {sortedStocks.map((stock: Record<string, number | string>) => <tr key={String(stock.stock_code)}><td><a href={`https://stockpage.10jqka.com.cn/${stock.stock_code}/`} target="_blank" rel="noreferrer">{stock.stock_code}</a></td><td>{stock.stock_name}</td><td>{format(stock.close)}</td><td className={Number(stock.pct_chg) >= 0 ? styles.up : styles.down}>{Number(stock.pct_chg) > 0 ? '+' : ''}{format(stock.pct_chg)}%</td><td>{stock.market_cap_rank ? `#${format(stock.market_cap_rank, 0)}` : '--'}</td><td>{stock.industry || '其他'}</td></tr>)}
        {!sortedStocks.length && <tr><td colSpan={6} className={styles.empty}>暂未形成可用样本；{sourceLabel}已回补 {Number(sourceReady).toLocaleString('zh-CN')}/{Number(progress?.universe || 0).toLocaleString('zh-CN')} 只，新的可用股票会自动显示。</td></tr>}
      </tbody></table></div>
    </> : <>
      <div className={styles.chartHead}><div className={styles.chartTitle}><b>{current[1]}</b><span>近三年组合净值 · 滚轮缩放 / 拖动底部滑条</span></div><div className={styles.chartMeta}><div className={styles.maControls}><span>均线</span>{MA_PERIODS.map(period => <button key={period} className={visibleMas[period] ? styles.active : ''} onClick={() => setVisibleMas(currentMas => ({ ...currentMas, [period]: !currentMas[period] }))}>MA{period}</button>)}</div><div className={styles.stats}><span>最大回撤 <strong>{percent(stats?.max_drawdown_pct)}</strong></span><span>夏普比率 <strong>{format(stats?.sharpe_ratio)}</strong></span><span>近30日标准差 <strong>{percent(stats?.stddev_30d_pct)}</strong></span></div></div></div>
      <div className={styles.performanceChart}><ReactECharts option={performanceOption} notMerge style={{ height: '100%' }} /></div>
      {!rows.length && <div className={styles.pending}>收益序列会随已回补的行情和因子样本逐步生成，页面会每 30 秒自动刷新。</div>}
    </>}
  </section>;
}
