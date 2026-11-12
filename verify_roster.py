"""离线验收：python verify_roster.py；仅在临时目录读写 Excel，绝不连接手机。"""
from contextlib import redirect_stdout
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from openpyxl import Workbook, load_workbook
import roster_plan as rp
from roster_workflow import Candidate, Receipt, match_person, run_task as _run_task
from unittest.mock import patch
from itertools import count

_case_ids = count()
def run_task(*args, **kwargs):
    args = list(args)
    args[3] = Path(args[3]) / f"case-{next(_case_ids)}"
    return _run_task(*args, **kwargs)


def expect_error(fn, text):
    try:
        fn()
    except (ValueError, RuntimeError) as exc:
        assert text in str(exc), str(exc)
    else:
        raise AssertionError("预期拒绝，但未报错")


def workbook(path, rows):
    wb = Workbook()
    for row in rows:
        wb.active.append(row)
    wb.save(path)
    wb.close()
    return path


def read(path):
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        return {ws.title: list(ws.values) for ws in wb}
    finally:
        wb.close()


class FakeBackend:
    def __init__(self, candidates, bad_delta=False, bad_receipt=False, bad_clear=False):
        self.candidates = candidates
        self.selected = []
        self.clicks = []
        self.queries = []
        self.submissions = []
        self.bad_delta = bad_delta
        self.bad_receipt = bad_receipt
        self.bad_clear = bad_clear

    def open(self, **kwargs):
        self.open_args = kwargs

    def begin_group(self, query):
        assert not self.selected
        self.queries.append(query)

    def search(self, query):
        mode = rp.search_kind(query)
        return [c for c in self.candidates if (c.sid == query if mode == "sid" else c.name.startswith(query))]

    def count(self):
        return len(self.selected)

    def select(self, key):
        self.clicks.append(key)
        if not self.bad_delta:
            self.selected.append(key)

    def clear_selection(self):
        if not self.bad_clear:
            self.selected.clear()

    def submit(self, *, students, score):
        self.submissions.append((students, score))
        assert len(students) == len(self.selected)
        count = len(students)
        return Receipt(tuple((p["name"], p["sid"]) for p in students),
                       10, 10 + count, count - int(self.bad_receipt), submitted=True)

    def end_group(self):
        self.selected.clear()


@patch("roster_workflow.wait_count", side_effect=lambda read, **kwargs: read())
def main(_wait):
    with TemporaryDirectory(prefix="roster-verify-") as directory:
        root = Path(directory)
        path = workbook(root / "名单.xlsx", [
            ["姓名", "学号", "分值", "多余列"],
            ["张三", "20250000001", "不是数字", "忽略"],
            ["李四", 20240000001, 99],
            ["张五", "20230000001", 100],
            ["张伟", "20230000002", 80],
            ["张伟", "20240000002", 90],
            ["邓六", "20230000003"],
            ["无效", "abc20230000004"],
            ["", "20230000005"],
            ["李四", 20240000001],
            ["区七", "20230000006"],
            ["张八", "20230000007.0"],
            ["张九", "２０２３００００００８"],
        ])
        original = path.read_bytes()
        plan = rp.prepare_roster(path)
        assert [p.name for p in plan.normal] == ["邓六", "李四", "区七", "张五", "张三"]
        assert [p.sid for p in plan.duplicates] == ["20230000002", "20240000002"]
        assert len(plan.logs) == 5
        assert len(rp.SURNAME_PINYIN) >= 300
        # 传统百家姓前 300 姓全部收录；以及需求中指定的多音姓。
        first300 = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯昝管卢莫经房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮龚程嵇邢滑裴陆荣翁荀羊於惠甄麴家封芮羿储靳汲邴糜松井段富巫乌焦巴弓牧隗山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹束龙叶幸司韶郜黎蓟薄印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴鬱胥能苍双闻莘党翟谭贡劳逄姬申扶堵"
        assert len(first300) == 300
        assert set(first300) <= rp.SURNAME_PINYIN.keys()
        assert [rp.SURNAME_PINYIN[c] for c in "曾单仇解区查"] == ["zeng", "shan", "qiu", "xie", "ou", "zha"]
        assert rp.surname("欧阳明") == "欧阳"
        assert rp.surname_key("龘甲") > rp.surname_key("张甲")
        assert rp.surname_key("龘甲") < rp.surname_key("龟甲")
        output = rp.write_plan(plan, root / "预处理.xlsx")
        sheets = read(output)
        assert list(sheets) == ["按姓氏表", "同名表", "处理日志"]
        assert sheets["按姓氏表"][1] == ("邓六", "20230000003")
        assert len(sheets["处理日志"]) == 6
        assert path.read_bytes() == original
        print("[OK] 拼音与学号排序、前 300 姓覆盖、同名拆分、去重、跳过日志、输入只读")

        conflict = workbook(root / "冲突.xlsx", [["姓名", "学号"], ["张甲", "20230000001"], ["李乙", "20230000001"]])
        expect_error(lambda: rp.prepare_roster(conflict), "同一学号")
        no_names = workbook(root / "缺表头.xlsx", [["学号"], ["20230000001"]])
        expect_error(lambda: rp.prepare_roster(no_names), "表头")
        name_only = workbook(root / "仅姓名.xlsx", [["姓名"], ["张三"], ["李四"]])
        assert len(rp.prepare_roster(name_only, allow_name_only=True).normal) == 2
        expect_error(lambda: rp.prepare_roster(name_only), "表头")
        for value in ("", "abc", "NaN", "Infinity", "-1", "1e3", "0,5", "０.５"):
            expect_error(lambda value=value: rp.parse_score(value), "分值")
        assert rp.parse_score("0.5") == Decimal("0.5")
        assert rp.parse_score("0") == Decimal("0")
        for value in ("zhang", "Z", "张3", "2023"):
            expect_error(lambda value=value: rp.search_kind(value), "搜索")
        print("[OK] 同号异名阻止、补签仅姓名、分值校验、禁止拼音")

        person = rp.Person(2, "张三", "20230000001", "按姓氏表")
        a = Candidate("a", "张三", "20230000001")
        b = Candidate("b", "张三", "20240000001")
        assert match_person(person, [a, b], "张")[1] == "失败"
        assert match_person(person, [a], person.sid)[0] == a
        assert match_person(person, [a, a], person.sid)[1] == "失败"
        assert match_person(person, [Candidate("x", "李四", person.sid)], person.sid)[1] == "失败"
        assert match_person(person, [Candidate("x", person.name, "")], "张")[1] == "失败"
        print("[OK] 精确姓名学号匹配、同名歧义/缺少身份不勾选")

        people = [Candidate(str(i), "张" + c, f"202300000{i:02}") for i, c in enumerate("甲乙丙丁戊己庚辛壬癸子丑", 1)]
        three = workbook(root / "三人.xlsx", [["姓名", "学号", "分值"]] + [[p.name, p.sid, "999"] for p in people[:3]])
        dry = FakeBackend(people)
        with redirect_stdout(StringIO()):
            result = run_task(three, "credit", "演示", root, score="0.5", backend_factory=lambda: dry)
        assert not result.error and len(dry.clicks) == 3 and not dry.submissions
        assert dry.count() == 0 and dry.queries == ["张"]
        assert dry.open_args["score"] == "0.5"
        assert all(row[4] is None for row in read(result.feedback_path)["演练结果"][1:])
        assert len(read(result.feedback_path)["待核对"]) == 1
        print("[OK] 搜张姓 12 人只勾名单 3 人、演练不提交、不伪报成功、表内分值忽略")

        with redirect_stdout(StringIO()):
            for task in ("credit", "admit", "makeup"):
                real = FakeBackend(people)
                result = run_task(three, task, "演示", root, score="0.5", real=True, confirmed=True, backend_factory=lambda: real)
                assert not result.error and result.submitted == 3
                assert result.confirmed == (0 if task == "credit" else 3)
                rows = read(result.feedback_path)["反馈"][1:]
                assert {r[4] for r in rows} == {{"credit": "已提交未核查", "admit": "已录取", "makeup": "已补签"}[task]}
                if task == "credit":
                    assert all(p["score"] == "0.5" for group, score in real.submissions for p in group)
                    assert all(score == "0.5" for group, score in real.submissions)
                if task == "makeup":
                    assert real.queries == [p.sid for p in people[:3]]
        expect_error(lambda: run_task(three, "credit", "演示", root, score="0.5", real=True), "显式确认")
        print("[OK] 三类正式反馈、统一分值传递、补签优先学号、正式确认门槛")

        with redirect_stdout(StringIO()):
            for backend, reason in ((FakeBackend(people, bad_delta=True), "+1"),):
                result = run_task(three, "credit", "演示", root, score="0.5", real=True, confirmed=True, backend_factory=lambda: backend)
                assert reason in result.error
                assert len(read(result.feedback_path)["待核对"]) == 4
                assert result.confirmed == 0
                if reason == "+1":
                    assert len(backend.clicks) == 1 and not backend.submissions
            result = run_task(three, "credit", "演示", root, score="0.5", backend_factory=lambda: FakeBackend(people, bad_clear=True))
            assert "清除" in result.error
            result = run_task(three, "credit", "演示", root, score="0.5", backend_factory=lambda: FakeBackend([]))
            sheets = read(result.feedback_path)
            assert len(sheets["待核对"]) == 4
            assert {r[4] for r in sheets["演练结果"][1:]} == {"未找到"}
            result = run_task(three, "credit", "演示", root, score="0.5")
            assert "未合并" in result.error
            assert all(r[4] == "未处理" for r in read(result.feedback_path)["演练结果"][1:])
        print("[OK] +1 失败立即停止、演练清理失败、3 人未找到、接口缺失反馈")

        duplicates = workbook(root / "同名.xlsx", [["姓名", "学号"], ["张伟", "20230000001"], ["张伟", "20240000001"]])
        backend = FakeBackend([Candidate("a", "张伟", "20230000001"), Candidate("b", "张伟", "20240000001")])
        with redirect_stdout(StringIO()):
            result = run_task(duplicates, "credit", "演示", root, score="0.5", backend_factory=lambda: backend)
        assert not result.error and backend.queries == ["20230000001", "20240000001"]
        full_backend = FakeBackend([Candidate(str(i), p.name, p.sid) for i, p in enumerate(plan.people)])
        with redirect_stdout(StringIO()):
            result = run_task(path, "credit", "演示", root, score="0.5", backend_factory=lambda: full_backend)
        assert not result.error and len(full_backend.clicks) == 7
        assert full_backend.queries == ["邓", "李", "区", "张", "20230000002", "20240000002"]
        empty = workbook(root / "空名单.xlsx", [["姓名", "学号"], ["", "错误"]])
        with redirect_stdout(StringIO()):
            result = run_task(empty, "credit", "演示", root, score="0.5",
                              backend_factory=lambda: (_ for _ in ()).throw(AssertionError("空名单不能连接")))
        assert "没有有效人员" in result.error
        assert len(read(result.plan_path)["处理日志"]) == 2
        mixed = workbook(root / "含未找到.xlsx", [["姓名", "学号"]] +
                         [[p.name, p.sid] for p in people[:3]] +
                         [["张缺"+c, f"202400000{i:02}"] for i, c in enumerate("甲乙丙", 1)])
        with redirect_stdout(StringIO()):
            result = run_task(mixed, "admit", "演示", root, real=True, confirmed=True,
                              backend_factory=lambda: FakeBackend(people))
        sheets = read(result.feedback_path)
        assert result.confirmed == 3 and len(sheets["反馈"]) == 7
        assert len(sheets["待核对"]) == 4
        assert all(row[4] == "未找到" for row in sheets["待核对"][1:])
        names20 = ["张"+c for c in "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉"]
        names_path = workbook(root / "20人补签.xlsx", [["姓名"]]+[[name] for name in names20])
        device20 = FakeBackend([Candidate(str(i), name, "") for i, name in enumerate(names20)] +
                               [Candidate("outside", "张外", "")])
        with redirect_stdout(StringIO()):
            result = run_task(names_path, "makeup", "演示", root, real=True, confirmed=True,
                              backend_factory=lambda: device20)
        assert result.confirmed == 20 and len(device20.clicks) == 20 and "outside" not in device20.clicks
        assert all(row[4] == "已补签" for row in read(result.feedback_path)["反馈"][1:])
        print("[OK] 混入 3 个未找到人员时待核对恰好 3 行、仅姓名 20 人补签、名单外人员不动")
        note = rp.feedback_row(person, "失败", "=1+1")
        feedback = rp.write_feedback([note], root / "公式文本.xlsx")
        wb = load_workbook(feedback)
        assert wb.active["F2"].data_type == "s"
        wb.close()
        print("[OK] 同名表自动转学号路径、反馈文字不会成为公式")
    print("全部通过（离线逻辑验收，不代表真机提交已验收）。")


if __name__ == "__main__":
    main()
