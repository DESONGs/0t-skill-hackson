# 数据源审计：模块数据需求 vs AVE 实际 API 能力

## 一、AVE API 完整能力清单

基于 `vendor/ave_cloud_skill/scripts/ave_data_rest.py` 审计，AVE v2 API（`https://data.ave-api.xyz/v2`）提供以下 25 个端点：

| # | CLI 命令 | API 端点 | 功能 | 当前框架是否使用 |
|---|---------|---------|------|----------------|
| 1 | `search` | `GET /v2/tokens` | 关键词搜索 token | ✓ discover_tokens |
| 2 | `platform-tokens` | `GET /v2/tokens/platform` | 按 Launchpad 平台筛选 token | ✗ 未使用 |
| 3 | `token` | `GET /v2/tokens/{addr}-{chain}` | Token 详情（价格/市值/FDV/流动性/24h量） | ✓ inspect_token |
| 4 | `price` | `POST /v2/tokens/price` | 批量价格查询（最多 200 个） | ✗ **未使用，高价值** |
| 5 | `kline-token` | `GET /v2/klines/token/{addr}-{chain}` | Token K 线（1/5/15/30/60/120/240/1440/4320/10080 分钟） | ✗ **未使用，高价值** |
| 6 | `kline-pair` | `GET /v2/klines/pair/{addr}-{chain}` | 交易对 K 线 | ✓ inspect_market |
| 7 | `kline-ondo` | `GET /v2/klines/pair/ondo/{pair}` | Ondo K 线（**支持 from_time/to_time**） | ✗ 未使用 |
| 8 | `holders` | `GET /v2/tokens/holders/{addr}-{chain}` | Token 持币地址分布 | ✓ inspect_token |
| 9 | `search-details` | `POST /v2/tokens/search` | 批量 Token 详情（最多 50 个） | ✗ **未使用，可替代逐个 inspect_token** |
| 10 | `txs` | `GET /v2/txs/{addr}-{chain}` | 交易对的 swap 交易 | ✓ inspect_market |
| 11 | `trending` | `GET /v2/tokens/trending` | 链上热门 token | ✗ **未使用** |
| 12 | `rank-topics` | `GET /v2/ranks/topics` | 排行榜主题列表 | ✗ 未使用 |
| 13 | `ranks` | `GET /v2/ranks` | Token 排行榜 | ✗ 未使用 |
| 14 | `risk` | `GET /v2/contracts/{addr}-{chain}` | 合约安全/风险报告 | ✓ inspect_token |
| 15 | `chains` | `GET /v2/supported_chains` | 支持的链列表 | ✗ 未使用 |
| 16 | `main-tokens` | `GET /v2/tokens/main` | 链的主要 token（BTC/ETH 等） | ✗ **未使用，M3 宏观数据源** |
| 17 | `address-txs` | `GET /v2/address/tx` | 钱包交易历史（支持分页/按 token 过滤/时间范围） | ✓ inspect_wallet |
| 18 | **`address-pnl`** | `GET /v2/address/pnl` | **钱包对特定 token 的 PnL** | ✗ **未使用！关键遗漏** |
| 19 | `wallet-tokens` | `GET /v2/address/walletinfo/tokens` | 钱包 token 持仓（分页/排序/过滤） | ✓ inspect_wallet |
| 20 | `wallet-info` | `GET /v2/address/walletinfo` | 钱包概况（余额/胜率/利润率） | ✓ inspect_wallet |
| 21 | `smart-wallets` | `GET /v2/address/smart_wallet/list` | 聪明钱列表（含盈利分布筛选） | ✗ 未使用 |
| 22 | `signals` | `GET /v2/signals/public/list` | 公共交易信号 | ✓ review_signals |
| 23 | **`liq-txs`** | `GET /v2/txs/liq/{addr}-{chain}` | **流动性交易（添加/移除/创建 LP）** | ✗ **未使用！M4 LP 锁定检查的数据源** |
| 24 | `tx-detail` | `GET /v2/txs/detail` | 单笔交易详情 | ✗ 未使用 |
| 25 | `pair` | `GET /v2/pairs/{addr}-{chain}` | 交易对详情 | ✓ inspect_market |

**结论**：框架使用了 25 个 API 中的 10 个（40%），有 5 个高价值 API 被完全忽略。

---

## 二、逐模块数据审计

### M1 - 数据采集与标准化

| 数据需求 | AVE 有无 | 具体 API | 文档中的假设 | 审计结论 |
|---------|---------|---------|------------|---------|
| 全量交易流水 | ✓ | `address-txs`（游标分页：`last_time` + `last_id` + `page_size`） | 文档假设用 `page_no` 分页 | **需修正**：AVE 用游标分页，非页码分页。修改方案见下文 |
| 持仓水位 | ✓ | `wallet-tokens`（支持 `pageSize` + `pageNO` + `sort` + 过滤） | 正确 | ✓ |
| 钱包概况 | ✓ | `wallet-info`（含 `total_profit_ratio`, `total_win_ratio`, `total_purchase`, `total_sold`） | 正确 | ✓ |
| Token 详情 | ✓ | `token` | 正确 | ✓ |
| 风险/安全 | ✓ | `risk`（contracts API） | 正确 | ✓ |
| Holders | ✓ | `holders`（支持排序、limit） | 正确 | ✓ |
| Signals | ✓ | `signals` | 正确 | ✓ |
| Gas 消耗 | **不确定** | `address-txs` 返回中**可能**含 gas 字段，但 `_normalize_wallet_activity_item` 未解析 | 文档标为 P2 | **需实测 AVE API 确认**。`tx-detail` 可能包含 gas 信息，需验证 |
| 交互协议分布（DEX来源） | **不确定** | `address-txs` 返回中**可能**含 router/dex 字段 | 文档标为暂时忽略 | **需实测确认**。`tx-detail` 更可能包含 |
| **并行 Token 查询优化** | ✓ | **`search-details`（POST，一次最多 50 个 token）** | 文档假设并行调用 `inspect_token` × N | **发现改进机会**：用 `search-details` 批量查询替代多次单独调用 |

**M1 关键修正**：

1. **分页方式修正**：`address-txs` 用游标分页（`last_time` + `last_id`），不是页码分页

```python
# 错误（文档中假设的）：
self._cli.run_json("address-txs", "--wallet", wallet, "--chain", chain, "--page-no", str(page))

# 正确（AVE 实际支持的）：
self._cli.run_json("address-txs", "--wallet", wallet, "--chain", chain,
                    "--page-size", "100",
                    "--last-time", last_time,  # 上一页最后一条的时间
                    "--last-id", last_id)       # 上一页最后一条的 ID
```

2. **批量 Token 查询优化**：可用 `search-details`（`POST /v2/tokens/search`）一次查最多 50 个 token，**替代** 4 次 `inspect_token` 调用。但 `search-details` 可能不含 risk_snapshot 和 holders，需实测确认返回字段。

---

### M2 - 交易配对与统计特征

| 数据需求 | AVE 有无 | 具体 API | 文档中的假设 | 审计结论 |
|---------|---------|---------|------------|---------|
| 完整交易历史（含买入价/卖出价） | ✓ | `address-txs` 返回含 `from_amount`, `to_amount`, `from_price_usd`, `to_price_usd` | 正确 | ✓ 代码已在解析 |
| **单 Token PnL** | **✓ 直接提供！** | **`address-pnl`**（`GET /v2/address/pnl`，参数：wallet, chain, token_address） | **文档未提及** | **重大发现**：AVE 直接提供了钱包对特定 token 的 PnL 数据。这可以**大幅简化甚至替代** M2 的手动配对计算 |
| 胜率 | ✓（间接） | `wallet-info` 返回 `total_win_ratio` | 文档假设需手动计算 | `wallet-info` 已有**全局**胜率。逐 token 胜率需用 `address-pnl` |
| 利润率 | ✓（间接） | `wallet-info` 返回 `total_profit_ratio` | 同上 | 全局利润率已有，逐 token 利润需 `address-pnl` |
| 总买入/卖出次数 | ✓ | `wallet-info` 返回 `total_purchase`, `total_sold` | 正确 | ✓ 已在使用 |
| 持仓周期 | ✗ **AVE不提供** | 需从 `address-txs` 的时间戳手动计算 | 文档假设正确 | ✓ 需自行计算 |
| 回撤容忍度 | ✗ **AVE不提供** | 需从 `address-pnl` + 时间序列推算 | 文档假设正确 | 需自行计算，但 `address-pnl` 能简化 |
| 加仓逻辑检测 | ✗ **AVE不提供** | 需从 `address-txs` 分析同一 token 多笔 buy 的金额模式 | 文档假设正确 | ✓ 需自行计算 |
| 盈亏归因（过滤空投） | ✗ **AVE不提供** | `address-txs` 包含 action 类型，可过滤 transfer（可能是空投） | 文档假设正确 | 部分可行：action=swap/buy/sell 为主动交易，action=transfer 可能是空投 |

**M2 关键修正**：

**`address-pnl` 是被忽略的关键 API**。建议混合策略：

```
方案 A（快速路径）：对每个 focus token 调用 address-pnl 获取 PnL 汇总
  → 输入: wallet + chain + token_address
  → 输出: 该 token 的总盈亏、买入成本、卖出收入等
  → 优点: 不需要手动配对，AVE 服务端已算好
  → 缺点: 不含持仓周期、加仓模式等细节

方案 B（完整路径）：address-pnl + address-txs 结合
  → address-pnl 获取每个 token 的 PnL 汇总
  → address-txs 的详细记录用于计算持仓周期和加仓模式
  → 两者并行调用
```

Provider 层需新增：

```python
# 在 AveRestProvider 中新增
def inspect_wallet_token_pnl(self, wallet: str, chain: str, token_address: str) -> dict:
    return self._cli.run_json("address-pnl",
        "--wallet", wallet, "--chain", chain, "--token", token_address)
```

---

### M3 - 市场环境上下文

| 数据需求 | AVE 有无 | 具体 API | 文档中的假设 | 审计结论 |
|---------|---------|---------|------------|---------|
| 当前价格 | ✓ | `token` → price_usd | 正确 | ✓ |
| 价格 1h/24h 变化率 | **✓ 可计算** | **`kline-token`**（interval=60, size=24 获取 24 个 1h K 线） | 文档假设用 `inspect_market` | **修正**：`kline-token` 比 `kline-pair` 更直接（不需要 pair_address），且框架**未使用此 API** |
| 波动率 | **✓ 可计算** | `kline-token` OHLCV 数据计算标准差/ATR | 文档假设正确 | ✓ 但需要额外 API 调用 |
| 流动性 | ✓ | `token` → liquidity_usd | 正确 | ✓ |
| 24h 交易量 | ✓ | `token` → volume_24h_usd | 正确 | ✓ |
| **链上热度** | **✓ 直接提供！** | **`trending`**（`GET /v2/tokens/trending`） | **文档未提及 trending API** | **发现**：trending 可直接判断 token 是否当前热门 |
| BTC/ETH 宏观状态 | **✓ 可获取** | `main-tokens`（获取链主 token）+ `token`（获取价格）或 `price`（批量价格） | 文档假设调用 inspect_token | **改进**：用 `price` API 批量查 BTC+ETH 只需**1 次调用** |
| BTC/ETH 24h 变化率 | **✓ 可计算** | `kline-token` 对 BTC/ETH 获取 24h K 线 | 文档假设正确 | 需额外调用 |
| **历史时间点市场状态（回溯）** | **部分 ✓** | `kline-token` 有 `size` 参数获取最近 N 根 K 线。**但不支持 from_time/to_time** | 文档标为 MVP 不做 | **确认**：`kline-token` 和 `kline-pair` 均**不支持**时间范围查询。只有 `kline-ondo` 支持 from_time/to_time，但仅限 Ondo 协议 |
| 社交媒体热度 | ✗ **AVE不提供** | signals 有部分覆盖但不含社交指标 | 文档标为暂时搁置 | ✓ 正确搁置 |

**M3 关键修正**：

1. **用 `kline-token` 替代 `inspect_market`（kline-pair）获取动量数据**

当前框架使用 `inspect_market` → 内部调用 `kline-pair`，但 `kline-pair` 需要 `pair_address`，而 `kline-token` 只需 `token_address`。在蒸馏场景中，我们已有 token_address（来自 focus_tokens），不一定有 pair_address。

```python
# 更直接的方案：
def fetch_token_kline(self, token_address: str, chain: str, interval: int = 60, size: int = 24):
    return self._cli.run_json("kline-token",
        "--address", token_address, "--chain", chain,
        "--interval", str(interval), "--size", str(size))
```

2. **用 `price` API 批量获取 BTC/ETH 价格（1 次调用）**

```python
def fetch_macro_prices(self):
    # 一次查询 BTC + ETH 价格
    return self._cli.run_json("price",
        "--tokens", "bitcoin-eth", "ethereum-eth")  # address-chain 格式
```

3. **`trending` 作为热度信号源**

```python
def fetch_trending(self, chain: str):
    return self._cli.run_json("trending", "--chain", chain, "--page-size", "20")
```

trending 结果可作为 `signal_context` 的补充——如果某 focus token 出现在 trending 列表中，标记 `is_trending: true`。

---

### M4 - 触发信号与风控过滤

| 数据需求 | AVE 有无 | 具体 API | 文档中的假设 | 审计结论 |
|---------|---------|---------|------------|---------|
| 入场因子蒸馏 | ✗ 需自行计算 | 基于 M2 + M3 的预处理数据 | 文档假设正确 | ✓ |
| Signals（异动信号） | ✓ | `signals` | 正确 | ✓ |
| Honeypot 检测 | ✓ | `risk` → honeypot | 正确 | ✓ |
| 买卖税率 | ✓ | `risk` → buy_tax_bps, sell_tax_bps | 正确 | ✓ |
| Risk level | ✓ | `risk` → risk_level, flags | 正确 | ✓ |
| Holder 集中度 | ✓ | `holders` → top_holder_share_pct | 正确 | ✓ |
| **LP 锁定检查** | **✓ 间接可判断！** | **`liq-txs`**（`GET /v2/txs/liq/{addr}-{chain}`，type=removeLiquidity） | **文档标为 P1 未确认数据源** | **重大发现**：`liq-txs` 返回流动性操作记录（addLiquidity / removeLiquidity / createPair），**可以**判断 LP 是否有大额 removeLiquidity — 若近期无 remove 记录则间接表明 LP 稳定 |
| 量价突破检测 | ✓ 可计算 | `kline-token` 的 OHLCV 数据 | 文档假设正确 | 需从 K 线计算 |
| **Top10 持币地址占比** | ✓ | `holders`（返回 holder 列表，可计算前 10 名占比总和） | 文档标为 P1 | ✓ 数据已有，需计算汇总 |

**M4 关键修正**：

1. **LP 锁定检查方案**

```python
def check_lp_stability(self, pair_address: str, chain: str) -> dict:
    """
    调用 liq-txs 检查最近是否有 removeLiquidity 操作。
    如果最近 7 天无 remove 操作 → LP 相对稳定。
    """
    raw = self._cli.run_json("liq-txs",
        "--address", pair_address, "--chain", chain,
        "--limit", "50", "--type", "removeLiquidity", "--sort", "desc")
    remove_txs = _section_list(raw, "items", "txs", "data")
    return {
        "has_recent_remove": len(remove_txs) > 0,
        "remove_count_recent": len(remove_txs),
        "lp_stability_label": "unstable" if len(remove_txs) > 3 else "stable"
    }
```

需要 `pair_address`，可从 `inspect_token → main_pair_ref → pair_address` 获取。

2. **Top10 Holder 占比计算**

```python
def compute_top10_concentration(holder_snapshot: dict) -> float:
    holders = holder_snapshot.get("holders", [])
    return sum(
        float(h.get("share_pct") or 0) 
        for h in holders[:10]
    )
```

`holders` API 默认按 balance 降序，前 10 条即为 Top10。

---

### M5 - LLM蒸馏与Skill生成

M5 不新增数据源需求，消费 M1-M4 的预处理结果。**无数据源问题**。  

开发前补充冻结口径：
- **M5 的蒸馏输入仍然只来自 AVE**。
- 若后续接入 `onchainos CLI`，其职责仅限于执行适配（wallet / security / swap / gateway），**不作为第二数据源回流给 M5**。

---

### M6 - 回测与置信度评估

| 数据需求 | AVE 有无 | 具体 API | 文档中的假设 | 审计结论 |
|---------|---------|---------|------------|---------|
| 历史交易用于回测 | ✓ | `address-txs`（分页拉取） | 正确 | ✓ |
| **历史时间点市场状态** | **✗ 无法精确回溯** | `kline-token` 不支持 from_time/to_time | 文档标为 MVP 不做 | **确认无法做**。只有 `kline-ondo` 支持时间范围查询，但限 Ondo 协议。**这是 AVE API 的硬限制** |
| PnL 数据 | ✓ | `address-pnl` 直接提供 | 文档假设手动计算 | 可用 `address-pnl` 简化 |
| 回测基准（Buy & Hold） | ✓ 可计算 | `kline-token`（size=最大值）获取长期价格走势 | 文档未提及 | 用最早和最新 K 线的 close 价计算 buy & hold 收益 |

**M6 关键修正**：

1. **回测中市场状态回溯的限制必须明确标注**

由于 `kline-token` 不支持 `from_time`，M6 的回测**无法**判断"交易发生时的市场状态"。只能用**当前的 K 线回溯最近 N 根**近似。

**可接受的近似方案**：
- 获取 `kline-token`（interval=1440, size=30） → 最近 30 天日 K 线
- 如果某笔交易发生在最近 30 天内，可以从日 K 线中找到对应日期的收盘价
- 超出 30 天的交易无法回溯 → 标记为 `market_context_unavailable`

2. **`address-pnl` 简化回测**

```
原方案: address-txs → 手动配对 → 计算每笔 PnL → 汇总
改进方案: address-pnl × N个focus_token → 直接获取 PnL → 仅对细节（持仓周期/加仓）做手动分析
```

3. **执行层能力不纳入数据审计**

若采用 `onchainos CLI` 做执行适配，需要明确它**不属于数据层**：
- 不参与 M6 回测数据计算
- 不替代 `address-txs / address-pnl / kline-token`
- 只用于回测之后的 `dry-run-ready / live-ready` 检查

---

## 三、被忽略的高价值 API 汇总

| API | 价值 | 应用模块 | 改进方案 |
|-----|------|---------|---------|
| **`address-pnl`** | **极高** | M2 | 直接获取 wallet+token 的 PnL，替代手动买卖配对的胜率计算 |
| **`kline-token`** | **极高** | M3 | 获取 token K 线计算价格动量和波动率，比 kline-pair 更方便（不需要 pair_address） |
| **`liq-txs`** | **高** | M4 | 检查 LP 流动性操作，间接判断 LP 稳定性 |
| **`price`** | **高** | M3 | 批量价格查询（最多 200 个），1 次调用获取 BTC+ETH+所有 focus token 价格 |
| **`trending`** | **中** | M3/M4 | 链上热门 token，补充热度信号 |
| `search-details` | 中 | M1 | 批量 token 详情（最多 50 个），可能替代多次 inspect_token |
| `smart-wallets` | 低 | — | 找其他聪明钱地址，用于策略对标（V2 功能） |
| `tx-detail` | 低 | M1 | 单笔交易详情，可能含 gas/DEX 路由信息，需实测 |

---

## 四、各模块数据修正汇总

### M1 修正

1. **分页拉取方案修正为游标分页**：

```python
def fetch_full_history(provider, wallet, chain, max_pages=5):
    all_activities = []
    last_time = None
    last_id = None
    for _ in range(max_pages):
        args = ["--wallet", wallet, "--chain", chain, "--page-size", "100"]
        if last_time:
            args.extend(["--last-time", last_time])
        if last_id:
            args.extend(["--last-id", last_id])
        raw = provider._cli.run_json("address-txs", *args)
        items = _section_list(raw, "result", "items", "txs")
        if not items:
            break
        all_activities.extend(items)
        # 游标取最后一条
        last_item = items[-1]
        last_time = last_item.get("timestamp") or last_item.get("time")
        last_id = last_item.get("tx_hash") or last_item.get("id")
        if not last_time:
            break
    return all_activities
```

2. **考虑用 `search-details` 替代多次 `inspect_token`**（需实测返回字段是否包含 risk）

3. **用 `price` 批量查替代多次 `token` 调用获取价格**（仅当只需价格、不需完整 token 详情时）

### M2 修正

1. **新增 `address-pnl` 调用**（高优先级）：

```python
# 对每个 focus token 并行调用
def fetch_token_pnls(provider, wallet, chain, focus_tokens):
    pnls = {}
    for token_ref in focus_tokens:
        addr = token_ref.get("token_address")
        if addr:
            raw = provider._cli.run_json("address-pnl",
                "--wallet", wallet, "--chain", chain, "--token", addr)
            pnls[addr] = _section_dict(raw)
    return pnls
```

2. **M2 统计策略调整为**：
   - `address-pnl` → 获取每个 token 的已实现 PnL（胜率、利润率）
   - `address-txs` → 仅用于计算持仓周期（买入→卖出时间差）和加仓模式
   - 不再需要完整的手动 FIFO 配对来计算 PnL（AVE 已算好）

### M3 修正

1. **用 `kline-token`（而非 `inspect_market`/`kline-pair`）获取动量数据**
2. **用 `price` 批量获取 BTC+ETH+focus tokens 的当前价格**
3. **新增 `trending` 调用补充热度信号**
4. **历史时间点回溯限制**：明确标注 `kline-token` 不支持 from_time，只能用最近 N 根 K 线近似

### M4 修正

1. **新增 `liq-txs` 调用做 LP 稳定性检查**
2. **新增 Top10 Holder 占比汇总计算**（数据已有，需增加聚合逻辑）

### M6 修正

1. **明确回测时间窗口限制**：只能回溯 kline-token 最大 size 范围内的交易
2. **用 `address-pnl` 作为回测 PnL 的 ground truth**

---

## 五、AVE 无法提供、且无替代方案的数据

| 数据 | 影响范围 | 解决思路 |
|------|---------|---------|
| **交易发生时的精确历史市场状态** | M3 回溯、M6 回测 | `kline-token` 只返回最近 N 根 K 线，无法查历史任意时间点。**短期无解**，只能用日 K 线（interval=1440, size=30）近似最近 30 天 |
| Gas 消耗数据 | M1 | `address-txs` 返回字段未确认。可尝试 `tx-detail` 获取但调用量大。**建议实测后决定**，优先级低 |
| DEX 路由/来源 | M1 | `address-txs` 和 `tx-detail` 可能含路由信息。**需实测**。harness 已标注可忽略 |
| 社交媒体热度 | M3 | AVE 无此数据。`trending` + `signals` 可部分覆盖"链上热度"，但无 Twitter/Discord 数据。**无法解决** |
| RSI / MACD 等技术指标 | M4 | AVE 不计算技术指标。需从 `kline-token` 的 OHLCV 自行计算。Python 端用简单公式即可（RSI ≈ 14 期 K 线的涨跌比） |
| LP 是否"锁定"（合约层面） | M4 | `liq-txs` 只能看到流动性操作记录，不能直接判断 LP 是否通过合约锁定（如 UniCrypt/Team.Finance）。`risk` API 的 `flags` 字段**可能**包含此标签，需实测 |

---

## 六、执行层边界（开发前最终冻结）

为避免“AVE + OKX”形成双数据路径，执行层边界固定如下：

- **AVE 负责**
  - 地址历史交易
  - 持仓/PnL
  - token 风险、holders、liquidity 相关蒸馏输入
  - K 线、价格、signals 等回测与蒸馏输入
- **onchainos CLI 负责**
  - 钱包登录与签名
  - security scan
  - swap execute / swap calldata
  - gateway simulate / broadcast / order tracking
- **禁止事项**
  - 禁止把 onchainos 的 market / signal / portfolio PnL 数据回灌进 M1-M6
  - 禁止用 onchainos 数据替代 AVE 回测结果
  - 禁止在 M5 prompt 中混用 AVE 与 onchainos 的同类市场字段

---

## 七、修正后的数据采集并行图

```
                         inspect_wallet
                              │
                    ┌─────────┼──────────┐
                    │         │          │
              focus_tokens  chain    wallet_summary
                    │
    ┌───────────────┼───────────────┬──────────────────┐
    │               │               │                  │
    ▼               ▼               ▼                  ▼
 kline-token    address-pnl     liq-txs            price (批量)
  × N tokens    × N tokens    × N pair_addrs      BTC+ETH+tokens
 (动量/波动率)   (PnL/胜率)    (LP稳定性)          (宏观+当前价)
    │               │               │                  │
    ▼               ▼               ▼                  ▼
  M3 预计算      M2 PnL 汇总    M4 LP 过滤        M3 Macro
                    │
                    ▼
              address-txs (游标分页)
              (持仓周期/加仓模式)
                    │
                    ▼
               M2 详细统计

  同时并行:
  ├── signals → M4
  ├── trending → M3/M4
  └── risk × N → M4 (已在 inspect_token 中)
```

**新增 API 调用量评估**（4 个 focus token）：

| API | 调用次数 | 耗时估计 |
|-----|---------|---------|
| inspect_wallet（wallet-info + wallet-tokens + address-txs） | 3 次 | ~3s（串行） |
| kline-token × 4 | 4 次 | ~2s（并行） |
| address-pnl × 4 | 4 次 | ~2s（并行） |
| price（批量） | 1 次 | ~1s |
| trending | 1 次 | ~1s |
| liq-txs × 4 | 4 次 | ~2s（并行） |
| signals | 1 次 | ~1s |
| token × 4 + risk × 4 + holders × 4 | 12 次 | ~3s（并行） |

**总计约 30 次 API 调用**，并行后预计 **~5-8s**（受限于 rate limit：free=1rps, normal=5rps, pro=20rps）。

**Rate limit 约束**：
- Free plan（1 rps）：30 次 ÷ 1 rps = **30s**
- Normal plan（5 rps）：30 次 ÷ 5 rps = **6s**
- Pro plan（20 rps）：30 次 ÷ 20 rps = **1.5s**

**建议**：蒸馏功能最低需要 Normal plan 才能获得合理的延迟体验。
