## Whole-branch review final fixes

- 修复 `contains` 模式的 `*` 通配符替换回归，并新增覆盖测试。
- root 设备的用户证书库改用 `root_shell` 探测；`su` 或权限探测失败返回 `unknown`。
- 清理 `test_traffic_addon.py` 未使用导入，并整理 `traffic_addon.py` 导入顺序。
- 验证：`uv run pytest tests/test_traffic_addon.py tests/test_android_p0.py -v`
- 结果：33 passed。
