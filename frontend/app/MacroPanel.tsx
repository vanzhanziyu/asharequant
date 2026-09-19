'use client';

import ReactECharts from 'echarts-for-react';

type DataRow = Record<string, number | string | null>;

const value = (input: unknown, digits = 2) => Number(input || 0).toLocaleString('zh-CN', { maximumFractionDigits: digits });

function zoom() {
  return [
    { type: 'inside', xAxisIndex: 0, start: 65, end: 100, zoomOnMouseWheel: true, moveOnMouseWheel: true },
    { type: 'slider', xAxisIndex: 0, start: 65, end: 100, bottom: 10, height: 20, borderColor: '#475569', fillerColor: '#33415588', textStyle: { color: '#94a3b8' } },
  ];
}

function TreasuryChart({ rows }: { rows: DataRow[] }) {
  return <ReactECharts style={{ height: '100%' }} option={{
    tooltip: { trigger: 'axis', backgroundColor: '#0f172a', borderColor: '#334155', textStyle: { color: '#f8fafc' }, valueFormatter: (number: number) => `${number.toFixed(3)}%` },
    legend: { data: ['2年期', '10年期', '30年期'], textStyle: { color: '#cbd5e1' }, top: 2 },
    grid: { left: 52, right: 24, top: 42, bottom: 52 },
    xAxis: { type: 'category', data: rows.map(row => row.trade_date), boundaryGap: false, axisLine: { lineStyle: { color: '#334155' } }, axisLabel: { color: '#94a3b8' } },
    yAxis: { type: 'value', scale: true, axisLabel: { color: '#94a3b8', formatter: '{value}%' }, splitLine: { lineStyle: { color: '#1e293b' } } }, dataZoom: zoom(),
    series: [['2年期', 'y2', '#38bdf8'], ['10年期', 'y10', '#f59e0b'], ['30年期', 'y30', '#a78bfa']].map(([name, key, color]) => ({ name, type: 'line', smooth: true, showSymbol: false, data: rows.map(row => row[key]), lineStyle: { width: 2 }, itemStyle: { color } })),
  }} />;
}

function SpreadChart({ rows }: { rows: DataRow[] }) {
  return <ReactECharts style={{ height: '100%' }} option={{
    tooltip: { trigger: 'axis', backgroundColor: '#0f172a', borderColor: '#334155', textStyle: { color: '#f8fafc' }, valueFormatter: (number: number) => `${number.toFixed(3)}%` },
    grid: { left: 52, right: 24, top: 24, bottom: 52 }, xAxis: { type: 'category', data: rows.map(row => row.trade_date), boundaryGap: false, axisLine: { lineStyle: { color: '#334155' } }, axisLabel: { color: '#94a3b8' } },
    yAxis: { type: 'value', scale: true, axisLabel: { color: '#94a3b8', formatter: '{value}%' }, splitLine: { lineStyle: { color: '#1e293b' } } }, dataZoom: zoom(),
    series: [{ name: '10Y−2Y 利差', type: 'line', smooth: true, showSymbol: false, data: rows.map(row => row.spread_10_2), lineStyle: { width: 2 }, itemStyle: { color: '#34d399' }, areaStyle: { color: '#34d39928' }, markLine: { silent: true, lineStyle: { color: '#64748b' }, data: [{ yAxis: 0 }] } }],
  }} />;
}

function PriceCard({ title, unit, rows, color }: { title: string; unit: string; rows: DataRow[]; color: string }) {
  const last = rows[rows.length - 1];
  const previous = rows[rows.length - 2];
  const change = Number(last?.close || 0) - Number(previous?.close || 0);
  const closeOnly = String(last?.source || '').startsWith('FRED');
  const hasFredHistory = rows.some(row => String(row.source || '').startsWith('FRED'));
  const sourceNote = hasFredHistory && !closeOnly ? '早期历史为 FRED 日收盘' : '';
  return <section className="macro-card price-card">
    <div className="macro-card-title"><span>{title}</span><span className="date">{String(last?.trade_date || '--')}</span></div>
    <div className="macro-value">{last ? value(last.close, title === 'XAUUSD 现货黄金' ? 2 : 4) : '--'} <small>{unit}</small></div>
    <p className={change >= 0 ? 'macro-change up' : 'macro-change down'}>较前日 {change >= 0 ? '+' : ''}{value(change, 4)}</p>
    <p className="price-source">数据源：{String(last?.source || 'Tushare')}{sourceNote ? ` · ${sourceNote}` : ''}</p>
    <div className="macro-chart"><ReactECharts style={{ height: '100%' }} option={{
      tooltip: { trigger: 'axis', axisPointer: { type: 'cross' }, backgroundColor: '#0f172a', borderColor: '#334155', textStyle: { color: '#f8fafc' } },
      grid: { left: 46, right: 18, top: 20, bottom: 52 }, xAxis: { type: 'category', data: rows.map(row => row.trade_date), scale: true, boundaryGap: false, axisLine: { lineStyle: { color: '#334155' } }, axisLabel: { color: '#94a3b8', fontSize: 10 } },
      yAxis: { scale: true, axisLabel: { color: '#94a3b8' }, splitLine: { lineStyle: { color: '#1e293b' } } }, dataZoom: zoom(),
      series: closeOnly ? [{ name: title, type: 'line', smooth: true, showSymbol: false, data: rows.map(row => row.close), lineStyle: { width: 2 }, itemStyle: { color }, areaStyle: { color: `${color}28` } }] : [
        { name: title, type: 'candlestick', data: rows.map(row => String(row.source || '').startsWith('FRED') ? '-' : [row.open, row.close, row.low, row.high]), itemStyle: { color, borderColor: color, color0: '#10b981', borderColor0: '#10b981' } },
        ...(hasFredHistory ? [{ name: 'FRED 历史收盘', type: 'line', showSymbol: false, connectNulls: false, data: rows.map(row => String(row.source || '').startsWith('FRED') ? row.close : '-'), lineStyle: { width: 1.5, type: 'dashed' }, itemStyle: { color: '#94a3b8' } }] : []),
      ],
    }} /></div>
  </section>;
}

export default function MacroPanel({ treasury, usdcnh, dollar, gold }: { treasury: DataRow[]; usdcnh: DataRow[]; dollar: DataRow[]; gold: DataRow[] }) {
  const latest = treasury[treasury.length - 1];
  return <section className="macro-panel">
    <div className="macro-summary"><div><h2>全球宏观监控</h2><p>近 3 年 · 美债期限结构、10Y−2Y 利差与全球定价指标（支持滚轮与滑条缩放）</p></div><div className="macro-yields"><span>2Y <b>{latest ? value(latest.y2, 3) : '--'}%</b></span><span>10Y <b>{latest ? value(latest.y10, 3) : '--'}%</b></span><span>10Y−2Y <b>{latest ? value(latest.spread_10_2, 3) : '--'}%</b></span></div></div>
    <div className="macro-grid"><section className="macro-card treasury-card"><div className="macro-card-title">美债收益率期限走势 <span>2Y / 10Y / 30Y</span></div><div className="macro-chart"><TreasuryChart rows={treasury} /></div></section><section className="macro-card spread-card"><div className="macro-card-title">10Y−2Y 利差跟踪 <span>收益率曲线陡峭化 / 倒挂</span></div><div className="macro-chart"><SpreadChart rows={treasury} /></div></section><PriceCard title="USDCNH 离岸人民币" unit="CNH" rows={usdcnh} color="#38bdf8" /><PriceCard title="USDOLLAR 美元指数" unit="" rows={dollar} color="#a78bfa" /><PriceCard title="XAUUSD 现货黄金" unit="USD/oz" rows={gold} color="#f59e0b" /></div>
  </section>;
}
