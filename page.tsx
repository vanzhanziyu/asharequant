'use client';

import { useState } from 'react';
import useSWR from 'swr';
import ReactECharts from 'echarts-for-react';
import {
  TrendingUp,
  TrendingDown,
  RefreshCw,
  Layers,
  ArrowUpRight,
  ArrowDownRight,
  LineChart,
  ChevronUp,
  Coins,
  BarChart2,
  Calendar,
} from 'lucide-react';

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

interface IndustrySummary {
  industry: string;
  count: number;
}

interface TurnoverOverview {
  trade_date: string;
  standard_date?: string;
  wind_micro_date?: string;
  total_amount: number;
  sh_amount: number;
  sz_amount: number;
  cyb_amount: number;
  kc50_amount: number;
  hl_amount: number;
  wind_micro_amount?: number;
  total_diff?: number;
  cyb_diff?: number;
  kc50_diff?: number;
  hl_diff?: number;
  wind_micro_diff?: number;
}

interface MarginItem {
  trade_date: string;
  rzrqye: number;
}

const getUpColor = (ratio: number) => {
  if (ratio > 0.7) return '#f43f5e';
  if (ratio > 0.4) return '#e11d48';
  if (ratio > 0.2) return '#be123c';
  return '#9f1239';
};

const getDownColor = (ratio: number) => {
  if (ratio > 0.7) return '#10b981';
  if (ratio > 0.4) return '#059669';
  if (ratio > 0.2) return '#047857';
  return '#065f46';
};

export default function Dashboard() {
  const [activeMainView, setActiveMainView] = useState<'emotion' | 'price'>('emotion');
  const [activeTab, setActiveTab] = useState<'up' | 'down'>('up');
  const [selectedPriceIndex, setSelectedPriceIndex] = useState<string>('000001.SH');

  // 💡 用于记录当前被点击锁定的行业名称
  const [selectedIndustry, setSelectedIndustry] = useState<string | null>(null);

  const priceIndices = [
    { code: '000001.SH', name: '上证指数' },
    { code: '000688.SH', name: '科创50指数' },
    { code: '000015.SH', name: '红利指数' },
    { code: '399102.SZ', name: '创业板综合指数' },
    { code: '8841423.WI', name: 'Wind微盘指数' },
  ];

  const [expandedCards, setExpandedCards] = useState<Record<string, boolean>>({
    margin: false,
    total: false,
    cyb: false,
    kc50: false,
    hl: false,
    windMicro: false,
  });

  const toggleCard = (key: string) => {
    setExpandedCards((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const { data: marginRes, mutate: refreshMargin } = useSWR(
    'http://127.0.0.1:8000/api/margin',
    fetcher
  );

  const { data: turnoverRes, mutate: refreshTurnover } = useSWR(
    'http://127.0.0.1:8000/api/turnover/overview',
    fetcher,
    { refreshInterval: 30000 }
  );

  const { data: historyRes, mutate: refreshHistory } = useSWR(
    'http://127.0.0.1:8000/api/turnover/history?days=365',
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

  const { data: klineRes, mutate: refreshKline } = useSWR(
    `http://127.0.0.1:8000/api/index/kline?code=${selectedPriceIndex}`,
    fetcher
  );

  const marginList: MarginItem[] = Array.isArray(marginRes) ? marginRes : marginRes?.data || [];
  const latestMargin = marginList.length > 0 ? marginList[marginList.length - 1] : null;
  const prevMargin = marginList.length > 1 ? marginList[marginList.length - 2] : null;
  const marginDiff =
    latestMargin && prevMargin
      ? Number((latestMargin.rzrqye - prevMargin.rzrqye).toFixed(2))
      : 0;

  const turnoverData: TurnoverOverview | null = turnoverRes?.data || null;
  const historyList = historyRes?.list || [];
  const klineList = klineRes?.list || [];

  const validWindHistory = historyList.filter((item: any) => item.wind_micro_amount && item.wind_micro_amount > 0);
  const latestValidWind = validWindHistory.length > 0 ? validWindHistory[validWindHistory.length - 1] : null;
  const prevValidWind = validWindHistory.length > 1 ? validWindHistory[validWindHistory.length - 2] : null;

  const displayWindAmount = latestValidWind ? latestValidWind.wind_micro_amount : (turnoverData?.wind_micro_amount || 0);

  const displayWindDate = turnoverData?.wind_micro_date || (latestValidWind ? latestValidWind.trade_date : '--');
  const standardDate = turnoverData?.standard_date || turnoverData?.trade_date || '--';

  const displayWindDiff = prevValidWind ? Number((displayWindAmount - prevValidWind.wind_micro_amount).toFixed(2)) : (turnoverData?.wind_micro_diff || 0);

  const rawLimitUpList: StockItem[] = upData?.stocks || [];
  const rawLimitDownList: StockItem[] = downData?.stocks || [];
  const activeDate = upData?.date || downData?.date || standardDate;

  const rawUpIndustrySummary: IndustrySummary[] = upData?.industry_summary || [];
  const rawDownIndustrySummary: IndustrySummary[] = downData?.industry_summary || [];

  const baseStockList = activeTab === 'up' ? rawLimitUpList : rawLimitDownList;

  // 💡 过滤逻辑：如果点击了某个行业，则表格仅展示该行业股票；否则展示全部
  const activeStockList = selectedIndustry
    ? baseStockList.filter((stock) => stock.industry === selectedIndustry)
    : baseStockList;

  const activeIndustrySummary = activeTab === 'up' ? rawUpIndustrySummary : rawDownIndustrySummary;

  const getMaxTurnoverInfo = (dataKey: string) => {
    if (!historyList.length) return { maxVal: 0, date: '--' };
    let maxItem = historyList[0];
    for (let i = 1; i < historyList.length; i++) {
      if ((historyList[i][dataKey] || 0) > (maxItem[dataKey] || 0)) {
        maxItem = historyList[i];
      }
    }
    return {
      maxVal: maxItem[dataKey] || 0,
      date: maxItem.trade_date || '--',
    };
  };

  const getMaxMarginInfo = () => {
    if (!marginList.length) return { maxVal: 0, date: '--' };
    let maxItem = marginList[0];
    for (let i = 1; i < marginList.length; i++) {
      if ((marginList[i].rzrqye || 0) > (maxItem.rzrqye || 0)) {
        maxItem = marginList[i];
      }
    }
    return {
      maxVal: maxItem.rzrqye || 0,
      date: maxItem.trade_date || '--',
    };
  };

  const getMarginChartOption = () => {
    const dates = marginList.map((item) => item.trade_date);
    const values = marginList.map((item) => item.rzrqye || 0);

    return {
      tooltip: {
        trigger: 'axis',
        backgroundColor: '#0f172a',
        borderColor: '#334155',
        textStyle: { color: '#f8fafc', fontSize: 11 },
        formatter: '{b}<br/>两融余额: <b>{c} 亿元</b>',
      },
      grid: { left: '3%', right: '3%', bottom: '18%', top: '18%', containLabel: true },
      dataZoom: [
        { type: 'inside', start: 70, end: 100 },
        {
          type: 'slider',
          show: true,
          bottom: '2%',
          height: 18,
          borderColor: '#334155',
          fillerColor: 'rgba(245, 158, 11, 0.2)',
          handleStyle: { color: '#f59e0b' },
          textStyle: { color: '#64748b', fontSize: 9 },
          start: 70,
          end: 100,
        },
      ],
      xAxis: {
        type: 'category',
        data: dates,
        axisLine: { lineStyle: { color: '#334155' } },
        axisLabel: { color: '#64748b', fontSize: 9 },
      },
      yAxis: {
        type: 'value',
        scale: true,
        splitLine: { lineStyle: { color: '#1e293b' } },
        axisLabel: { color: '#64748b', fontSize: 9 },
      },
      series: [
        {
          name: '两融余额',
          type: 'line',
          smooth: true,
          showSymbol: false,
          data: values,
          itemStyle: { color: '#f59e0b' },
          areaStyle: {
            color: {
              type: 'linear',
              x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: '#f59e0b40' },
                { offset: 1, color: '#f59e0b00' },
              ],
            },
          },
        },
      ],
    };
  };

  const getSingleChartOption = (dataKey: string, color: string, titleName: string) => {
    const targetList = dataKey === 'wind_micro_amount'
      ? historyList.filter((item: any) => item[dataKey] && item[dataKey] > 0)
      : historyList;

    const dates = targetList.map((item: any) => item.trade_date);
    const values = targetList.map((item: any) => item[dataKey] || 0);

    return {
      tooltip: {
        trigger: 'axis',
        backgroundColor: '#0f172a',
        borderColor: '#334155',
        textStyle: { color: '#f8fafc', fontSize: 11 },
        formatter: '{b}<br/>成交额: <b>{c} 亿</b>',
      },
      grid: { left: '3%', right: '3%', bottom: '18%', top: '18%', containLabel: true },
      dataZoom: [
        { type: 'inside', start: 70, end: 100 },
        {
          type: 'slider',
          show: true,
          bottom: '2%',
          height: 18,
          borderColor: '#334155',
          fillerColor: `${color}30`,
          handleStyle: { color },
          textStyle: { color: '#64748b', fontSize: 9 },
          start: 70,
          end: 100,
        },
      ],
      xAxis: {
        type: 'category',
        data: dates,
        axisLine: { lineStyle: { color: '#334155' } },
        axisLabel: { color: '#64748b', fontSize: 9 },
      },
      yAxis: {
        type: 'value',
        splitLine: { lineStyle: { color: '#1e293b' } },
        axisLabel: { color: '#64748b', fontSize: 9 },
      },
      series: [
        {
          name: titleName,
          type: 'line',
          smooth: true,
          showSymbol: false,
          data: values,
          itemStyle: { color },
          areaStyle: {
            color: {
              type: 'linear',
              x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: `${color}40` },
                { offset: 1, color: `${color}00` },
              ],
            },
          },
        },
      ],
    };
  };

  const getKlineChartOption = () => {
    if (!klineList.length) return {};

    const dates = klineList.map((item: any) => item.trade_date);
    const values = klineList.map((item: any) => [item.open, item.close, item.low, item.high]);
    const volumes = klineList.map((item: any) => ({
      value: item.volume,
      itemStyle: { color: item.close >= item.open ? '#f43f5e' : '#10b981' }
    }));

    return {
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
        backgroundColor: '#0f172a',
        borderColor: '#334155',
        textStyle: { color: '#f8fafc', fontSize: 11 },
      },
      legend: { data: ['K线', '成交量'], textStyle: { color: '#94a3b8' }, top: 0 },
      grid: [
        { left: '5%', right: '5%', top: '12%', height: '62%' },
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

  const getTreemapOption = (summaryData: IndustrySummary[], isUp: boolean) => {
    if (!summaryData.length) return {};
    const maxVal = Math.max(...summaryData.map((item) => item.count), 1);

    const formattedData = summaryData.map((item) => {
      const name = item.industry || '未分类';
      const value = item.count;
      const ratio = value / maxVal;
      const isSelected = selectedIndustry === name;

      return {
        name,
        value,
        itemStyle: {
          color: isUp ? getUpColor(ratio) : getDownColor(ratio),
          borderColor: isSelected ? '#38bdf8' : '#020617',
          borderWidth: isSelected ? 3 : 1.5,
        },
      };
    });

    return {
      tooltip: {
        formatter: '{b}: {c} 家',
        backgroundColor: '#0f172a',
        borderColor: '#334155',
        textStyle: { color: '#f8fafc', fontSize: 12 },
      },
      series: [
        {
          type: 'treemap',
          data: formattedData,
          left: 0, top: 0, right: 0, bottom: 0,
          width: '100%', height: '100%',
          roam: false,
          nodeClick: false,
          breadcrumb: { show: false },
          label: {
            show: true,
            formatter: (params: any) => `${params.name}\n${params.value}家`,
            fontSize: 10,
            color: '#ffffff',
          },
          // 💡 修复：禁用悬停时的额外边框高亮变形，避免多区块同时出现高亮框
          emphasis: {
            itemStyle: {
              borderColor: undefined,
              borderWidth: undefined,
            },
          },
          select: {
            itemStyle: {
              borderColor: '#38bdf8',
              borderWidth: 3,
            },
          },
        },
      ],
    };
  };

  const handleRefresh = () => {
    refreshMargin();
    refreshTurnover();
    refreshHistory();
    refreshUp();
    refreshDown();
    refreshKline();
  };

  const renderDiffBadge = (diff?: number) => {
    if (diff === undefined || diff === null) return null;
    const isUp = diff >= 0;
    return (
      <div className={`flex items-center text-[11px] font-semibold mt-0.5 ${isUp ? 'text-rose-400' : 'text-emerald-400'}`}>
        {isUp ? <ArrowUpRight className="w-3 h-3 mr-0.5" /> : <ArrowDownRight className="w-3 h-3 mr-0.5" />}
        较上日 {isUp ? `+${diff}` : diff} 亿
      </div>
    );
  };

  const maxMarginInfo = getMaxMarginInfo();

  return (
    <div className="h-screen bg-slate-950 text-slate-100 p-4 font-sans flex flex-col overflow-hidden">
      <header className="flex justify-between items-center mb-3 border-b border-slate-800 pb-2.5 shrink-0">
        <div className="flex items-center gap-6">
          <div>
            <h1 className="text-lg font-bold tracking-tight text-slate-50 flex items-center gap-2">
              <Layers className="text-blue-500 w-5 h-5" />
              东方财富 & 万得 市场监控终端
            </h1>
            <p className="text-[11px] text-slate-400 mt-0.5">
              监控基准交易日：<span className="text-blue-400 font-mono">{activeDate || '数据准备中...'}</span>
            </p>
          </div>

          <div className="flex bg-slate-900 p-1 rounded-lg border border-slate-800">
            <button
              onClick={() => setActiveMainView('emotion')}
              className={`px-3 py-1 text-xs font-semibold rounded-md transition cursor-pointer ${
                activeMainView === 'emotion' ? 'bg-blue-600 text-white shadow' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              市场情绪监控看板
            </button>
            <button
              onClick={() => setActiveMainView('price')}
              className={`px-3 py-1 text-xs font-semibold rounded-md transition cursor-pointer ${
                activeMainView === 'price' ? 'bg-blue-600 text-white shadow' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              价格走势看板 (K线)
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

      {activeMainView === 'emotion' ? (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 flex-1 min-h-0">
          <div className="lg:col-span-5 flex flex-col overflow-y-auto pr-1">
            <div className="grid grid-cols-2 gap-3">
              {/* 1. 两融余额 */}
              {(() => {
                const isExpanded = expandedCards.margin;
                return (
                  <div className="bg-slate-900 border border-amber-500/20 rounded-xl p-3 shadow-md flex flex-col justify-between transition-all col-span-2">
                    <div>
                      <div className="flex justify-between items-center mb-1">
                        <span className="text-xs text-amber-400 font-bold flex items-center gap-1">
                          <Coins className="w-3.5 h-3.5" /> 融资融券余额 (近1年)
                        </span>
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] text-slate-400 flex items-center gap-0.5 bg-slate-800/80 px-1.5 py-0.5 rounded font-mono">
                            <Calendar className="w-3 h-3 text-amber-400" /> {latestMargin?.trade_date ?? '--'}
                          </span>
                          <button
                            onClick={() => toggleCard('margin')}
                            className="text-amber-400 hover:text-amber-300 p-0.5 rounded cursor-pointer"
                          >
                            {isExpanded ? <ChevronUp className="w-4 h-4" /> : <LineChart className="w-3.5 h-3.5" />}
                          </button>
                        </div>
                      </div>
                      <div className="flex items-baseline justify-between">
                        <div className="text-xl font-extrabold text-slate-50 font-mono">
                          {latestMargin?.rzrqye?.toLocaleString() ?? '--'}
                          <span className="text-[10px] text-slate-400 font-normal ml-0.5">亿</span>
                        </div>
                        {renderDiffBadge(marginDiff)}
                      </div>
                    </div>
                    <div className="text-[10px] text-slate-400 mt-1.5 border-t border-slate-800/60 pt-1 flex justify-between">
                      <span>数据源: 交易所/Tushare</span>
                      <span>最高: <strong className="text-amber-400">{maxMarginInfo.maxVal} 亿</strong> ({maxMarginInfo.date})</span>
                    </div>
                    {isExpanded && (
                      <div className="mt-2 pt-2 border-t border-slate-800">
                        <div className="h-52">
                          <ReactECharts option={getMarginChartOption()} style={{ height: '100%', width: '100%' }} />
                        </div>
                      </div>
                    )}
                  </div>
                );
              })()}

              {/* 2. 两市总成交额 */}
              {(() => {
                const isExpanded = expandedCards.total;
                const maxInfo = getMaxTurnoverInfo('total_amount');
                return (
                  <div className="bg-slate-900 border border-blue-500/20 rounded-xl p-3 shadow-md flex flex-col justify-between transition-all col-span-2">
                    <div>
                      <div className="flex justify-between items-center mb-1">
                        <span className="text-xs text-blue-400 font-bold flex items-center gap-1">
                          <BarChart2 className="w-3.5 h-3.5" /> 两市总成交额
                        </span>
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] text-slate-400 flex items-center gap-0.5 bg-slate-800/80 px-1.5 py-0.5 rounded font-mono">
                            <Calendar className="w-3 h-3 text-blue-400" /> {standardDate}
                          </span>
                          <button onClick={() => toggleCard('total')} className="text-blue-400 hover:text-blue-300 p-0.5 rounded cursor-pointer">
                            {isExpanded ? <ChevronUp className="w-4 h-4" /> : <LineChart className="w-3.5 h-3.5" />}
                          </button>
                        </div>
                      </div>
                      <div className="flex items-baseline justify-between">
                        <div className="text-xl font-extrabold text-slate-50 font-mono">
                          {turnoverData?.total_amount ? turnoverData.total_amount.toLocaleString() : '--'}
                          <span className="text-[10px] text-slate-400 font-normal ml-0.5">亿</span>
                        </div>
                        {renderDiffBadge(turnoverData?.total_diff)}
                      </div>
                    </div>
                    <div className="text-[10px] text-slate-400 mt-2 border-t border-slate-800/60 pt-1.5 flex justify-between">
                      <span>沪: {turnoverData?.sh_amount ?? '--'} 亿 | 深: {turnoverData?.sz_amount ?? '--'} 亿</span>
                      <span>最高: <strong className="text-blue-400">{maxInfo.maxVal} 亿</strong> ({maxInfo.date})</span>
                    </div>
                    {isExpanded && (
                      <div className="mt-2 pt-2 border-t border-slate-800">
                        <div className="h-52">
                          <ReactECharts option={getSingleChartOption('total_amount', '#3b82f6', '两市总成交额')} style={{ height: '100%', width: '100%' }} />
                        </div>
                      </div>
                    )}
                  </div>
                );
              })()}

              {/* 3. 创业板综 */}
              {(() => {
                const isExpanded = expandedCards.cyb;
                return (
                  <div className={`bg-slate-900 border border-slate-800 rounded-xl p-3 shadow-md flex flex-col justify-between transition-all ${isExpanded ? 'col-span-2' : 'col-span-1'}`}>
                    <div>
                      <div className="flex justify-between items-center mb-1">
                        <span className="text-xs text-slate-300 font-bold">创业板综</span>
                        <div className="flex items-center gap-1.5">
                          <span className="text-[9px] text-slate-400 bg-slate-800/80 px-1 py-0.5 rounded font-mono">
                            {standardDate}
                          </span>
                          <button onClick={() => toggleCard('cyb')} className="text-cyan-400 hover:text-cyan-300 p-0.5 rounded cursor-pointer">
                            {isExpanded ? <ChevronUp className="w-4 h-4" /> : <LineChart className="w-3.5 h-3.5" />}
                          </button>
                        </div>
                      </div>
                      <div className="text-xl font-extrabold text-slate-50 font-mono">
                        {turnoverData?.cyb_amount ? turnoverData.cyb_amount.toLocaleString() : '--'}
                        <span className="text-[10px] text-slate-400 font-normal ml-0.5">亿</span>
                      </div>
                      {renderDiffBadge(turnoverData?.cyb_diff)}
                    </div>
                    <div className="text-[10px] text-slate-400 mt-2 border-t border-slate-800/60 pt-1.5 font-mono">399102.SZ</div>
                    {isExpanded && (
                      <div className="mt-2 pt-2 border-t border-slate-800">
                        <div className="h-52">
                          <ReactECharts option={getSingleChartOption('cyb_amount', '#06b6d4', '创业板综成交额')} style={{ height: '100%', width: '100%' }} />
                        </div>
                      </div>
                    )}
                  </div>
                );
              })()}

              {/* 4. 科创50 */}
              {(() => {
                const isExpanded = expandedCards.kc50;
                return (
                  <div className={`bg-slate-900 border border-slate-800 rounded-xl p-3 shadow-md flex flex-col justify-between transition-all ${isExpanded ? 'col-span-2' : 'col-span-1'}`}>
                    <div>
                      <div className="flex justify-between items-center mb-1">
                        <span className="text-xs text-slate-300 font-bold">科创50</span>
                        <div className="flex items-center gap-1.5">
                          <span className="text-[9px] text-slate-400 bg-slate-800/80 px-1 py-0.5 rounded font-mono">
                            {standardDate}
                          </span>
                          <button onClick={() => toggleCard('kc50')} className="text-purple-400 hover:text-purple-300 p-0.5 rounded cursor-pointer">
                            {isExpanded ? <ChevronUp className="w-4 h-4" /> : <LineChart className="w-3.5 h-3.5" />}
                          </button>
                        </div>
                      </div>
                      <div className="text-xl font-extrabold text-slate-50 font-mono">
                        {turnoverData?.kc50_amount ? turnoverData.kc50_amount.toLocaleString() : '--'}
                        <span className="text-[10px] text-slate-400 font-normal ml-0.5">亿</span>
                      </div>
                      {renderDiffBadge(turnoverData?.kc50_diff)}
                    </div>
                    <div className="text-[10px] text-slate-400 mt-2 border-t border-slate-800/60 pt-1.5 font-mono">000688.SH</div>
                    {isExpanded && (
                      <div className="mt-2 pt-2 border-t border-slate-800">
                        <div className="h-52">
                          <ReactECharts option={getSingleChartOption('kc50_amount', '#a855f7', '科创50成交额')} style={{ height: '100%', width: '100%' }} />
                        </div>
                      </div>
                    )}
                  </div>
                );
              })()}

              {/* 5. 红利指数 */}
              {(() => {
                const isExpanded = expandedCards.hl;
                return (
                  <div className={`bg-slate-900 border border-slate-800 rounded-xl p-3 shadow-md flex flex-col justify-between transition-all ${isExpanded ? 'col-span-2' : 'col-span-1'}`}>
                    <div>
                      <div className="flex justify-between items-center mb-1">
                        <span className="text-xs text-slate-300 font-bold">红利指数</span>
                        <div className="flex items-center gap-1.5">
                          <span className="text-[9px] text-slate-400 bg-slate-800/80 px-1 py-0.5 rounded font-mono">
                            {standardDate}
                          </span>
                          <button onClick={() => toggleCard('hl')} className="text-amber-400 hover:text-amber-300 p-0.5 rounded cursor-pointer">
                            {isExpanded ? <ChevronUp className="w-4 h-4" /> : <LineChart className="w-3.5 h-3.5" />}
                          </button>
                        </div>
                      </div>
                      <div className="text-xl font-extrabold text-slate-50 font-mono">
                        {turnoverData?.hl_amount ? turnoverData.hl_amount.toLocaleString() : '--'}
                        <span className="text-[10px] text-slate-400 font-normal ml-0.5">亿</span>
                      </div>
                      {renderDiffBadge(turnoverData?.hl_diff)}
                    </div>
                    <div className="text-[10px] text-slate-400 mt-2 border-t border-slate-800/60 pt-1.5 font-mono">000015.SH</div>
                    {isExpanded && (
                      <div className="mt-2 pt-2 border-t border-slate-800">
                        <div className="h-52">
                          <ReactECharts option={getSingleChartOption('hl_amount', '#f59e0b', '红利指数成交额')} style={{ height: '100%', width: '100%' }} />
                        </div>
                      </div>
                    )}
                  </div>
                );
              })()}

              {/* 6. 万得微盘股 */}
              {(() => {
                const isExpanded = expandedCards.windMicro;
                return (
                  <div className={`bg-slate-900 border border-slate-800 rounded-xl p-3 shadow-md flex flex-col justify-between transition-all ${isExpanded ? 'col-span-2' : 'col-span-1'}`}>
                    <div>
                      <div className="flex justify-between items-center mb-1">
                        <span className="text-xs text-slate-300 font-bold">万得微盘股</span>
                        <div className="flex items-center gap-1.5">
                          <span className="text-[9px] text-amber-400/90 bg-slate-800/80 px-1.5 py-0.5 rounded font-mono border border-amber-500/20">
                            {displayWindDate}
                          </span>
                          <button onClick={() => toggleCard('windMicro')} className="text-slate-400 hover:text-slate-200 p-0.5 rounded cursor-pointer">
                            {isExpanded ? <ChevronUp className="w-4 h-4" /> : <LineChart className="w-3.5 h-3.5" />}
                          </button>
                        </div>
                      </div>
                      <div className="text-xl font-extrabold text-slate-50 font-mono">
                        {displayWindAmount ? displayWindAmount.toLocaleString() : '--'}
                        <span className="text-[10px] text-slate-400 font-normal ml-0.5">亿</span>
                      </div>
                      {renderDiffBadge(displayWindDiff)}
                    </div>
                    <div className="text-[10px] text-slate-400 mt-2 border-t border-slate-800/60 pt-1.5 font-mono">8841423.WI</div>
                    {isExpanded && (
                      <div className="mt-2 pt-2 border-t border-slate-800">
                        <div className="h-52">
                          <ReactECharts option={getSingleChartOption('wind_micro_amount', '#94a3b8', '万得微盘股成交额')} style={{ height: '100%', width: '100%' }} />
                        </div>
                      </div>
                    )}
                  </div>
                );
              })()}
            </div>
          </div>

          <div className="lg:col-span-7 bg-slate-900 border border-slate-800 rounded-xl p-3.5 shadow-xl flex flex-col h-full min-h-0 overflow-hidden">
            <div className="flex items-center justify-between border-b border-slate-800 pb-2 mb-3 shrink-0">
              <div className="flex gap-2">
                <button
                  onClick={() => {
                    setActiveTab('up');
                    setSelectedIndustry(null);
                  }}
                  className={`flex items-center gap-1.5 px-3 py-1 text-xs font-bold rounded-lg transition cursor-pointer ${
                    activeTab === 'up' ? 'bg-rose-500/20 text-rose-400 border border-rose-500/30' : 'text-slate-400 hover:bg-slate-800'
                  }`}
                >
                  <TrendingUp className="w-3.5 h-3.5" />
                  🚀 涨停股池 ({rawLimitUpList.length})
                </button>
                <button
                  onClick={() => {
                    setActiveTab('down');
                    setSelectedIndustry(null);
                  }}
                  className={`flex items-center gap-1.5 px-3 py-1 text-xs font-bold rounded-lg transition cursor-pointer ${
                    activeTab === 'down' ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30' : 'text-slate-400 hover:bg-slate-800'
                  }`}
                >
                  <TrendingDown className="w-3.5 h-3.5" />
                  📉 跌停股池 ({rawLimitDownList.length})
                </button>
              </div>
            </div>

            <div className="mb-3 shrink-0">
              <div className="flex items-center justify-between mb-1.5">
                <p className="text-[11px] text-slate-400 font-medium flex items-center gap-2">
                  <span>{activeTab === 'up' ? '🔥 涨停行业分布热力图' : '🧊 跌停行业分布热力图'}</span>
                  {selectedIndustry && (
                    <span className="text-sky-400 bg-sky-950/60 px-2 py-0.5 rounded border border-sky-800/50">
                      当前筛选行业: <b>{selectedIndustry}</b>
                    </span>
                  )}
                </p>
                {selectedIndustry && (
                  <button
                    onClick={() => setSelectedIndustry(null)}
                    className="text-[10px] text-slate-400 hover:text-slate-200 underline cursor-pointer"
                  >
                    取消筛选
                  </button>
                )}
              </div>
              <div className="h-32 w-full relative bg-slate-950/50 rounded-lg overflow-hidden border border-slate-800">
                {activeIndustrySummary.length > 0 ? (
                  <ReactECharts
                    option={getTreemapOption(activeIndustrySummary, activeTab === 'up')}
                    style={{ height: '100%', width: '100%' }}
                    onEvents={{
                      click: (params: any) => {
                        if (params && params.name) {
                          const clickedName = params.name;
                          setSelectedIndustry((prev) => (prev === clickedName ? null : clickedName));
                        }
                      },
                    }}
                  />
                ) : (
                  <div className="h-full flex items-center justify-center text-xs text-slate-500">暂无行业分布数据</div>
                )}
              </div>
            </div>

            {/* 股票列表部分 */}
            <div className="flex-1 overflow-y-auto min-h-0 rounded-lg border border-slate-800 bg-slate-950/30">
              <table className="w-full text-left border-collapse">
                <thead className="sticky top-0 bg-slate-900/90 backdrop-blur text-[11px] text-slate-400 border-b border-slate-800">
                  <tr>
                    <th className="py-2 px-3">代码</th>
                    <th className="py-2 px-3">名称</th>
                    <th className="py-2 px-3">最新价</th>
                    <th className="py-2 px-3">涨跌幅</th>
                    <th className="py-2 px-3">连板</th>
                    <th className="py-2 px-3">行业</th>
                    <th className="py-2 px-3">首封时间</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/50 text-xs">
                  {activeStockList.map((stock, idx) => (
                    <tr key={idx} className="hover:bg-slate-800/40 transition">
                      <td className="py-2 px-3 font-mono text-slate-400">{stock.stock_code}</td>
                      <td className="py-2 px-3 font-semibold text-slate-200">{stock.stock_name}</td>
                      <td className="py-2 px-3 font-mono text-slate-100">{stock.last_price?.toFixed(2)}</td>
                      <td className={`py-2 px-3 font-mono font-bold ${activeTab === 'up' ? 'text-rose-400' : 'text-emerald-400'}`}>
                        {stock.change_pct > 0 ? `+${stock.change_pct.toFixed(2)}%` : `${stock.change_pct?.toFixed(2)}%`}
                      </td>
                      <td className="py-2 px-3 font-mono text-amber-400">{stock.limit_num}连板</td>
                      <td className="py-2 px-3 text-slate-400">{stock.industry}</td>
                      <td className="py-2 px-3 font-mono text-slate-400">{stock.first_limit_time || '--'}</td>
                    </tr>
                  ))}
                  {activeStockList.length === 0 && (
                    <tr>
                      <td colSpan={7} className="py-8 text-center text-slate-500 text-xs">暂无相关股票数据</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      ) : (
        /* 价格走势看板 (K线) */
        <div className="flex-1 bg-slate-900 border border-slate-800 rounded-xl p-4 flex flex-col min-h-0">
          <div className="flex items-center justify-between mb-4 shrink-0">
            <div className="flex items-center gap-2">
              {priceIndices.map((idx) => (
                <button
                  key={idx.code}
                  onClick={() => setSelectedPriceIndex(idx.code)}
                  className={`px-3 py-1.5 text-xs font-semibold rounded-lg transition cursor-pointer ${
                    selectedPriceIndex === idx.code
                      ? 'bg-blue-600 text-white shadow'
                      : 'bg-slate-800 text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {idx.name}
                </button>
              ))}
            </div>
          </div>
          <div className="flex-1 w-full min-h-0 bg-slate-950/40 rounded-lg border border-slate-800 p-2">
            <ReactECharts option={getKlineChartOption()} style={{ height: '100%', width: '100%' }} />
          </div>
        </div>
      )}
    </div>
  );
}
