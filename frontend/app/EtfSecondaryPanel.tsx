'use client';

import { useEffect, useMemo, useState } from 'react';
import useSWR from 'swr';
import ReactECharts from 'echarts-for-react';
import styles from './EtfFlowPanel.module.css';

type Value = number | null;
type Bands = { main: Value; net_amount: Value };
type Fund = Bands & { ts_code: string; name: string; group: string; data_date: string | null; main_pct: Value; close: Value; change_pct: Value; flow_5: Value; flow_20: Value };
type FlowResponse = {
  date: string | null; expected_date: string | null; complete_date: string | null; stale: boolean;
  checked_at: string | null; success_at: string | null; source: string;
  summary: { main: Value; flow_5: Value; flow_20: Value; covered: number; expected: number };
  categories: string[]; funds: Fund[]; groups: { name: string; main: Value }[];
  daily: (Bands & { trade_date: string; covered: number; expected: number; available_main: Value })[];
  errors: { ts_code: string; error: string }[];
};
type LiveFund = { ts_code: string; name: string; group: string; main: Value; small: Value; medium: Value; large: Value; super_large: Value; checked_at: string | null };
type LiveResponse = { date: string | null; source: string; checked_at: string | null; summary: { main: Value; available_main: Value; covered: number; expected: number }; funds: LiveFund[]; groups: { name: string; main: Value; covered: number; expected: number }[]; errors: { ts_code: string; error: string }[] };
const CATEGORIES = ['全部', '沪深300', '上证50', '中证500', '中证1000', '中证A500', '科创50', '创业板'];
const KEY = 'ashare-broad-etf-secondary-group-v1';
const API = process.env.NEXT_PUBLIC_API_BASE_URL || (typeof window !== 'undefined' && ['localhost', '127.0.0.1'].includes(window.location.hostname) ? 'http://127.0.0.1:8000' : '');
const fmt = (n: Value | undefined, digits = 2) => n == null || !Number.isFinite(n) ? '—' : n.toLocaleString('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: digits });
const signed = (n: Value | undefined) => n == null ? '—' : `${n > 0 ? '+' : ''}${fmt(n)}`;
const color = (n: Value | undefined) => n == null ? '#64748b' : n >= 0 ? '#fb7185' : '#34d399';
const fetcher = async (url: string): Promise<FlowResponse> => { const response = await fetch(url); if (!response.ok) throw new Error('资金流向读取失败'); return response.json(); };
const liveFetcher = async (url: string): Promise<LiveResponse> => { const response = await fetch(url); if (!response.ok) throw new Error('今日快照读取失败'); return response.json(); };
const timeLabel = (value: string | null | undefined) => value?.replace('T', ' ').replace('+08:00', '') || '暂无';
const columns: [keyof Bands | 'flow_5' | 'flow_20' | 'main_pct' | 'close' | 'change_pct', string][] = [
  ['main', '主力净额'], ['net_amount', '主动买卖净额'], ['flow_5', '近5日主力'], ['flow_20', '近20日主力'], ['main_pct', '主力净占比'], ['close', '收盘价'], ['change_pct', '涨跌幅'],
];

export default function EtfSecondaryPanel() {
  const [group, setGroup] = useState('全部');
  const [ready, setReady] = useState(false);
  const [sort, setSort] = useState<{ key: typeof columns[number][0]; ascending: boolean }>({ key: 'main', ascending: false });
  useEffect(() => { try { const saved = localStorage.getItem(KEY); if (saved && CATEGORIES.includes(saved)) setGroup(saved); } catch {} setReady(true); }, []);
  useEffect(() => { if (ready) { try { localStorage.setItem(KEY, group); } catch {} } }, [group, ready]);
  const { data, error, isLoading, isValidating, mutate } = useSWR(`${API}/api/broad-etf/secondary-flows?group=${encodeURIComponent(group)}`, fetcher, { refreshInterval: 60000 });
  const { data: live, error: liveError, mutate: refreshLive } = useSWR(`${API}/api/broad-etf/secondary-live?group=${encodeURIComponent(group)}`, liveFetcher, { refreshInterval: 60000 });
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
    legend: { top: 6, textStyle: { color: '#94a3b8' } },
    grid: { top: 60, bottom: 72, left: 70, right: 25 },
    xAxis: { type: 'category', data: daily.map(d => d.trade_date), axisLabel: { color: '#94a3b8' }, axisLine: { lineStyle: { color: '#334155' } } },
    yAxis: { type: 'value', name: '净流入 / 亿元', nameTextStyle: { color: '#94a3b8' }, axisLabel: { color: '#94a3b8' }, splitLine: { lineStyle: { color: '#1e293b' } } },
    dataZoom: [{ type: 'inside', start: 50, end: 100 }, { type: 'slider', start: 50, end: 100, bottom: 16, height: 22, textStyle: { color: '#94a3b8' }, borderColor: '#334155' }],
    series: [
      { name: '主力净额', type: 'bar', barMaxWidth: 18, data: daily.map(d => ({ value: d.main, itemStyle: { color: color(d.main) } })) },
      { name: '主动买卖净额', type: 'line', showSymbol: false, connectNulls: false, data: daily.map(d => d.net_amount), lineStyle: { color: '#60a5fa', width: 1.8 }, itemStyle: { color: '#60a5fa' } },
    ],
  };
  return <section className={styles.panel}>
    <div className={styles.heading}><div><h2>二级市场资金流向</h2><p>今日快照与历史走势分开统计 · 固定宽基 ETF 样本</p></div><button className="button" onClick={() => { mutate(); refreshLive(); }} disabled={isValidating}>{isValidating ? '读取中…' : '刷新'}</button></div>
    <div className={styles.tabs}>{CATEGORIES.map(item => <button key={item} className={`button ${group === item ? 'active' : ''}`} onClick={() => setGroup(item)}>{item}</button>)}</div>
    <div className={styles.liveCard}>
      <div className={styles.liveHeading}><div><h3>今日二级市场快照 <span>东方财富 · {live?.date || '等待今日数据'}</span></h3><p className={styles.caption}>最近采集 {timeLabel(live?.checked_at)}；主力净额为大单与超大单净额之和，金额单位：亿元。</p></div><div className={styles.liveTotal}><span>{live?.summary.main == null && live?.summary.available_main != null ? '已获样本小计' : '今日主力净额'}</span><strong style={{ color: color(live?.summary.main ?? live?.summary.available_main) }}>{signed(live?.summary.main ?? live?.summary.available_main)} 亿</strong><small>{live?.summary.covered || 0} / {live?.summary.expected ?? 0} 只</small></div></div>
      {liveError && <p className={styles.notice}>今日快照接口暂不可用，正在重试。</p>}
      {live && live.summary.covered < live.summary.expected && <p className={styles.notice}>{live.summary.covered ? '今日部分 ETF 尚未返回数据，以上仅为已获样本小计，不是完整合计。' : '数据源尚未返回今日资金流向；历史数据在下方继续展示。'}不会把缺失值当作零。</p>}
      {live && live.summary.covered > 0 && <div className={styles.table}><table className={styles.liveTable}><thead><tr><th>ETF / 跟踪指数</th><th>主力净额</th><th>超大单</th><th>大单</th><th>中单</th><th>小单</th><th>采集时间</th></tr></thead><tbody>{live.funds.map(fund => <tr key={fund.ts_code}><td><b>{fund.name}</b><small>{fund.ts_code} · {fund.group}</small></td>{(['main', 'super_large', 'large', 'medium', 'small'] as const).map(key => <td key={key} style={{ color: color(fund[key]) }}>{signed(fund[key])}</td>)}<td>{timeLabel(fund.checked_at)}</td></tr>)}</tbody></table></div>}
      <p className={styles.caption}>当日数据可能延迟；与下方新浪历史数据的订单分类口径不同，不拼接计算。资金流向不能识别投资者身份，也不等于 ETF 净申购。</p>
    </div>
    {error && <p className={styles.notice}>资金流向接口暂不可用，正在重试。{data ? '下方保留上次读取结果。' : ''}</p>}
    {isLoading && !data && <p className={styles.caption}>正在读取二级市场资金流向…</p>}
    {data && summary && <>
      <div className={styles.stats}>
        {([['单日主力净额', summary.main], ['近5交易日主力', summary.flow_5], ['近20交易日主力', summary.flow_20]] as [string, Value][]).map(([label, value]) => <article key={label}><span>{label}</span><strong style={{ color: color(value) }}>{signed(value)}<small> 亿</small></strong><em>{value == null ? '数据尚未齐全' : '单笔成交额 ≥ 100 万元'}</em></article>)}
        <article><span>数据日有效样本</span><strong>{summary.covered}<small> / {summary.expected} 只</small></strong><em>最后成功采集 {data.success_at?.replace('T', ' ').replace('+08:00', '') || '暂无'}</em></article>
      </div>
      {data.stale && <p className={styles.notice}>数据源尚未提供 {data.expected_date} 的记录；下方是 {data.date || '暂无'} 的数据。新浪 ETF 历史资金流向可能次日才发布。</p>}
      {summary.covered < summary.expected && <p className={styles.notice}>部分 ETF 数据缺失，总额和滚动统计不将缺失值当作零。最新完整样本日：{data.complete_date || '暂无'}。</p>}
      {!!data.errors.length && <p className={styles.notice}>{data.errors.length} 只 ETF 最近采集失败，已保留历史数据，下一轮自动重试。</p>}
      <h3 className={styles.historyHeading}>历史走势 <span>新浪财经 · 数据日 {data.date || '等待采集'}</span></h3>
      <div className={styles.visuals}>
        <div className={styles.chartCard}><h3>二级市场净额历史 <span>柱：主力；蓝线：主动买卖总净额</span></h3><div className={styles.chart}>{daily.length ? <ReactECharts option={option} style={{ height: '100%' }} /> : <p className="empty">暂无有效资金流向数据；不会使用申赎数据代替。</p>}</div><p className={styles.caption}>可用历史 {daily[0]?.trade_date || '—'} 至 {data.date || '—'}，受数据源回溯范围限制；新增记录持续保存。</p></div>
        <aside className={styles.groups}><h3>各指数单日主力净额</h3>{data.groups.map(item => <div className={styles.group} key={item.name}><div><span>{item.name}</span><b style={{ color: color(item.main) }}>{signed(item.main)} 亿</b></div><div className={styles.track}><i style={{ width: `${Math.abs(item.main || 0) / Math.max(1, ...data.groups.map(g => Math.abs(g.main || 0))) * 100}%`, background: color(item.main) }} /></div></div>)}<p className={styles.caption}>按订单大小分类，不代表资金进入 ETF 基金资产，也不能识别国家队。</p></aside>
      </div>
      <div className={styles.tableCard}><h3>ETF 资金流向明细 <span>金额单位：亿元（占比、涨跌幅除外）· 点击表头排序</span></h3><div className={styles.table}><table><thead><tr><th>ETF / 跟踪指数</th>{columns.map(([key, label]) => <th key={key} aria-sort={sort.key === key ? sort.ascending ? 'ascending' : 'descending' : 'none'}><button onClick={() => setSort({ key, ascending: sort.key === key ? !sort.ascending : false })}>{label}{sort.key === key ? sort.ascending ? ' ↑' : ' ↓' : ''}</button></th>)}<th>数据日期</th></tr></thead><tbody>{sorted.map(fund => <tr key={fund.ts_code}><td><b>{fund.name}</b><small>{fund.ts_code} · {fund.group}</small></td>{columns.map(([key]) => <td key={key} style={{ color: color(fund[key]) }}>{key === 'close' ? fmt(fund[key], 3) : signed(fund[key])}{(key === 'main_pct' || key === 'change_pct') && fund[key] != null ? '%' : ''}</td>)}<td>{fund.data_date || '暂无'}{fund.data_date !== data.date ? '（待更新）' : ''}</td></tr>)}</tbody></table></div></div>
      <details className={styles.method}><summary>统计口径与更新时间</summary><p>数据源：新浪财经。主动买卖净额取 `netamount`，主力净额取 `r0_net`，按单笔成交额 100 万元以上分类。主力是主动买卖净额的子集，不能相加；交易方向根据行情识别，不能确认投资者身份，也不可与份额法净申购相加。</p><p>同一批固定 ETF 样本，不覆盖所有宽基产品。周一至周五北京时间 09:00—16:55 每 5 分钟检查，20:40 再检查一次；ETF 资金流向历史可能次日发布。页面每分钟读取数据库，点击刷新不会强制数据商更新。</p><p>最近检查：{data.checked_at || '尚未采集'}；历史不足 5 / 20 个交易日或窗口存在缺失时，相应统计显示“—”。</p></details>
    </>}
  </section>;
}
