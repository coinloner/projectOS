from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "AI应用开发项目备选方案_前五项.docx"

INK = "17202A"
MUTED = "5E6B78"
ACCENT = "176B65"
ACCENT_LIGHT = "EAF4F2"
LINE = "D8E1E5"
WHITE = "FFFFFF"


PROJECTS = [
    {
        "index": "01",
        "name": "MeetFlow｜会议转写与智能纪要系统",
        "position": "业务展示效果最好，兼顾 Python 后端、异步任务、语音处理与 LLM 应用。",
        "difficulty": "中等",
        "cycle": "3—4 周",
        "value": "高",
        "background": (
            "面向访谈、周会、评审会等长音频场景，解决人工整理耗时、行动项遗漏以及长文本摘要不稳定的问题。"
            "首版聚焦批量音频上传，不急于加入多人实时协作。"
        ),
        "flow": "音频上传 → 异步转写 → 分段文本入库 → 长文本切块摘要 → 结构化合并 → Markdown / DOCX 导出",
        "features": [
            "音频上传、格式校验、任务创建、状态查询和取消。",
            "接入 FunASR，保存分段文本、时间戳和识别状态。",
            "按句子与 Token 数切块，并发生成分段摘要后进行二次合并。",
            "使用 Pydantic 校验会议概要、议题、决策、行动项和风险项。",
            "Redis + Celery 执行转写、摘要和导出任务，支持幂等、重试与超时。",
            "会议所有权校验、JWT 登录、操作日志及 Markdown / DOCX 导出。",
        ],
        "stack": "FastAPI、Pydantic、SQLAlchemy、PostgreSQL、Redis、Celery、FunASR、LLM API、Vue、Docker Compose",
        "evidence": [
            "上传一段真实会议音频，完整展示转写、摘要和导出过程。",
            "准备短音频、长音频、空白音频和异常文件测试用例。",
            "记录真实转写耗时、摘要耗时、失败重试次数与导出成功率。",
        ],
        "questions": "长文本如何切块；重复内容如何去除；任务如何防止重复执行；转写失败后如何恢复；行动项如何校验。",
        "boundary": "首版不写“企业级、1 秒延迟、98% 成功率、数据零丢失”等未经实际测试的结果。",
        "resume": (
            "设计会议音频异步处理链路，完成音频上传、FunASR 分段转写、时间戳存储和任务状态管理；"
            "针对长会议文本实现分块并发摘要与结构化合并，通过 Schema 校验及失败重试提升纪要生成稳定性，并支持 Markdown / DOCX 导出。"
        ),
    },
    {
        "index": "02",
        "name": "DocMind｜企业文档知识库与可溯源问答系统",
        "position": "最稳妥的 LLM / RAG 项目，关键是把检索质量、引用溯源和权限隔离做深。",
        "difficulty": "中等",
        "cycle": "3—5 周",
        "value": "高",
        "background": (
            "面向内部制度、产品手册和技术资料查询，解决传统关键词搜索难以理解语义、生成答案缺少原文依据的问题。"
            "重点不是聊天界面，而是文档处理和检索链路。"
        ),
        "flow": "文档上传 → 解析清洗 → 分块与版本化 → 异步向量化 → 混合召回 → 重排序 → 带引用生成",
        "features": [
            "支持 PDF、DOCX、Markdown 上传、解析、删除及版本更新。",
            "按标题层级、段落和 Token 约束进行结构化分块，并保存页码和来源。",
            "实现关键词与向量双路召回，通过融合算法和 reranker 排序。",
            "生成答案时返回原始文件、页码、引用片段和检索分数。",
            "知识库级所有权和访问权限过滤，避免跨用户召回。",
            "建立小型标注问答集，计算 Recall@K、MRR 和引用命中情况。",
        ],
        "stack": "FastAPI、PostgreSQL、Redis、Celery、Qdrant / pgvector、Docling、Embedding、Reranker、LLM API、Vue",
        "evidence": [
            "准备一组公开文档和人工标注问题，保留基线与优化后的检索结果。",
            "展示答案引用定位、文档更新后索引重建和权限过滤。",
            "比较固定长度切块、结构化切块以及是否重排序的实际差异。",
        ],
        "questions": "为什么需要混合检索；切块粒度如何选择；文档更新如何处理；删除数据如何同步；如何评估 RAG。",
        "boundary": "避免只封装 LangChain；没有测试集时不写“准确率大幅提升”。",
        "resume": (
            "构建文档解析、分块、索引、检索与生成五阶段链路，采用关键词与向量双路召回及融合重排序；"
            "设计文档版本化索引、知识库权限过滤和引用溯源机制，使回答能够定位至原始文件与页码，并建立检索评估集验证召回效果。"
        ),
    },
    {
        "index": "03",
        "name": "AuditFlow｜AI 文档解析与结构化审核平台",
        "position": "与 ProjectOS 互补性最强，更突出 Python 后端、业务规则和可靠性设计。",
        "difficulty": "中等",
        "cycle": "3—5 周",
        "value": "高",
        "background": (
            "选择合同、报销材料、企业资质或申请材料中的一个明确场景，将非结构化文件转成结构化字段，"
            "再通过确定性规则与 LLM 辅助判断完成审核，低置信度结果交给人工复核。"
        ),
        "flow": "文件上传 → OCR / 版面解析 → 字段抽取 → Schema 校验 → 规则审核 → LLM 辅助判断 → 人工复核",
        "features": [
            "文档上传、格式检测、OCR / 版面解析和页面级原文保存。",
            "使用结构化输出抽取主体、金额、日期、条款等业务字段。",
            "Pydantic Schema 校验字段类型，异常字段进入修复或复核队列。",
            "金额、日期、必填项和跨字段一致性采用确定性规则校验。",
            "审核结论关联原文证据位置，支持人工修订与结果版本管理。",
            "异步处理、失败重试、幂等控制、审计日志和结果导出。",
        ],
        "stack": "FastAPI、Pydantic、SQLAlchemy、PostgreSQL、Redis、Celery、Docling / OCR、LLM API、Vue、Docker Compose",
        "evidence": [
            "构造正常、缺字段、格式错误和内容冲突等不同样例。",
            "统计字段抽取正确率、规则命中率、人工复核率和单文档耗时。",
            "展示每条审核结论对应的原文证据和人工修改记录。",
        ],
        "questions": "规则审核和 LLM 审核如何分工；低置信度如何处理；字段错误如何修复；如何保证结论可追溯。",
        "boundary": "不要宣称具备法律意见能力或完全代替人工审核；项目定位为信息提取和辅助审核。",
        "resume": (
            "设计文档解析、结构化抽取和规则审核流水线，通过 Pydantic Schema、字段修复重试及人工复核机制处理模型输出不稳定问题；"
            "将审核结果与页面原文证据关联，并实现任务幂等、版本管理和操作审计。"
        ),
    },
    {
        "index": "04",
        "name": "TicketPilot｜智能工单分类与回复辅助系统",
        "position": "最容易做成完整业务系统，贴近新人后端工程师可能接触的 CRUD、状态流转和权限场景。",
        "difficulty": "中低",
        "cycle": "2—4 周",
        "value": "中高",
        "background": (
            "面向内部 IT 支持或 SaaS 客服场景，自动识别工单类别、优先级和责任模块，检索相似案例并生成建议回复；"
            "AI 只提供建议，最终由人工确认。"
        ),
        "flow": "问题提交 → 工单分类 → 优先级判断 → 相似案例检索 → 建议回复 → 人工确认 → 状态流转与反馈",
        "features": [
            "用户、客服、管理员角色及工单创建、指派、处理、关闭流程。",
            "LLM 输出类别、优先级、摘要和置信度，低置信度自动转人工分类。",
            "从历史工单和知识库中检索相似案例，生成带来源的建议回复。",
            "记录采用、修改和拒绝 AI 建议的反馈，用于离线评估。",
            "实现状态机约束、并发更新控制、操作日志和消息通知。",
            "管理端统计分类分布、处理时长、转人工比例及建议采用率。",
        ],
        "stack": "FastAPI、SQLAlchemy、PostgreSQL、Redis、Celery、Qdrant / pgvector、LLM API、Vue、Docker Compose",
        "evidence": [
            "准备不同问题类型和紧急程度的模拟工单数据集。",
            "展示自动分类、人工覆盖、建议回复和完整状态流转。",
            "统计分类准确率、低置信度转人工率和建议采用率。",
        ],
        "questions": "工单状态如何约束；重复提交如何处理；置信度阈值怎么定；多人同时更新如何避免覆盖。",
        "boundary": "不虚构真实客户或客服数据，使用公开、脱敏或自行构造的数据集。",
        "resume": (
            "实现工单创建、指派、处理和关闭状态流转，接入 LLM 完成类别、优先级和摘要的结构化判断；"
            "结合历史案例检索生成回复建议，并通过置信度阈值、人工确认和反馈记录控制自动化风险。"
        ),
    },
    {
        "index": "05",
        "name": "ImageFlow Lite｜AI 生图任务调度与管理平台",
        "position": "适合补充 AIGC / 多模态能力，但环境和算力成本高于前四项。",
        "difficulty": "中高",
        "cycle": "4—6 周",
        "value": "中高",
        "background": (
            "面向个人创作者提供统一的文生图任务入口，重点解决生图请求耗时长、任务状态不透明、失败难恢复和图片管理分散的问题。"
            "首版通过 ComfyUI API 或云端生图 API 完成，不设计虚假的多 GPU 集群。"
        ),
        "flow": "提示词提交 → 参数校验与增强 → 异步排队 → 模型调用 → 状态回传 → 图片存储 → 历史记录",
        "features": [
            "文生图任务创建、参数模板、提示词增强和历史记录。",
            "Redis + Celery 管理异步队列，支持排队、执行、成功、失败和取消状态。",
            "封装 ComfyUI API 或第三方服务适配层，隔离上层业务与模型实现。",
            "任务超时、指数退避重试、失败原因记录和重复请求幂等。",
            "图片与元数据统一存储，支持结果查询、下载和再次生成。",
            "简单节点健康检查、并发限制和管理端任务监控。",
        ],
        "stack": "FastAPI、PostgreSQL、Redis、Celery、ComfyUI API / 生图 API、MinIO、Vue、Docker Compose",
        "evidence": [
            "展示从任务提交、排队、生成到图片保存的完整链路。",
            "模拟超时、节点离线和调用失败，验证重试与恢复逻辑。",
            "记录真实单任务耗时、队列等待时间、失败率和缓存命中情况。",
        ],
        "questions": "为什么使用异步队列；如何取消运行中的任务；模型适配层如何设计；图片和元数据如何保持一致。",
        "boundary": "没有多 GPU 环境时不写弹性伸缩、GPU 抢占、吞吐提升或 P99 优化。",
        "resume": (
            "构建 AI 生图异步任务管理平台，封装模型服务适配层并使用 Redis + Celery 管理任务排队、超时、重试和取消；"
            "实现图片结果与生成参数持久化、节点健康检查和任务监控，降低模型调用失败对业务流程的影响。"
        ),
    },
]


def set_font(run, size=10.0, bold=False, color=INK, name="Aptos"):
    run.font.name = name
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = RGBColor.from_string(color)
    r_pr = run._element.get_or_add_rPr()
    fonts = r_pr.rFonts
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        r_pr.insert(0, fonts)
    fonts.set(qn("w:eastAsia"), "PingFang SC")
    fonts.set(qn("w:ascii"), name)
    fonts.set(qn("w:hAnsi"), name)
    return run


def shade_cell(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def cell_margin(cell, top=75, start=95, bottom=75, end=95):
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for edge, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{edge}"))
        if node is None:
            node = OxmlElement(f"w:{edge}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def table_borders(table, color=LINE, size="5"):
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.first_child_found_in("w:tblBorders")
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tbl_pr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        node = borders.find(qn(f"w:{edge}"))
        if node is None:
            node = OxmlElement(f"w:{edge}")
            borders.append(node)
        node.set(qn("w:val"), "single")
        node.set(qn("w:sz"), size)
        node.set(qn("w:color"), color)


def add_page_number(paragraph):
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    set_font(paragraph.add_run("项目备选方案  ·  "), 8.2, color=MUTED)
    run = paragraph.add_run()
    fld_char_1 = OxmlElement("w:fldChar")
    fld_char_1.set(qn("w:fldCharType"), "begin")
    instr_text = OxmlElement("w:instrText")
    instr_text.set(qn("xml:space"), "preserve")
    instr_text.text = " PAGE "
    fld_char_2 = OxmlElement("w:fldChar")
    fld_char_2.set(qn("w:fldCharType"), "end")
    run._r.append(fld_char_1)
    run._r.append(instr_text)
    run._r.append(fld_char_2)
    set_font(run, 8.2, color=MUTED)


def configure_section(section):
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(1.55)
    section.bottom_margin = Cm(1.45)
    section.left_margin = Cm(1.75)
    section.right_margin = Cm(1.75)
    section.header_distance = Cm(0.5)
    section.footer_distance = Cm(0.55)
    add_page_number(section.footer.paragraphs[0])


def add_title(doc, text, subtitle=None):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(4)
    set_font(p.add_run(text), 18, True, INK)
    if subtitle:
        p2 = doc.add_paragraph()
        p2.paragraph_format.space_after = Pt(13)
        set_font(p2.add_run(subtitle), 9.5, False, ACCENT)


def add_heading(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(9)
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.keep_with_next = True
    set_font(p.add_run(text), 11.2, True, ACCENT)
    return p


def add_body(doc, text, after=5):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.line_spacing = 1.2
    set_font(p.add_run(text), 9.6, False, INK)
    return p


def add_bullets(doc, items):
    for item in items:
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = Cm(0.48)
        p.paragraph_format.first_line_indent = Cm(-0.32)
        p.paragraph_format.space_after = Pt(3)
        p.paragraph_format.line_spacing = 1.15
        set_font(p.add_run("—  "), 9.3, True, ACCENT)
        set_font(p.add_run(item), 9.3, False, INK)


def add_label_text(doc, label, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.line_spacing = 1.16
    set_font(p.add_run(label), 9.4, True, INK)
    set_font(p.add_run(text), 9.4, False, INK)


def add_summary_table(doc):
    headers = ["优先级", "项目", "难度", "周期", "简历价值", "适合方向"]
    rows = [
        ["首选", "MeetFlow 会议助手", "中", "3—4周", "高", "Python后端 / AI应用"],
        ["备选1", "DocMind 文档知识库", "中", "3—5周", "高", "LLM / RAG"],
        ["备选2", "AuditFlow 文档审核", "中", "3—5周", "高", "Python后端 / AI应用"],
        ["备选3", "TicketPilot 智能工单", "中低", "2—4周", "中高", "Python后端"],
        ["备选4", "ImageFlow Lite 生图平台", "中高", "4—6周", "中高", "AIGC / 多模态"],
    ]
    table = doc.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    widths = [Cm(1.65), Cm(4.4), Cm(1.35), Cm(1.7), Cm(1.85), Cm(4.25)]
    for index, width in enumerate(widths):
        table.columns[index].width = width
    table_borders(table)
    for i, header in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.width = widths[i]
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        cell_margin(cell)
        shade_cell(cell, ACCENT)
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        set_font(p.add_run(header), 8.8, True, WHITE)
    for r_idx, values in enumerate(rows):
        cells = table.add_row().cells
        for i, value in enumerate(values):
            cells[i].width = widths[i]
            cells[i].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            cell_margin(cells[i])
            if r_idx % 2 == 0:
                shade_cell(cells[i], "F7FAFA")
            p = cells[i].paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER if i != 1 else WD_ALIGN_PARAGRAPH.LEFT
            set_font(p.add_run(value), 8.65, i == 0, ACCENT if i == 0 else INK)


def add_project_page(doc, project):
    section = doc.add_section(WD_SECTION.NEW_PAGE)
    configure_section(section)

    header = doc.add_table(rows=1, cols=2)
    header.alignment = WD_TABLE_ALIGNMENT.CENTER
    header.autofit = False
    header.columns[0].width = Cm(2.15)
    header.columns[1].width = Cm(14.3)
    table_borders(header, color=WHITE, size="0")
    left, right = header.rows[0].cells
    cell_margin(left, 115, 90, 115, 90)
    cell_margin(right, 70, 180, 70, 0)
    shade_cell(left, ACCENT)
    left.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    p = left.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_font(p.add_run(project["index"]), 19, True, WHITE)
    right.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    p = right.paragraphs[0]
    set_font(p.add_run(project["name"]), 16, True, INK)
    p2 = right.add_paragraph()
    p2.paragraph_format.space_before = Pt(2)
    set_font(p2.add_run(project["position"]), 9.2, False, MUTED)

    table = doc.add_table(rows=1, cols=3)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    labels = [("复刻难度", project["difficulty"]), ("建议周期", project["cycle"]), ("简历价值", project["value"])]
    for cell, (label, value) in zip(table.rows[0].cells, labels):
        cell_margin(cell, 80, 100, 80, 100)
        shade_cell(cell, ACCENT_LIGHT)
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        set_font(p.add_run(f"{label}  "), 8.7, False, MUTED)
        set_font(p.add_run(value), 9.2, True, ACCENT)
    table_borders(table, color=WHITE, size="0")

    add_heading(doc, "项目背景与范围")
    add_body(doc, project["background"])
    add_label_text(doc, "核心流程：", project["flow"])

    add_heading(doc, "建议 MVP 功能")
    add_bullets(doc, project["features"])

    add_heading(doc, "建议技术栈")
    add_body(doc, project["stack"])

    add_heading(doc, "需要留下的真实证据")
    add_bullets(doc, project["evidence"])

    add_heading(doc, "面试高频追问")
    add_body(doc, project["questions"])

    add_heading(doc, "表述边界")
    box = doc.add_table(rows=1, cols=1)
    box.alignment = WD_TABLE_ALIGNMENT.CENTER
    table_borders(box, color=ACCENT_LIGHT, size="6")
    cell = box.cell(0, 0)
    shade_cell(cell, "F6FAF9")
    cell_margin(cell, 100, 130, 100, 130)
    p = cell.paragraphs[0]
    p.paragraph_format.line_spacing = 1.16
    set_font(p.add_run(project["boundary"]), 9.2, False, INK)

    add_heading(doc, "完成后可改写为简历描述")
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.38)
    p.paragraph_format.right_indent = Cm(0.25)
    p.paragraph_format.line_spacing = 1.24
    set_font(p.add_run("“" + project["resume"] + "”"), 9.5, False, ACCENT)


def build_document():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    configure_section(doc.sections[0])

    normal = doc.styles["Normal"]
    normal.font.name = "Aptos"
    normal.font.size = Pt(9.6)
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "PingFang SC")
    normal.paragraph_format.space_after = Pt(0)

    props = doc.core_properties
    props.title = "AI应用开发项目备选方案｜前五项"
    props.subject = "Python后端 / AI应用开发求职项目规划"
    props.author = "李德鸿"
    props.keywords = "Python, FastAPI, AI应用, LLM, RAG, 项目规划"

    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(34)
    p.paragraph_format.space_after = Pt(8)
    set_font(p.add_run("AI 应用开发"), 15, True, ACCENT)
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(7)
    set_font(p.add_run("项目备选方案"), 30, True, INK)
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(20)
    set_font(p.add_run("前五项 · Python 后端 / AI 应用开发求职项目规划"), 11, False, MUTED)

    box = doc.add_table(rows=1, cols=1)
    table_borders(box, color=ACCENT_LIGHT, size="6")
    cell = box.cell(0, 0)
    shade_cell(cell, ACCENT_LIGHT)
    cell_margin(cell, 160, 170, 160, 170)
    p = cell.paragraphs[0]
    p.paragraph_format.line_spacing = 1.25
    set_font(p.add_run("目标："), 10, True, ACCENT)
    set_font(
        p.add_run(
            "在 ProjectOS 之外选择一个能真实完成、可演示、可测试、经得起面试追问的业务型项目。"
            "所有简历成果均应以实际实现和测试数据为准。"
        ),
        10,
        False,
        INK,
    )

    add_heading(doc, "选择原则")
    add_bullets(
        doc,
        [
            "与 ProjectOS 形成互补：突出数据库、权限、异步任务、业务状态和交付闭环。",
            "控制首版范围：先完成一条可靠主链路，再扩展流式处理、协作或复杂调度。",
            "保留工程证据：代码、测试、接口文档、演示数据、故障场景和真实测量结果。",
            "允许被深入追问：每一项写进简历的能力，都能画出流程并定位到自己的实现。",
        ],
    )

    add_heading(doc, "候选项目总览")
    add_summary_table(doc)

    add_heading(doc, "组合建议")
    add_label_text(doc, "综合型岗位：", "ProjectOS + MeetFlow，业务展示直观，技术覆盖均衡。")
    add_label_text(doc, "Python 后端 / AI 应用：", "ProjectOS + AuditFlow，业务规则和可靠性设计更突出。")
    add_label_text(doc, "LLM / RAG：", "ProjectOS + DocMind，重点展示检索、评估和引用溯源。")
    add_label_text(doc, "快速完成：", "ProjectOS + TicketPilot，更容易在较短时间做出完整业务闭环。")
    add_label_text(doc, "AIGC / 多模态：", "ProjectOS + ImageFlow Lite，但需要预留模型环境与算力成本。")

    for project in PROJECTS:
        add_project_page(doc, project)

    section = doc.add_section(WD_SECTION.NEW_PAGE)
    configure_section(section)
    add_title(doc, "最终决策建议", "先选方向，再锁定一个项目完成，不建议五个同时开工")
    add_heading(doc, "当前推荐顺序")
    add_bullets(
        doc,
        [
            "首选 MeetFlow：演示效果最好，招聘方容易理解，后端与 AI 能力覆盖均衡。",
            "第二选择 AuditFlow：技术叙事更稳，更能体现规则、可靠性和人工复核设计。",
            "第三选择 DocMind：适合明确投递 LLM / RAG 岗位，但必须加入评估与引用能力。",
            "时间有限时选择 TicketPilot；明确投递 AIGC 岗位时再选择 ImageFlow Lite。",
        ],
    )
    add_heading(doc, "落地顺序")
    steps = [
        ("1", "确定一个明确业务场景和不超过 6 项的 MVP 功能。"),
        ("2", "先完成数据库模型、状态机、核心 API 和一条端到端主链路。"),
        ("3", "补充异步任务、重试、幂等、权限、日志和异常处理。"),
        ("4", "建立测试数据与评价指标，运行真实测试并保留结果。"),
        ("5", "完成部署文档、演示脚本、架构图和面试追问题库。"),
        ("6", "最后根据已经完成的功能和数据更新简历，不提前写成果。"),
    ]
    table = doc.add_table(rows=0, cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    table.columns[0].width = Cm(1.4)
    table.columns[1].width = Cm(14.7)
    table_borders(table, color=WHITE, size="0")
    for number, text in steps:
        cells = table.add_row().cells
        cell_margin(cells[0], 90, 60, 90, 60)
        cell_margin(cells[1], 90, 90, 90, 90)
        shade_cell(cells[0], ACCENT)
        shade_cell(cells[1], "F7FAFA")
        p = cells[0].paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        set_font(p.add_run(number), 10, True, WHITE)
        p = cells[1].paragraphs[0]
        set_font(p.add_run(text), 9.5, False, INK)

    add_heading(doc, "重要说明")
    add_body(
        doc,
        "本文档中的简历描述是完成目标，不代表项目当前已经具备这些能力。实施后应删除未完成内容，并将“提升、降低、成功率、延迟”等结果替换为本人真实测试数据。",
    )
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(18)
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    set_font(p.add_run("整理日期：2026 年 9 月 8 日"), 8.6, False, MUTED)

    doc.save(OUTPUT)
    print(OUTPUT)


if __name__ == "__main__":
    build_document()
