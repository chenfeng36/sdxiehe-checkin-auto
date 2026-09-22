# =====================================================================================
# 智能查寝 自动签到脚本（山东协和学院 xg.sdxiehe.edu.cn）
#
# 功能：用账号密码自动登录统一身份认证平台（cookie 过期时重新登录，可自动过滑块验证码），
#       进入“智能查寝”页面后自动点击“签到”/“晚归签到”按钮完成签到，并记录结果。
#
# 运行方式：
#   .venv\Scripts\python.exe autoCheckIn.py     #源码方式运行
#   dist\自动签到.exe                            #打包后双击运行
#   python autoCheckIn.py --dry-run             #只检测按钮不点击，用于安全试运行
#   python autoCheckIn.py --headless            #无头模式（不显示浏览器窗口）
#   （环境变量 CHECKIN_DRY_RUN=1 同样可以开启试运行模式）
#
# 退出码：0=成功/已签到；1=失败/异常；2=配置缺失（已生成模板）；3=未到签到时间
#
# 生成/使用的文件（都在脚本或 exe 所在目录）：
#   config.json        账号、密码、经纬度、monkey_verify、checkin_url 等配置；首次运行自动生成模板
#   login.json         浏览器登录状态(cookie)；登录成功后自动保存，下次运行免登录
#   login_account.txt  记录 login.json 属于哪个账号，防止换账号后误用旧 cookie
#   log.log            每次运行结果的日志（每行：时间 | 账号 | 结果 | 用时）
#   日期_<签到结果>.png  每次运行结束时的页面截图（如“2026-09-22_签到成功.png”）
#
# 代码结构（从上到下）：
#   1. 配置与常量（Config 类 / 默认值 / 文件路径）
#   2. 工具函数：控制台最小化 / 日志 / config.json 读写
#   3. CaptchaSolver 类：滑块验证码（本地图像算法，纯 numpy，不依赖 OpenCV）
#   4. 外部接口：verify() / get_checkin_labels() / click_checkin_button()
#   5. 主流程：prepare_config / ensure_login / do_checkin / finalize / auto_checkin
# =====================================================================================
from dataclasses import dataclass
from playwright.sync_api import sync_playwright, Geolocation, Page
from win10toast import ToastNotifier
import argparse
import os
import sys
import time
import traceback
import json
import random
import win32api
import win32event
import winerror
import win32gui
import win32con
import base64
import numpy as np


#打包成 exe 后：配置/日志/截图都放在 exe 所在目录，方便整个文件夹拷到别的电脑
if getattr(sys, "frozen", False):
    base_dir=os.path.dirname(os.path.abspath(sys.executable))
    this_file_name=os.path.basename(sys.executable)
else:
    base_dir=os.path.dirname(os.path.abspath(__file__))
    this_file_name=os.path.basename(__file__)

#以下三项是 config.json 里可配置项的默认值（旧配置没写这些键时用默认值）
DEFAULT_CHECKIN_URL="https://xg.sdxiehe.edu.cn/xsfw/sys/swmzncqapp/*default/index.do?/xscq/kqqdx#/xscq/kqqdx"   #智能查寝页面
DEFAULT_MAX_RETRY_TIMES=40      #“不在签到范围内”时最多重试次数（每次等 10 秒，40 次 ≈ 6.7 分钟）
DEFAULT_LOGIN_WAIT_TIMEOUT=300  #等待登录完成的最长时间（秒），超时视为登录失败

LOGIN_PAGE_TITLE="统一身份认证平台"   #统一身份认证页标题（cookie 失效时会跳到这里）
#登录失败时页面上可能出现的提示文字，检测到就提前结束等待（只用于自动登录场景）
LOGIN_ERROR_HINTS=["用户名或密码错误","密码错误","账号或密码错误","账号不存在","认证失败"]

file_encoding='utf-8'   #所有读写文件统一用 utf-8

#定位（经纬度）：默认值为学校坐标，可在 config.json 里修改；
#签到时会作为浏览器的“虚拟定位”上报，坐标不对页面会显示“不在签到范围内”
DEFAULT_LONGITUDE=117.261944   #默认经度（学校坐标）
DEFAULT_LATITUDE=36.739722     #默认纬度（学校坐标）

#config.json 里“未填写账号/密码”的占位值（历史拼写 unknow，为兼容旧配置保留；读取时也接受 unknown）
UNKNOWN="unknow"


@dataclass
class Config:
    """config.json 对应的运行时配置

    username/password 存明文（文件里的 base64 编解码由 load_config/save_config 负责），
    未填写时是 UNKNOWN 占位值；monkey_verify=True 表示脚本自动过滑块验证码。
    """
    username: str = UNKNOWN
    password: str = UNKNOWN
    longitude: float = DEFAULT_LONGITUDE
    latitude: float = DEFAULT_LATITUDE
    monkey_verify: bool = False
    checkin_url: str = DEFAULT_CHECKIN_URL
    max_retry_times: int = DEFAULT_MAX_RETRY_TIMES
    login_wait_timeout: int = DEFAULT_LOGIN_WAIT_TIMEOUT

    @property
    def has_credentials(self) -> bool:
        """config.json 里是否填了账号和密码（没填则走手动登录流程）"""
        return self.username != UNKNOWN and self.password != UNKNOWN


#设置环境变量 CHECKIN_DRY_RUN=1（或命令行 --dry-run）后，只检测签到按钮不点击，用于安全试运行
dry_run = os.environ.get("CHECKIN_DRY_RUN")=="1"
headless = False    #是否无头模式运行（命令行 --headless 参数开启）

state_file_name=os.path.join(base_dir,"login.json")            #浏览器登录状态(cookie)
config_file_name=os.path.join(base_dir,"config.json")          #账号密码等配置
log_file_name=os.path.join(base_dir,"log.log")                 #运行日志
#记录 login.json 这个登录状态属于哪个账号，用于避免换账号后仍用旧cookie签到
login_account_file_name=os.path.join(base_dir,"login_account.txt")

def minimize_self_window():
    """最小化当前控制台窗口，找不到窗口就忽略，不影响签到流程"""
    hwnd=0
    try:
        import win32console
        hwnd=win32console.GetConsoleWindow()#双击 exe 运行时，当前控制台窗口
    except Exception:
        hwnd=0
    if not hwnd:
        try:
            hwnd=win32gui.FindWindow(None, this_file_name)
        except Exception:
            hwnd=0
    if hwnd:
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
        except Exception:
            pass

def write_log(content):
    """把一行结果追加写入 log.log（行首加“年-月-日 时:分:秒”），失败时返回 False"""
    try:
        stamp=time.strftime("%Y-%m-%d %H:%M:%S")
        with open(log_file_name,"a",encoding=file_encoding) as file:
            file.write(stamp+" | "+content+"\n")
        return True
    except Exception:
        print("日志写入失败")
        return False

def load_login_account():
    """读取 login.json 是哪个账号留下的"""
    try:
        with open(login_account_file_name,"r",encoding=file_encoding) as file:
            return file.read().strip()
    except Exception:
        return ""

def save_login_account(account):
    """记录本次登录状态属于哪个账号"""
    try:
        with open(login_account_file_name,"w",encoding=file_encoding) as file:
            file.write(str(account))
    except Exception:
        pass

def _decode_secret(value, label):
    """把 config.json 里的账号/密码还原成明文

    支持两种写法：明文，或 "#" 开头的 base64。
    空值 / 占位值(unknow) / 解码失败 都按“未填写”处理（返回 UNKNOWN）。
    """
    if not isinstance(value,str) or not value.strip():
        return UNKNOWN
    raw=value.strip()
    if raw in (UNKNOWN,"unknown"):
        return UNKNOWN
    if raw.startswith("#"):
        try:
            return base64.b64decode(raw[1:]).decode("utf-8")
        except Exception as e:
            print("警告：config.json 里的%s(base64)无法解码，按未填写处理（%s）"%(label,e))
            return UNKNOWN
    return raw

def _encode_secret(value):
    """账号/密码写回文件时统一编码成 "#"+base64（未填写则原样写占位值）"""
    if value==UNKNOWN:
        return UNKNOWN
    return "#"+base64.b64encode(value.encode("utf-8")).decode("utf-8")

def _to_float(value, default):
    """安全转 float：空值/None/非法文本都返回默认值"""
    try:
        return float(value)
    except (TypeError,ValueError):
        return default

def _to_bool(value):
    """兼容 JSON 布尔和 "true"/"false" 字符串两种写法"""
    if isinstance(value,bool):
        return value
    return str(value).strip().lower() in ("true","1","yes","on")

def _to_int(value, default):
    """安全转 int：空值/None/非法文本都返回默认值"""
    try:
        return int(value)
    except (TypeError,ValueError):
        return default

def _to_str(value, default):
    """安全取字符串：空值/None/非字符串返回默认值"""
    if isinstance(value,str) and value.strip():
        return value.strip()
    return default

def load_config():
    """读取 config.json

    返回值：Config 对象；文件不存在或内容损坏时返回 None（主流程会走“生成模板”分支）。
    损坏的文件不会删除，而是备份为 config.json.broken，方便手动恢复。
    """
    if not os.path.exists(config_file_name):
        return None
    try:
        with open(config_file_name,"r",encoding=file_encoding) as file:
            data=json.load(file)
        if not isinstance(data,dict):
            raise ValueError("config.json 根节点不是 JSON 对象")
    except Exception as e:
        broken_file=config_file_name+".broken"
        print("config.json 读取失败：%s"%e)
        try:
            if os.path.exists(broken_file):
                os.remove(broken_file)#只保留最近一次的损坏文件
            os.replace(config_file_name,broken_file)
            print("原文件已备份为："+broken_file)
        except Exception as e2:
            print("备份损坏的 config.json 失败：%s"%e2)
        return None

    print(config_file_name+"文件存在")

    location=data.get("location")
    if not isinstance(location,dict):
        location={}
    return Config(
        username=_decode_secret(data.get("username"),"账号"),
        password=_decode_secret(data.get("password"),"密码"),
        longitude=_to_float(location.get("longitude"),DEFAULT_LONGITUDE),
        latitude=_to_float(location.get("latitude"),DEFAULT_LATITUDE),
        monkey_verify=_to_bool(data.get("monkey_verify",False)),
        checkin_url=_to_str(data.get("checkin_url"),DEFAULT_CHECKIN_URL),
        max_retry_times=_to_int(data.get("max_retry_times"),DEFAULT_MAX_RETRY_TIMES),
        login_wait_timeout=_to_int(data.get("login_wait_timeout"),DEFAULT_LOGIN_WAIT_TIMEOUT),
    )

def save_config(config):
    """把配置写回 config.json

    写回的账号/密码是 "#"+base64 形式（见 _encode_secret）；
    expired 字段写当天日期（历史字段名，实际含义是“上次运行时间”）。
    """
    now=time.localtime()
    data={
        "username": _encode_secret(config.username),
        "password": _encode_secret(config.password),
        "location": {
            "longitude": str(config.longitude),
            "latitude": str(config.latitude),
        },
        "monkey_verify": config.monkey_verify,
        "checkin_url": config.checkin_url,
        "max_retry_times": config.max_retry_times,
        "login_wait_timeout": config.login_wait_timeout,
        "expired": {"year": now.tm_year, "month": now.tm_mon, "day": now.tm_mday},
    }
    try:
        with open(config_file_name,"w",encoding=file_encoding) as file:
            json.dump(data,file,ensure_ascii=False,indent=4)
        return True
    except Exception:
        print("发生错误,config文件写入失败")
        return False

# ==================== 滑块验证码处理（本地图像算法，纯 numpy） ====================


class CaptchaSolver:
    """验证码处理器：本地图像算法定位缺口，一次拖到位

    实测确认的规律：
      - 底图 canvas 上的缺口 = 原图内容被半透明黑色遮罩盖住（内容还在，只是整体变暗）
      - 拼图块 canvas.block 上的块 = 缺口处的原图内容（其余是透明区域）
      - 于是用“零均值归一化相关(NCC)”在底图上搜索与拼图块内容最相似的位置：
        NCC 对整体明暗变化不敏感，正好免疫遮罩变暗，能精确到 1 像素
    只用 numpy 实现，不依赖 OpenCV/Pillow。
    """

    # 在页面里执行的 JS：一次拿回两个 canvas 的几何信息 + 全部像素数据。
    # 像素用 getImageData 取出后转 base64 传回 Python（大数组跨进程传会慢）。
    JS_GET_CANVAS = """
    () => {
        const canvases = Array.from(document.querySelectorAll('canvas'));
        const isBlock = (c) => typeof c.className === 'string' && c.className.indexOf('block') >= 0;
        const bg = canvases.find((c) => !isBlock(c)) || null;
        const block = canvases.find(isBlock) || null;
        const geom = (c) => {
            if (!c) return null;
            const r = c.getBoundingClientRect();
            return {x: r.x, y: r.y, width: r.width, height: r.height,
                    pixelWidth: c.width, pixelHeight: c.height};
        };
        const pixels = (c) => {
            if (!c) return '';
            const data = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
            const bytes = new Uint8Array(data.buffer);
            let binary = '';
            const chunk = 0x8000;
            for (let i = 0; i < bytes.length; i += chunk) {
                binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
            }
            return btoa(binary);
        };
        return {bg: geom(bg), block: geom(block), bgPixels: pixels(bg), blockPixels: pixels(block)};
    }
    """

    # 拖动后复查用：读拼图块 canvas 当前在页面里的 CSS x 坐标
    JS_BLOCK_X = """
    () => {
        const c = document.querySelector('canvas.block');
        return c ? c.getBoundingClientRect().x : 0;
    }
    """

    def __init__(self, page: Page):
        self.page = page

    def read_canvases(self):
        """读取两个 canvas 的几何信息与像素数据

        返回 dict（bg/block 几何 + bg_rgba/block_rgba 两个 numpy 数组），
        页面上找不到画布时返回 None。"""
        info = self.page.evaluate(self.JS_GET_CANVAS)
        if not info or not info.get("bg") or not info.get("block"):
            return None     #验证码还没渲染出来（或已经消失了）
        bg, block = info["bg"], info["block"]
        #base64 → bytes → numpy：按 canvas 内部尺寸 reshape 成 (高, 宽, 4通道RGBA)
        bg_rgba = np.frombuffer(base64.b64decode(info["bgPixels"]), dtype=np.uint8)
        bg_rgba = bg_rgba.reshape((bg["pixelHeight"], bg["pixelWidth"], 4))
        block_rgba = np.frombuffer(base64.b64decode(info["blockPixels"]), dtype=np.uint8)
        block_rgba = block_rgba.reshape((block["pixelHeight"], block["pixelWidth"], 4))
        return {"bg": bg, "block": block, "bg_rgba": bg_rgba, "block_rgba": block_rgba}

    @staticmethod
    def to_gray(rgba):
        """转灰度"""
        return (0.299 * rgba[:, :, 0] + 0.587 * rgba[:, :, 1] + 0.114 * rgba[:, :, 2]).astype(np.float32)

    @staticmethod
    def piece_box(block_rgba):
        """拼图块在它自己画布内的外接矩形 + 不透明掩膜

        拼图块画布大部分是透明区域(alpha≈0)，alpha>10 的像素就是拼图块本身。"""
        mask = block_rgba[:, :, 3] > 10
        ys, xs = np.nonzero(mask)
        if len(xs) == 0:
            return None, mask    #整块画布都是透明的（拼图块还没画出来）
        return (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())), mask

    @staticmethod
    def find_gap(bg_gray, piece_gray, piece_mask, box, y_tolerance=4):
        """在底图上穷举搜索缺口位置，用零均值 NCC(归一化互相关) 打分

        原理：缺口 = 底图上被半透明遮罩压暗的同一块内容，内容本身没变；
        NCC 对整体明暗变化免疫，所以拼图块内容与缺口处内容的相关性最高。
        y_tolerance：纵向只在拼图块初始 y 附近 ±4 像素内搜索（缺口高度是固定的）。

        Returns:
            (缺口x, 缺口y, 匹配得分)：画布内部像素坐标；没找到有效匹配时 x/y 为 None、得分 0
        """
        x0, y0, x1, y1 = box
        template = piece_gray[y0:y1 + 1, x0:x1 + 1]
        mask = piece_mask[y0:y1 + 1, x0:x1 + 1]
        values = template[mask]             #拼图块的不透明像素灰度值
        values = values - values.mean()     #减均值：对整体明暗变化免疫（遮罩压暗不影响匹配）
        norm = float(np.sqrt((values * values).sum()))
        if norm == 0:
            return None, None, 0.0

        height, width = template.shape
        bg_height, bg_width = bg_gray.shape
        best_score, best_x, best_y = -2.0, None, None
        y_start = max(0, y0 - y_tolerance)
        y_end = min(bg_height - height, y0 + y_tolerance)
        #在底图上逐行逐列滑动窗口，取相关性最高的位置
        for y in range(y_start, y_end + 1):
            row = bg_gray[y:y + height]
            for x in range(0, bg_width - width + 1):
                patch = row[:, x:x + width][mask]
                patch = patch - patch.mean()
                denom = float(np.sqrt((patch * patch).sum())) * norm
                if denom <= 0:
                    continue
                score = float((patch * values).sum() / denom)
                if score > best_score:
                    best_score, best_x, best_y = score, x, y
        return best_x, best_y, best_score
    
    def generate_track(self, distance):
        """生成拟人滑动轨迹（缓入缓出+轻微抖动），总和精确等于 distance"""
        if distance <= 0:
            return [float(distance)]
        steps = int(min(40, max(12, abs(distance) / 6.0)))
        points = []
        for index in range(1, steps + 1):
            t = index / float(steps)
            points.append(distance * (3 * t * t - 2 * t * t * t))   # 平滑S曲线：先快后慢
        for index in range(len(points) - 1):
            points[index] += random.uniform(-0.8, 0.8)               # 中间点抖动
        points[-1] = distance                                        # 落点精确
        track = []
        previous = 0.0
        for value in points:
            step = value - previous
            if step <= 0.05:
                step = 0.05
            track.append(step)
            previous += step
        return track

    def slide_by(self, cursor, y, distance):
        """按拟人轨迹把鼠标从 cursor 处移动 distance 像素，返回移动后的 x 坐标

        每步之间 sleep 十几毫秒，模拟真人拖动速度（拖太快会被判定为机器）。"""
        for step in self.generate_track(distance):
            cursor += step
            self.page.mouse.move(cursor, y + random.uniform(-0.5, 0.5))
            time.sleep(random.uniform(0.012, 0.022))
        return cursor

    def drag_to_gap(self, images, gap_x):
        """把拼图块拖到缺口处：几何换算 → 按下拖动 → 实测补偿 → 松手

        Returns:
            bool: 是否真的执行了拖动（拼图块/拖动柄没找到时返回 False）
        """
        bg = images["bg"]
        box, _ = self.piece_box(images["block_rgba"])
        if box is None:
            return False    #整个拼图块画布都是透明的，说明拼图块还没画出来

        scale_bg = bg["width"] / float(bg["pixelWidth"])     # 底图画布内部像素 → CSS 像素
        scale_block = images["block"]["width"] / float(images["block"]["pixelWidth"])
        block_rect_before = float(self.page.evaluate(self.JS_BLOCK_X))
        piece_before = block_rect_before + box[0] * scale_block   #拖动前拼图块的 CSS x 坐标

        #拖动距离 = (缺口位置 − 拼图块起点) 换算成 CSS 像素；≤0 说明算反了，给个最小值兜底
        target = (gap_x - box[0]) * scale_bg
        if target <= 0:
            target = 1.0
        print("  缺口x=%d，拼图块x=%d，换算拖动距离=%.1f CSS像素" % (gap_x, box[0], target))

        # 拖动起点 = 滑块(拖动柄)的中心点；找不到说明滑块还没渲染出来，放弃本次拖动
        slider_box = self.page.locator("div.slider").first.bounding_box()
        if slider_box is None:
            print("  没找到拖动滑块，放弃本次拖动")
            return False
        start_x = slider_box["x"] + slider_box["width"] / 2
        start_y = slider_box["y"] + slider_box["height"] / 2

        self.page.mouse.move(start_x, start_y)
        time.sleep(random.uniform(0.05, 0.12))    #先把鼠标移到滑块上，稍作停顿
        self.page.mouse.down()                    #按下左键
        time.sleep(random.uniform(0.05, 0.15))    #再停顿一下才开始拖（模拟真人）

        cursor = self.slide_by(start_x, start_y, target)
        time.sleep(random.uniform(0.15, 0.3))

        # 实测补偿：重新读一次拼图块位置，把误差补上（拖动轨迹是估算的，只补一次）
        try:
            images2 = self.read_canvases()
            box2 = None
            if images2 is not None:                 #画布可能刚好被刷新掉了，读不到就跳过补偿
                box2, _ = self.piece_box(images2["block_rgba"])
            block_rect_after = float(self.page.evaluate(self.JS_BLOCK_X))
            if box2 is not None:
                piece_after = block_rect_after + box2[0] * scale_block
                residual = target - (piece_after - piece_before)
                if abs(residual) > 1.0:
                    print("  实测偏差 %.1f 像素，进行补偿" % residual)
                    self.slide_by(cursor, start_y, residual)
                    time.sleep(random.uniform(0.1, 0.2))
        except Exception as e:
            print("  补偿测量失败(忽略):", e)

        self.page.mouse.up()
        return True
    
    def slider_visible(self):
        """滑块验证还在不在"""
        try:
            return self.page.locator("div.slider").first.is_visible()
        except Exception:
            return False

    def verify_success(self):
        """判断验证是否通过"""
        try:
            if self.page.title() != "统一身份认证平台":
                return True                     # 已经跳转，说明验证通过
            return not self.slider_visible()    # 滑块消失也算通过
        except Exception:
            return False

    def solve(self, max_attempts: int = 10):
        """执行验证：定位缺口 → 拖到位 → 检查结果，失败就刷新验证码重试

        单次尝试：滑块还在吗 → 读画布 → 找拼图块 → NCC 找缺口
                  → 拖到位 → 等 1.5 秒判断是否通过；不通过就刷新换一张图
        """
        for attempt in range(1, max_attempts + 1):
            try:
                if not self.slider_visible():
                    print("  滑块已消失，视为验证通过")
                    return True

                images = self.read_canvases()
                if images is None:
                    print("  没读到验证码画布")
                    time.sleep(1)
                    continue

                block_rgba = images["block_rgba"]
                box, mask = self.piece_box(block_rgba)
                if box is None:
                    print("  没找到拼图块")
                    time.sleep(1)
                    continue

                gap_x, gap_y, score = self.find_gap(self.to_gray(images["bg_rgba"]),
                                                    self.to_gray(block_rgba),
                                                    mask, box)
                print("  第%d次尝试：定位缺口 x=%s y=%s 相似度=%.3f" % (attempt, gap_x, gap_y, score))
                if gap_x is None or score < 0.5:#0.5 是经验阈值：NCC 这么低基本可以断定定位错了
                    print("  定位结果不可靠，重试")
                    self.click_refresh()
                    time.sleep(1)
                    continue

                if not self.drag_to_gap(images, gap_x):
                    #拼图块或拖动柄没找到（画布/滑块刚好没渲染出来），刷新验证码后重试
                    print("  拖动没有执行，刷新验证码后重试")
                    self.click_refresh()
                    time.sleep(1.2)
                    continue
                time.sleep(1.5)

                if self.verify_success():
                    print("  验证成功！（第%d次尝试）" % attempt)
                    return True

                print("  验证未通过，刷新验证码后重试")
                self.click_refresh()
                time.sleep(1.2)

            except Exception as e:
                print("  验证码处理异常:", e)
                try:
                    self.page.mouse.up()#异常时鼠标可能还按着，先松开再重试
                except Exception:
                    pass
                time.sleep(1)
        return False

    def click_refresh(self):
        """点验证码上的刷新按钮换一张图"""
        try:
            self.page.locator(".refreshIcon").first.click(timeout=3000)
        except Exception:
            pass


# ==================== 外部接口 ====================

def verify(page: Page, max_attempts: int = 10) -> bool:
    """
    验证码处理入口：本地图像算法定位缺口，一次拖到位

    在 ensure_login() 里、monkey_verify 为 true 时被调用（登录提交后自动过滑块）。

    Args:
        page: Playwright页面对象
        max_attempts: 最大尝试次数

    Returns:
        bool: 是否成功
    """
    solver = CaptchaSolver(page)
    return solver.solve(max_attempts)


def get_checkin_labels(page: Page):
    """读取页面上可见的签到按钮，返回 [(文案, locator), ...]

    注意：页面上会同时存在“签到”和“晚归签到”两个同类元素，
    必须先 count() + nth(i) 逐个取，直接用 page.locator(...) 调用
    is_visible()/click()/text_content() 在 strict mode 下会直接抛异常。
    """
    result=[]
    labels = page.locator('.xg-font-maintext-sub-weight')
    for index in range(labels.count()):
        label = labels.nth(index)
        try:
            if label.is_visible():
                result.append(((label.text_content() or "").strip(), label))
        except Exception:
            continue
    return result


def click_checkin_button(page: Page, label, text: str) -> bool:
    """点击签到按钮，依次尝试多种方式，任一成功即返回 True

    1. 直接点文案元素（click 事件会冒泡到外层按钮容器）
    2. 在 JS 里从文案往上找“像按钮”的祖先元素再点击
    3. 老页面结构的备用选择器
    """
    # 1.直接点击文案元素（click 事件会冒泡到外层按钮容器）
    try:
        label.click(timeout=5000)
        print("已点击签到按钮:"+text)
        return True
    except Exception as e:
        print("直接点击签到按钮失败:"+str(e))

    # 2.点击最近的“按钮状”祖先元素
    try:
        info = label.evaluate("""el => {
            let cur = el;
            for (let i = 0; i < 6 && cur; i++) {
                const cs = getComputedStyle(cur);
                const cls = typeof cur.className === 'string' ? cur.className : '';
                if (cur.tagName === 'BUTTON' || cur.tagName === 'A' || cs.cursor === 'pointer'
                    || cur.getAttribute('onclick') || /btn|button|action|submit/i.test(cls)) {
                    cur.click();
                    return cur.tagName + '.' + cls;
                }
                cur = cur.parentElement;
            }
            el.click();
            return 'self';
        }""")
        print("已点击按钮容器:"+str(info))
        return True
    except Exception as e:
        print("点击按钮容器失败:"+str(e))

    # 3.旧的备用选择器
    for selector in [".xg-font-title-2", ".action-button"]:
        try:
            button = page.locator(selector).first
            if button.count() > 0 and button.is_visible():
                button.click(timeout=5000)
                print("已点击备用选择器:"+selector)
                return True
        except Exception as e:
            print("备用选择器点击失败:"+selector+" "+str(e))
    return False

# ---------------------------------------------------------------------------------------------------------------------------
# ==================== 主流程 ====================

# ---------- 登录/按钮状态判断的辅助函数 ----------

def wait_until(condition, timeout, interval=1.0):
    """轮询等待条件成立（condition 是无参函数），最多等 timeout 秒

    Returns:
        bool: 条件是否成立（超时后返回最后一次判断结果）
    """
    deadline=time.time()+timeout
    while time.time()<deadline:
        if condition():
            return True
        time.sleep(interval)
    return bool(condition())

def is_auth_page(page):
    """是否还停留在统一身份认证页（cookie 失效或登录未完成）

    标题为主信号；站点改版导致标题变化时，用登录表单是否可见兜底（只增加敏感性，不减少）。
    """
    try:
        if page.title()==LOGIN_PAGE_TITLE:
            return True
        return page.locator("#login_submit").first.is_visible()
    except Exception:
        return False

def find_login_error(page):
    """页面上是否出现登录失败提示，返回命中的提示文字；没有则返回空字符串"""
    try:
        content=page.content()
    except Exception:
        return ""
    for hint in LOGIN_ERROR_HINTS:
        if hint in content:
            return hint
    return ""

def normalize_label(text):
    """归一化按钮文案：去掉半角/全角空格，便于宽容匹配"""
    return text.replace(" ","").replace("\u3000","")

def is_checkin_text(text):
    """按钮文案是否属于“可点击的签到按钮”（精确或带后缀，如“签到（剩余 1 次）”）"""
    normalized=normalize_label(text)
    return (normalized in ("签到","晚归签到")
            or normalized.startswith("签到")
            or normalized.startswith("晚归签到"))

def prepare_config():
    """读取配置；缺失或损坏时生成模板并退出（首次运行流程）

    Returns:
        Config 对象；返回 None 表示配置刚生成，应先让用户填写后再运行
    """
    config=load_config()
    if config is not None:
        return config
    print("\a")
    #生成配置文件模板（损坏的原文件已被备份为 config.json.broken）
    save_config(Config())
    print("config文件缺失或已损坏")
    print("已自动生成配置文件:"+config_file_name)
    print("请打开该文件，把 username / password 填成你的账号密码后重新运行；")
    print("如果不填（保持 unknow），重新运行后需要在弹出的浏览器窗口里手动登录。")
    if os.path.exists(state_file_name):
        os.remove(state_file_name)#不存在config，直接移除login.json文件
    #首次运行不再直接打开浏览器，等用户填好配置后重新运行
    if sys.stdin.isatty():
        input("按回车键退出...")
    return None


def create_context(browser, config):
    """新建浏览器上下文：带虚拟定位/时区；有 login.json 则带 cookie 打开（免登录）"""
    geolocation: Geolocation = {
    "longitude": config.longitude,
    "latitude": config.latitude,
    }
    #有 login.json 就带着 cookie 打开（免登录），没有则开一个全新会话
    if os.path.exists(state_file_name):
        return browser.new_context(
            storage_state=state_file_name,
            geolocation=geolocation,
            locale="zh-Hans-CN",
            timezone_id="Asia/Shanghai",
            permissions=["geolocation"],
        )
    return browser.new_context(
        geolocation=geolocation,
        locale="zh-Hans-CN",
        timezone_id="Asia/Shanghai",
        permissions=["geolocation"],
    )


def ensure_login(page, context, config):
    """检查登录状态：cookie 有效直接返回 True；失效则自动登录（含滑块验证码/手动登录提醒）

    Returns:
        bool: 登录是否完成（False=账号密码错误或等待超时，本次不应继续签到）
    """
    print("浏览器页面标题:", page.title())

    #统一身份认证平台 该标题/登录表单说明cookie过期
    if(not is_auth_page(page)):
        return True
    # ---------- cookie 失效 → 自动登录 ----------
    page.click("#userNameLogin_a")#自动点击登陆选项卡
    time.sleep(1)
    page.click("#rememberMe")#勾选7天内自动登录

    if(config.has_credentials):#自动输入账号和密码
        page.fill("#username",config.username)
        time.sleep(1)
        page.fill("#password",config.password)
        time.sleep(1)
        page.click("#login_submit")
        #等验证码出现或页面开始跳转（最多 3 秒，出现哪种都继续）
        wait_until(lambda: (not is_auth_page(page)) or page.locator("div.slider").count()>0,
                   timeout=3, interval=0.5)
        if(not config.monkey_verify):
            #不自动过验证码：弹一个系统通知，提醒用户去浏览器里手动拖滑块
            try:
                toaster=ToastNotifier()
                toaster.show_toast(this_file_name,"请过人机验证",duration=5,threaded=True)
            except Exception:
                pass
        else:
            #monkey_verify 为 true：用本地图像算法自动过滑块验证码
            verify_res=verify(page)
            if(verify_res==False):
                print("验证失败")
                if page.title()=="智能查寝":
                    print("因未知原因未检测到验证通过,但是验证码已经验证完成")
                    print("开始尝试签到流程")
            else:
                print("验证成功")
    else:
        #config.json 没填账号密码：弹通知提醒用户在浏览器窗口里手动登录
        try:
            toaster=ToastNotifier()
            toaster.show_toast(this_file_name,"请输入账号和密码",duration=5,threaded=True)
        except Exception:
            pass

    if os.path.exists(state_file_name):
        os.remove(state_file_name)

    #等待登录完成：手动登录时在这里等用户操作；自动登录账号密码错误时，
    #页面上会出现错误提示，检测到就立即结束等待；等待超过 login_wait_timeout 秒也算失败
    start_time=time.time()
    while is_auth_page(page):
        if config.has_credentials:
            error_hint=find_login_error(page)
            if error_hint:
                print("检测到登录失败提示["+error_hint+"]，请检查 config.json 里的账号密码")
                return False
        if time.time()-start_time>config.login_wait_timeout:
            print("等待登录超时（超过 %d 秒），放弃本次签到" % config.login_wait_timeout)
            return False
        time.sleep(2)

    #登录成功后立即保存cookie，避免下次运行还要重新登录
    context.storage_state(path=state_file_name)
    if(config.has_credentials):
        save_login_account(config.username)#记录该登录状态属于哪个账号
    print("已保存登录状态到 "+state_file_name)
    return True


def do_checkin(page, context, config):
    """进入签到页，定位签到按钮并点击（dry_run 模式下只检测不点击）

    Returns:
        str: 结果文字（签到成功 / 签到失败 / 已签到 / 未到签到时间 / 试运行未点击）
    """
    time.sleep(1)
    geolocation: Geolocation = {
    "longitude": config.longitude,
    "latitude": config.latitude,
    }
    context.set_geolocation(geolocation)
    if page.locator('.van-icon-replay').first.is_visible():
        page.locator('.van-icon-replay').first.click()#刷新地址，确保定位正确

    time.sleep(1)                 #签到成功时候会出现报错的情况----找不到按钮

    #检测签到按钮
    #页面同时存在“签到”“晚归签到”等多个同类元素，必须 count()+nth() 逐个取，
    #直接用 page.locator(...) 调用 is_visible()/click() 会触发 strict mode 异常导致脚本崩溃
    #循环观察按钮文案（每轮重新扫一遍页面）：
    #  “不在签到范围内”→ 没到时间，等 10 秒再看（最多 max_retry_times 次）
    #  “签到”/“晚归签到” → 点击签到（试运行模式下跳过点击）
    #  没有按钮        → 可能今天已经签过（看页面上的“归宿记录”关键字）
    retryTimes=0
    while True:
        candidates=get_checkin_labels(page)
        if not candidates:
            #没有签到按钮：可能今天已经签过了（页面显示归宿记录），也可能页面结构变了
            try:
                content=page.content()
            except Exception:
                content=""
            for keyword in ["正常归宿","归宿时间","已签到","签到成功"]:
                if keyword in content:
                    print("页面上没有签到按钮，但已显示["+keyword+"]，视为今日已签到")
                    return "已签到"
            print("未找到签到按钮元素")
            return "签到失败"

        #优先选择“签到”，其次“晚归签到”（先精确匹配）
        target=None
        for priority in ["签到","晚归签到"]:
            for item in candidates:
                if item[0]==priority:
                    target=item
                    break
            if target is not None:
                break
        if target is None:
            #没有精确匹配：宽容匹配（文案可能带后缀/空格，如“签到（剩余 1 次）”）
            for item in candidates:
                if is_checkin_text(item[0]):
                    target=item
                    break
        if target is None:
            target=candidates[0]
        text,label=target
        normalized=normalize_label(text)

        #根据按钮文案决定下一步（宽容匹配，兼容带空格/后缀的文案）：
        if("不在签到范围内" in normalized):
            #还没到签到时间段（比如还没到查寝时间），等 10 秒后再看一次
            #max_retry_times 为负数时表示无限重试（一直等到能签到为止）
            if(config.max_retry_times>=0 and retryTimes>config.max_retry_times):
                print("抵达最大尝试次数,停止尝试签到")
                return "未到签到时间"    #给个结果，避免截图文件名变成空的 .png
            retryTimes+=1
            print("未到签到时间，等待...")
            time.sleep(10)
        elif(is_checkin_text(text)):
            if(dry_run):
                #试运行模式：验证“能不能找到按钮”这条链路，不真的点击
                print("试运行模式(CHECKIN_DRY_RUN=1)：检测到可签到按钮["+text+"]，跳过点击")
                return "试运行未点击"
            time.sleep(5)               #点之前稍等，让页面渲染完
            clicked=click_checkin_button(page,label,text)
            time.sleep(5)               #点完后等页面响应，再检查按钮状态
            if(not clicked):
                print("签到失败-[未能点击签到按钮]")
                return "签到失败"
            #点击后再看一次按钮：按钮消失/文案变化才算确认签到成功
            try:
                remaining=[t for t,_ in get_checkin_labels(page)]
            except Exception:
                remaining=[]
            print("点击后按钮状态:"+str(remaining))
            if any(is_checkin_text(t) for t in remaining):
                print("点击后签到按钮仍在，无法确认签到结果")
                return "已点击未确认"
            print("处于签到时间内,已签到")
            return "签到成功"
        else:
            print("错误的文本:")
            print("按钮数值结果获取:"+str([t for t,_ in candidates]))
            return "签到失败"


def capture_screenshot(page, result):
    """保存本次运行的结果截图（文件名：日期_结果，如 2026-09-22_签到成功.png）

    截图失败只打印提示，不影响主流程。
    """
    try:
        date_text=time.strftime("%Y-%m-%d")
        shot_path=os.path.join(base_dir,"%s_%s.png"%(date_text,result))
        page.screenshot(path=shot_path)
        return shot_path
    except Exception as e:
        print("截图保存失败：%s"%e)
        return ""


def finalize(page, config, result, error_text="", elapsed=0.0):
    """收尾留痕：写回配置、写日志、截图（无论签到成功还是中途异常都会执行）"""
    save_config(config)
    account_text=config.username if config.has_credentials else "手动登录"
    log_text="账号[%s] | %s | 用时 %.1f 秒"%(account_text, result, elapsed)
    if error_text:
        log_text+=" | "+error_text
    write_log(log_text)
    if page is not None:
        capture_screenshot(page, result)


def exit_code_for(result):
    """把签到结果映射为进程退出码（0=成功类；3=未到签到时间；其他=失败/异常）"""
    if result in ("签到成功","已签到","试运行未点击","已点击未确认"):
        return 0
    if result=="未到签到时间":
        return 3
    return 1


def auto_checkin():
    """签到主流程（编排）：读配置 → 启动浏览器 → 登录 → 签到 → 收尾留痕"""
    # ---------- 第 1 步：读取 config.json（缺失时生成模板并退出） ----------
    print("如果是首次登陆，或者cookie已经过期，请注意重新登陆，cookie过期时间通常为一周")
    print("当前窗口名称为:"+this_file_name)
    minimize_self_window()# 最小化窗口

    config=prepare_config()
    if config is None:
        return 2    #配置缺失/损坏，已生成模板（退出码 2）

    #login.json 若是别的账号留下的登录状态，先删掉，避免用错账号签到
    if(config.has_credentials and os.path.exists(state_file_name)):
        saved_account=load_login_account()
        if(saved_account and saved_account!=config.username):
            print("注意：login.json 属于账号["+saved_account+"]，与 config.json 的账号["+config.username+"]不一致")
            print("已删除旧的登录状态，本次将用 config.json 里的账号重新登录")
            os.remove(state_file_name)

    result="未知结果"
    error_text=""
    start_time=time.time()     #用于日志里记录本次耗时
    page=None
    browser=None
    # ---------- 第 2 步：启动浏览器（用本机安装的 Edge，无需 playwright install） ----------
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                channel="msedge",    # 指定使用 Edge
                headless=headless    # 默认 False；命令行 --headless 可开无头模式
            )
            try:
                # ---------- 第 3 步：打开签到页，确保登录（含验证码） ----------
                context = create_context(browser, config)
                page = context.new_page()#开启浏览器界面
                page.goto(config.checkin_url)
                #等页面标题渲染出来再判断登录状态（最多 5 秒，超时也继续）
                wait_until(lambda: page.title()!="", timeout=5, interval=0.5)
                if ensure_login(page, context, config):
                    # ---------- 第 4 步：定位签到按钮并完成签到 ----------
                    result = do_checkin(page, context, config)

                    #保存cookie：下次运行就不用再登录了
                    context.storage_state(path=state_file_name)
                    print("已完成签到流程，请注意查看结果")
                else:
                    result="登录失败"
                    print("登录未完成，本次跳过签到步骤")
            except Exception as e:
                traceback.print_exc()
                error_text="运行异常: %s: %s"%(type(e).__name__,e)
                if result=="未知结果":
                    result="运行异常"
            finally:
                #收尾留痕：无论成功失败都要写配置/写日志/截图，且必须在关闭浏览器前执行
                finalize(page, config, result, error_text, time.time()-start_time)
                if not error_text:
                    time.sleep(10)     #留时间给用户看结果，再关浏览器
                try:
                    browser.close()
                except Exception:
                    pass
    except Exception as e:
        #playwright 启动失败等极端情况：至少留下一条日志
        traceback.print_exc()
        finalize(None, config, "运行异常", "运行异常: %s: %s"%(type(e).__name__,e), time.time()-start_time)
        result="运行异常"
    return exit_code_for(result)



_single_instance_mutex=None   #单实例互斥体句柄（模块级持有，防止被回收导致失效）


def parse_args():
    """解析命令行参数（环境变量 CHECKIN_DRY_RUN=1 仍然有效）"""
    parser=argparse.ArgumentParser(description="智能查寝自动签到（山东协和学院）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只检测签到按钮不点击，用于安全试运行（等价于 CHECKIN_DRY_RUN=1）")
    parser.add_argument("--headless", action="store_true",
                        help="无头模式运行，不显示浏览器窗口")
    return parser.parse_args()


def is_already_running():
    """是否已有另一个实例在运行（用命名互斥体判断）"""
    global _single_instance_mutex
    try:
        #pywin32-stubs 把首个参数标成必填，实际 API 允许传 None（默认安全属性），这里忽略类型警告
        _single_instance_mutex=win32event.CreateMutex(None, False, "qiandao_auto_single_instance")  # type: ignore
        return win32api.GetLastError()==winerror.ERROR_ALREADY_EXISTS
    except Exception as e:
        print("单实例检查失败（忽略，继续运行）：%s"%e)
        return False    #互斥体不可用时（极端情况）不阻塞运行


if __name__ == "__main__":
    #只有直接运行本文件才会执行（被 import 时不执行）
    args=parse_args()
    dry_run=dry_run or args.dry_run   #模块级赋值：命令行参数与环境变量都支持
    headless=args.headless
    if is_already_running():
        print("检测到已有实例正在运行，本次启动直接退出（避免两个窗口互相干扰）")
        if getattr(sys, "frozen", False) and sys.stdin.isatty():
            input("按回车键退出...")
        sys.exit(0)
    code=0
    try:
        code=auto_checkin()
    except Exception:
        traceback.print_exc()
        code=1
        if getattr(sys, "frozen", False) and sys.stdin.isatty():
            input("程序出现异常，按回车键退出...")#双击 exe 运行时防止窗口一闪而过，方便看报错
    sys.exit(code)
