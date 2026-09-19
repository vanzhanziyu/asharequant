'use client';

import ReactECharts from 'echarts-for-react';
import React from 'react';
import useSWR from 'swr';
import styles from './VixPanel.module.css';

const API = process.env.NEXT_PUBLIC_API_BASE_URL ||
  (typeof window !== 'undefined' && ['localhost', '127.0.0.1'].includes(window.location.hostname)
    ? 'http://127.0.0.1:8000'
    : '');
const fetcher = (url: string) => fetch(url).then(async response => {
  if (!response.ok) throw new Error(`请求失败 (${response.status})`);
  return response.json();
});
const products = [
  ['510050', '50ETF'], ['510300', '沪深300ETF'], ['588080', '科创50ETF'], ['159915', '创业板ETF'],
] as const;
const metricLabels = {
  vix: '标准 VIX', pcivd: '认沽认购隐波差（PCIVD）', ivhv: 'IV−HV', skew: '波动率偏斜（Skew）', cone: '波动率圆锥（Cone）',
} as const;
type Metric = keyof typeof metricLabels;
const fmt = (input: unknown, digits = 2) => input === null || input === undefined ? '--' : Number(input).toFixed(digits);

export default function VixPanel() {
  const [symbol, setSymbol] = React.useState<string>('510050');
  const [metric, setMetric] = React.useState<Metric>('vix');
  const [selectedDate, setSelectedDate] = React.useState('');
  const isCurveMetric = metric === 'skew' || metric === 'cone';
  const metricEndpoint = metric === 'ivhv' ? 'iv-hv' : metric;
  const data = useSWR(isCurveMetric ? null : `${API}/api/${metricEndpoint}?symbol=${symbol}&days=3000`, fetcher, { refreshInterval: 30 * 60 * 1000, revalidateOnFocus: true });
  const curveData = useSWR(isCurveMetric ? `${API}/api/iv-curves?symbol=${symbol}&days=45` : null, fetcher, { refreshInterval: 30 * 60 * 1000, revalidateOnFocus: true });
  const rows = data.data?.list || []; const stats = data.data?.stats || {};
  const curveDates: string[] = curveData.data?.dates || [];
  const currentCurve = selectedDate ? curveData.data?.curves?.[selectedDate] : undefined;
  const currentLabel = products.find(([code]) => code === symbol)?.[1] || symbol;
  const valueField = metric === 'vix' ? 'standard_vix_pct' : metric === 'pcivd' ? 'pcivd_pct' : 'iv_hv_pct';
  const valueUnit = metric === 'vix' ? '%' : 'pp';
  const dateIndex = Math.max(0, curveDates.indexOf(selectedDate));
  React.useEffect(() => {
    if (isCurveMetric && curveDates.length && !curveDates.includes(selectedDate)) setSelectedDate(curveDates[curveDates.length - 1]);
  }, [isCurveMetric, selectedDate, curveDates.join('|')]);
  const refresh = async () => {
    await fetch(`${API}/api/vix/refresh`, { method: 'POST' });
    data.mutate(); curveData.mutate();
  };
  const option = {
    animation: false,
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'cross', label: { backgroundColor: '#334155' } },
      backgroundColor: '#0f172a', borderColor: '#334155', textStyle: { color: '#f8fafc' },
      formatter: (items: { axisValue: string; data: number; dataIndex: number }[]) => {
        const item = items[0]; const row = rows[item?.dataIndex];
        if (!item || !row) return '';
        if (metric === 'pcivd') return `${item.axisValue}<br/><b>PCIVD：${fmt(item.data, 2)} pp</b><br/>认沽 IV：${fmt(row.put_iv_pct)}%<br/>认购 IV：${fmt(row.call_iv_pct)}%<br/>选用到期日：${row.expiry_date || '--'}（${row.days_to_expiry ?? '--'}天）<br/>平值行权价：${fmt(row.strike, 3)}`;
        if (metric === 'ivhv') return `${item.axisValue}<br/><b>IV−HV：${fmt(item.data, 2)} pp</b><br/>标准 VIX（IV）：${fmt(row.standard_vix_pct)}%<br/>30 日历史波动率（HV）：${fmt(row.historical_vol_pct)}%`;
        return `${item.axisValue}<br/><b>${currentLabel}：${fmt(item.data, 2)}%</b><br/>近月：${row.near_expiry || '--'}（${row.near_days ?? '--'}天）<br/>次月：${row.next_expiry || '--'}（${row.next_days ?? '--'}天）`;
      },
    },
    grid: { left: 58, right: 26, top: 24, bottom: 66 },
    xAxis: { type: 'category', data: rows.map((row: { trade_date: string }) => row.trade_date), boundaryGap: false, axisLine: { lineStyle: { color: '#334155' } }, axisLabel: { color: '#94a3b8', fontSize: 10 } },
    yAxis: { type: 'value', scale: true, name: metric === 'vix' ? 'VIX (%)' : metric === 'pcivd' ? 'PCIVD (pp)' : 'IV−HV (pp)', nameTextStyle: { color: '#94a3b8', padding: [0, 0, 0, -8] }, splitLine: { lineStyle: { color: '#1e293b' } }, axisLabel: { color: '#94a3b8', formatter: metric === 'vix' ? '{value}%' : '{value} pp' } },
    dataZoom: [
      { type: 'inside', xAxisIndex: 0, start: 0, end: 100, zoomOnMouseWheel: true, moveOnMouseWheel: true, moveOnMouseMove: true },
      { type: 'slider', xAxisIndex: 0, start: 0, end: 100, bottom: 12, height: 22, borderColor: '#475569', fillerColor: '#38bdf844', handleStyle: { color: '#38bdf8' }, textStyle: { color: '#94a3b8' } },
    ],
    series: [{ name: `${currentLabel} ${metricLabels[metric]}`, type: 'line', showSymbol: false, smooth: false, data: rows.map((row: Record<string, number>) => row[valueField]), lineStyle: { color: metric === 'vix' ? '#38bdf8' : metric === 'pcivd' ? '#f59e0b' : '#a78bfa', width: 2.2 }, itemStyle: { color: metric === 'vix' ? '#38bdf8' : metric === 'pcivd' ? '#f59e0b' : '#a78bfa' }, areaStyle: { color: metric === 'vix' ? '#38bdf822' : metric === 'pcivd' ? '#f59e0b22' : '#a78bfa22' }, markLine: { silent: true, symbol: 'none', lineStyle: { color: '#64748b', type: 'dashed' }, data: stats.current !== null && stats.current !== undefined ? [{ yAxis: stats.current, label: { formatter: `当前 ${fmt(stats.current)} ${valueUnit}`, color: '#cbd5e1' } }] : [] } }],
  };
  const curveOption = metric === 'skew' ? {
    animation: false, color: ['#f472b6', '#38bdf8'],
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' }, backgroundColor: '#0f172a', borderColor: '#334155', textStyle: { color: '#f8fafc' }, valueFormatter: (value: number) => `${fmt(value)}%` },
    legend: { top: 2, textStyle: { color: '#cbd5e1' }, data: ['虚值认沽', '虚值认购'] },
    grid: { left: 58, right: 26, top: 42, bottom: 45 },
    xAxis: { type: 'value', scale: true, name: '行权价', nameTextStyle: { color: '#94a3b8' }, axisLine: { lineStyle: { color: '#334155' } }, splitLine: { lineStyle: { color: '#1e293b' } }, axisLabel: { color: '#94a3b8', formatter: (value: number) => fmt(value, 3) } },
    yAxis: { type: 'value', scale: true, name: 'IV (%)', nameTextStyle: { color: '#94a3b8' }, splitLine: { lineStyle: { color: '#1e293b' } }, axisLabel: { color: '#94a3b8', formatter: '{value}%' } },
    series: [
      { name: '虚值认沽', type: 'line', smooth: true, showSymbol: true, symbolSize: 5, data: (currentCurve?.skew?.puts || []).map((point: { strike: number; iv_pct: number }) => [point.strike, point.iv_pct]), lineStyle: { width: 2.2 } },
      { name: '虚值认购', type: 'line', smooth: true, showSymbol: true, symbolSize: 5, data: (currentCurve?.skew?.calls || []).map((point: { strike: number; iv_pct: number }) => [point.strike, point.iv_pct]), lineStyle: { width: 2.2 } },
    ],
  } : {
    animation: false, color: ['#38bdf8', '#f472b6'],
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' }, backgroundColor: '#0f172a', borderColor: '#334155', textStyle: { color: '#f8fafc' }, formatter: (items: { axisValue: string; data: number; dataIndex: number }[]) => {
      const point = currentCurve?.cone?.[items[0]?.dataIndex];
      return `${items[0]?.axisValue || ''}<br/>平值行权价：${fmt(point?.strike, 3)}<br/>认购 IV：${fmt(point?.call_iv_pct)}%<br/>认沽 IV：${fmt(point?.put_iv_pct)}%`;
    } },
    legend: { top: 2, textStyle: { color: '#cbd5e1' }, data: ['平值认购', '平值认沽'] },
    grid: { left: 58, right: 26, top: 42, bottom: 45 },
    xAxis: { type: 'category', data: (currentCurve?.cone || []).map((point: { label: string }) => point.label), axisLine: { lineStyle: { color: '#334155' } }, axisLabel: { color: '#94a3b8' } },
    yAxis: { type: 'value', scale: true, name: 'IV (%)', nameTextStyle: { color: '#94a3b8' }, splitLine: { lineStyle: { color: '#1e293b' } }, axisLabel: { color: '#94a3b8', formatter: '{value}%' } },
    series: [
      { name: '平值认购', type: 'line', smooth: true, showSymbol: true, symbolSize: 6, data: (currentCurve?.cone || []).map((point: { call_iv_pct: number }) => point.call_iv_pct), lineStyle: { width: 2.2 } },
      { name: '平值认沽', type: 'line', smooth: true, showSymbol: true, symbolSize: 6, data: (currentCurve?.cone || []).map((point: { put_iv_pct: number }) => point.put_iv_pct), lineStyle: { width: 2.2 } },
    ],
  };
  return <section className="vix-panel">
    <div className={styles.filterBar}>
      <div className={styles.filterGroup}>
        <span className={styles.filterLabel}>期权标的</span>
        <div className={styles.segment} role="tablist" aria-label="期权标的">
          {products.map(([code, name]) => <button key={code} type="button" role="tab" aria-selected={symbol === code} className={`${styles.choice} ${symbol === code ? styles.active : ''}`} onClick={() => { setSymbol(code); setSelectedDate(''); }}>{name}</button>)}
        </div>
      </div>
      <span className={styles.separator} aria-hidden="true" />
      <div className={styles.filterGroup}>
        <span className={styles.filterLabel}>指标</span>
        <div className={styles.segment} role="tablist" aria-label="波动率指标">
          {(Object.keys(metricLabels) as Metric[]).map(key => <button key={key} type="button" role="tab" aria-selected={metric === key} title={metricLabels[key]} className={`${styles.choice} ${metric === key ? styles.active : ''}`} onClick={() => { setMetric(key); if (key === 'skew' || key === 'cone') setSelectedDate(''); }}>{key === 'vix' ? '标准 VIX' : key === 'pcivd' ? 'PCIVD' : key === 'ivhv' ? 'IV−HV' : key === 'skew' ? 'Skew' : 'Cone'}</button>)}
        </div>
      </div>
    </div>
    {isCurveMetric && <div className={styles.dateControl}>
      <div className={styles.dateMeta}><span>查看交易日</span><strong>{selectedDate || '加载中'}</strong><em>拖动滑条查看最近一个月曲线</em></div>
      <input aria-label="曲线交易日" type="range" min="0" max={Math.max(0, curveDates.length - 1)} value={dateIndex} disabled={!curveDates.length} onChange={event => setSelectedDate(curveDates[Number(event.target.value)] || '')} />
      <div className={styles.dateRange}><span>{curveDates[0] || '--'}</span><span>{curveDates[curveDates.length - 1] || '--'}</span></div>
    </div>}
    <div className="vix-summary"><div><h2>{currentLabel} · {metricLabels[metric]}</h2><p>{metric === 'vix' ? '30 日标准 VIX · 逐张期权日收盘价计算 · 2019 年至今' : metric === 'pcivd' ? '近月平值认沽 IV − 认购 IV · 剩余到期日 ≤ 8 天切换次月' : metric === 'ivhv' ? '标准 VIX（IV）− 30 个交易日历史波动率（HV）· 时间逐日对齐' : metric === 'skew' ? `当前月份虚值认沽与虚值认购 · ${currentCurve?.skew?.expiry_date || '--'} 到期` : '各到期月平值认购与认沽 IV · 横向比较期限结构'}</p></div><div className="vix-actions"><span className={`vix-state ${(isCurveMetric ? curveData.data?.history_status : data.data?.history_status) === 'completed' ? 'ready' : ''}`}>{(isCurveMetric ? curveData.data?.history_status : data.data?.history_status) === 'completed' ? (isCurveMetric ? '最近一月已就绪' : '2019 至今已就绪') : '历史数据回补中'}</span><button className="button" onClick={refresh}>更新数据</button></div></div>
    {!isCurveMetric && <div className="vix-stats"><article><span>当前 {metric === 'vix' ? 'VIX' : metric === 'pcivd' ? 'PCIVD' : 'IV−HV'}</span><b>{fmt(stats.current)}<small>{valueUnit}</small></b><em>{stats.current_date || '--'}</em></article><article><span>历史最大值</span><b>{fmt(stats.maximum)}<small>{valueUnit}</small></b><em>已入库样本</em></article><article><span>历史最小值</span><b>{fmt(stats.minimum)}<small>{valueUnit}</small></b><em>{stats.observations || 0} 个有效交易日</em></article><article><span>当前所处百分位</span><b>{fmt(stats.percentile)}<small>%</small></b><em>历史值不高于当前的比例</em></article></div>}
    <div className="vix-chart"><p className="zoom-hint">{isCurveMetric ? '拖动上方交易日滑条 · 十字光标查看对应行权价或到期月' : '滚轮缩放 · 底部滑条平移 · 十字光标查看具体交易日数据'}</p><ReactECharts option={isCurveMetric ? curveOption : option} notMerge style={{ height: '100%' }} /></div>
    <p className="vix-footnote">{metric === 'vix' ? '数据源：上交所/深交所公开合约挂牌记录与 Tushare 期权日线收盘价。无成交日沿用最近有效收盘价；月度到期日若可用期限不足两份，则该日不产生 VIX 值。' : metric === 'pcivd' ? 'PCIVD = 近月平值认沽期权的 Black-Scholes 隐含波动率 − 同行权价认购期权的隐含波动率；近月剩余到期日不超过 8 天时，选用次月平值期权。' : metric === 'ivhv' ? 'IV−HV = 当日标准 VIX − 标的最近 30 个交易日对数收益率的样本标准差（按 √252 年化）；正值表示隐含波动率高于已实现波动率。' : metric === 'skew' ? 'Skew 选择最近到期月份：行权价低于标的收盘价的认沽，以及行权价高于标的收盘价的认购；每一个点均由该合约收盘价反推 IV。' : 'Cone 对每个可用到期月份选择最接近标的收盘价、且认购与认沽均有效的同行权价；两条线分别展示这对平值期权的 IV。'}</p>
  </section>;
}
