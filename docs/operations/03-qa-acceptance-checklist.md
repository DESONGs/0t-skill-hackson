# QA Acceptance Checklist

## 1. 通用检查

- 代码是否只写在 `0t-skill_enterprise/`
- 是否改动了非 owner 目录
- 是否补齐文档
- 是否补齐测试

## 2. 网关检查

- 是否只实现 5 个公开动作
- 是否只使用白名单原始命令
- 是否没有引入 trade / WSS
- 是否标准化输出

## 3. analysis-core 检查

- 是否只通过 gateway 取数
- 是否输出 `report.md` 和 `report.json`
- 是否没有直接依赖 AVE 原始字段

## 4. workflow 检查

- 三个 preset 是否可跑通
- 是否存在失败降级
- 是否有 fixtures 和验收样例

## 5. 演化检查

- 是否只对 `analysis-core` 生效
- 是否能生成 case / proposal / candidate
- 是否保留完整 artifact
