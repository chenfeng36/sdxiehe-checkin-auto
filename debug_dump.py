"""诊断脚本：抓取签到页面结构，用于确定“签到”按钮的正确选择器。

用法：
    .venv\\Scripts\\python.exe debug_dump.py

运行后：
1. 会弹出一个 Edge 窗口打开签到页；
2. 如果停在登录页，请你在窗口里手动登录（点“签到”之前的状态即可）；
3. 登录成功后脚本会自动抓取页面结构，输出：
   - page_dump.html   （完整页面源码）
   - page_dump.png    （整页截图）
   - login.json       （登录 cookie，方便正式脚本下次免登录）
   - 控制台里会打印“签到”相关元素的祖先链和可点击元素列表
"""

import os
import time

from playwright.sync_api import sync_playwright

checkIn_URL = "https://xg.sdxiehe.edu.cn/xsfw/sys/swmzncqapp/*default/index.do?/xscq/kqqdx#/xscq/kqqdx"
state_file_name = "login.json"

FIND_TEXT_JS = """
() => {
    const result = [];
    const targets = ['签到', '晚归签到', '不在签到范围内', '已签到'];
    const walk = (el) => {
        const text = (el.textContent || '').trim();
        if (targets.includes(text) && el.children.length === 0) {
            const chain = [];
            let cur = el;
            for (let i = 0; i < 8 && cur && cur.tagName; i++) {
                let cls = '';
                if (typeof cur.className === 'string') {
                    cls = cur.className;
                } else if (cur.className && cur.className.baseVal) {
                    cls = cur.className.baseVal;
                }
                const rect = cur.getBoundingClientRect();
                chain.push({
                    tag: cur.tagName,
                    cls: cls,
                    id: cur.id || '',
                    style: cur.getAttribute('style') || '',
                    onclick: cur.getAttribute('onclick') || '',
                    cursor: getComputedStyle(cur).cursor,
                    rect: [Math.round(rect.x), Math.round(rect.y), Math.round(rect.width), Math.round(rect.height)]
                });
                cur = cur.parentElement;
            }
            result.push({ text: text, html: el.outerHTML, chain: chain });
        }
        for (const child of el.children) {
            walk(child);
        }
    };
    walk(document.body);
    return result;
}
"""

FIND_CLICKABLE_JS = """
() => {
    const res = [];
    document.querySelectorAll('div, span, button, a, li, p').forEach(el => {
        const cs = getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        if (rect.width < 60 || rect.height < 20 || rect.width > 700 || rect.height > 250) return;
        if (rect.width === 0 || rect.height === 0) return;
        const text = (el.textContent || '').trim();
        if (!text || text.length > 30) return;
        const looksClickable = cs.cursor === 'pointer' || el.getAttribute('onclick') ||
            cs.backgroundColor !== 'rgba(0, 0, 0, 0)' ||
            el.tagName === 'BUTTON' || el.tagName === 'A';
        if (!looksClickable) return;
        if (el.children.length > 2) return;
        res.push({
            tag: el.tagName,
            cls: typeof el.className === 'string' ? el.className : '',
            text: text,
            cursor: cs.cursor,
            bg: cs.backgroundColor,
            rect: [Math.round(rect.x), Math.round(rect.y), Math.round(rect.width), Math.round(rect.height)]
        });
    });
    return res.slice(0, 60);
}
"""


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="msedge",
            headless=False,
        )
        if os.path.exists(state_file_name):
            context = browser.new_context(storage_state=state_file_name)
        else:
            context = browser.new_context()
        page = context.new_page()
        page.goto(checkIn_URL)
        time.sleep(3)
        print("当前页面标题:", page.title())

        if page.title() == "统一身份认证平台":
            print(">>> 需要在浏览器窗口里手动登录（含人机验证），脚本最多等待 5 分钟...")
        deadline = time.time() + 300
        while page.title() == "统一身份认证平台" and time.time() < deadline:
            time.sleep(3)

        time.sleep(10)  # 等待签到页数据加载完成

        print("登录后标题:", page.title())
        print("当前 URL:", page.url)

        with open("page_dump.html", "w", encoding="utf-8") as f:
            f.write(page.content())
        page.screenshot(path="page_dump.png", full_page=True)
        context.storage_state(path=state_file_name)

        print("\n=== 1. “签到”文字相关元素及其祖先链 ===")
        found = page.evaluate(FIND_TEXT_JS)
        if not found:
            print("(没找到签到相关文字元素，可能页面还在加载或需要滚动)")
        for item in found:
            print("文字:", item["text"])
            print("元素 HTML:", item["html"][:300])
            for i, node in enumerate(item["chain"]):
                print(f"  第{i}层: <{node['tag']} class=\"{node['cls']}\" id=\"{node['id']}\" "
                      f"cursor={node['cursor']} rect={node['rect']} style=\"{node['style'][:60]}\" "
                      f"onclick=\"{node['onclick'][:60]}\">")
            print("-" * 60)

        print("\n=== 2. 页面上疑似可点击元素 ===")
        for el in page.evaluate(FIND_CLICKABLE_JS):
            print(el)

        print("\n已保存 page_dump.html / page_dump.png / login.json")
        print("请把控制台输出（或 page_dump.html）发给助手分析。")
        input("按回车关闭浏览器...")
        browser.close()


if __name__ == "__main__":
    main()
