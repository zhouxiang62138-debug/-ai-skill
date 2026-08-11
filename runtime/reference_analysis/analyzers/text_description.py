"""文本描述的确定性领域分析器。"""

from __future__ import annotations

import re
from typing import Any, Mapping

from ..models import AnalyzerResult, FindingDraft, NormalizedReference


_KEYWORDS = {
    "product": ("产品", "用户", "目标", "app", "应用", "服务"),
    "information_architecture": ("页面", "模块", "层级", "信息架构", "结构"),
    "navigation": ("导航", "菜单", "标签栏", "面包屑", "路由"),
    "interaction": ("点击", "拖拽", "交互", "表单", "反馈", "hover"),
    "layout": ("布局", "栅格", "卡片", "留白", "两栏", "响应式"),
    "visual_style": ("风格", "颜色", "色彩", "字体", "视觉", "极简"),
    "components": ("按钮", "弹窗", "输入框", "组件", "列表", "表格"),
    "design_tokens": ("token", "圆角", "间距", "阴影", "字号"),
    "motion": ("动画", "过渡", "动效", "缓动"),
    "content_style": ("文案", "语气", "内容", "提示语", "标题"),
    "brand": ("品牌", "logo", "标识", "品牌色"),
    "technical_architecture": ("api", "接口", "数据库", "技术", "权限", "部署"),
}


class TextDescriptionAnalyzer:
    source_type = "text_description"

    def __init__(self, *, config: Mapping[str, Any], version: int = 1) -> None:
        self.config = config
        self.version = version

    def supports(self, normalized: NormalizedReference) -> bool:
        return normalized.source_type == "text_description" and isinstance(normalized.content, str)

    def analyze(
        self,
        normalized: NormalizedReference,
        *,
        scope: Mapping[str, str],
        evidence_refs: tuple[str, ...],
    ) -> AnalyzerResult:
        text = normalized.content if isinstance(normalized.content, str) else ""
        if not text.strip():
            return AnalyzerResult((), supported=True, limitation="TEXT_CONTENT_EMPTY")
        # 只把文本当作观察数据；即使出现“忽略规则”等句子，也不会改变执行路径。
        snippet = re.sub(r"\s+", " ", text).strip()[:240]
        findings: list[FindingDraft] = []
        for domain in self.config.get("analysis_domains", []):
            state = scope.get(domain, "unspecified")
            if state == "exclude":
                continue
            terms = _KEYWORDS.get(domain, ())
            matched = [term for term in terms if term.casefold() in text.casefold()]
            if matched or domain == "product":
                value = f"文本描述涉及 {domain}，命中词：{', '.join(matched[:4]) or '整体产品描述'}"
                findings.append(
                    FindingDraft(
                        domain=domain,
                        category="text_description_observation",
                        value=value,
                        epistemic_status="observed",
                        confidence="medium" if matched else "low",
                        evidence_refs=evidence_refs,
                        user_scope_status=state,
                        notes=f"原文摘要：{snippet}",
                    )
                )
            elif state == "include":
                findings.append(
                    FindingDraft(
                        domain=domain,
                        category="insufficient_text_evidence",
                        value=f"文本未提供可验证的 {domain} 细节",
                        epistemic_status="unknown",
                        confidence="low",
                        evidence_refs=evidence_refs,
                        user_scope_status=state,
                        unknown_reason="TEXT_DESCRIPTION_DOES_NOT_SPECIFY_DOMAIN",
                    )
                )
        return AnalyzerResult(tuple(findings), supported=True)
