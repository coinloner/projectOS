from __future__ import annotations

from pathlib import Path

from PIL import Image
from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PAGE = Path("/private/tmp/resume_render/resume.png")
PHOTO = ROOT / "output" / "resume_photo.png"
OUTPUT = ROOT / "output" / "李德鸿_Python后端_AI应用开发_简历_极简高级版.docx"

NAVY = "183C3C"
BLUE = "16706A"
DARK = "1B1D20"
GRAY = "62686D"
LIGHT = "DDE3E3"


def set_cell_margins(cell, top=35, start=35, bottom=35, end=35):
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for margin, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{margin}"))
        if node is None:
            node = OxmlElement(f"w:{margin}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def set_cell_shading(cell, fill: str):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_table_borders(table, color="FFFFFF", size="0"):
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.first_child_found_in("w:tblBorders")
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tbl_pr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        tag = f"w:{edge}"
        node = borders.find(qn(tag))
        if node is None:
            node = OxmlElement(tag)
            borders.append(node)
        node.set(qn("w:val"), "nil" if size == "0" else "single")
        node.set(qn("w:sz"), size)
        node.set(qn("w:color"), color)


def set_run_font(run, size=9.3, bold=False, color=DARK, name="Aptos"):
    run.font.name = name
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = RGBColor.from_string(color)
    r_pr = run._element.get_or_add_rPr()
    r_fonts = r_pr.rFonts
    if r_fonts is None:
        r_fonts = OxmlElement("w:rFonts")
        r_pr.insert(0, r_fonts)
    r_fonts.set(qn("w:eastAsia"), "PingFang SC")
    r_fonts.set(qn("w:ascii"), name)
    r_fonts.set(qn("w:hAnsi"), name)
    return run


def configure_paragraph(p, before=0, after=0, line=1.08, keep=False):
    pf = p.paragraph_format
    pf.space_before = Pt(before)
    pf.space_after = Pt(after)
    pf.line_spacing_rule = WD_LINE_SPACING.SINGLE
    pf.line_spacing = line
    pf.keep_together = keep
    return p


def add_hyperlink(paragraph, text: str, url: str, size=9.2, color=BLUE):
    part = paragraph.part
    rel_id = part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink", is_external=True)
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), rel_id)
    run = OxmlElement("w:r")
    r_pr = OxmlElement("w:rPr")
    r_fonts = OxmlElement("w:rFonts")
    r_fonts.set(qn("w:ascii"), "Aptos")
    r_fonts.set(qn("w:hAnsi"), "Aptos")
    r_fonts.set(qn("w:eastAsia"), "PingFang SC")
    r_pr.append(r_fonts)
    c = OxmlElement("w:color")
    c.set(qn("w:val"), color)
    r_pr.append(c)
    sz = OxmlElement("w:sz")
    sz.set(qn("w:val"), str(int(size * 2)))
    r_pr.append(sz)
    run.append(r_pr)
    text_node = OxmlElement("w:t")
    text_node.text = text
    run.append(text_node)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def add_bottom_border(paragraph, color=BLUE, size="10", space="2"):
    p_pr = paragraph._p.get_or_add_pPr()
    p_bdr = p_pr.find(qn("w:pBdr"))
    if p_bdr is None:
        p_bdr = OxmlElement("w:pBdr")
        p_pr.append(p_bdr)
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), size)
    bottom.set(qn("w:space"), space)
    bottom.set(qn("w:color"), color)
    p_bdr.append(bottom)


def add_left_border(paragraph, color=BLUE, size="18", space="8"):
    p_pr = paragraph._p.get_or_add_pPr()
    p_bdr = p_pr.find(qn("w:pBdr"))
    if p_bdr is None:
        p_bdr = OxmlElement("w:pBdr")
        p_pr.append(p_bdr)
    left = OxmlElement("w:left")
    left.set(qn("w:val"), "single")
    left.set(qn("w:sz"), size)
    left.set(qn("w:space"), space)
    left.set(qn("w:color"), color)
    p_bdr.append(left)


def add_section_title(doc, text: str):
    english = {
        "个人概况": "PROFILE",
        "教育经历": "EDUCATION",
        "相关研发经历": "EXPERIENCE",
        "项目经历": "PROJECT",
        "专业技能": "SKILLS",
    }.get(text, "")
    p = doc.add_paragraph()
    configure_paragraph(p, before=8.0, after=4.5, line=1)
    p.paragraph_format.keep_with_next = True
    add_left_border(p)
    set_run_font(p.add_run(text), size=11.0, bold=True, color=DARK)
    if english:
        set_run_font(p.add_run(f"   {english}"), size=7.8, bold=True, color=BLUE, name="Aptos")
    return p


def add_role_row(doc, left: str, right: str, subtitle: str | None = None):
    table = doc.add_table(rows=1, cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    table.columns[0].width = Cm(14.0)
    table.columns[1].width = Cm(4.0)
    set_table_borders(table)
    left_cell, right_cell = table.rows[0].cells
    set_cell_margins(left_cell, 0, 0, 0, 0)
    set_cell_margins(right_cell, 0, 0, 0, 0)
    p = left_cell.paragraphs[0]
    configure_paragraph(p, after=0, line=1)
    set_run_font(p.add_run(left), size=10.4, bold=True, color=DARK)
    if subtitle:
        set_run_font(p.add_run(f"   {subtitle}"), size=8.55, color=BLUE)
    p = right_cell.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    configure_paragraph(p, after=0, line=1)
    set_run_font(p.add_run(right), size=8.7, bold=True, color=GRAY)
    return table


def add_bullet(doc, label: str, text: str, level=0, size=9.15, after=2.35):
    p = doc.add_paragraph()
    configure_paragraph(p, after=after, line=1.16, keep=True)
    p.paragraph_format.left_indent = Cm(0.43 + level * 0.35)
    p.paragraph_format.first_line_indent = Cm(-0.31)
    set_run_font(p.add_run("—  "), size=size, bold=True, color=BLUE)
    if label:
        set_run_font(p.add_run(label), size=size, bold=True, color=DARK)
    set_run_font(p.add_run(text), size=size, color=DARK)
    return p


def make_photo():
    if not SOURCE_PAGE.exists():
        return None
    image = Image.open(SOURCE_PAGE).convert("RGB")
    # Coordinates are taken from the original A4 render. Keep the ID photo only.
    crop = image.crop((1134, 55, 1332, 274)).convert("L").convert("RGB")
    crop.save(PHOTO, quality=95)
    return PHOTO


def build_document():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    photo = make_photo()

    doc = Document()
    section = doc.sections[0]
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(1.05)
    section.bottom_margin = Cm(0.95)
    section.left_margin = Cm(1.45)
    section.right_margin = Cm(1.45)
    section.header_distance = Cm(0.35)
    section.footer_distance = Cm(0.35)

    normal = doc.styles["Normal"]
    normal.font.name = "Aptos"
    normal.font.size = Pt(9.3)
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "PingFang SC")
    normal.paragraph_format.space_after = Pt(0)

    props = doc.core_properties
    props.title = "李德鸿｜Python后端 / AI应用开发简历"
    props.subject = "求职简历"
    props.author = "李德鸿"
    props.keywords = "Python, FastAPI, AI应用, Agent, LLM, ProjectOS"

    header = doc.add_table(rows=1, cols=2)
    header.alignment = WD_TABLE_ALIGNMENT.CENTER
    header.autofit = False
    header.columns[0].width = Cm(14.85)
    header.columns[1].width = Cm(2.75)
    set_table_borders(header)
    left, right = header.rows[0].cells
    set_cell_margins(left, 0, 0, 0, 60)
    set_cell_margins(right, 0, 20, 0, 0)
    left.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    right.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER

    p = left.paragraphs[0]
    configure_paragraph(p, after=1.5, line=1)
    set_run_font(p.add_run("李德鸿"), size=24, bold=True, color=DARK)

    p = left.add_paragraph()
    configure_paragraph(p, after=4.0, line=1)
    set_run_font(p.add_run("PYTHON BACKEND  ·  AI APPLICATION"), size=9.2, bold=True, color=BLUE, name="Aptos")

    p = left.add_paragraph()
    configure_paragraph(p, after=0, line=1)
    set_run_font(p.add_run("17818016852   ·   "), size=8.65, color=GRAY)
    add_hyperlink(p, "1537263837@qq.com", "mailto:1537263837@qq.com", size=8.65)
    set_run_font(p.add_run("   ·   "), size=8.65, color=GRAY)
    add_hyperlink(p, "github.com/coinloner", "https://github.com/coinloner", size=8.65)
    set_run_font(p.add_run("   ·   2025 届本科"), size=8.65, color=GRAY)

    if photo:
        p = right.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        p.add_run().add_picture(str(photo), width=Cm(2.35), height=Cm(2.60))

    rule = doc.add_paragraph()
    configure_paragraph(rule, before=3.5, after=0, line=1)
    add_bottom_border(rule, color=LIGHT, size="6", space="0")

    add_section_title(doc, "个人概况")
    p = doc.add_paragraph()
    configure_paragraph(p, after=2.2, line=1.18, keep=True)
    p.paragraph_format.left_indent = Cm(0.14)
    set_run_font(
        p.add_run(
            "2025 届数据科学与大数据技术本科，具备 Python 后端与 LLM 应用项目实践。"
            "能够围绕实际问题完成需求拆分、接口设计、任务编排、代码实现、测试与技术文档沉淀；"
            "独立持续开发多 Agent 软件交付编排平台，重点实践 FastAPI、异步执行、DAG 调度、"
            "工具权限控制、容器化运行和故障恢复，希望从 Python 后端 / AI 应用开发岗位切入。"
        ),
        size=9.25,
    )

    add_section_title(doc, "教育经历")
    add_role_row(doc, "广东技术师范大学", "2021.09—2025.06", "数据科学与大数据技术 · 本科")
    p = doc.add_paragraph()
    configure_paragraph(p, after=2, line=1.12, keep=True)
    p.paragraph_format.left_indent = Cm(0.2)
    set_run_font(p.add_run("主修：Python 程序设计、数据库系统原理、算法设计与分析、机器学习、Spark 大数据分析"), size=8.8, color=GRAY)

    add_section_title(doc, "相关研发经历")
    add_role_row(doc, "个人研发实践", "2026.07—至今", "Python / AI 应用开发")
    p = doc.add_paragraph()
    configure_paragraph(p, before=2.0, after=1.5, line=1.05, keep=True)
    set_run_font(p.add_run("ProjectOS  /  半托管多 Agent 软件交付编排平台"), size=10.2, bold=True, color=DARK)
    set_run_font(p.add_run("   Python · FastAPI · CrewAI · Pydantic · SQLite/FTS5 · Docker · Git"), size=8.25, color=GRAY)
    p = doc.add_paragraph()
    configure_paragraph(p, after=2.0, line=1.05, keep=True)
    p.paragraph_format.left_indent = Cm(0.2)
    set_run_font(p.add_run("项目地址："), size=8.65, bold=True, color=GRAY)
    add_hyperlink(p, "github.com/coinloner/projectOS", "https://github.com/coinloner/projectOS", size=8.65)
    add_bullet(doc, "项目背景：", "针对 LLM 直接生成代码时过程不可控、上下文易丢失、失败后难恢复的问题，设计 Requirement → Architecture → Task → Bootstrap → Code → Test → Review 的半托管软件交付链路。")
    add_bullet(doc, "计划建模：", "将自然语言 Planner 和固定 Workflow 统一编译为结构化 ExecutionPlan/WorkItem DAG，使用模型校验输入输出、节点依赖和计划完整性，避免无效任务进入执行阶段。")
    add_bullet(doc, "并发执行：", "实现 GraphRunner 就绪节点调度、分批有界并发、Agent 级并发限制、错误分类与有限重试；通过版本化 checkpoint 保存节点状态并支持受校验的断点恢复。")
    add_bullet(doc, "Agent 与产物：", "划分 7 类领域 Agent，并以标准执行上下文连接 ToolGateway；设计 staged/candidate/promotion 产物生命周期，使模型输出经过验证和审查后再进入正式交付。")
    add_bullet(doc, "安全与隔离：", "通过工具注册、路径白名单和 capability approval 限制 Agent 权限；结合 Git worktree/ChangeSet 隔离代码分区，在 Docker Sandbox 中运行测试并采集执行证据。")
    add_bullet(doc, "可观测与接口：", "以 Trace 记录节点、工具、产物和失败事件，使用 SQLite FTS5 提供预算化 Memory 召回；通过 FastAPI 暴露项目创建、运行进度、审批、Trace 查询和恢复接口。")
    add_bullet(doc, "工程产出：", "持续维护架构、API、流程定义和验证矩阵文档；当前约 3.4 万行 Python 代码、59 个测试模块、500+ 测试方法，并明确记录进程内执行器等 MVP 生产化边界。", after=2.6)

    add_section_title(doc, "项目经历")
    add_role_row(doc, "微博舆情分析系统", "2025.03—2025.05", "数据采集 / 文本分类 / 可视化")
    add_bullet(doc, "数据链路：", "使用 Python 与 Selenium 搭建独立采集流程，完成数据清洗、去噪、分词与结构化存储。", size=9.0)
    add_bullet(doc, "模型应用：", "基于 PyTorch 接入 BERT 预训练模型完成正负向文本分类，整理数据集准备、训练、推理及结果输出流程。", size=9.0)
    add_bullet(doc, "展示与查询：", "使用 Vue.js 构建可视化看板，支持按时间范围和关键词筛选话题，并展示情绪变化趋势。", size=9.0)
    add_bullet(doc, "项目认识：", "完成从数据采集、模型应用到前端展示的端到端实践，并关注采集稳定性、文本预处理和模型输出可解释性。", size=9.0, after=2.4)

    add_section_title(doc, "专业技能")
    skills = doc.add_table(rows=4, cols=2)
    skills.alignment = WD_TABLE_ALIGNMENT.CENTER
    skills.autofit = False
    skills.columns[0].width = Cm(2.55)
    skills.columns[1].width = Cm(15.05)
    set_table_borders(skills, color=LIGHT, size="4")
    skill_texts = [
        ("Python 后端", "熟悉 Python、FastAPI、Pydantic、REST API 与 asyncio，能够完成参数校验、异常处理、配置管理和接口文档编写。"),
        ("LLM / Agent", "具备 LLM API 接入、结构化输出、上下文组织、Agent 注册、Tool Calling、Workflow/DAG 编排与失败恢复实践。"),
        ("数据与应用", "使用 SQLite/FTS5，掌握 MySQL 与基础 SQL；了解 Redis 常见数据结构和缓存/任务队列应用；接触 PyTorch、BERT、Selenium、Vue.js。"),
        ("工程能力", "使用 Git 管理版本与分支，能够在 Linux 环境开发，具备 Docker/Compose、unittest、日志排查、架构与 API 文档沉淀经验。"),
    ]
    for row, (label, value) in zip(skills.rows, skill_texts):
        label_cell, value_cell = row.cells
        set_cell_margins(label_cell, 70, 45, 70, 45)
        set_cell_margins(value_cell, 70, 55, 70, 45)
        p = label_cell.paragraphs[0]
        configure_paragraph(p, after=0, line=1.03, keep=True)
        set_run_font(p.add_run(label.upper()), size=8.35, bold=True, color=BLUE)
        p = value_cell.paragraphs[0]
        configure_paragraph(p, after=0, line=1.05, keep=True)
        set_run_font(p.add_run(value), size=8.65, color=DARK)

    # Avoid adding a visible footer; embed a small page number field only.
    footer = section.footer
    fp = footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    configure_paragraph(fp, after=0, line=1)
    set_run_font(fp.add_run(" "), size=1, color="FFFFFF")

    doc.save(OUTPUT)
    print(OUTPUT)


if __name__ == "__main__":
    build_document()
