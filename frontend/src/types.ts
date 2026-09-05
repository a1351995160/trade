export interface BacktestMetrics {
  total_return: number
  annual_return: number
  max_drawdown: number
  win_rate: number
  profit_loss_ratio: number
  trade_count: number
  benchmark_return: number
  final_cash: number
  final_equity: number
  start_date: number
  end_date: number
}

export interface Trade {
  code: string
  signal_type: string
  buy_date: number
  buy_price: number
  sell_date: number
  sell_price: number
  sell_reason: string
  pnl: number
  pnl_pct: number
  holding_days: number
}

export interface BacktestResult {
  metrics: BacktestMetrics
  trades: Trade[]
  trade_count: number
  report_path: string
}

export interface BacktestStatus {
  status: 'running' | 'done' | 'error'
  progress: number
  message: string
  result: BacktestResult | null
}

export interface SignalItem {
  code: string
  market: number
  signal_date: number
  signal_type: string
  price_ref: number
  stop_low: number
  score: number
}

export interface KlineBar {
  date: number
  open: number
  high: number
  low: number
  close: number
  volume: number
  macd: number
}

export interface BiMark {
  start_date: number
  end_date: number
  direction: 'up' | 'down'
  high: number
  low: number
}

export interface SegmentMark {
  start_date: number
  end_date: number
  direction: 'up' | 'down'
  high: number
  low: number
}

export interface SignalMark {
  date: number
  type: string
  direction: 'buy' | 'sell'
  price: number
}

export interface KlineData {
  code: string
  market: number
  kline: KlineBar[]
  merged: { date: number; high: number; low: number; open: number; close: number }[]
  bi: BiMark[]
  signals: SignalMark[]
  segments: SegmentMark[]
}
