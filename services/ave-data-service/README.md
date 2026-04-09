# ave-data-service

这里将实现 AVE 外部数据服务。

职责：

- 对接 AVE REST
- 统一返回 envelope
- 处理鉴权、缓存、限流和错误码

不负责：

- 分析逻辑
- 报告生成
- trade 功能
