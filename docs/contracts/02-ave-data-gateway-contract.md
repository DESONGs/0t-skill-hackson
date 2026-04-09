# AVE Data Gateway Contract

本文件是 `ave-data-gateway` 的正式合同。

## 1. 公开动作

只允许：

- `discover_tokens`
- `inspect_token`
- `inspect_market`
- `inspect_wallet`
- `review_signals`

## 2. 原始命令白名单

只允许内部使用：

- `search`
- `trending`
- `ranks`
- `token`
- `risk`
- `holders`
- `kline-pair`
- `txs`
- `wallet-info`
- `wallet-tokens`
- `address-txs`
- `signals`

## 3. 排除项

明确排除：

- 全部 WSS
- 全部 trade
- `platform-tokens`
- `rank-topics`
- `chains`
- `main-tokens`
- `kline-token`
- `kline-ondo`
- `liq-txs`
- `tx-detail`
- `price`
- `search-details`
- `address-pnl`
- `smart-wallets`

## 4. 稳定数据域

- `token_discovery`
- `token_profile`
- `market_activity`
- `wallet_profile`
- `signal_feed`

## 5. 输出约束

- 不暴露 AVE 原始字段
- 缺失值必须标记 `unavailable`
- `kline` 必须在 gateway 层标准化
