"""媒体类工具(v0.9.39 #84 从 registry.py 搬出来)。

拍图取字(OCR)、零依赖 SVG 图表、图片生成。

搬的是**原文**:每个 `Tool(...)` 的字段与描述一字未改,只是换了文件。
注册表里这些工具的**先后顺序**由 `registry.build_default_registry()` 决定,
并由 `tests/test_registry_split.py` 逐字钉住 —— 别在这里自己排。
"""
from __future__ import annotations

from minicode import charts, images, ocr
from minicode.tools.base import Tool


def tools() -> list[Tool]:
    """媒体类工具。"""
    return [
        Tool(
            name="ocr_image",
            description="把图片里的文字取出来(拍错题、截图通知、黑板照片)。结果末尾一定会写「用了哪条后端 + 该后端的可靠性告警」:Windows 自带引擎对公式/上下标/手写不可靠,视觉模型可能夹带补全。所以取到的文本只当待核对草稿,不要当已核实原文引用;认不出来的地方宁可说「看不清」也不要猜。没有可用后端时会照实说缺什么、怎么补,不会硬编内容。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "图片路径(相对工作区或绝对路径),png/jpg 等"},
                    "backend": {"type": "string",
                                "description": "auto(默认,先走本机离线引擎) / system(只要 Windows 引擎) / vision(只要视觉模型)"},
                    "lang": {"type": "string",
                             "description": "系统引擎的识别语言,如 zh-Hans-CN / en-US;留空按系统用户配置,系统没装这个包会说明并退回"},
                    "max_chars": {"type": "integer", "description": "返回文本上限,默认 6000"},
                },
                "required": ["path"],
            },
            handler=ocr.ocr_image,
            trust="external",
            read_only=True,
        ),
        Tool(
            name="render_chart",
            description="把一组数字画成自包含的 SVG 图表(成绩对比、每周时长、学分构成),写到工作区 charts/ 下。零依赖、离线,不联网也不读本机数据:data 里的每个数值都必须由你或用户明确给出,程序不取数、不补数、不把缺失值当 0 —— 数据不全就先用对应工具查到、或直说查不到,别先画。source 要写真实来源(教务系统导出/用户提供 等),它会印在图脚注;确实没来源就留空,返回值里会提示你这图没有来源信息。",
            parameters={
                "type": "object",
                "properties": {
                    "chart_type": {"type": "string",
                                   "description": "bar(比大小)/ line(看趋势)/ pie(看占比,最多 10 块)"},
                    "data": {"description": "数据:[{label,value}] 数组、[[标签,数值]] 或 {标签:数值};数值必须是有限数字",
                             "type": ["array", "object"]},
                    "title": {"type": "string", "description": "图标题"},
                    "source": {"type": "string",
                               "description": "数据来源,会印在图脚注;没有就留空(返回值会提醒)"},
                    "name": {"type": "string", "description": "文件名(不含扩展名),默认取标题"},
                    "donut": {"type": "boolean", "description": "饼图是否画成环形,默认 false"},
                    "width": {"type": "integer", "description": "宽,默认 720"},
                    "height": {"type": "integer", "description": "高,默认 420"},
                },
                "required": ["chart_type", "data"],
            },
            handler=charts.chart_file,
            read_only=False,
        ),
        Tool(
            name="generate_image",
            description=(
                "生成图片,写到工作区 images/ 下。两条后端各管一件事,别混:"
                "① api(文生图模型)—— 需要 config.json 的 capabilities.images 里配好"
                "provider_id + 模型名 + 该供应商已有 Key,才能按 prompt 画出插图;"
                "② card(本地文字卡片)—— 不要网络也不要 Key,把 text/title 排成 PNG,"
                "画的就是这些字。**没配好接口时不要指望凭 prompt 出图**:那种情况要么"
                "给用户做文字卡片(必须传 text),要么如实说缺什么。返回值一定会写清楚"
                "用了哪条后端、字节数、以及「卡片是排版不是 AI 绘图」这类边界。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string",
                               "description": "给文生图模型的图像描述(只有配了接口才有效)"},
                    "text": {"type": "string",
                             "description": "要印在卡片上的文字(本地出图用),多行用 \\n"},
                    "title": {"type": "string", "description": "卡片大标题"},
                    "backend": {"type": "string",
                                "description": "auto(默认)/ api(只要文生图接口)/ card(只要本地排版)"},
                    "size": {"type": "string",
                             "description": "如 1024x1024;接口用默认 1024x1024,卡片默认 1080x1440"},
                    "name": {"type": "string", "description": "文件名(不含扩展名)"},
                },
            },
            handler=images.image_generate,
            read_only=False,
        ),
    ]
