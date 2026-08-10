from .models import ToolRisk


class PermissionGate:
    _CONFIRMATION_REQUIRED = {
        ToolRisk.DESTRUCTIVE,
        ToolRisk.PRIVILEGED,
        ToolRisk.EXTERNAL,
    }

    def requires_confirmation(self, risk: ToolRisk, *, crosses_workspace: bool = False) -> bool:
        return crosses_workspace or risk in self._CONFIRMATION_REQUIRED

    def reason(self, risk: ToolRisk, tool_name: str) -> str:
        labels = {
            ToolRisk.DESTRUCTIVE: "该操作可能删除或覆盖数据",
            ToolRisk.PRIVILEGED: "该操作需要管理员或系统级权限",
            ToolRisk.EXTERNAL: "该操作会修改外部系统状态",
        }
        return labels.get(risk, f"工具 {tool_name} 需要确认")
