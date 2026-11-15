"""Render curated JSON into reviewable Markdown; no model or network requests."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KB = ROOT / "knowledge/youth-conditioning"
OUT = ROOT / "courses/youth-conditioning"


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path, lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    sources = {s["id"]: s for s in read(KB / "sources.json")}
    cards = {c["id"]: c for c in read(KB / "cards.json")}
    course = read(OUT / "curriculum.json")
    lines = ["# 国际证据与本地来源目录", "", "核查日期：2026-11-14。非系统综述；访问层级如实记录。仅保存原创归纳、元数据与链接，不复制外部全文。", ""]
    for s in sources.values():
        lines += [f"## {s['id']} · {s['title']}", "", f"- 年份/类型：{s['year'] or '未注明'} / {s['kind']}",
                  f"- 核查层级：{s['verification']}", f"- 定位：{s['locator']}", f"- 采用范围：{s['scope']}", f"- 限制：{s['limitation']}"]
        if s["url"]:
            lines.append(f"- 来源：[原始页面]({s['url']})")
        if s.get("alternate_url"):
            lines.append(f"- 对照：[官方/出版商页面]({s['alternate_url']})")
        if s.get("doi"):
            lines.append(f"- DOI：{s['doi']}")
        lines.append("")
    write(KB / "来源目录.md", lines)
    lines = ["# 青少年体能知识卡 · V0.1", "", "状态：教研草稿，待专业审核。摘要是来源归纳；教学应用为本项目设计；不输出个体医疗或训练处方。", ""]
    for card in cards.values():
        lines += [f"## {card['id']} · {card['title']}", "", f"**知识归纳**：{card['summary']}", "",
                  f"**教学应用建议**：{card['teaching_application']}", "", f"**边界**：{card['limits']}", "", "**证据回查**：", ""]
        for r in card["references"]:
            s = sources[r["source"]]
            label = f"{s['id']} {s['title']}"
            label = f"[{label}]({s['url']})" if s["url"] else label
            lines.append(f"- {label}；定位：{r['locator']}；核对：{s['verification']}。")
        lines.append("")
    write(KB / "知识卡.md", lines)
    lines = [f"# {course['title']} · 48课时教研初稿", "", "版本：V0.1；待专业审核。", "",
             f"对象：{course['audience']}。训练服务对象为青少年。",
             "", "48课时 × 45分钟＝2160分钟＝36小时；24次课，每次2课时。理论18课时，实践/案例30课时。",
             "", "这是独立核心课程，不自动获得原150/200学时等级完成或资格认定。足球专项、运动康复与高级竞技周期后置。",
             "", "课程时间是成年学习者的培训时间，不是青少年单次训练时长。每次90分钟是有效教学时间，中途休息另排；实践课含讲解、观察和反馈，不是90分钟连续运动。",
             "", "教学顺序、练习、时长分配、作业和评分属于本次教研设计；知识原则通过知识卡回查。不将完整教案称为已验证的国际标准方案。", "", "## 模块安排", "", "|模块|内容|课时|", "|---|---|---|"]
    for m in course["modules"]:
        lines.append(f"|{m['id']}|{m['title']}|{m['hours']}|")
    lines += ["", "## 逐次课教案", ""]
    for lesson in course["lessons"]:
        lines += [f"### {lesson['id']} · {lesson['title']}", "", f"模块{lesson['module']}；2课时：理论{lesson['theory']}、实践/案例{lesson['practice']}。", "", "**学习目标**：", ""]
        lines += [f"- {x}" for x in lesson["objectives"]]
        lines += ["", "**核心讲解**：", ""]
        lines += [f"{i}. {x}" for i, x in enumerate(lesson["content"], 1)]
        timing = "5分钟目标与状态确认 → 20分钟概念讲解 → 20分钟案例判断 → 35分钟任务演练 → 10分钟反馈与评价（45理论+45实践）" if lesson["theory"] else "10分钟准备与安全确认 → 15分钟教师示范 → 40分钟分组演练 → 15分钟同伴/教师复核 → 10分钟复盘（90实践）"
        lines += ["", f"**90分钟教学流程建议**：{timing}。", "", f"**演练任务**：{lesson['practice_task']}", "", f"**提交物**：{lesson['deliverable']}", "", "**教师观察与验收**：", ""]
        lines += [f"- {x}" for x in lesson["assessment"]]
        lines += ["", f"**课后巩固**：{lesson['homework']}（不额外计入48课时）。", "", f"**原任务映射**：{'、'.join(lesson['tasks'])}（映射表示采用相关内容，不代表完成原任务全部能力）。", "", "**依据与回查**：", ""]
        for cid in lesson["cards"]:
            c = cards[cid]
            refs = []
            for r in c["references"]:
                s = sources[r["source"]]
                refs.append(f"[{s['id']}]({s['url']}) · {r['locator']}" if s["url"] else f"{s['id']} · {r['locator']}")
            lines.append(f"- {cid} {c['title']}：{'；'.join(refs)}。")
        lines.append("")
    lines += ["## 评价建议（本次教研设计，待实施方确认）", "", "过程任务30%、监督下试教40%、综合作品与答辩30%。建议总评达到70/100，且关键安全项全部通过；未通过者明确补做项。此为新版试行建议，不继承原基础级/高级的权重与证书条件。", "", "先用模拟数据与成年同伴演练。48课时不包含真实八周实施周期；后续真实项目须单独安排现场监督、参与同意及记录。", "", "理论可在线导学；技能示范、操作与试教需有合格教师及适宜条件。48课时中可选异步理论最多8课时，其余同步/现场；这是本次建议，学时需有任务证据。", "", "## 交付状态", "", "已完成课程结构、知识卡、逐次课教案与表单初稿；未完成专家签审、动作视频、真实班级试教、学习效果验证或正式证书方案。"]
    write(OUT / "青少年体能训练核心课程_48课时.md", lines)
    print("Rendered source directory, knowledge cards and 24-lesson course.")


if __name__ == "__main__":
    main()
