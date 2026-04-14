# Public Repo Agent Guide

这个仓库是对外公开副本，不是内部全量开发仓。

## 第一原则

- 不回填密钥、账号、工作区路径或本地运行痕迹
- 不重新引入私有 vendor、私有服务、测试缓存和修复过程文档
- 所有公开说明以 `README.md` 和 `CONFIGURATION.md` 为准

## 允许修改

- 根级说明文档
- `0t-skill_hackson_v2ing/src/` 核心源码
- `0t-skill_hackson_v2ing/frontend/` 展示资源
- `0t-skill_hackson_v2ing/skills/` 下的 example skills 说明性内容

## 默认禁止

- 提交 `.env`、`.ot-workspace`、`.venv`、缓存文件
- 提交私有依赖镜像、vendored 运行时和上游同步目录
- 提交 fix、debug、QA 过程文档
- 在 example skill 中加入本地绝对路径

## 修改 example skills 时

- 保持原始 skill 包结构不变
- 可以做脱敏
- 不要把示例改成依赖本地私有环境才能读取的版本

## 新增配置或依赖时

- 先更新 `CONFIGURATION.md`
- 再更新相关 README
- 最后再改源码
