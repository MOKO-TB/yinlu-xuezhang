---
name: pdf
description: PDF 合并/拆分/提取文本(pypdf)与网页转 PDF(Edge 无头打印):奖助学金材料合并、课件拆页、通知存档
libs: pypdf
---

# 什么时候用

- **合并**:奖助学金申请要把申请表+证明材料多个 PDF 合成一个;
- **拆分**:把一本课件/合集抽出某几页;
- **提取**:从 PDF 里取文字(通知内容、参考文献段落)再整理;
- **网页/HTML 转 PDF**:把网页通知、生成的 HTML 页面存成 PDF 存档。

# 工作流

**write_file 写脚本 → py_script 运行 → 检查退出码 0 → 报告产物路径**。
内置环境带 pypdf;网页转 PDF 用系统自带的 Edge(不用装任何东西)。

# 合并 / 拆分 / 提取(pypdf)

```python
from pypdf import PdfReader, PdfWriter

# 合并:材料顺序按用户给的清单,别自作主张
writer = PdfWriter()
for name in ["申请表.pdf", "身份证复印件.pdf", "成绩单.pdf"]:
    reader = PdfReader(name)
    for page in reader.pages:
        writer.add_page(page)
with open("合并后的材料.pdf", "wb") as f:
    writer.write(f)
print("合并完成,共", len(writer.pages), "页")

# 拆分:取第 3-5 页(页码从 1 数,转成 0 起下标)
reader = PdfReader("课件合集.pdf")
writer = PdfWriter()
for i in range(2, 5):
    writer.add_page(reader.pages[i])
with open("抽出的一章.pdf", "wb") as f:
    writer.write(f)

# 提取文本
reader = PdfReader("通知.pdf")
text = "\n".join(page.extract_text() or "" for page in reader.pages)
print(text[:1500])
```

# 网页 / HTML 转 PDF(Edge 无头打印)

先把要转的内容写成 .html(或用户给的是网页 URL),然后**用 bash 工具**调
Edge 无头打印(各机器 Edge 路径基本是下面两个之一,先探测):

```bash
EDGE="C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
[ -f "$EDGE" ] || EDGE="C:/Program Files/Microsoft/Edge/Application/msedge.exe"
"$EDGE" --headless=new --disable-gpu --no-pdf-header-footer \
  --print-to-pdf="D:/工作区/通知存档.pdf" "file:///D:/工作区/通知.html"
```

- URL 是网上页面时,把最后的 `file:///...` 换成 https 地址即可;
- `--no-pdf-header-footer` 去掉页眉页脚的日期和 URL;
- 脚本里检查产物存在且大于 0 字节才算成功;Edge 路径找不到时如实说,
  让用户用浏览器手动 Ctrl+P 打印,不要编产物。

# 坑

- **加密/带权限的 PDF**(贷款合同、带水印的证书)读出来是空的或报错,
  如实告知"这个 PDF 有密码保护,无法处理",不要反复重试。
- `extract_text()` 对**扫描件**(图片型 PDF)取不到文字——它没有文本层。
  这种情况告诉用户需要 OCR,超出当前能力,别硬编内容。
- 提取的文本会丢版式(表格变成串行文字),整理时按语义重排,注明"从 PDF
  提取,格式有出入以原文件为准"。
- 合并几百页的大文件时 print 进度;输出文件名别覆盖原文件。

# 口径

- 奖助学金、合同的原始材料只做合并不改内容;拆分/重命名不影响原件
  (产物存新文件)。
- 涉及身份证、家庭信息的 PDF,提醒用户发给别人前确认收件人。
