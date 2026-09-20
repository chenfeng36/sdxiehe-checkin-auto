"""验证码调试脚本：抓取滑块验证码的原图与 DOM 结构，用于开发 OpenCV 缺口识别算法。

特点：
  - 使用独立的浏览器环境（不带 cookie），一定会走到登录页；
  - 会自动填账号密码并提交，触发滑块验证，然后把验证码素材全部存到 captcha_debug\\ 目录；
  - 【不会】拖动滑块、不会点击签到，抓完即关闭浏览器，不影响你的账号。

用法：
    .venv\\Scripts\\python.exe captcha_debug.py            # 默认抓 3 轮
    .venv\\Scripts\\python.exe captcha_debug.py 1          # 只抓 1 轮
"""

import base64
import json
import os
import sys
import time

from playwright.sync_api import sync_playwright

checkIn_URL = "https://xg.sdxiehe.edu.cn/xsfw/sys/swmzncqapp/*default/index.do?/xscq/kqqdx#/xscq/kqqdx"
base_dir = os.path.dirname(os.path.abspath(__file__))
dump_dir = os.path.join(base_dir, "captcha_debug")

JS_CANVAS_INFO = """
() => {
    const out = [];
    document.querySelectorAll('canvas').forEach((c, i) => {
        const r = c.getBoundingClientRect();
        const cs = getComputedStyle(c);
        out.push({
            index: i,
            id: c.id || '',
            cls: (typeof c.className === 'string') ? c.className : '',
            canvasWidth: c.width,
            canvasHeight: c.height,
            cssWidth: Math.round(r.width * 100) / 100,
            cssHeight: Math.round(r.height * 100) / 100,
            left: Math.round(r.x * 100) / 100,
            top: Math.round(r.y * 100) / 100,
            display: cs.display,
            visibility: cs.visibility,
            opacity: cs.opacity
        });
    });
    return out;
}
"""

JS_CANVAS_DATA = """
() => {
    const out = [];
    document.querySelectorAll('canvas').forEach((c) => {
        let data = '';
        try { data = c.toDataURL('image/png'); } catch (e) { data = 'ERROR:' + e; }
        out.push(data);
    });
    return out;
}
"""

JS_SLIDER_INFO = """
() => {
    const s = document.querySelector('div.slider');
    if (!s) return null;
    const r = s.getBoundingClientRect();
    return {
        cls: typeof s.className === 'string' ? s.className : '',
        style: s.getAttribute('style') || '',
        left: Math.round(r.x * 100) / 100,
        top: Math.round(r.y * 100) / 100,
        width: Math.round(r.width * 100) / 100,
        height: Math.round(r.height * 100) / 100,
        html: s.outerHTML.slice(0, 500)
    };
}
"""

JS_CAPTCHA_HTML = """
() => {
    const c = document.querySelector('canvas.block') || document.querySelector('canvas');
    if (!c) return '';
    let cur = c;
    for (let i = 0; i < 4 && cur.parentElement; i++) {
        cur = cur.parentElement;
    }
    return cur.outerHTML;
}
"""

JS_ENV = """
() => ({
    dpr: window.devicePixelRatio,
    innerWidth: window.innerWidth,
    innerHeight: window.innerHeight,
    title: document.title
})
"""


def load_credentials():
    """从 config.json 读取账号密码（支持 base64 形式）"""
    path = os.path.join(base_dir, "config.json")
    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)
    username, password = data["username"], data["password"]
    if isinstance(username, str) and username.startswith("#"):
        username = base64.b64decode(username[1:]).decode("utf-8")
    if isinstance(password, str) and password.startswith("#"):
        password = base64.b64decode(password[1:]).decode("utf-8")
    return username, password


def save_data_url(data_url, path):
    """把 data:image/png;base64,... 存成文件"""
    if not data_url.startswith("data:image"):
        return False
    try:
        with open(path, "wb") as file:
            file.write(base64.b64decode(data_url.split(",", 1)[1]))
        return True
    except Exception as e:
        print("  保存失败:", e)
        return False


def dump_round(page, round_index):
    """抓取一轮验证码素材，返回是否有 canvas"""
    prefix = os.path.join(dump_dir, "round%d" % round_index)

    env = page.evaluate(JS_ENV)
    print("  页面环境:", env)

    canvases = page.evaluate(JS_CANVAS_INFO)
    print("  canvas 数量:", len(canvases))
    for item in canvases:
        print("   ",
              "index=%s class=%r id=%r canvas=%sx%s css=%sx%s left=%s top=%s display=%s"
              % (item["index"], item["cls"], item["id"], item["canvasWidth"], item["canvasHeight"],
                 item["cssWidth"], item["cssHeight"], item["left"], item["top"], item["display"]))

    datas = page.evaluate(JS_CANVAS_DATA)
    for index, data_url in enumerate(datas):
        path = "%s_canvas%d.png" % (prefix, index)
        if save_data_url(data_url, path):
            print("  已保存:", os.path.basename(path))

    with open("%s_info.json" % prefix, "w", encoding="utf-8") as file:
        json.dump({"env": env, "canvases": canvases,
                   "slider": page.evaluate(JS_SLIDER_INFO)},
                  file, ensure_ascii=False, indent=2)

    html = page.evaluate(JS_CAPTCHA_HTML)
    if html:
        with open("%s_dom.html" % prefix, "w", encoding="utf-8") as file:
            file.write(html)

    page.screenshot(path="%s_page.png" % prefix)
    try:
        page.locator("div.slider").first.screenshot(path="%s_area.png" % prefix)
    except Exception as e:
        print("  验证码区域截图失败:", e)

    return len(canvases) > 0


def main():
    rounds = 3
    if len(sys.argv) > 1:
        rounds = int(sys.argv[1])

    os.makedirs(dump_dir, exist_ok=True)
    username, password = load_credentials()
    print("账号:", username[:4] + "****" + "（不打印完整账号密码）")
    print("素材目录:", dump_dir)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=False)
        context = browser.new_context(locale="zh-Hans-CN", timezone_id="Asia/Shanghai")
        page = context.new_page()

        def login_and_wait_captcha(round_index):
            """打开登录页并提交账号密码，等待滑块出现"""
            page.goto(checkIn_URL)
            time.sleep(3)
            print("  标题:", page.title())
            if page.title() != "统一身份认证平台":
                print("  没有进入登录页，跳过本轮")
                return False

            page.click("#userNameLogin_a")   # 账号登录选项卡
            time.sleep(1)
            page.click("#rememberMe")
            page.fill("#username", username)
            time.sleep(0.5)
            page.fill("#password", password)
            time.sleep(0.5)
            page.click("#login_submit")
            print("  已提交登录，等待滑块出现...")

            try:
                page.wait_for_selector("div.slider", timeout=30000)
            except Exception as e:
                print("  没等到滑块:", e)
                page.screenshot(path=os.path.join(dump_dir, "round%d_no_slider.png" % round_index))
                return False
            time.sleep(2)   # 等验证码图片加载完
            return True

        for round_index in range(1, rounds + 1):
            print("=== 第 %d 轮 ===" % round_index)
            if round_index == 1:
                if not login_and_wait_captcha(round_index):
                    continue
            else:
                # 点验证码上的刷新按钮换一张图，避免反复提交登录
                try:
                    page.locator(".refreshIcon").first.click()
                    print("  已点击刷新按钮，获取新验证码")
                    time.sleep(2)
                except Exception as e:
                    print("  刷新验证码失败，改用重新登录:", e)
                    if not login_and_wait_captcha(round_index):
                        continue

            dump_round(page, round_index)

        print("全部抓取完成，素材在:", dump_dir)
        print("请查看 round*_canvas*.png，并把这些文件交给助手分析。")
        browser.close()


if __name__ == "__main__":
    main()
