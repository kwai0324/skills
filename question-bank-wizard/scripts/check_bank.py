"""复用题库程序的规则，规划配额并验收分步出题任务。"""
import argparse
import importlib
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def load_program(project):
    project = Path(project).resolve()
    packaged = (project / "app/core/question_generator.py").is_file()
    core = project / "app/core" if packaged else project
    config_path = project / "app/config.py" if packaged else project / "config.py"
    require(config_path.is_file(), "程序目录缺少题型配置。")
    for name in ("question_generator.py", "question_identity.py",
                 "course_profiles.py", "excel_text_safety.py"):
        require((core / name).is_file(), f"程序目录缺少{name}。")
    sys.path.insert(0, str(project))
    return (importlib.import_module("app.config" if packaged else "config"),
            importlib.import_module("app.core.question_generator" if packaged
                                    else "question_generator"))


def core_module(name, rules):
    prefix = rules.__package__ + "." if rules.__package__ else ""
    return importlib.import_module(prefix + name)


def check_spec(bank, known_types):
    require(isinstance(bank, dict), "任务容器必须为JSON对象。")
    require(isinstance(bank.get("course_name"), str)
            and bank["course_name"].strip(), "缺少课程名称。")
    require(bank.get("education_level") in ("", "中专", "专科", "本科"),
            "培养层次须为中专、专科、本科或空字符串。")
    chapters = bank.get("chapters")
    require(isinstance(chapters, list) and chapters
            and all(isinstance(ch, str) and ch.strip() for ch in chapters),
            "章节须为非空字符串数组。")
    require(len(set(chapters)) == len(chapters), "章节名称重复。")
    counts = bank.get("counts")
    require(isinstance(counts, dict) and counts, "缺少每卷题型数量。")
    require(set(counts) <= set(known_types), "包含程序不支持的题型键。")
    require(all(type(count) is int and count >= 0 for count in counts.values())
            and sum(counts.values()) > 0, "每卷题量须为非负整数且合计大于0。")
    require(bank.get("paper_labels") in (["单卷"], ["A", "B"]),
            "卷别须为单卷，或按A、B排列的2卷。")


def answer_targets(counts, rules):
    targets = {}
    if counts.get("single"):
        targets["single"] = rules.single_answer_quotas(counts["single"])
    if counts.get("multiple"):
        targets["multiple"] = rules.multiple_answer_quotas(counts["multiple"])
    if counts.get("judge"):
        total = counts["judge"]
        targets["judge"] = {"正确": total // 2, "错误": total - total // 2}
    return targets


def make_plan(bank, project):
    config, rules = load_program(project)
    check_spec(bank, config.QUESTION_TYPES)
    chapter_count = len(bank["chapters"])
    chapter_counts = {}
    for kind, total in bank["counts"].items():
        base, remainder = divmod(total, chapter_count)
        chapter_counts[kind] = [base + int(i < remainder)
                               for i in range(chapter_count)]
    return {"course_name": bank["course_name"],
            "paper_labels": bank["paper_labels"],
            "per_paper_counts": bank["counts"],
            "chapters": bank["chapters"], "chapter_counts": chapter_counts,
            "answer_quotas": answer_targets(bank["counts"], rules),
            "total_parent_questions": sum(bank["counts"].values())
            * len(bank["paper_labels"])}


def check_text(value):
    if isinstance(value, str):
        for char in value:
            name = unicodedata.name(char, "")
            require(ord(char) <= 0xFFFF and char != "\ufffd"
                    and "SUBSCRIPT" not in name and "SUPERSCRIPT" not in name,
                    "题目包含乱码、补充平面字符或Unicode上下标。")
    elif isinstance(value, dict):
        for child in value.values():
            check_text(child)
    elif isinstance(value, list):
        for child in value:
            check_text(child)


def check_item(item, kind, rules, english=False):
    require(isinstance(item, dict), "题目必须为JSON对象。")
    if kind in ("case", "reading"):
        material = "case" if kind == "case" else "passage"
        require(isinstance(item.get(material), str) and item[material].strip(),
                "复合题缺少有效材料。")
        require(not item.get("q"), "复合题父项使用材料字段，不另设题干。")
        questions = item.get("questions")
        require(isinstance(questions, list), "复合题缺少子题数组。")
        require(2 <= len(questions) <= 3 if kind == "case" else len(questions) == 5,
                "案例须含2至3个子题，阅读须含5个子题。")
        if kind == "reading" and english:
            words = re.findall(r"[A-Za-z]+(?:['’-][A-Za-z]+)*", item[material])
            require(200 <= len(words) <= 300, "英语阅读材料须含200至300个英文单词。")
        local_index = core_module("question_identity", rules).DuplicateIndex()
        for index, sub in enumerate(questions, 1):
            try:
                check_item(sub, "single" if kind == "reading" else "short", rules)
                require(not local_index.contains(sub), "复合题内部子题重复。")
                local_index.add(sub)
            except ValueError as exc:
                raise ValueError(f"第{index}个子题：{exc}") from exc
        return
    fields = ["q", "ans"]
    if kind in ("single", "multiple", "cloze", "judge", "comprehensive"):
        fields.append("exp")
    for field in fields:
        require(isinstance(item.get(field), str) and item[field].strip(),
                f"{field}字段须为非空字符串。")
    require(rules.strip_question_preface(item["q"]) == item["q"],
            "题干包含额外题号、知识点清单或命题说明。")
    if kind in ("single", "multiple", "cloze"):
        letters = "ABCDE" if kind == "multiple" else "ABCD"
        require(all(isinstance(item.get(letter), str) and item[letter].strip()
                    for letter in letters), "选择题选项缺失或不是文本。")
        require(kind == "multiple" or not item.get("E"), "此题型只允许4个选项。")
        normalized = rules.normalize_choice_item(item, multiple=kind == "multiple")
        require(normalized is not None, "选项、答案或解析存在格式问题或明确冲突。")
        require(normalized["ans"] == item["ans"] and normalized["q"] == item["q"],
                "答案须为规范大写字母，题干不得包含多选标记或额外题号。")
        if kind == "cloze":
            blanks = re.findall(r"_{2,}", item["q"])
            require(blanks == ["____"], "选词填空须包含恰好1处连续4个下划线。")
    if kind == "judge":
        require(item["ans"] in ("正确", "错误"), "判断题答案只能为正确或错误。")


def history_items(data, rules):
    if isinstance(data, list):
        items = data
    else:
        require(isinstance(data, dict) and isinstance(data.get("papers"), dict),
                "历史排除JSON须为本技能任务容器或题干材料对象数组。")
        items = []
        for results in data["papers"].values():
            require(isinstance(results, dict), "历史卷的题型字典无效。")
            for group in results.values():
                require(isinstance(group, list), "历史题型内容须为数组。")
                items.extend(group)
    question_text_key = core_module("question_identity", rules).question_text_key
    require(all(isinstance(item, dict) and question_text_key(item) for item in items),
            "历史排除文件包含没有题干或材料的对象。")
    return items


def validate_bank(bank, project, exclude_paths=()):
    config, rules = load_program(project)
    check_spec(bank, config.QUESTION_TYPES)
    validate_exportable_results = core_module(
        "excel_text_safety", rules).validate_exportable_results
    DuplicateIndex = core_module("question_identity", rules).DuplicateIndex
    excluded = []
    for path in exclude_paths:
        excluded.extend(history_items(read_json(path), rules))
    duplicate_index = DuplicateIndex(excluded)
    papers = bank.get("papers")
    require(isinstance(papers, dict) and set(papers) == set(bank["paper_labels"]),
            "实际卷别与方案不符，可能缺少A卷或B卷。")
    targets = answer_targets(bank["counts"], rules)
    language = bank.get("language") or rules._detect_language(bank["course_name"])
    require(isinstance(language, str), "目标语言须为字符串。")
    summaries = {}
    for label in bank["paper_labels"]:
        results = papers[label]
        require(isinstance(results, dict) and set(results) <= set(bank["counts"]),
                f"{label}包含方案以外的题型，或题型字典无效。")
        for kind, count in bank["counts"].items():
            items = results.get(kind, [])
            name = config.QUESTION_TYPES[kind]
            require(isinstance(items, list) and len(items) == count,
                    f"{label}的{name}数量不符，要求{count}道。")
            for index, item in enumerate(items, 1):
                try:
                    check_item(item, kind, rules, english=language == "英语")
                    chapter = item.get("ch")
                    require(type(chapter) is int and 0 <= chapter < len(bank["chapters"]),
                            "章节下标缺失或超出范围。")
                    if "ch_name" in item:
                        require(item["ch_name"] == bank["chapters"][chapter],
                                "章节名称与章节下标不一致。")
                    check_text(item)
                    validate_exportable_results({kind: [item]}, exam_platform=True)
                    require(not duplicate_index.contains(item),
                            "题干或材料与本任务其他题目或历史排除题重复。")
                    duplicate_index.add(item)
                except ValueError as exc:
                    raise ValueError(f"{label}的{name}第{index}道：{exc}") from exc
            if kind in targets:
                actual = Counter(item["ans"] for item in items)
                target = Counter(targets[kind])
                differences = [f"{answer}实有{actual[answer]}道、应有{target[answer]}道"
                               for answer in sorted(set(actual) | set(target))
                               if actual[answer] != target[answer]]
                require(not differences, f"{label}的{name}配额不符："
                        + "；".join(differences))
        positive_counts = {kind: n for kind, n in bank["counts"].items() if n}
        if positive_counts == config.STANDARD_QUESTION_COUNTS:
            errors, _ = rules.validate_standard_bank(results)
            require(not errors, f"{label}未通过程序的标准300题验收。")
        summaries[label] = {kind: len(items) for kind, items in results.items()}
    return {"papers": summaries, "history_exclusion_count": len(excluded),
            "total_parent_questions": sum(bank["counts"].values()) * len(papers)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "check"))
    parser.add_argument("bank", type=Path)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--exclude", action="append", default=[], type=Path)
    args = parser.parse_args()
    try:
        bank = read_json(args.bank)
        if args.command == "plan":
            print(json.dumps(make_plan(bank, args.project), ensure_ascii=False, indent=2))
        else:
            result = validate_bank(bank, args.project, args.exclude)
            print(f"结构与配额校验通过。已检查{len(result['papers'])}卷，"
                  f"共{result['total_parent_questions']}道顶层题，"
                  f"历史排除题数为{result['history_exclusion_count']}道。")
    except (ValueError, OSError, ImportError) as exc:
        print(f"校验失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
