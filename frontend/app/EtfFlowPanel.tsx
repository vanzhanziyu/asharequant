'use client';

import { useEffect, useMemo, useState } from 'react';
import useSWR from 'swr';
import ReactECharts from 'echarts-for-react';
import styles from './EtfFlowPanel.module.css';

type Value = number | null;
type Flows = { net_flow: Value; flow_5: Value; flow_20: Value };
type Fund = Flows & { ts_code: string; name: string; group: string; nav: Value; shares: Value; assets: Value; amount: Value; volume_multiple: Value; status: string };
type Response = {
  date: string | null; complete_date: string | null; checked_at: string | null;
  summary: Flows & { covered: number; expected: number };
  categories: string[]; funds: Fund[]; groups: (Flows & { name: string })[];
  daily: { trade_date: string; net_flow: Value; available_flow: Value; covered: number; expected: number; cumulative: Value }[];
  errors: { ts_code: string; error: string }[]; method: string;
};
const KEY = 'ashare-broad-etf-group-v1';
const API = process.env.NEXT_PUBLIC_API_BASE_URL || (typeof window !== 'undefined' && ['localhost', '127.0.0.1'].includes(window.location.hostname) ? 'http://127.0.0.1:8000' : '');
const fmt = (n: Value | undefined, digits = 2) => n == null ? '—' : n.toLocaleString('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: digits });
const signed = (n: Value | undefined) => n == null ? '—' : `${n > 0 ? '+' : ''}${fmt(n)}`;
const color = (n: Value | undefined) => n == null ? '#64748b' : n >= 0 ? '#fb7185' : '#34d399';
const fetcher = async (url: string): Promise<Response> => { const res = await fetch(url); if (!res.ok) throw new Error(`接口请求失败 (${res.status})`); return res.json(); };

export default function EtfFlowPanel() {
  const [group, setGroup] = useState('全部');
  const [ready, setReady] = useState(false);
  const [sort, setSort] = useState<{ key: 'net_flow' | 'flow_5' | 'flow_20' | 'assets' | 'amount'; ascending: boolean }>({ key: 'net_flow', ascending: false });
  useEffect(() => { const saved = localStorage.getItem(KEY); if (saved && ['全部', '沪深300', '上证50', '中证500', '中证1000', '中证A500', '科创50', '创业板'].includes(saved)) setGroup(saved); setReady(true); }, []);
  useEffect(() => { if (ready) localStorage.setItem(KEY, group); }, [group, ready]);
  const { data, error, isLoading, isValidating, mutate } = useSWR<Response>(`${API}/api/broad-etf/flows?group=${encodeURIComponent(group)}`, fetcher, { refreshInterval: 60000 });
  const sorted = useMemo(() => [...(data?.funds || [])].sort((a, b) => {
    const x = a[sort.key], y = b[sort.key];
    if (x == null) return y == null ? 0 : 1;
    if (y == null) return -1;
    return (x - y) * (sort.ascending ? 1 : -1);
  }), [data, sort]);
  const daily = data?.daily || [];
  const summary = data?.summary;
  const setOrder = (key: typeof sort.key) => setSort({ key, ascending: key === sort.key ? !sort.ascending : false });
  const option = {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', backgroundColor: '#0f172a', borderColor: '#334155', textStyle: { color: '#e2e8f0' }, valueFormatter: (v: Value) => v == null ? '待补齐' : `${fmt(v)} 亿` },
    legend: { top: 6, textStyle: { color: '#94a3b8' }, data: ['单日估算净申购', '累计估算净申购'] },
    grid: { top: 56, bottom: 72, left: 68, right: 78 },
    xAxis: { type: 'category', data: daily.map(d => d.trade_date), axisLabel: { color: '#94a3b8' }, axisLine: { lineStyle: { color: '#334155' } } },
    yAxis: [
      { type: 'value', name: '单日 / 亿元', nameTextStyle: { color: '#94a3b8' }, axisLabel: { color: '#94a3b8' }, splitLine: { lineStyle: { color: '#1e293b' } } },
      { type: 'value', name: '累计 / 亿元', nameTextStyle: { color: '#94a3b8' }, axisLabel: { color: '#94a3b8' }, splitLine: { show: false } },
    ],
    dataZoom: [{ type: 'inside', start: 75, end: 100 }, { type: 'slider', start: 75, end: 100, bottom: 16, height: 22, textStyle: { color: '#94a3b8' }, borderColor: '#334155' }],
    series: [
      { name: '单日估算净申购', type: 'bar', barMaxWidth: 16, data: daily.map(d => ({ value: d.net_flow, itemStyle: { color: color(d.net_flow) } })) },
      { name: '累计估算净申购', type: 'line', yAxisIndex: 1, showSymbol: false, connectNulls: false, data: daily.map(d => d.cumulative), lineStyle: { color: '#60a5fa', width: 2 }, itemStyle: { color: '#60a5fa' } },
    ],
  };
  return <section className={styles.panel}>
    <div className={styles.heading}><div><h2>宽基净流入统计</h2><p>选定 ETF 样本 · 份额法估算净申购 · 数据日 {data?.date || '等待采集'}</p></div><button className="button" onClick={() => mutate()} disabled={isValidating}>{isValidating ? '更新中…' : '刷新'}</button></div>
    <div className={styles.tabs}>{['全部', ...(data?.categories || ['沪深300', '上证50', '中证500', '中证1000', '中证A500', '科创50', '创业板'])].map(item => <button key={item} className={`button ${group === item ? 'active' : ''}`} onClick={() => setGroup(item)}>{item}</button>)}</div>
    {error && <div className="error">宽基数据暂不可用，请稍后刷新。</div>}
    {isLoading && !data && <p className={styles.notice}>正在读取宽基历史数据…</p>}
    {data && summary && <>
      <div className={styles.stats}>
        {([['单日估算净申购', summary.net_flow], ['近5交易日', summary.flow_5], ['近20交易日', summary.flow_20]] as [string, Value][]).map(([label, value]) => <article key={label}><span>{label}</span><strong style={{ color: color(value) }}>{signed(value)}<small> 亿</small></strong><em>{value == null ? '数据尚未齐全' : '正值净申购 / 负值净赎回'}</em></article>)}
        <article><span>当日有效样本</span><strong>{summary.covered}<small> / {summary.expected} 只</small></strong><em>所选指数样本，非全市场 ETF</em></article>
      </div>
      {summary.covered < summary.expected && <p className={styles.notice}>份额或净值仍待补齐，最新完整样本日：{data.complete_date || '暂无'}。总额缺失时不按零处理。</p>}
      {!!data.errors.length && <p className={styles.notice}>部分接口同步失败，已保留历史数据，后续定时任务会重试。</p>}
      <div className={styles.visuals}>
        <div className={styles.chartCard}><h3>资金流向历史 <span>红色净申购 · 绿色净赎回</span></h3><div className={styles.chart}>{daily.length ? <ReactECharts option={option} notMerge style={{ height: '100%' }} /> : <p className="empty">正在等待首轮采集</p>}</div><p className={styles.caption}>累计值从首个完整样本日开始；缺失日期保留断点。滚轮缩放查看历史。</p></div>
        <aside className={styles.groups}><h3>各指数单日净申购</h3>{data.groups.map(item => {
          const max = Math.max(1, ...data.groups.map(g => Math.abs(g.net_flow || 0)));
          return <div className={styles.group} key={item.name}><div><span>{item.name}</span><b style={{ color: color(item.net_flow) }}>{signed(item.net_flow)} 亿</b></div><div className={styles.track}><i style={{ width: `${Math.abs(item.net_flow || 0) / max * 100}%`, background: color(item.net_flow) }} /></div></div>;
        })}<p className={styles.caption}>按所列 ETF 汇总。大额净申购反映资金动向，不能确认国家队身份。</p></aside>
      </div>
      <div className={styles.tableCard}><h3>ETF 明细 <span>点击数值表头排序</span></h3><div className={styles.table}><table><thead><tr><th>ETF / 跟踪指数</th>{([['net_flow', '单日净申购'], ['flow_5', '近5日'], ['flow_20', '近20日'], ['assets', '规模'], ['amount', '成交额']] as [typeof sort.key, string][]).map(([key, label]) => <th key={key} aria-sort={sort.key === key ? sort.ascending ? 'ascending' : 'descending' : 'none'}><button onClick={() => setOrder(key)}>{label}（亿）{sort.key === key ? sort.ascending ? ' ↑' : ' ↓' : ''}</button></th>)}<th>成交额 / 前20日均值</th><th>数据状态</th></tr></thead><tbody>{sorted.map(fund => <tr key={fund.ts_code}><td><b>{fund.name}</b><small>{fund.ts_code} · {fund.group}</small></td><td style={{ color: color(fund.net_flow) }}>{signed(fund.net_flow)}</td><td style={{ color: color(fund.flow_5) }}>{signed(fund.flow_5)}</td><td style={{ color: color(fund.flow_20) }}>{signed(fund.flow_20)}</td><td>{fmt(fund.assets)}</td><td>{fmt(fund.amount)}</td><td>{fmt(fund.volume_multiple)} 倍</td><td>{fund.status}</td></tr>)}</tbody></table></div></div>
      <details className={styles.method}><summary>统计口径与更新时间</summary><p>{data.method} ETF 申赎也可用一篮子证券完成，金额是估算的申赎价值，并非实际现金到账。</p><p>样本固定为表中 ETF，不代表所有宽基产品。大额流入可能来自多类投资者；国家队身份需结合公开持仓披露或公告核实。疑似份额折算不计入，待核实后回补。</p><p>云端每日 09:10、10:10、13:40、20:40、22:40（北京时间）同步并存入数据库。份额和净值分批发布，通常需次日确认前一交易日数据。最近检查：{data.checked_at || '尚未采集'}。</p></details>
    </>}
  </section>;
}
