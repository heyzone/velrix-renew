import os
import time
import random
import imaplib
import email
import re
import urllib.request
import urllib.parse
from seleniumbase import SB
from selenium.webdriver.common.action_chains import ActionChains

# ============================================================
# 配置（从环境变量读取）
# ============================================================

_account = os.environ["VELRIX_ACCOUNT"].split(",")
VELRIX_USERNAME = _account[0].strip()
GMAIL_ADDRESS   = _account[1].strip()
GMAIL_PASSWORD  = _account[2].strip()

LOCAL_PROXY  = "http://127.0.0.1:8080"
RENEW_URL    = "https://www.velrix.net/flow/renew"

_tg_raw = os.environ.get("TG_BOT", "")
if _tg_raw and "," in _tg_raw:
    _tg        = _tg_raw.split(",")
    TG_CHAT_ID = _tg[0].strip()
    TG_TOKEN   = _tg[1].strip()
else:
    TG_CHAT_ID = ""
    TG_TOKEN   = ""


# ============================================================
# 工具函数
# ============================================================

def now_str():
    import datetime
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def send_tg(result: str, due_date: str = None):
    lines = [
        "🎮 Velrix 服务器续期通知",
        f"🕐 运行时间: {now_str()}",
        f"👤 账号: {VELRIX_USERNAME}",
        f"📊 续期结果: {result}",
    ]
    if due_date:
        lines.append(f"📅 到期时间: {due_date}")
    msg = "\n".join(lines)

    if not TG_TOKEN or not TG_CHAT_ID:
        print("⚠️  TG 未配置，跳过推送")
        return
    url  = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": TG_CHAT_ID, "text": msg}).encode()
    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=15):
            print("📨 TG 推送成功")
    except Exception as e:
        print(f"⚠️  TG 推送失败：{e}")


def save_debug(sb, tag: str):
    """保存截图 + 页面源码，方便调试。"""
    try:
        sb.save_screenshot(f"velrix_{tag}.png")
    except Exception:
        pass
    try:
        with open(f"velrix_{tag}.html", "w", encoding="utf-8") as f:
            f.write(sb.get_page_source())
    except Exception:
        pass


def human_delay(lo=0.6, hi=1.4):
    """模拟人类操作间隔。"""
    time.sleep(random.uniform(lo, hi))


def js_mouse_click(sb, element):
    """用 JS MouseEvent 触发点击，兼容 React/Vue 事件系统。"""
    sb.execute_script("""
        arguments[0].dispatchEvent(new MouseEvent('mouseover', {bubbles:true}));
        arguments[0].dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
        arguments[0].dispatchEvent(new MouseEvent('mouseup',   {bubbles:true}));
        arguments[0].dispatchEvent(new MouseEvent('click',     {bubbles:true, cancelable:true}));
    """, element)


# ============================================================
# IMAP 读取 Gmail OTP（6位）
# ============================================================

def fetch_otp_from_gmail(wait_seconds: int = 90) -> str:
    print(f"📬 连接 Gmail，最长等待 {wait_seconds}s ...")
    deadline = time.time() + wait_seconds

    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_ADDRESS, GMAIL_PASSWORD)

    spam_folder = None
    _, folder_list = mail.list()
    for f in folder_list:
        decoded = f.decode("utf-8", errors="ignore")
        if any(k in decoded for k in ["Spam", "Junk", "垃圾", "spam", "junk"]):
            match = re.search(r'"([^"]+)"\s*$', decoded) or re.search(r'(\S+)\s*$', decoded)
            if match:
                spam_folder = match.group(1).strip('"')
                print("🗑️  找到垃圾邮件文件夹，一并监控")
                break

    folders_to_check = ["INBOX"] + ([spam_folder] if spam_folder else [])

    seen_uids: dict[str, set] = {}
    for folder in folders_to_check:
        try:
            status, _ = mail.select(folder)
            if status != "OK":
                raise Exception(f"select 失败: {status}")
            _, data = mail.uid("search", None, "ALL")
            seen_uids[folder] = set(data[0].split())
        except Exception as e:
            print(f"⚠️  初始化文件夹 {folder} 出错: {e}")
            seen_uids[folder] = set()

    while time.time() < deadline:
        time.sleep(5)
        for folder in folders_to_check:
            try:
                status, _ = mail.select(folder)
                if status != "OK":
                    continue
                _, data = mail.uid("search", None, 'FROM "velrix"')
                all_uids = set(data[0].split())
                new_uids = all_uids - seen_uids[folder]

                for uid in new_uids:
                    seen_uids[folder].add(uid)
                    _, msg_data = mail.uid("fetch", uid, "(RFC822)")
                    raw = msg_data[0][1]
                    msg = email.message_from_bytes(raw)

                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            ct = part.get_content_type()
                            if ct == "text/plain":
                                body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                                break
                        if not body:
                            for part in msg.walk():
                                if part.get_content_type() == "text/html":
                                    html = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                                    body = re.sub(r'<[^>]+>', ' ', html)
                                    break
                    else:
                        body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")

                    otp_match = re.search(r'\b(\d{6})\b', body)
                    if otp_match:
                        code = otp_match.group(1)
                        print(f"✅ Gmail OTP 获取成功: {code}")
                        mail.logout()
                        return code

            except Exception as e:
                print(f"⚠️  轮询 {folder} 出错: {e}")
                continue

    mail.logout()
    raise TimeoutError("❌ Gmail OTP 等待超时")


# ============================================================
# JS 辅助
# ============================================================

_JS_HAS_MODAL    = '(function(){ return !!document.querySelector(\'[role="dialog"]\'); })()'
_JS_REMOVE_MODAL = """
(function() {
    var d = document.querySelector('[role="dialog"]');
    if (d) d.remove();
    var o = document.querySelector('[data-slot="overlay"]');
    if (o) o.remove();
    document.body.style.overflow = '';
    document.body.style.pointerEvents = '';
})()
"""
_JS_CLICK_ACCEPT = """
(function() {
    var btns = document.querySelectorAll('button');
    for (var i = 0; i < btns.length; i++) {
        var txt = btns[i].textContent.replace(/\\s+/g, ' ').trim();
        if (txt.indexOf('Accept all') !== -1) {
            btns[i].dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true}));
            return 'clicked:' + txt;
        }
    }
    return null;
})()
"""


# ============================================================
# 关闭隐私弹窗
# ============================================================

def dismiss_privacy_modal(sb) -> None:
    print("🍪 等待隐私弹窗...")

    modal_found = False
    for _ in range(15):
        try:
            if sb.execute_script(_JS_HAS_MODAL):
                modal_found = True
                break
        except Exception:
            pass
        time.sleep(1)

    if not modal_found:
        print("ℹ️  未检测到隐私弹窗，跳过")
        return

    print("🍪 检测到弹窗，尝试点击 Accept all ...")
    try:
        result = sb.execute_script(_JS_CLICK_ACCEPT)
        if result:
            print(f"✅ 隐私弹窗已关闭（{result}）")
            time.sleep(1.5)
            return
    except Exception as e:
        print(f"⚠️  JS click 异常: {e}")

    print("⚠️  JS click 无效，强制移除弹窗 DOM ...")
    try:
        sb.execute_script(_JS_REMOVE_MODAL)
        print("✅ 弹窗 DOM 已强制移除")
        time.sleep(0.5)
    except Exception as e:
        print(f"⚠️  DOM 移除也失败: {e}")


# ============================================================
# 等待页面切换到指定步骤（通过 h2 文本判断）
# ============================================================

def wait_for_page_title(sb, keyword: str, timeout: int = 30) -> bool:
    for _ in range(timeout):
        try:
            h2 = sb.execute_script(
                "(function(){ var h = document.querySelector('h2,h1'); return h ? h.innerText : ''; })()"
            )
            if h2 and keyword.lower() in h2.lower():
                print(f"✅ 页面已切换: {h2.strip()}")
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


# ============================================================
# 用 ActionChains 模拟真实鼠标点击按钮
# ============================================================

def click_button_human(sb, xpaths: list) -> bool:
    """
    依次尝试多个 XPath，找到可见按钮后用 ActionChains 模拟真实鼠标点击。
    同时兜底用 JS MouseEvent。返回是否点击成功。
    """
    for sel in xpaths:
        try:
            if not sb.is_element_visible(sel):
                continue
            el = sb.find_element(sel)
            # 滚动到可视区域
            sb.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            human_delay(0.2, 0.5)
            # ActionChains 真实鼠标移动 + 点击
            ActionChains(sb.driver).move_to_element(el).pause(
                random.uniform(0.1, 0.3)
            ).click().perform()
            print(f"✅ ActionChains 点击成功: {sel}")
            return True
        except Exception as e:
            print(f"⚠️  ActionChains 失败 ({sel}): {e}，尝试 JS click ...")
            try:
                el = sb.find_element(sel)
                js_mouse_click(sb, el)
                print(f"✅ JS MouseEvent 点击成功: {sel}")
                return True
            except Exception as e2:
                print(f"⚠️  JS click 也失败: {e2}")
            continue
    return False


# ============================================================
# 检测页面错误提示
# ============================================================

def get_page_error(sb) -> str | None:
    return sb.execute_script("""
(function(){
    var sels = [
        '[role="alert"]',
        '[data-slot="description"]',
        '.text-error',
        'p[class*="error"]',
        'p[class*="red"]',
        'span[class*="error"]'
    ];
    for(var i=0;i<sels.length;i++){
        var els = document.querySelectorAll(sels[i]);
        for(var j=0;j<els.length;j++){
            var t = els[j].innerText.trim();
            if(t && t.length > 3) return t;
        }
    }
    return null;
})()
    """)


# ============================================================
# 主续期流程
# ============================================================

def do_renew(sb) -> None:
    print("🔄 打开续期页面...")
    sb.open(RENEW_URL)
    time.sleep(3)
    save_debug(sb, "renew_open")

    dismiss_privacy_modal(sb)
    save_debug(sb, "after_privacy")

    # ── Step 1：输入用户名 ────────────────────────────────────
    print("📝 等待用户名输入框...")
    try:
        sb.wait_for_element_visible('input#username', timeout=20)
    except Exception:
        print("❌ 用户名输入框加载失败")
        save_debug(sb, "no_username")
        send_tg("❌ 用户名输入框加载失败")
        return

    # 先点击输入框，再逐字符输入（更像真实人类）
    sb.click('input#username')
    human_delay(0.3, 0.6)
    sb.type('input#username', VELRIX_USERNAME)
    print(f"✅ 已输入用户名: {VELRIX_USERNAME}")
    human_delay(0.8, 1.5)   # 输入完等一会儿再点按钮

    # 点击 Continue（ActionChains + JS 双保险）
    print("🖱️  点击 Continue ...")
    continue_xpaths = [
        '//button[.//span[contains(text(),"Continue")]]',
        '//button[contains(normalize-space(),"Continue")]',
        '//span[@data-slot="label" and contains(text(),"Continue")]/..',
        'button[type="submit"]',
    ]
    if not click_button_human(sb, continue_xpaths):
        print("❌ Continue 按钮未找到")
        save_debug(sb, "no_continue")
        send_tg("❌ Continue 按钮未找到")
        return

    # 立刻保存点击后状态（关键调试点）
    time.sleep(2)
    save_debug(sb, "after_click_continue")

    # 检测是否有错误提示（用户名不存在等）
    err = get_page_error(sb)
    if err:
        print(f"❌ 点击 Continue 后页面报错: {err}")
        send_tg(f"❌ Continue 被拒绝: {err}")
        return

    # ── Step 2：等待页面切换到验证步骤 ───────────────────────
    print("⏳ 等待「Verify Your Email」页面渲染（最长 40s）...")
    if not wait_for_page_title(sb, "Verify", timeout=40):
        print("⚠️  未检测到 Verify 标题，继续尝试查找 PIN 框...")

    save_debug(sb, "after_continue")

    # 等待 PIN 输入框（支持多种 selector）
    pin_sel = None
    for sel in [
        'input[autocomplete="one-time-code"]',
        'input[aria-label*="pin"]',
        'input[aria-label*="Pin"]',
        'input[aria-label*="OTP"]',
        'input[inputmode="numeric"]',
    ]:
        try:
            sb.wait_for_element_visible(sel, timeout=5)
            pin_sel = sel
            print(f"✅ PIN 输入框已找到: {sel}")
            break
        except Exception:
            continue

    if not pin_sel:
        print("❌ PIN 输入框加载失败")
        save_debug(sb, "no_pin")
        send_tg("❌ PIN 输入框加载失败")
        return

    print("✅ 验证码输入框已出现，开始获取 OTP ...")
    save_debug(sb, "pin_ready")

    try:
        code = fetch_otp_from_gmail(wait_seconds=90)
    except TimeoutError as e:
        print(e)
        save_debug(sb, "otp_timeout")
        send_tg("❌ Gmail OTP 获取超时")
        return

    # ── 填入验证码 ────────────────────────────────────────────
    print(f"⌨️  填入验证码: {code}")
    for i, char in enumerate(code):
        js = """
(function() {
    var inputs = document.querySelectorAll('input[aria-label*="pin"], input[autocomplete="one-time-code"]');
    // 如果是单个大输入框，直接整体填
    if (inputs.length === 1) {
        var inp = inputs[0];
        var nativeSetter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value'
        ).set;
        nativeSetter.call(inp, 'FULL_CODE');
        inp.dispatchEvent(new Event('input',  { bubbles: true }));
        inp.dispatchEvent(new Event('change', { bubbles: true }));
        return 'single-input';
    }
    // 多个小格子逐位填
    var inp = inputs[INDEX];
    if (!inp) return 'no-input-INDEX';
    var nativeSetter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value'
    ).set;
    nativeSetter.call(inp, 'CHAR');
    inp.dispatchEvent(new Event('input',  { bubbles: true }));
    inp.dispatchEvent(new Event('change', { bubbles: true }));
    inp.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'CHAR' }));
    inp.dispatchEvent(new KeyboardEvent('keyup',   { bubbles: true, key: 'CHAR' }));
    return 'multi-input-INDEX';
})()
        """.replace("FULL_CODE", code).replace("INDEX", str(i)).replace("CHAR", char)
        result = sb.execute_script(js)
        # 如果是单输入框，只需执行一次
        if result and result.startswith("single"):
            print("✅ 单输入框模式，整体填入完成")
            break
        time.sleep(random.uniform(0.1, 0.2))

    print("✅ 验证码已填入")
    human_delay(0.5, 1.0)
    save_debug(sb, "code_filled")

    # 点击 Verify Code
    print("🚀 点击 Verify Code ...")
    verify_xpaths = [
        '//button[.//span[contains(text(),"Verify Code")]]',
        '//button[contains(normalize-space(),"Verify Code")]',
        '//span[@data-slot="label" and contains(text(),"Verify Code")]/..',
        'button[type="submit"]',
    ]
    if not click_button_human(sb, verify_xpaths):
        print("❌ Verify Code 按钮未找到")
        save_debug(sb, "no_verify")
        send_tg("❌ Verify Code 按钮未找到")
        return

    # ── Step 3：等待续期结果 ──────────────────────────────────
    print("⏳ 等待续期结果...")
    due_date  = None
    succeeded = False

    for _ in range(60):
        time.sleep(0.5)
        try:
            desc_text = sb.execute_script("""
(function() {
    var els = document.querySelectorAll('div[data-slot="description"], p, span, div');
    for (var i = 0; i < els.length; i++) {
        var t = els[i].innerText || '';
        if (t.indexOf('renewed successfully') !== -1 ||
            t.indexOf('renewal successful') !== -1 ||
            t.indexOf('Next renewal') !== -1) {
            return t.trim();
        }
    }
    return null;
})()
            """)
            if desc_text:
                print(f"🎉 续期成功消息: {desc_text}")
                date_match = re.search(r'Next renewal due[:\s]+(\S+)', desc_text)
                due_date   = date_match.group(1) if date_match else desc_text.strip()
                succeeded  = True
                break
        except Exception:
            continue

    save_debug(sb, "result")

    if succeeded:
        print(f"✅ 续期完成，下次续期时间: {due_date}")
        send_tg("✅ 续期成功", due_date)
    else:
        error_msg = get_page_error(sb) or "（无法读取页面状态）"
        print(f"❌ 未检测到成功消息，页面提示: {error_msg}")
        send_tg(f"❌ 续期失败：{error_msg}")


# ============================================================
# 主入口
# ============================================================

def run_script():
    print("🔧 启动浏览器...")
    with SB(uc=True, test=True, proxy=LOCAL_PROXY) as sb:
        print("🚀 浏览器就绪！")
        print("🌐 验证出口 IP ...")
        try:
            sb.open("https://api.ipify.org/?format=json")
            ip_text = sb.get_text('body')
            ip_text = re.sub(r'(\d+\.\d+\.\d+\.)\d+', r'\1**', ip_text)
            print(f"✅ 出口 IP：{ip_text}")
        except Exception:
            print("⚠️  IP 验证超时，跳过")
        do_renew(sb)


if __name__ == "__main__":
    run_script()
