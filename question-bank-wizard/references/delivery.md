# 本地执行与成品交付

默认程序目录为下列代码中的project。程序搬迁后替换该路径。校验脚本兼容原根目录布局及新版app布局，不读取账号配置，不发起模型请求。

为每次任务创建独立目录，例如在当前工作目录的outputs中使用含日期与随机后缀的目录名。在任务目录保存bank.json和必要来源记录，最终成品放入尚不存在的成品目录。

## 规划与验收

在本次任务目录执行。plan只验证方案并打印每章题量及整卷配额，不验证空题目数组。check要求实际生成的全部卷已经完成。

```powershell
python -X utf8 "C:/Users/Administrator/.codex/skills/question-bank-wizard/scripts/check_bank.py" plan bank.json --project "E:/amiaoproject/百宝箱源码/apps/出题/题库生成工具"
python -X utf8 "C:/Users/Administrator/.codex/skills/question-bank-wizard/scripts/check_bank.py" check bank.json --project "E:/amiaoproject/百宝箱源码/apps/出题/题库生成工具"
```

需要排除旧题时，在check命令后追加下列参数。多个文件重复追加该参数。继续任务时使用同样的排除范围。

```powershell
--exclude "历史题干.json"
```

JSON容器同时保存完整内容和进度，但不是当前GUI的自动导入格式。不要承诺双击JSON可以直接导入程序，也不要自动写入程序的历史数据库或覆盖断点。

## 自研考试系统Excel

只在check通过后导出。以下Python代码在任务目录执行。需要历史去重时，将实际历史文件同步传给validate_bank的第3个参数。

```python
import importlib
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
sys.path.insert(0, "C:/Users/Administrator/.codex/skills/question-bank-wizard/scripts")
from check_bank import core_module, load_program, read_json, validate_bank
project = Path("E:/amiaoproject/百宝箱源码/apps/出题/题库生成工具")
bank = read_json("bank.json")
validate_bank(bank, project)
config, rules = load_program(project)
profiles = core_module("course_profiles", rules)
export_prefix = "app.exporters." if rules.__package__ else ""
exporter = importlib.import_module(export_prefix + "export_self_exam_excel")
template = Path(config.SELF_EXAM_EXCEL_TEMPLATE)
if not template.is_file():
    template = Path(config.TEMPLATE_DIR) / "自有考试系统-题目导入模板.xlsx"
if not template.is_file():
    raise FileNotFoundError("缺少自研考试系统Excel模板。")
with zipfile.ZipFile(template) as source:
    root = ET.fromstring(source.read("xl/workbook.xml"))
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    available = {node.get("name") for node in root.findall("m:sheets/m:sheet", ns)}
needed = {exporter._SHEET_NAMES[k] for k, count in bank["counts"].items() if count}
if not needed <= available:
    raise ValueError("模板缺少工作表：" + "、".join(sorted(needed - available)))
output = Path("成品")
output.mkdir(exist_ok=False)
course = profiles.course_task_key(bank["course_name"], bank["education_level"])
for label in bank["paper_labels"]:
    suffix = "" if label == "单卷" else f"_{label}卷"
    path = output / (profiles.get_safe_course_filename(course) + suffix + ".xlsx")
    exporter.export_to_self_exam_excel(bank["papers"][label], bank["chapters"],
                                      course, str(path), template_path=str(template))
```

技能创建时发现磁盘上的旧模板名为自有考试系统题目导入模板，有31个工作表，缺少综合分析题。默认以当前程序配置的模板为准，仅在所选题型工作表齐全时复用旧模板，不修改原模板。综合分析任务需要包含该工作表的新版模板，或选择保留完整解析的其他交付格式；不能静默漏题。

## Word与其他格式

Word优先调用export_self_exam_word.py中的export_to_self_exam_word，能保留阅读子题解析与综合分析解析。导出后按下列方式把编号标点转为中文，保留题目原始内容。

```python
import re
from docx import Document
word_exporter = importlib.import_module(export_prefix + "export_self_exam_word")
for label in bank["paper_labels"]:
    suffix = "" if label == "单卷" else f"_{label}卷"
    path = output / (profiles.get_safe_course_filename(course) + suffix + ".docx")
    word_exporter.export_to_self_exam_word(bank["papers"][label], bank["chapters"], course, str(path))
    document = Document(path)
    for paragraph in document.paragraphs:
        changed = re.sub(r"^(\d+|[ABCDE])\.\s*", r"\1、", paragraph.text)
        if changed != paragraph.text:
            paragraph.text = changed
    document.save(path)
```

Word代码复用前段已校验的bank、course和output等变量。只导出Word时仍需先读bank、运行validate_bank，通过load_program取得规则和模块前缀、生成安全课程名并创建独立成品目录，无需加载Excel模板。

考试系统Excel使用export_exam_excel.py中的export_to_exam_excel，综合分析等主观题映射到简答工作表，案例与阅读写入复合题表。知识点Excel使用export_excel.py中的export_to_excel，生成xls，覆盖单选、多选、判断，名词解释、简答、论述、综合分析和翻译合并到问答工作表；不会导出案例、阅读和选词填空。用户选择该格式且包含不支持题型时，先说明并选择其他完整格式。

普通Word的export_to_word会遗漏阅读子题解析与综合分析解析，不把该输出当成完整解析版。若必须采用普通Word版式，补全本次生成文档中的解析后再交付。

## 交付前核对

重新读取输出文件，按卷核对顶层题量、复合题子题量、答案和解析。Excel核对所选工作表、表头、文本值及没有空白题目表；Word核对编号、题目、选项、答案与解析。办公文件有排版要求时，使用环境可用的渲染预览并检查。

保留原始bank.json和来源记录。交付最终文件的绝对路径链接，说明新增题量与实际去重范围。当前环境缺少模板或渲染能力时，准确说明已完成与未完成部分，不把结构校验通过说成已通过教学审核或实际平台导入测试。
