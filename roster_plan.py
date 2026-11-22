"""本地名单规划与反馈；不连接手机，不读取名单中的分值。"""
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import os
from uuid import uuid4

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill


# 百家姓常用单姓（含前 300 姓）；采用姓氏读音，不按姓名中汉字的普通读音。
_SURNAME_ROWS = """
赵钱孙李 zhao qian sun li
周吴郑王 zhou wu zheng wang
冯陈褚卫 feng chen chu wei
蒋沈韩杨 jiang shen han yang
朱秦尤许 zhu qin you xu
何吕施张 he lv shi zhang
孔曹严华 kong cao yan hua
金魏陶姜 jin wei tao jiang
戚谢邹喻 qi xie zou yu
柏水窦章 bai shui dou zhang
云苏潘葛 yun su pan ge
奚范彭郎 xi fan peng lang
鲁韦昌马 lu wei chang ma
苗凤花方 miao feng hua fang
俞任袁柳 yu ren yuan liu
酆鲍史唐 feng bao shi tang
费廉岑薛 fei lian cen xue
雷贺倪汤 lei he ni tang
滕殷罗毕 teng yin luo bi
郝邬安常 hao wu an chang
乐于时傅 yue yu shi fu
皮卞齐康 pi bian qi kang
伍余元卜 wu yu yuan bu
顾孟平黄 gu meng ping huang
和穆萧尹 he mu xiao yin
姚邵湛汪 yao shao zhan wang
祁毛禹狄 qi mao yu di
米贝明臧 mi bei ming zang
计伏成戴 ji fu cheng dai
谈宋茅庞 tan song mao pang
熊纪舒屈 xiong ji shu qu
项祝董梁 xiang zhu dong liang
杜阮蓝闵 du ruan lan min
席季麻强 xi ji ma qiang
贾路娄危 jia lu lou wei
江童颜郭 jiang tong yan guo
梅盛林刁 mei sheng lin diao
钟徐邱骆 zhong xu qiu luo
高夏蔡田 gao xia cai tian
樊胡凌霍 fan hu ling huo
虞万支柯 yu wan zhi ke
昝管卢莫 zan guan lu mo
经房裘缪 jing fang qiu miao
干解应宗 gan xie ying zong
丁宣贲邓 ding xuan ben deng
郁单杭洪 yu shan hang hong
包诸左石 bao zhu zuo shi
崔吉钮龚 cui ji niu gong
程嵇邢滑 cheng ji xing hua
裴陆荣翁 pei lu rong weng
荀羊於惠 xun yang yu hui
甄麴家封 zhen qu jia feng
芮羿储靳 rui yi chu jin
汲邴糜松 ji bing mi song
井段富巫 jing duan fu wu
乌焦巴弓 wu jiao ba gong
牧隗山谷 mu kui shan gu
车侯宓蓬 che hou mi peng
全郗班仰 quan xi ban yang
秋仲伊宫 qiu zhong yi gong
宁仇栾暴 ning qiu luan bao
甘钭厉戎 gan tou li rong
祖武符刘 zu wu fu liu
景詹束龙 jing zhan shu long
叶幸司韶 ye xing si shao
郜黎蓟薄 gao li ji bo
印宿白怀 yin su bai huai
蒲邰从鄂 pu tai cong e
索咸籍赖 suo xian ji lai
卓蔺屠蒙 zhuo lin tu meng
池乔阴鬱 chi qiao yin yu
胥能苍双 xu neng cang shuang
闻莘党翟 wen shen dang zhai
谭贡劳逄 tan gong lao pang
姬申扶堵 ji shen fu du
冉宰郦雍 ran zai li yong
郤璩桑桂 xi qu sang gui
濮牛寿通 pu niu shou tong
边扈燕冀 bian hu yan ji
郏浦尚农 jia pu shang nong
温别庄晏 wen bie zhuang yan
柴瞿阎充 chai qu yan chong
慕连茹习 mu lian ru xi
宦艾鱼容 huan ai yu rong
向古易慎 xiang gu yi shen
戈廖庾终 ge liao yu zhong
暨居衡步 ji ju heng bu
都耿满弘 du geng man hong
匡国文寇 kuang guo wen kou
广禄阙东 guang lu que dong
欧殳沃利 ou shu wo li
蔚越夔隆 yu yue kui long
师巩厍聂 shi gong she nie
晁勾敖融 chao gou ao rong
冷訾辛阚 leng zi xin kan
那简饶空 na jian rao kong
曾毋沙乜 zeng wu sha nie
养鞠须丰 yang ju xu feng
巢关蒯相 chao guan kuai xiang
查后荆红 zha hou jing hong
游竺权逯 you zhu quan lu
盖益桓公 ge yi huan gong
区朴尉甯 ou piao wei ning
"""
SURNAME_PINYIN = {}
for _line in _SURNAME_ROWS.strip().splitlines():
    _chars, *_sounds = _line.split()
    if len(_chars) != len(_sounds):
        raise ValueError("姓氏映射数据不完整")
    SURNAME_PINYIN.update(zip(_chars, _sounds))
COMPOUND = dict(item.split(":") for item in (
    "欧阳:ouyang 司马:sima 上官:shangguan 夏侯:xiahou 诸葛:zhuge "
    "闻人:wenren 东方:dongfang 皇甫:huangfu 尉迟:yuchi 公羊:gongyang "
    "澹台:tantai 公冶:gongye 宗政:zongzheng 濮阳:puyang 淳于:chunyu "
    "单于:chanyu 太叔:taishu 申屠:shentu 公孙:gongsun 仲孙:zhongsun "
    "轩辕:xuanyuan 令狐:linghu 钟离:zhongli 宇文:yuwen 长孙:zhangsun "
    "慕容:murong 鲜于:xianyu 司徒:situ 司空:sikong 端木:duanmu"
).split())


def surname(name):
    return name[:2] if name[:2] in COMPOUND else name[:1]


def surname_key(name):
    family = surname(name)
    sound = COMPOUND.get(family, SURNAME_PINYIN.get(family))
    return (0, sound, family) if sound else (1, family, family)


def parse_score(value):
    text = str(value).strip()
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", text):
        raise ValueError("分值不能为空，且必须为非负数字，例如 0.5。")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("分值格式不正确。") from exc
    if not result.is_finite():
        raise ValueError("分值必须是有限数字。")
    return result


def search_kind(query):
    if re.fullmatch(r"[0-9]{11}", query):
        return "sid"
    if re.fullmatch(r"[\u3400-\u4dbf\u4e00-\u9fff]+", query):
        return "surname"
    raise ValueError("搜索只支持 11 位学号或汉字，禁止拼音。")


@dataclass(frozen=True)
class Person:
    row: int
    name: str
    sid: str
    group: str

    @property
    def identity(self):
        return self.name, self.sid


@dataclass
class Plan:
    normal: list
    duplicates: list
    logs: list

    @property
    def people(self):
        return self.normal + self.duplicates

    def batches(self):
        groups = defaultdict(list)
        for person in self.normal:
            groups[surname(person.name)].append(person)
        for family, people in groups.items():
            search_kind(family)
            yield family, people
        for person in self.duplicates:
            search_kind(person.sid)
            yield person.sid, [person]


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


ROSTER_DUPLICATE = "名单内重名，已全部跳过"


def prepare_roster(path, allow_name_only=False, *, skip_duplicate_names=False):
    wb = load_workbook(path, read_only=True, data_only=True)
    logs, people, sid_names, seen = [], [], {}, set()
    try:
        rows = wb.worksheets[0].iter_rows(values_only=True)
        header = [_cell(v) for v in next(rows, ())]
        if "姓名" not in header or ("学号" not in header and not allow_name_only):
            raise ValueError("名单表头须含「姓名」。" if allow_name_only else "名单表头须含「姓名」「学号」。")
        for required in ("姓名", "学号"):
            if header.count(required) > 1:
                raise ValueError(f"表头重复：{required}")
        name_col = header.index("姓名")
        sid_col = header.index("学号") if "学号" in header else None
        # 先按原始姓名计数，空学号、相同学号或无效学号都不能掩盖另一行同名者。
        if skip_duplicate_names:
            rows = list(rows)
            name_counts = Counter(_cell(row[name_col]) for row in rows if name_col < len(row))
        for number, row in enumerate(rows, 2):
            name = _cell(row[name_col]) if name_col < len(row) else ""
            sid = _cell(row[sid_col]) if sid_col is not None and sid_col < len(row) else ""
            reason = ""
            if not name:
                reason = "姓名为空"
            elif skip_duplicate_names and name_counts[name] > 1:
                reason = ROSTER_DUPLICATE
            elif not sid and allow_name_only:
                pass
            elif not re.fullmatch(r"[0-9]{11}", sid):
                reason = "学号必须是 11 位数字"
            if not reason:
                try:
                    search_kind(surname(name))
                except ValueError:
                    reason = "姓名须以汉字姓氏开头，不使用拼音搜索"
            if reason:
                logs.append((number, name, sid, reason))
                continue
            if sid and sid in sid_names and sid_names[sid] != name:
                raise ValueError(f"第 {number} 行：同一学号 {sid} 对应不同姓名，已停止。")
            if sid:
                sid_names[sid] = name
            identity = (name, sid)
            if identity in seen:
                logs.append((number, name, sid, "重复记录，已去重"))
                continue
            seen.add(identity)
            people.append((number, name, sid))
    finally:
        wb.close()
    names = Counter(name for _, name, _ in people)
    normal, duplicates = [], []
    for number, name, sid in people:
        if names[name] > 1 and not sid:
            logs.append((number, name, sid, "同名且缺少学号，无法区分，已跳过"))
            continue
        group = "同名表" if names[name] > 1 else "按姓氏表"
        person = Person(number, name, sid, group)
        (duplicates if group == "同名表" else normal).append(person)
    key = lambda person: (surname_key(person.name), person.sid, person.name, person.row)
    return Plan(sorted(normal, key=key), sorted(duplicates, key=key), logs)


def _append(ws, values):
    # 外部文本只作文字写入，避免以 = 开头的姓名/备注成为 Excel 公式。
    ws.append(list(values))
    for cell in ws[ws.max_row]:
        if isinstance(cell.value, str):
            cell.data_type = "s"


def _save(wb, output):
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    for ws in wb:
        ws.freeze_panes = "A2"
        if not ws.auto_filter.ref:
            ws.auto_filter.ref = ws.dimensions
        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="DDE6F5")
        for column in ws.columns:
            ws.column_dimensions[column[0].column_letter].width = min(55, max(14, max(len(str(c.value or "")) for c in column) + 2))
    temporary = output.with_name(output.stem + ".tmp-" + uuid4().hex + ".xlsx")
    try:
        wb.save(temporary)
        os.replace(temporary, output)
    finally:
        wb.close()
        temporary.unlink(missing_ok=True)
    return output


def output_path(out_dir, prefix):
    return Path(out_dir).resolve() / f"{prefix}-{datetime.now():%Y%m%d-%H%M%S-%f}.xlsx"


def write_plan(plan, output):
    wb = Workbook()
    wb.remove(wb.active)
    for title, people in (("按姓氏表", plan.normal), ("同名表", plan.duplicates)):
        ws = wb.create_sheet(title)
        _append(ws, ["姓名", "学号"])
        for person in people:
            _append(ws, [person.name, person.sid])
    ws = wb.create_sheet("处理日志")
    _append(ws, ["原始行号", "姓名", "学号", "原因"])
    for row in plan.logs:
        _append(ws, row)
    return _save(wb, output)


RESULTS = {"重名跳过", "已勾选未提交", "已录取", "已补签", "已发分", "未找到", "失败", "未处理", "已提交未核查", "提交结果待核对"}
FEEDBACK_HEADER = ["序号", "姓名", "学号", "分组", "结果", "说明", "时间"]


def feedback_row(person, result, note, *, dry=False):
    if result not in RESULTS and not (dry and result == ""):
        raise ValueError("未知反馈结果；演练不能写成已提交成功。")
    return [person.row, person.name, person.sid, person.group, result, note,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")]


def write_feedback(rows, output, *, dry=False):
    wb = Workbook()
    ws = wb.active
    ws.title = "演练结果" if dry else "反馈"
    pending = wb.create_sheet("待核对")
    for sheet in (ws, pending):
        _append(sheet, FEEDBACK_HEADER)
    for number, row in enumerate(rows, 1):
        if len(row) != 7 or (row[4] not in RESULTS and not (dry and row[4] == "")):
            raise ValueError("反馈记录格式不正确。")
        values = [number, *row[1:]]
        _append(ws, values)
        if row[4] in {"重名跳过", "未找到", "失败", "提交结果待核对", "已勾选未提交"}:
            _append(pending, values)
    return _save(wb, output)
