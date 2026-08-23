-- ================================================================
--  数据库 Schema（SQLite / PostgreSQL 兼容）
-- ================================================================
--  注意：
--    - 不使用 AUTOINCREMENT（PostgreSQL 不支持）
--    - INTEGER PRIMARY KEY 在 SQLite 中自动递增，PostgreSQL 用 SERIAL
--    - 时间戳默认值使用 CURRENT_TIMESTAMP（两者都支持）
-- ================================================================

-- ── 快讯模块 ──

CREATE TABLE IF NOT EXISTS flash_news (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    time TEXT NOT NULL,
    cluster TEXT,
    is_pushed INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS flash_analyses (
    id SERIAL PRIMARY KEY,
    time TEXT NOT NULL,
    model TEXT,
    clusters_json TEXT,
    output_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS flash_reviews (
    id SERIAL PRIMARY KEY,
    phase TEXT NOT NULL,
    markdown TEXT NOT NULL,
    signals_json TEXT,
    time TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS flash_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ── 宏观历史 ──

CREATE TABLE IF NOT EXISTS macro_history (
    id SERIAL PRIMARY KEY,
    time TEXT NOT NULL,
    data_json TEXT NOT NULL
);

-- ── 宏观每日快照（早盘锁定，按日期归档） ──

CREATE TABLE IF NOT EXISTS macro_daily (
    date TEXT PRIMARY KEY,
    data_json TEXT NOT NULL
);

-- ── 回测历史日线（东财回填，幂等） ──

CREATE TABLE IF NOT EXISTS backtest_prices (
    id SERIAL PRIMARY KEY,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL DEFAULT 0,
    UNIQUE(code, date)
);

-- ── ETF 收盘 ──

CREATE TABLE IF NOT EXISTS etf_close (
    id SERIAL PRIMARY KEY,
    date TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    holdings_json TEXT NOT NULL,
    UNIQUE(date)
);

-- ── 信号跟踪 ──

CREATE TABLE IF NOT EXISTS etf_signals (
    id TEXT PRIMARY KEY,
    etf_name TEXT NOT NULL,
    direction TEXT NOT NULL,
    trend TEXT,
    support TEXT,
    resistance TEXT,
    status TEXT NOT NULL DEFAULT 'waiting',
    entry_condition_json TEXT,
    stop_loss TEXT,
    take_profit TEXT,
    entry_price REAL,
    exit_price REAL,
    profit REAL,
    is_win INTEGER,
    source TEXT,
    reasoning TEXT,
    validation_json TEXT,
    tech_score INTEGER,
    tech_grade TEXT,
    position_json TEXT,
    entries_json TEXT,
    exits_json TEXT,
    expire_reason TEXT,
    last_checked_price REAL,
    created_at TEXT NOT NULL,
    closed_at TEXT
);

CREATE TABLE IF NOT EXISTS etf_price_history (
    id SERIAL PRIMARY KEY,
    etf_name TEXT NOT NULL,
    date TEXT NOT NULL,
    price REAL NOT NULL,
    timestamp TEXT,
    UNIQUE(etf_name, date)
);

-- ── 战法选股 ──

CREATE TABLE IF NOT EXISTS strategy_results (
    id SERIAL PRIMARY KEY,
    strategy_name TEXT NOT NULL,
    scan_date TEXT NOT NULL,
    count INTEGER DEFAULT 0,
    results_json TEXT NOT NULL,
    UNIQUE(strategy_name, scan_date)
);

CREATE TABLE IF NOT EXISTS strategy_watch (
    id SERIAL PRIMARY KEY,
    strategy_name TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    entry_price REAL,
    stop_loss REAL,
    target_price REAL,
    added_date TEXT,
    extra_json TEXT,
    UNIQUE(strategy_name, code)
);

-- ── 信号跟踪（完整状态 JSON 快照）──

CREATE TABLE IF NOT EXISTS tracking_state (
    id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    data_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- ── 调度状态 ──

CREATE TABLE IF NOT EXISTS schedule_state (
    task TEXT PRIMARY KEY,
    done_date TEXT NOT NULL
);

-- ── 全市场行情收盘快照（盘后/周末免刷新 + _valid_codes 持久化）──
-- 单行存储（key 固定 'latest'）：每个交易日收盘后由调度器写入。
-- 盘后/周末/重启后直接从快照恢复内存缓存，避免 2-4 分钟全量扫描。
-- stocks_json 约 4000 只股票全字段（~1MB），valid_codes_json 为有效代码列表。
CREATE TABLE IF NOT EXISTS market_snapshot (
    key TEXT PRIMARY KEY,
    stocks_json TEXT NOT NULL,
    valid_codes_json TEXT NOT NULL,
    saved_at TEXT NOT NULL
);

-- ── 用户表 ──

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── 用户数据（跨设备同步，按 user_id 隔离）──

CREATE TABLE IF NOT EXISTS user_watchlist (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    target_price REAL,
    note TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, code)
);

CREATE TABLE IF NOT EXISTS user_trade_plans (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    buy_price REAL NOT NULL,
    stop_loss REAL NOT NULL,
    target REAL NOT NULL,
    reason TEXT DEFAULT '',
    expected TEXT DEFAULT '',
    status TEXT DEFAULT 'waiting',
    hit_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_portfolio (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    shares REAL NOT NULL,
    cost REAL NOT NULL,
    note TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, code)
);

-- ── 索引优化 ──

CREATE INDEX IF NOT EXISTS idx_flash_news_time ON flash_news(time DESC);
CREATE INDEX IF NOT EXISTS idx_flash_analyses_time ON flash_analyses(time DESC);
CREATE INDEX IF NOT EXISTS idx_flash_reviews_phase ON flash_reviews(phase, time DESC);
CREATE INDEX IF NOT EXISTS idx_macro_history_time ON macro_history(time DESC);
CREATE INDEX IF NOT EXISTS idx_etf_close_date ON etf_close(date DESC);
CREATE INDEX IF NOT EXISTS idx_etf_signals_status ON etf_signals(status);
CREATE INDEX IF NOT EXISTS idx_etf_signals_etf ON etf_signals(etf_name);
CREATE INDEX IF NOT EXISTS idx_etf_price_etf_date ON etf_price_history(etf_name, date);
CREATE INDEX IF NOT EXISTS idx_strategy_results_name ON strategy_results(strategy_name, scan_date DESC);
CREATE INDEX IF NOT EXISTS idx_strategy_watch_name ON strategy_watch(strategy_name);

-- 战法回测结果
CREATE TABLE IF NOT EXISTS strategy_backtest (
    id SERIAL PRIMARY KEY,
    strategy_name TEXT NOT NULL,
    backtest_date TEXT NOT NULL,
    stats_json TEXT NOT NULL,
    trades_json TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(strategy_name, backtest_date)
);
CREATE INDEX IF NOT EXISTS idx_strategy_backtest_name ON strategy_backtest(strategy_name, backtest_date DESC);
CREATE INDEX IF NOT EXISTS idx_user_watchlist_uid ON user_watchlist(user_id);
CREATE INDEX IF NOT EXISTS idx_user_trade_plans_uid ON user_trade_plans(user_id);
CREATE INDEX IF NOT EXISTS idx_user_portfolio_uid ON user_portfolio(user_id);
