# 0T Skill Enterprise Public Source

这个目录是主工程的公开源码副本，保留了蒸馏、reflection、skill 编译和执行适配的核心代码，以及两个 example skills。

## 当前保留内容

- `src/ot_skill_enterprise/`
  - 核心业务源码
- `frontend/`
  - 静态前端资源
- `skills/`
  - 两个地址生成的 example skills
- `LICENSE`
- `agent.md`

## 当前不保留内容

- 本地运行环境
- 私有 vendor 依赖
- 测试文件
- 启动脚本与 compose 文件
- 服务侧实现目录
- 工作区产物与 QA 报告

## 你应该把它看成什么

这是一个“公开源码骨架”，适合：

- 展示项目分层
- 说明 skill 生成产物长什么样
- 让外部阅读核心实现

这不是一个开箱即跑的完整工程。恢复完整运行依赖所需的内容见：

- [../CONFIGURATION.md](../CONFIGURATION.md)

## Example skills

当前保留两个 example skills：

- `wallet-style-test-bsc-9048f6-20260415-671d5c3d`
- `wallet-style-test-bsc-bac453-20260415-845b5f24`

这两个目录保留完整 skill 包结构，用于展示钱包风格蒸馏后的产物格式。
