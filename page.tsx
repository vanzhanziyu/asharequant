'use client';

import { useState } from 'react';
import useSWR from 'swr';
import ReactECharts from 'echarts-for-react';
import { Layers, RefreshCw } from 'lucide-react';

const fetcher = (url: string) => fetch(url).then((res) => res.json());

interface StockItem {
  stock_code: string;
  stock_name: string;
  last_price: number;
  change_pct: number;
  limit_type: string;
  status: number;
  industry: string;
  first_limit_time?: string;
  limit_num: number;
}

export default function Dashboard() {
  const [activeMainTab, setActiveMainTab] = useState<'emotion' | 'price'>('emotion');
  const [activeTab, setActiveTab] = useState<'up' | 'down'>('up');
  const [selectedPriceIndex, setSelectedPriceIndex] = useState<string>('000001.SH');

  const priceIndices = [
    { code: '000001.SH', name: '上证指数' },
    { code: '000688.SH', name: '科创50指数' },
    { code: '000015.SH', name: '红利指数' },
    { code: '399102.SZ', name: '创业板综合指数' },
    { code: '8841431.WI', name: 'Wind微盘指数' },
  ];

  const { data: turnoverRes, mutate: refreshTurnover } = useSWR(
    'http://127.0.0.1:8000/api/turnover/overview',
    fetcher,
    { refreshInterval: 30000 }
  );

  const { data: historyRes, mutate: refreshHistory } = useSWR(
    'http://127.0.0.1:8000/api/turnover/history?days=365',
    fetcher
  );

  const { data: klineRes, mutate: refreshKline } = useSWR(
    `http://127.0.0.1:8000/api/index/kline?code=${selectedPriceIndex}`,
    fetcher
  );

  const { data: upData, mutate: refreshUp } = useSWR(
    'http://127.0.0.1:8000/api/limit_stocks?limit_type=limit_up',
    fetcher,
    { refreshInterval: 30000 }
  );

  const { data: downData, mutate: refreshDown } = useSWR(
    'http://127.0.0.1:8000/api/limit_stocks?limit_type=limit_down',
    fetcher,
    { refreshInterval: 30000 }
  );

  const turnoverData = turnoverRes?.data || null;
  const klineList = klineRes?.list || [];

  const rawLimitUpList: StockItem[] = upData?.stocks || [];
  const rawLimitDownList: StockItem[] = downData?.stocks || [];
  const activeDate = upData?.date || downData?.date || turnoverData?.trade_date || '';

  const activeStockList = activeTab === 'up' ? rawLimitUpList : rawLimitDownList;

  const getKlineChartOption = () => {
    if (!klineList.length) return {};

    const dates = klineList.map((item: any) => item.trade_date);
    const values = klineList.map((item: any) => [item.open, item.close, item.low, item.high]);
    const volumes = klineList.map((item: any) => ({
      value: item.volume,
      itemStyle: { color: item.close >= item.open ? '#f43f5e' : '#10b981' }
    }));

    return {
      backgroundColor: '#0f172a',
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
        backgroundColor: '#0f172a',
        borderColor: '#334155',
        textStyle: { color: '#f8fafc', fontSize: 11 },
      },
      legend: { data: ['K线', '成交量'], textStyle: { color: '#94a3b8' }, top: 5 },
      grid: [
        { left: '5%', right: '5%', top: '15%', height: '60%' },
        { left: '5%', right: '5%', top: '80%', height: '15%' }
      ],
      xAxis: [
        {
          type: 'category',
          data: dates,
          scale: true,
          boundaryGap: false,
          axisLine: { lineStyle: { color: '#334155' } },
          axisLabel: { color: '#64748b', fontSize: 10 },
        },
        {
          type: 'category',
          gridIndex: 1,
          data: dates,
          axisTick: { show: false },
          axisLabel: { show: false },
        }
      ],
      yAxis: [
        {
          scale: true,
          splitLine: { lineStyle: { color: '#1e293b' } },
          axisLabel: { color: '#64748b', fontSize: 10 },
        },
        {
          scale: true,
          gridIndex: 1,
          splitNumber: 2,
          axisLabel: { show: false },
          axisLine: { show: false },
          splitLine: { show: false }
        }
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: [0, 1], start: 50, end: 100 },
        { type: 'slider', xAxisIndex: [0, 1], bottom: 2, height: 16, borderColor: '#334155', textStyle: { color: '#64748b' } }
      ],
      series: [
        {
          name: 'K线',
          type: 'candlestick',
          data: values,
          itemStyle: {
            color: '#f43f5e',
            color0: '#10b981',
            borderColor: '#f43f5e',
            borderColor0: '#10b981',
          }
        },
        {
          name: '成交量',
          type: 'bar',
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: volumes
        }
      ]
    };
  };

  const handleRefresh = () => {
    refreshTurnover();
    refreshHistory();
    refreshUp();
    refreshDown();
    refreshKline();
  };

  return (
    <div className="h-screen bg-slate-950 text-slate-100 p-4 font-sans flex flex-col overflow-hidden">
      <header className="flex justify-between items-center mb-3 border-b border-slate-800 pb-2.5 shrink-0">
        <div className="flex items-center gap-6">
          <div>
            <h1 className="text-lg font-bold tracking-tight text-slate-50 flex items-center gap-2">
              <Layers className="text-blue-500 w-5 h-5" />
              东方财富 & 万得 市场情绪监控终端
            </h1>
            <p className="text-[11px] text-slate-400 mt-0.5">
              监控基准交易日：<span className="text-blue-400 font-mono">{activeDate || '数据准备中...'}</span>
            </p>
          </div>

          <div className="flex bg-slate-900 p-1 rounded-lg border border-slate-800">
            <button
              onClick={() => setActiveMainTab('emotion')}
              className={`px-3 py-1 text-xs font-semibold rounded-md transition ${activeMainTab === 'emotion' ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-slate-200'}`}
            >
              市场情绪监控
            </button>
            <button
              onClick={() => setActiveMainTab('price')}
              className={`px-3 py-1 text-xs font-semibold rounded-md transition ${activeMainTab === 'price' ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-slate-200'}`}
            >
              价格监控 (K线)
            </button>
          </div>
        </div>

        <button
          onClick={handleRefresh}
          className="flex items-center gap-1.5 px-2.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-xs font-medium rounded-lg border border-slate-700 transition cursor-pointer"
        >
          <RefreshCw className="w-3.5 h-3.5 text-blue-400" /> 刷新数据
        </button>
      </header>

      {activeMainTab === 'emotion' ? (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 flex-1 min-h-0">
          <div className="lg:col-span-5 flex flex-col overflow-y-auto pr-1">
            <div className="text-sm text-slate-400 p-4 bg-slate-900 rounded-xl border border-slate-800">
              市场情绪概览与成交额数据已正常加载...
            </div>
          </div>

          <div className="lg:col-span-7 bg-slate-900 border border-slate-800 rounded-xl p-3 flex flex-col h-full overflow-hidden">
            <div className="flex justify-between items-center mb-2 border-b border-slate-800 pb-2">
              <div className="flex gap-2">
                <button onClick={() => setActiveTab('up')} className={`px-3 py-1 text-xs font-bold rounded-lg ${activeTab === 'up' ? 'bg-rose-600/20 text-rose-400 border border-rose-500/30' : 'text-slate-400'}`}>涨停股票池</button>
                <button onClick={() => setActiveTab('down')} className={`px-3 py-1 text-xs font-bold rounded-lg ${activeTab === 'down' ? 'bg-emerald-600/20 text-emerald-400 border border-emerald-500/30' : 'text-slate-400'}`}>跌停股票池</button>
              </div>
            </div>
            <div className="flex-1 overflow-y-auto">
              <table className="w-full text-left border-collapse text-xs">
                <thead>
                  <tr className="text-slate-400 border-b border-slate-800">
                    <th className="py-1.5">代码</th>
                    <th>名称</th>
                    <th>最新价</th>
                    <th>涨跌幅</th>
                    <th>连板</th>
                    <th>行业</th>
                  </tr>
                </thead>
                <tbody>
                  {activeStockList.map((s, idx) => (
                    <tr key={idx} className="border-b border-slate-800/50 hover:bg-slate-800/40">
                      <td className="py-1.5 font-mono text-slate-300">{s.stock_code}</td>
                      <td className="font-bold text-slate-200">{s.stock_name}</td>
                      <td className="font-mono text-slate-300">{s.last_price}</td>
                      <td className={`font-mono font-semibold ${s.change_pct >= 0 ? 'text-rose-400' : 'text-emerald-400'}`}>+{s.change_pct}%</td>
                      <td><span className="px-1.5 py-0.5 bg-slate-800 text-amber-400 text-[10px] rounded">{s.limit_num}连板</span></td>
                      <td className="text-slate-400">{s.industry}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      ) : (
        <div className="flex flex-col flex-1 bg-slate-900 border border-slate-800 rounded-xl p-4 gap-4 overflow-hidden">
          <div className="flex items-center gap-3 shrink-0 flex-wrap">
            <span className="text-xs text-slate-400 font-medium">选择监控指数：</span>
            {priceIndices.map((idx) => (
              <button
                key={idx.code}
                onClick={() => setSelectedPriceIndex(idx.code)}
                className={`px-3.5 py-1.5 rounded-lg text-xs font-semibold transition cursor-pointer ${
                  selectedPriceIndex === idx.code
                    ? 'bg-blue-600 text-white shadow-lg shadow-blue-600/30'
                    : 'bg-slate-800 text-slate-300 hover:bg-slate-700 border border-slate-700'
                }`}
              >
                {idx.name} <span className="text-[10px] opacity-70 ml-1 font-mono">({idx.code})</span>
              </button>
            ))}
          </div>

          <div className="flex-1 w-full min-h-0 bg-slate-950 rounded-xl border border-slate-800/80 p-2">
            <ReactECharts
              option={getKlineChartOption()}
              style={{ width: '100%', height: '100%' }}
              notMerge={true}
            />
          </div>
        </div>
      )}
    </div>
  );
}
