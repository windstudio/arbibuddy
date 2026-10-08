"""八类文书的声明式名称，不承载私有载荷或运行时法律判断。"""
from dataclasses import dataclass

@dataclass(frozen=True)
class TemplateDefinition:
    template_id: str
    title: str

TEMPLATE_REGISTRY = {
    'employment-obligation-demand-letter': TemplateDefinition('employment-obligation-demand-letter', '劳动用工义务催告函'),
    'labor-arbitration-application': TemplateDefinition('labor-arbitration-application', '劳动人事争议仲裁申请书'),
    'labor-arbitration-defense': TemplateDefinition('labor-arbitration-defense', '劳动人事争议仲裁答辩书'),
    'evidence-catalog': TemplateDefinition('evidence-catalog', '证据目录'),
    'forced-termination-notice': TemplateDefinition('forced-termination-notice', '被迫解除劳动合同通知书'),
    'shanghai-cancellation-restriction-application': TemplateDefinition('shanghai-cancellation-restriction-application', '限制公司注销申请书'),
    'property-preservation-application': TemplateDefinition('property-preservation-application', '财产保全申请书'),
    'enforcement-application': TemplateDefinition('enforcement-application', '强制执行申请书'),
}

def get_template(template_id: str) -> TemplateDefinition:
    try:
        return TEMPLATE_REGISTRY[template_id]
    except KeyError as error:
        raise ValueError(f"不支持的文书类型：{template_id}") from error
