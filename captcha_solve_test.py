"""实测新滑块算法：真实打开登录页 → 自动登录 → 触发滑块 → 调用新的 verify()。

只做登录验证，不做签到。运行后会打印每次尝试的定位结果与耗时。
"""

import base64
import json
import os
import time

from playwright.sync_api import sync_playwright

from autoCheckIn import verify          # 复用主脚本里的新算法

checkIn_URL = "https://xg.sdxiehe.edu.cn/xsfw/sys/swmzncqapp/*default/index.do?/xscq/kqqdx#/xscq/kqqdx"
base_dir = os.path.dirname(os.path.abspath(__file__))


def load_credentials():
    path = os.path.join(base_dir, "config.json")
    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)
    username, password = data["username"], data["password"]
    if isinstance(username, str) and username.startswith("#"):
        username = base64.b64decode(username[1:]).decode("utf-8")
    if isinstance(password, str) and password.startswith("#"):
        password = base64.b64decode(password[1:]).decode("utf-8")
    return username, password


def main():
    username, password = load_credentials()
    print("账号:", username[:4] + "****")

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=False)
        context = browser.new_context(locale="zh-Hans-CN", timezone_id="Asia/Shanghai")
        page = context.new_page()
        page.goto(checkIn_URL)
        time.sleep(3)
        print("标题:", page.title())
        if page.title() != "统一身份认证平台":
            print("不是登录页，退出")
            browser.close()
            return

        page.click("#userNameLogin_a")
        time.sleep(1)
        page.click("#rememberMe")
        page.fill("#username", username)
        page.fill("#password", password)
        page.click("#login_submit")
        print("已提交，等待滑块...")

        page.wait_for_selector("div.slider", timeout=30000)
        time.sleep(2)

        start = time.time()
        result = verify(page)
        cost = time.time() - start

        print("=" * 40)
        print("verify() 返回:", result, " 耗时 %.1f 秒" % cost)
        time.sleep(2)
        print("当前标题:", page.title())
        print("登录是否成功:", page.title() != "统一身份认证平台")
        time.sleep(3)
        browser.close()


if __name__ == "__main__":
    main()
