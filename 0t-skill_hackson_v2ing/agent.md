# Inner Public Agent Guide

这个目录只保留公开副本需要暴露的源码和 example skills。

## 边界

- `src/` 是主要代码区
- `frontend/` 是展示资源
- `skills/` 是 example skills

## 不要补回这些内容

- 私有 vendor
- 本地工作区与缓存
- 测试目录
- 启动脚本与服务编排
- 本地密钥或账号配置

## 修改约束

- 如果改了源码结构，先同步更新根级 `CONFIGURATION.md`
- 如果改了 example skills，只做脱敏或说明性调整
- 不要在公开副本里制造“可直接运行”的假象
