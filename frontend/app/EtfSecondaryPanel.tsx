'use client';

import { useEffect, useMemo, useState } from 'react';
import useSWR from 'swr';
import ReactECharts from 'echarts-for-react';
import styles from './EtfFlowPanel.module.css';

type Value = number | null;
type Bands = { main: Value; super_large: Value; large: Value; medium: Value; small: Value };
type Fund = Bands & { ts_code: string; name: string; group: string; data_date: string | null; main_pct: Value; close: Value; change_pct: Value; flow_5: Value; flow_20: Value };
type FlowResponse = {
  date: string | null; expected_date: string | null; complete_date: string | null; stale: boolean;
  checked_at: string | null; success_at: string | null; source: string;
  summary: { main: Value; flow_5: Value; flow_20: Value; covered: number; expected: number };
  categories: string[]; funds: Fund[]; groups: { name: string; main: Value }[];
  daily: (Bands & { trade_date: string; covered: number; expected: number; available_main: Value })[];
  errors: { ts_code: string; error: string }[];
};
const CATEGORIES = ['全部', '沪深300', '上证50', '中证500', '中证1000', '中证A500', '科创50', '创业板'];
const KEY = 'ashare-broad-etf-secondary-group-v1';
const API = process.env.NEXT_PUBLIC_API_BASE_URL || (typeof window !== 'undefined' && ['localhost', '127.0.0.1'].includes(window.location.hostname) ? 'http://127.0.0.1:8000' : '');
const fmt = (n: Value | undefined, digits = 2) => n == null || !Number.isFinite(n) ? '—' : n.toLocaleString('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: digits });
const signed = (n: Value | undefined) => n == null ? '—' : `${n > 0 ? '+' : ''}${fmt(n)}`;
const color = (n: Value | undefined) => n == null ? '#64748b' : n >= 0 ? '#fb7185' : '#34d399';
const fetcher = async (url: string): Promise<FlowResponse> => { const response = await fetch(url); if (!response.ok) throw new Error('资金流向读取失败'); return response.json(); };
const columns: [keyof Bands | 'flow_5' | 'flow_20' | 'main_pct', string][] = [
  ['main', '主力净流入'], ['super_large', '超大单'], ['large', '大单'], ['medium', '中单'], ['small', '小单'], ['flow_5', '近5日主力'], ['flow_20', '近20日主力'], ['main_pct', '主力净占比'],
];

export default function EtfSecondaryPanel() {
  const [group, setGroup] = useState('全部');
  const [ready, setReady] = useState(false);
  const [sort, setSort] = useState<{ key: typeof columns[number][0]; ascending: boolean }>({ key: 'main', ascending: false });
  useEffect(() => { try { const saved = localStorage.getItem(KEY); if (saved && CATEGORIES.includes(saved)) setGroup(saved); } catch {} setReady(true); }, []);
  useEffect(() => { if (ready) { try { localStorage.setItem(KEY, group); } catch {} } }, [group, ready]);
  const { data, error, isLoading, isValidating, mutate } = useSWR(`${API}/api/broad-etf/secondary-flows?group=${encodeURIComponent(group)}`, fetcher, { refreshInterval: 60000 });
  const sorted = useMemo(() => [...(data?.funds || [])].sort((a, b) => {
    const x = a[sort.key], y = b[sort.key];
    if (x == null) return y == null ? 0 : 1;
    if (y == null) return -1;
    return (x - y) * (sort.ascending ? 1 : -1);
  }), [data, sort]);
  const daily = data?.daily || [];
  const summary = data?.summary;
  const option = {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', backgroundColor: '#0f172a', borderColor: '#334155', textStyle: { color: '#e2e8f0' }, valueFormatter: (v: Value) => v == null ? '数据缺失' : `${fmt(v)} 亿` },
    legend: { top: 6, textStyle: { color: '#94a3b8' }, selected: { '超大单': false, '大单': false, '中单': false, '小单': false } },
    grid: { top: 60, bottom: 72, left: 70, right: 25 },
    xAxis: { type: 'category', data: daily.map(d => d.trade_date), axisLabel: { color: '#94a3b8' }, axisLine: { lineStyle: { color: '#334155' } } },
    yAxis: { type: 'value', name: '净流入 / 亿元', nameTextStyle: { color: '#94a3b8' }, axisLabel: { color: '#94a3b8' }, splitLine: { lineStyle: { color: '#1e293b' } } },
    dataZoom: [{ type: 'inside', start: 50, end: 100 }, { type: 'slider', start: 50, end: 100, bottom: 16, height: 22, textStyle: { color: '#94a3b8' }, borderColor: '#334155' }],
    series: [
      { name: '主力净流入', type: 'bar', barMaxWidth: 18, data: daily.map(d => ({ value: d.main, itemStyle: { color: color(d.main) } })) },
      ...(['super_large', 'large', 'medium', 'small'] as const).map((field, i) => ({ name: ['超大单', '大单', '中单', '小单'][i], type: 'line', showSymbol: false, connectNulls: false, data: daily.map(d => d[field]), lineStyle: { width: 1.5 }, itemStyle: { color: ['#a78bfa', '#60a5fa', '#fbbf24', '#2dd4bf'][i] } })),
    ],
  };
  return <section className={styles.panel}>
    <div className={styles.heading}><div><h2>二级市场资金流向</h2><p>东方财富 · 选定宽基 ETF · 数据日 {data?.date || '等待采集'} · 盘中为当日累计快照，非逐笔实时</p></div><button className="button" onClick={() => mutate()} disabled={isValidating}>{isValidating ? '读取中…' : '刷新'}</button></div>
    <div className={styles.tabs}>{CATEGORIES.map(item => <button key={item} className={`button ${group === item ? 'active' : ''}`} onClick={() => setGroup(item)}>{item}</button>)}</div>
    {error && <p className={styles.notice}>资金流向接口暂不可用，正在重试。{data ? '下方保留上次读取结果。' : ''}</p>}
    {isLoading && !data && <p className={styles.caption}>正在读取二级市场资金流向…</p>}
    {data && summary && <>
      <div className={styles.stats}>
        {([['单日主力净流入', summary.main], ['近5交易日主力', summary.flow_5], ['近20交易日主力', summary.flow_20]] as [string, Value][]).map(([label, value]) => <article key={label}><span>{label}</span><strong style={{ color: color(value) }}>{signed(value)}<small> 亿</small></strong><em>{value == null ? '数据尚未齐全' : '主力 = 超大单 + 大单'}</em></article>)}
        <article><span>当日有效样本</span><strong>{summary.covered}<small> / {summary.expected} 只</small></strong><em>最后成功采集 {data.success_at?.replace('T', ' ').replace('+08:00', '') || '暂无'}</em></article>
      </div>
      {data.stale && <p className={styles.notice}>数据源尚未提供 {data.expected_date} 的记录；下方是 {data.date || '暂无'} 的数据，不代表今天。</p>}
      {summary.covered < summary.expected && <p className={styles.notice}>部分 ETF 数据缺失，总额和滚动统计不将缺失值当作零。最新完整样本日：{data.complete_date || '暂无'}。</p>}
      {!!data.errors.length && <p className={styles.notice}>{data.errors.length} 只 ETF 最近采集失败，已保留历史数据，下一轮自动重试。</p>}
      <div className={styles.visuals}>
        <div className={styles.chartCard}><h3>二级市场净流入历史 <span>红色净流入 · 绿色净流出 · 点击图例切换分类</span></h3><div className={styles.chart}>{daily.length ? <ReactECharts option={option} style={{ height: '100%' }} /> : <p className="empty">暂无有效资金流向数据；不会使用申赎数据代替。</p>}</div><p className={styles.caption}>可用历史 {daily[0]?.trade_date || '—'} 至 {data.date || '—'}，受数据源回溯范围限制；新增记录持续保存。</p></div>
        <aside className={styles.groups}><h3>各指数单日主力净流入</h3>{data.groups.map(item => <div className={styles.group} key={item.name}><div><span>{item.name}</span><b style={{ color: color(item.main) }}>{signed(item.main)} 亿</b></div><div className={styles.track}><i style={{ width: `${Math.abs(item.main || 0) / Math.max(1, ...data.groups.map(g => Math.abs(g.main || 0))) * 100}%`, background: color(item.main) }} /></div></div>)}<p className={styles.caption}>按订单大小分类，不代表资金进入 ETF 基金资产，也不能识别国家队。</p></aside>
      </div>
      <div className={styles.tableCard}><h3>ETF 资金流向明细 <span>金额单位：亿元 · 点击表头排序</span></h3><div className={styles.table}><table><thead><tr><th>ETF / 跟踪指数</th>{columns.map(([key, label]) => <th key={key} aria-sort={sort.key === key ? sort.ascending ? 'ascending' : 'descending' : 'none'}><button onClick={() => setSort({ key, ascending: sort.key === key ? !sort.ascending : false })}>{label}{sort.key === key ? sort.ascending ? ' ↑' : ' ↓' : ''}</button></th>)}<th>数据日期</th></tr></thead><tbody>{sorted.map(fund => <tr key={fund.ts_code}><td><b>{fund.name}</b><small>{fund.ts_code} · {fund.group}</small></td>{columns.map(([key]) => <td key={key} style={{ color: color(fund[key]) }}>{signed(fund[key])}{key === 'main_pct' && fund[key] != null ? '%' : ''}</td>)}<td>{fund.data_date || '暂无'}{fund.data_date !== data.date ? '（待更新）' : ''}</td></tr>)}</tbody></table></div></div>
      <details className={styles.method}><summary>统计口径与更新时间</summary><p>数据源：东方财富。主力净流入 = 超大单净流入 + 大单净流入，各项按照数据商买卖方向及订单大小口径计算。净流入并非市场新增现金，不能确认投资者身份，也不可与份额法净申购相加。</p><p>同一批固定 ETF 样本，不覆盖所有宽基产品。周一至周五北京时间 09:00—16:55 每 5 分钟采集，20:40 再检查一次；非交易日不会产生新交易记录。页面每分钟读取数据库，点击刷新不会强制数据商更新。盘中数值会变化，收盘后的数值也可能被数据商修订。</p><p>最近检查：{data.checked_at || '尚未采集'}；历史不足 5 / 20 个交易日或窗口存在缺失时，相应统计显示“—”。</p></details>
    </>}
  </section>;
}
