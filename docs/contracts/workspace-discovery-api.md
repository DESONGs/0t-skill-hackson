# 契约：工作区发现接口 (Workspace Discovery API)

为了提升前端用户体验，将手动输入工作区路径改为下拉列表选择，后端需要提供一个用于发现可用工作区的接口。

## 1. 接口定义

- **Endpoint:** `GET /api/workspaces`
- **Description:** 扫描并返回当前系统内可用的工作区列表。
- **Auth:** 无（当前为只读控制面模式）

### Response Schema (200 OK)

```json
{
  "items": [
    {
      "id": "string",    // 传递给其他 API 的路径参数值 (如 ".ot-workspace")
      "name": "string",  // UI 显示名称
      "path": "string"   // 绝对路径（仅限后端记录，前端脱敏显示）
    }
  ],
  "count": "number"
}
```

## 2. 后端扫描与识别逻辑

后端在收到请求时，应执行以下探测逻辑：

1. **默认探测：** 扫描项目根目录（CWD）下的第一级子目录。
2. **识别特征：** 满足以下任一条件的目录应被判定为有效工作区：
   - 目录名为 `.ot-workspace`。
   - 目录下包含 `evolution-registry/` 子目录。
   - 目录下包含 `run-store/` 子目录。
3. **脱敏处理：** 
   - `id` 字段应返回相对路径或标识符，确保前端在调用 `/api/overview?workspace_dir={id}` 时能正确定位。
   - `name` 字段建议格式为：`别名 (路径后缀)`。

## 3. 配置支持 (环境变量)

为了支持复杂的部署环境，后端应支持以下配置：

- `OT_ALLOWED_WORKSPACES`: 
  - 如果设置了此变量（逗号分隔的路径列表），后端应**停止自动扫描**，直接返回这些预定义的路径。
  - 示例：`OT_ALLOWED_WORKSPACES=.ot-workspace,/tmp/research-runs`
- `OT_WORKSPACE_SCAN_DEPTH`: 限制扫描深度，默认为 `1`。

## 4. 安全性要求

- **路径合法性检查：** 所有的工作区路径必须位于项目根目录下，或者在白名单内。禁止通过该接口探测系统敏感目录。
- **错误处理：** 如果扫描失败，应返回空列表 `{"items": [], "count": 0}` 而不是抛出 500 错误。

## 5. 前端集成建议

- 前端已实现降级逻辑：若 `/api/workspaces` 返回非 200 状态，将默认显示 `.ot-workspace`。
- 后端上线此接口后，前端下拉列表将自动填充。
