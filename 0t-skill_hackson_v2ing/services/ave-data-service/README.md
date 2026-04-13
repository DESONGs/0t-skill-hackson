# ave-data-service

This service is the stable AVE data backend used by the provider adapter layer.

职责：

- 对接 AVE REST
- 统一返回 envelope
- 处理鉴权、缓存、限流和错误码
- Serve as the replaceable data source behind `ot_skill_enterprise.providers.ave`

不负责：

- 分析逻辑
- 报告生成
- trade 功能
