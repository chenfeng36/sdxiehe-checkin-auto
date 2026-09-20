from playwright.sync_api import sync_playwright, Geolocation
from win10toast import ToastNotifier
import os
import sys
import time
import json
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

maxRetryTimes=40

success=True
failure=False
unknow="unknow"

checkIn_result=""

checkIn_URL="https://xg.sdxiehe.edu.cn/xsfw/sys/swmzncqapp/*default/index.do?/xscq/kqqdx#/xscq/kqqdx"

file_encoding='utf-8'

#经纬度
defult_longitude="117.261944"
defult_latitude="36.739722"

longitude=float(defult_longitude)
latitude=float(defult_latitude)

monkey_verify="false"


#设置环境变量 CHECKIN_DRY_RUN=1 后，只检测签到按钮不点击，用于安全试运行
dry_run = os.environ.get("CHECKIN_DRY_RUN")=="1"

state_file_name=os.path.join(base_dir,"login.json")
config_file_name=os.path.join(base_dir,"config.json")
log_file_name=os.path.join(base_dir,"log.log")
#记录 login.json 这个登录状态属于哪个账号，用于避免换账号后仍用旧cookie签到
login_account_file_name=os.path.join(base_dir,"login_account.txt")

username=unknow
password=unknow

raw_username=unknow
raw_password=unknow

expired="""\"expired\":{\"year\":\"ERROR\",\"month\":\"ERROR\",\"day\":\"ERROR\"}"""
nowTime="%d.%d.%d"%(time.localtime().tm_year,time.localtime().tm_mon,time.localtime().tm_mday)
location=None

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

def writeLog(content):
    try:
        with open(log_file_name,"a",encoding=file_encoding) as file:
            file.write(nowTime+"-"+content+"\n")
        return success
    except:
        print("日志写入失败")
        return failure
def loadLoginAccount():
    """读取 login.json 是哪个账号留下的"""
    try:
        with open(login_account_file_name,"r",encoding=file_encoding) as file:
            return file.read().strip()
    except:
        return ""

def saveLoginAccount(account):
    """记录本次登录状态属于哪个账号"""
    try:
        with open(login_account_file_name,"w",encoding=file_encoding) as file:
            file.write(str(account))
    except:
        pass

def loadUserData():
    global raw_username,raw_password,username,password,this_file_name,file_encoding,defult_longitude,defult_latitude,longitude,latitude,monkey_verify,state_file_name,config_file_name,username,password,expired,success,failure,unknow,checkIn_result,nowtime,checkIn_URL,location

    if(os.path.exists(config_file_name)): #存在config文件，读取保存的内容
        print(config_file_name+"文件存在")
        with open(config_file_name, 'r', encoding=file_encoding) as file:
            json_str = file.read()
        
        print(json_str)
        try:
            data = json.loads(json_str)
        except:
            print("发生错误,config.json文件读取失败")
            os.remove(config_file_name)
            return failure
        else:
            raw_username=data['username']
            raw_password=data['password']

            expired=data['expired']
            longitude=float(data['location']['longitude'])
            latitude=float(data['location']['latitude'])
            monkey_verify=data['monkey_verify']


            if raw_username!=unknow:
                if raw_username[0]!='#':#明文账号
                    username=raw_username
                    raw_username='#'+base64.b64encode(raw_username.encode('utf-8')).decode('utf-8')
                else:
                    username=base64.b64decode(raw_username[1:]).decode('utf-8')

            if raw_password!=unknow:
                if raw_password[0]!='#':#明文密码
                    password=raw_password
                    raw_password='#'+base64.b64encode(raw_password.encode('utf-8')).decode('utf-8')
                else:
                    password=base64.b64decode(raw_password[1:]).decode('utf-8')


            return success
    else:
        return failure

def writeUserData():
    global this_file_name,file_encoding,raw_username,raw_password,expired,defult_longitude,defult_latitude,longitude,latitude,monkey_verify,state_file_name,config_file_name,success,failure,unknow,checkIn_result,nowtime,checkIn_URL,location
    try:
        with open(config_file_name, 'w', encoding=file_encoding) as file:
            file.write('''{
    \"username\":\"%s\",
    \"password\":\"%s\",
    \"location\":
    {
        \"longitude\":\"%s\",
        \"latitude\":\"%s\"
    },
    \"monkey_verify\":\"%s\",
    \"expired\":{\"year\":%d,\"month\":%d,\"day\":%d}
}'''%(raw_username,raw_password,longitude,latitude,monkey_verify,time.localtime().tm_year,time.localtime().tm_mon,time.localtime().tm_mday)
        )
    except:
        print("发生错误,config文件写入失败")
        return failure
    else:
        return success

"""---------------------------------------------------------------------------------------------------------------------------"""
from playwright.sync_api import Page
import random


class CaptchaSolver:
    """验证码处理器：本地图像算法定位缺口，一次拖到位

    实测确认的规律：
      - 底图 canvas 上的缺口 = 原图内容被半透明黑色遮罩盖住（内容还在，只是整体变暗）
      - 拼图块 canvas.block 上的块 = 缺口处的原图内容（其余是透明区域）
      - 于是用“零均值归一化相关(NCC)”在底图上搜索与拼图块内容最相似的位置：
        NCC 对整体明暗变化不敏感，正好免疫遮罩变暗，能精确到 1 像素
    只用 numpy 实现，不依赖 OpenCV/Pillow。
    """

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

    JS_BLOCK_X = """
    () => {
        const c = document.querySelector('canvas.block');
        return c ? c.getBoundingClientRect().x : 0;
    }
    """

    def __init__(self, page: Page):
        self.page = page

    def read_canvases(self):
        """读取两个 canvas 的几何信息与像素数据"""
        info = self.page.evaluate(self.JS_GET_CANVAS)
        if not info or not info.get("bg") or not info.get("block"):
            return None
        bg, block = info["bg"], info["block"]
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
        """拼图块在它自己画布内的外接矩形 + 不透明掩膜"""
        mask = block_rgba[:, :, 3] > 10
        ys, xs = np.nonzero(mask)
        if len(xs) == 0:
            return None, mask
        return (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())), mask

    @staticmethod
    def find_gap(bg_gray, piece_gray, piece_mask, box, y_tolerance=4):
        """在拼图块起点附近穷举，用零均值 NCC 找缺口位置

        Returns:
            (缺口x, 缺口y, 匹配得分) 画布内部像素坐标
        """
        x0, y0, x1, y1 = box
        template = piece_gray[y0:y1 + 1, x0:x1 + 1]
        mask = piece_mask[y0:y1 + 1, x0:x1 + 1]
        values = template[mask]
        values = values - values.mean()
        norm = float(np.sqrt((values * values).sum()))
        if norm == 0:
            return None, None, 0.0

        height, width = template.shape
        bg_height, bg_width = bg_gray.shape
        best_score, best_x, best_y = -2.0, None, None
        y_start = max(0, y0 - y_tolerance)
        y_end = min(bg_height - height, y0 + y_tolerance)
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
        """按拟人轨迹把鼠标从 cursor 处移动 distance 像素，返回移动后的 x 坐标"""
        for step in self.generate_track(distance):
            cursor += step
            self.page.mouse.move(cursor, y + random.uniform(-0.5, 0.5))
            time.sleep(random.uniform(0.012, 0.022))
        return cursor

    def drag_to_gap(self, images, gap_x):
        """把拼图块拖到缺口处：几何换算 → 拖动 → 实测补偿 → 松手"""
        bg = images["bg"]
        box, _ = self.piece_box(images["block_rgba"])
        if box is None:
            return False

        scale_bg = bg["width"] / float(bg["pixelWidth"])     # 底图画布内部像素 → CSS 像素
        scale_block = images["block"]["width"] / float(images["block"]["pixelWidth"])
        block_rect_before = float(self.page.evaluate(self.JS_BLOCK_X))
        piece_before = block_rect_before + box[0] * scale_block

        target = (gap_x - box[0]) * scale_bg
        if target <= 0:
            target = 1.0
        print("  缺口x=%d，拼图块x=%d，换算拖动距离=%.1f CSS像素" % (gap_x, box[0], target))

        slider_box = self.page.locator("div.slider").first.bounding_box()
        start_x = slider_box["x"] + slider_box["width"] / 2
        start_y = slider_box["y"] + slider_box["height"] / 2

        self.page.mouse.move(start_x, start_y)
        time.sleep(random.uniform(0.05, 0.12))
        self.page.mouse.down()
        time.sleep(random.uniform(0.05, 0.15))

        cursor = self.slide_by(start_x, start_y, target)
        time.sleep(random.uniform(0.15, 0.3))

        # 实测补偿：重新读一次拼图块位置，把误差补上（只补一次）
        try:
            images2 = self.read_canvases()
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
        """执行验证：定位缺口 → 拖到位 → 检查结果，失败才重试"""
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
                if gap_x is None or score < 0.5:
                    print("  定位结果不可靠，重试")
                    self.click_refresh()
                    time.sleep(1)
                    continue

                self.drag_to_gap(images, gap_x)
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
                    self.page.mouse.up()
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

    Args:
        page: Playwright页面对象
        max_attempts: 最大尝试次数

    Returns:
        bool: 是否成功
    """
    solver = CaptchaSolver(page)
    return solver.solve(max_attempts)


def get_checkin_labels(page: Page):
    """读取页面上可见的签到按钮，返回 [(locator, 文案), ...]

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
                result.append((label, (label.text_content() or "").strip()))
        except Exception:
            continue
    return result


def click_checkin_button(page: Page, label, text: str) -> bool:
    """点击签到按钮，依次尝试多种方式"""
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

"""---------------------------------------------------------------------------------------------------------------------------"""
def autoCheckIn():
    global this_file_name,file_encoding,defult_longitude,defult_latitude,longitude,latitude,monkey_verify,state_file_name,config_file_name,username,password,expired,success,failure,unknow,checkIn_result,nowtime,checkIn_URL,location

    print("如果是首次登陆，或者cookie已经过期，请注意重新登陆，cookie过期时间通常为一周")

    print("当前窗口名称为:"+this_file_name)
    minimize_self_window()# 最小化窗口

    if(loadUserData()==failure):#不存在config文件
        print("\a")
        #保存配置文件
        writeUserData()
        print("config文件缺失")
        print("已自动生成配置文件:"+config_file_name)
        print("请打开该文件，把 username / password 填成你的账号密码后重新运行；")
        print("如果保持 unknow，则需要在弹出的浏览器窗口里手动登录。")
        if(os.path.exists(state_file_name)):
            os.remove(state_file_name)#不存在config，直接移除login.json文件
    #login.json 若是别的账号留下的登录状态，先删掉，避免用错账号签到
    if(username!=unknow and os.path.exists(state_file_name)):
        saved_account=loadLoginAccount()
        if(saved_account and saved_account!=username):
            print("注意：login.json 属于账号["+saved_account+"]，与 config.json 的账号["+username+"]不一致")
            print("已删除旧的登录状态，本次将用 config.json 里的账号重新登录")
            os.remove(state_file_name)
    geolocation: Geolocation = {
    "longitude": float(longitude),
    "latitude": float(latitude),
    }
    location=geolocation

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="msedge",    # 指定使用 Edge
            headless=False       # 设为 True 则无头模式运行
        )

        context=None
        if os.path.exists(state_file_name):#存在cookie，直接使用
            context = browser.new_context(
            storage_state=state_file_name,
            geolocation=geolocation,
            locale="zh-Hans-CN",
            timezone_id="Asia/Shanghai",
            permissions=["geolocation"],
            )
        else:#默认无cookie
            context = browser.new_context(
            geolocation=geolocation,
            locale="zh-Hans-CN",
            timezone_id="Asia/Shanghai",
            permissions=["geolocation"],
            )
        page = context.new_page()#开启浏览器界面
        page.goto(checkIn_URL)
        time.sleep(2)
        print("浏览器页面标题:", page.title())

        #检查cookie是否可用,如果不可用，则会进入此处理流程
        #统一身份认证平台 该标题说明cookie过期
        if(page.title()=="统一身份认证平台"):
            page.click("#userNameLogin_a")#自动点击登陆选项卡
            time.sleep(1)
            page.click("#rememberMe")#勾选7天内自动登录

            if(username!=unknow and password!=unknow):#自动输入账号和密码
                page.fill("#username",username)
                time.sleep(1)
                page.fill("#password",password)
                time.sleep(1)
                page.click("#login_submit")
                time.sleep(1)
                if(monkey_verify=="false"):
                    try:
                        toaster=ToastNotifier()
                        toaster.show_toast(this_file_name,"请过人机验证",duration=5,threaded=True)
                    except:
                        pass
                else:
                    verify_res=verify(page)
                    if(verify_res==False):
                        print("验证失败")
                        if page.title()=="智能查寝":
                            print("因未知原因未检测到验证通过,但是验证码已经验证完成")
                            print("开始尝试签到流程")
                    else:
                        print("验证成功")
            else:
                try:
                    toaster=ToastNotifier()
                    toaster.show_toast(this_file_name,"请输入账号和密码",duration=5,threaded=True)
                except:
                    pass

            if os.path.exists(state_file_name):
                os.remove(state_file_name)
            while(page.title()=="统一身份认证平台"):
                time.sleep(5)
            #登录成功后立即保存cookie，避免下次运行还要重新登录
            context.storage_state(path=state_file_name)
            if(username!=unknow):
                saveLoginAccount(username)#记录该登录状态属于哪个账号
            print("已保存登录状态到 "+state_file_name)



        time.sleep(1)
        context.set_geolocation(geolocation)
        if page.locator('.van-icon-replay').first.is_visible()==True:
            page.locator('.van-icon-replay').first.click()#刷新地址，确保定位正确
        
        time.sleep(1)                 #签到成功时候会出现报错的情况----找不到按钮



        #检测签到按钮
        #页面同时存在“签到”“晚归签到”等多个同类元素，必须 count()+nth() 逐个取，
        #直接用 page.locator(...) 调用 is_visible()/click() 会触发 strict mode 异常导致脚本崩溃
        retryTimes=0
        while True:
            candidates=get_checkin_labels(page)
            if not candidates:
                #没有签到按钮：可能今天已经签过了（页面显示归宿记录），也可能页面结构变了
                printed_already=False
                try:
                    content=page.content()
                except Exception:
                    content=""
                for keyword in ["正常归宿","归宿时间","已签到","签到成功"]:
                    if keyword in content:
                        print("页面上没有签到按钮，但已显示["+keyword+"]，视为今日已签到")
                        checkIn_result="已签到"
                        printed_already=True
                        break
                if not printed_already:
                    print("未找到签到按钮元素")
                    checkIn_result="签到失败"
                break

            #优先选择“签到”，其次“晚归签到”
            target=None
            for priority in ["签到","晚归签到"]:
                for item in candidates:
                    if item[1]==priority:
                        target=item
                        break
                if target is not None:
                    break
            if target is None:
                target=candidates[0]
            label,text=target
            # try:
            #     text = page.text_content('.xg-font-maintext-sub-weight')#.xg-font-maintext-sub-weight是签到按钮
            # except BaseException:
            #     print("发生未知错误")
            #     #保存cookie
            #     context.storage_state(path=state_file_name)
            #     #保存配置文件
            #     writeUserData()
            #     time.sleep(10)
            #     return 0

            if(text=="不在签到范围内"):
                if(retryTimes>maxRetryTimes and maxRetryTimes>=0):
                    print("抵达最大尝试次数,停止尝试签到")
                    break
                retryTimes+=1
                print("未到签到时间，等待...")
                time.sleep(10)
            elif(text=="签到" or text=="晚归签到"):
                if(dry_run):
                    print("试运行模式(CHECKIN_DRY_RUN=1)：检测到可签到按钮["+text+"]，跳过点击")
                    checkIn_result="试运行未点击"
                    break
                time.sleep(5)
                if(click_checkin_button(page,label,text)):
                    checkIn_result="签到成功"
                else:
                    print("签到失败-[未能点击签到按钮]")
                    checkIn_result="签到失败"
                time.sleep(5)
                try:
                    print("点击后按钮状态:"+str([t for _,t in get_checkin_labels(page)]))
                except Exception:
                    pass
                print("处于签到时间内,已签到")
                

                break
            else:
                print("错误的文本:")
                checkIn_result="签到失败"
                print("按钮数值结果获取:"+str([t for _,t in candidates]))
                break
        #保存cookie
        context.storage_state(path=state_file_name)
        writeUserData()
        writeLog(checkIn_result)

        print("已完成签到流程，请注意查看结果")
        page.screenshot(path=os.path.join(base_dir,"%s.png"%(checkIn_result)))#保存结果
        print("完成")



        #os.system("pause")



        time.sleep(10)
        browser.close()
        return 0



if __name__ == "__main__":
    try:
        autoCheckIn()
    except Exception:
        import traceback
        traceback.print_exc()
        if getattr(sys, "frozen", False):
            input("程序出现异常，按回车键退出...")
