"""
通知工具集。

提供 Webhook、邮件等通知能力。
"""

from __future__ import annotations

import json
import re
from typing import Any

from power_aiops.agents.tools.base import (
    Tool,
    ToolCategory,
    ToolMetadata,
    ToolResult,
)


class WebhookTool(Tool):
    """Webhook 通知工具.

    向指定 URL 发送 HTTP POST 请求。
    支持 JSON 和表单数据。
    """

    def __init__(self, timeout: int = 10) -> None:
        super().__init__()
        self._timeout = timeout

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="webhook",
            description="发送 HTTP POST 请求到指定 URL，用于通知集成（钉钉、企业微信、飞书等）。",
            category=ToolCategory.SYSTEM,
            parameters={
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Webhook URL",
                    },
                    "data": {
                        "type": "object",
                        "description": "发送的数据（JSON 对象）",
                    },
                    "message": {
                        "type": "string",
                        "description": "纯文本消息内容（与 data 二选一）",
                    },
                    "headers": {
                        "type": "object",
                        "description": "自定义 HTTP 头",
                    },
                    "content_type": {
                        "type": "string",
                        "description": "Content-Type (默认 application/json)",
                    },
                },
                "required": ["url"],
            },
            examples=[
                'webhook(url="https://oapi.dingtalk.com/robot/send?access_token=xxx", message="故障告警: CPU 99%")',
                'webhook(url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx", data={"msgtype": "text", "text": {"content": "告警"}})',
            ],
            tags=["webhook", "notification", "http", "post"],
        )

    def execute(self, **kwargs) -> ToolResult:
        url = kwargs.get("url")
        data = kwargs.get("data")
        message = kwargs.get("message")
        headers = kwargs.get("headers", {})
        content_type = kwargs.get("content_type", "application/json")

        if not url:
            return ToolResult(success=False, error="url is required")

        try:
            import urllib.request
            import urllib.error

            # 构建请求数据
            if data:
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            elif message:
                # 尝试解析为 JSON 格式
                if content_type == "application/json":
                    body = json.dumps({"msgtype": "text", "text": {"content": message}}, ensure_ascii=False).encode("utf-8")
                else:
                    body = message.encode("utf-8")
            else:
                body = b"{}"

            # 构建请求头
            req_headers = {
                "Content-Type": content_type,
                "Content-Length": str(len(body)),
            }
            req_headers.update(headers)

            # 发送请求
            req = urllib.request.Request(url, data=body, headers=req_headers, method="POST")

            with urllib.request.urlopen(req, timeout=self._timeout) as response:
                response_body = response.read().decode("utf-8")
                response_code = response.getcode()

            return ToolResult(
                success=200 <= response_code < 300,
                data={
                    "status_code": response_code,
                    "response": response_body[:1000],  # 限制响应大小
                },
            )

        except urllib.error.HTTPError as e:
            return ToolResult(
                success=False,
                error=f"HTTP error: {e.code} {e.reason}",
                data={"status_code": e.code, "response": e.read().decode("utf-8")[:500]},
            )
        except urllib.error.URLError as e:
            return ToolResult(success=False, error=f"Connection error: {e.reason}")
        except Exception as e:
            return ToolResult(success=False, error=f"Webhook failed: {str(e)}")


class MarkdownToHtmlTool(Tool):
    """Markdown 转 HTML 工具.

    将 Markdown 转换为 HTML，便于邮件发送。
    """

    def __init__(self) -> None:
        super().__init__()

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="markdown_to_html",
            description="将 Markdown 格式转换为 HTML，便于邮件发送或网页展示。",
            category=ToolCategory.SYSTEM,
            parameters={
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Markdown 格式的内容",
                    },
                    "include_styles": {
                        "type": "boolean",
                        "description": "是否包含内联样式（用于邮件，默认 True）",
                    },
                },
                "required": ["content"],
            },
            examples=[
                'markdown_to_html(content="# 标题\\n\\n正文内容")',
            ],
            tags=["html", "markdown", "email", "convert"],
        )

    def execute(self, **kwargs) -> ToolResult:
        content = kwargs.get("content", "")
        include_styles = kwargs.get("include_styles", True)

        if not content.strip():
            return ToolResult(success=False, error="Empty content")

        try:
            html = self._convert_to_html(content, include_styles)
            return ToolResult(success=True, data={"html": html})

        except Exception as e:
            return ToolResult(success=False, error=f"Conversion failed: {str(e)}")

    def _convert_to_html(self, markdown_text: str, include_styles: bool) -> str:
        """将 Markdown 转换为 HTML."""
        import re

        if include_styles:
            html = self._html_with_styles(markdown_text)
        else:
            html = self._html_basic(markdown_text)

        return html

    def _html_basic(self, text: str) -> str:
        """基本 HTML 转换."""
        html_parts = []
        lines = text.split("\n")
        in_code_block = False
        in_list = False

        for line in lines:
            stripped = line.strip()

            if stripped.startswith("```"):
                if in_code_block:
                    html_parts.append("</code></pre>")
                else:
                    html_parts.append("<pre><code>")
                in_code_block = not in_code_block
            elif in_code_block:
                html_parts.append(self._escape_html(line))
            elif stripped.startswith("# "):
                html_parts.append(f"<h1>{self._escape_html(stripped[2:])}</h1>")
            elif stripped.startswith("## "):
                html_parts.append(f"<h2>{self._escape_html(stripped[3:])}</h2>")
            elif stripped.startswith("### "):
                html_parts.append(f"<h3>{self._escape_html(stripped[4:])}</h3>")
            elif stripped.startswith("- ") or stripped.startswith("* "):
                if not in_list:
                    html_parts.append("<ul>")
                    in_list = True
                html_parts.append(f"<li>{self._escape_html(stripped[2:])}</li>")
            elif re.match(r"^\d+\. ", stripped):
                if not in_list:
                    html_parts.append("<ol>")
                    in_list = True
                html_parts.append(f"<li>{self._escape_html(re.sub(r'^\d+\. ', '', stripped))}</li>")
            elif stripped.startswith("---"):
                if in_list:
                    html_parts.append("</ul>" if not stripped.startswith("1.") else "</ol>")
                    in_list = False
                html_parts.append("<hr>")
            elif stripped.startswith("|"):
                # 简单表格处理
                if "|--" not in stripped:
                    html_parts.append(f"<p>{self._escape_html(stripped)}</p>")
            elif stripped:
                if in_list:
                    html_parts.append("</ul>" if not stripped.startswith("1.") else "</ol>")
                    in_list = False
                html_parts.append(f"<p>{self._escape_html(stripped)}</p>")

        if in_list:
            html_parts.append("</ul>")

        return "\n".join(html_parts)

    def _html_with_styles(self, text: str) -> str:
        """带样式的 HTML（适合邮件）。"""
        base_html = self._html_basic(text)

        # 邮件友好的内联样式
        styles = """
        <style>
        body {
            font-family: 'Microsoft YaHei', 'Segoe UI', Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }
        h1 {
            color: #2c3e50;
            border-bottom: 3px solid #3498db;
            padding-bottom: 10px;
            margin-top: 20px;
        }
        h2 {
            color: #34495e;
            border-bottom: 1px solid #ecf0f1;
            padding-bottom: 5px;
        }
        h3 { color: #7f8c8d; }
        code {
            background-color: #f8f9fa;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: 'Consolas', 'Monaco', monospace;
            font-size: 0.9em;
            color: #e74c3c;
        }
        pre {
            background-color: #2c3e50;
            color: #ecf0f1;
            padding: 15px;
            border-radius: 5px;
            overflow-x: auto;
        }
        pre code {
            background: none;
            color: inherit;
            padding: 0;
        }
        ul, ol {
            padding-left: 25px;
        }
        li { margin: 5px 0; }
        hr {
            border: none;
            border-top: 1px solid #bdc3c7;
            margin: 20px 0;
        }
        table {
            border-collapse: collapse;
            width: 100%;
            margin: 15px 0;
        }
        th, td {
            border: 1px solid #bdc3c7;
            padding: 10px;
            text-align: left;
        }
        th {
            background-color: #3498db;
            color: white;
        }
        tr:nth-child(even) {
            background-color: #ecf0f1;
        }
        </style>
        """

        return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    {styles}
</head>
<body>
{base_html}
</body>
</html>"""

    def _escape_html(self, text: str) -> str:
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )


class TextTemplateTool(Tool):
    """文本模板工具.

    提供告警通知的标准文本模板。
    """

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="format_notification",
            description="格式化告警通知文本，支持多种格式。",
            category=ToolCategory.SYSTEM,
            parameters={
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "通知标题",
                    },
                    "content": {
                        "type": "string",
                        "description": "通知内容",
                    },
                    "severity": {
                        "type": "string",
                        "description": "严重程度: critical, warning, info",
                    },
                    "format": {
                        "type": "string",
                        "description": "格式: text, dingtalk, feishu, enterprise_wechat",
                    },
                },
                "required": ["title", "content"],
            },
            examples=[
                'format_notification(title="CPU告警", content="服务器 CPU 使用率 99%", severity="critical", format="dingtalk")',
            ],
            tags=["notification", "template", "format", "alert"],
        )

    def execute(self, **kwargs) -> ToolResult:
        title = kwargs.get("title", "")
        content = kwargs.get("content", "")
        severity = kwargs.get("severity", "info")
        format_type = kwargs.get("format", "text")

        if not title and not content:
            return ToolResult(success=False, error="title or content is required")

        try:
            formatted = self._format_message(title, content, severity, format_type)
            return ToolResult(success=True, data={"formatted": formatted, "format": format_type})
        except Exception as e:
            return ToolResult(success=False, error=f"Formatting failed: {str(e)}")

    def _format_message(self, title: str, content: str, severity: str, format_type: str) -> str:
        """格式化消息."""
        # 严重程度图标
        icons = {
            "critical": "🔴 严重",
            "warning": "🟡 警告",
            "info": "🔵 信息",
        }
        icon = icons.get(severity.lower(), "🔵 信息")

        if format_type == "text":
            return f"{icon} {title}\n\n{content}"

        elif format_type == "dingtalk":
            return json.dumps({
                "msgtype": "markdown",
                "markdown": {
                    "title": title,
                    "text": f"### {icon} {title}\n\n{content}"
                }
            }, ensure_ascii=False)

        elif format_type == "feishu":
            return json.dumps({
                "msg_type": "interactive",
                "card": {
                    "header": {
                        "title": {"tag": "plain_text", "content": f"{icon} {title}"}
                    },
                    "elements": [
                        {"tag": "div", "text": {"tag": "lark_md", "content": content}}
                    ]
                }
            }, ensure_ascii=False)

        elif format_type == "enterprise_wechat":
            return json.dumps({
                "msgtype": "markdown",
                "markdown": {
                    "content": f"### {icon} {title}\n\n{content}"
                }
            }, ensure_ascii=False)

        else:
            return f"{icon} {title}\n\n{content}"
