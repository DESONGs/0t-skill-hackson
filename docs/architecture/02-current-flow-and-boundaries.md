# Current Flow And Boundaries

## 1. 主链路

本项目复用 `skill_enterprise` 已有的运行思路，固定主链路为：

1. `workflow`
   - 接受任务
   - 选择模式
   - 指定 skill 组合
2. `engine`
   - 暴露统一执行入口
   - 对 skill 做 find / execute
3. `runtime`
   - 按 action contract 执行 skill
   - 生成 artifact
4. `feedback`
   - 记录运行结果
5. `lab`
   - 把失败和改进机会转成 candidate
6. `registry`
   - 存储 skill、projection、feedback、promotion

## 2. 本项目与平台内核的边界

本项目不重写下列内核：

- workflow 主入口
- engine 主入口
- runtime dispatcher
- lab orchestration 基础能力
- registry 基础能力

本项目只做 glue 与业务层：

- AVE service
- gateway skill
- analysis skill
- preset
- report schema
- feedback 到 `analysis-core` 的接线

## 3. 运行边界

### 3.1 `ave-data-service`

- 可访问公网
- 持有 AVE 凭据
- 不做分析

### 3.2 `ave-data-gateway`

- 可以访问 `ave-data-service`
- 不能直接调用 trade
- 不能进入自动演化

### 3.3 `analysis-core`

- 不直接访问公网
- 不直接依赖 AVE 原始命令
- 只能读取 gateway 产出的稳定数据域

## 4. 演化边界

进入闭环的只有：

- `analysis-core` prompt / 模板 / eval / preset 组合

不进入闭环的包括：

- `ave-data-service`
- `ave-data-gateway`
- AVE 上游行为
