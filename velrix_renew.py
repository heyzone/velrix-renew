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
from selenium.webdriver.common.by import By

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

def calc_remaining(due_date_str: str) -> str:
    """根据到期日字符串计算剩余时间，格式如 '2026/3/25'"""
    import datetime
    try:
        due = datetime.datetime.strptime(due_date_str.strip(), "%Y/%m/%d")
        delta = due - datetime.datetime.now()
        total_seconds = int(delta.total_seconds())
        if total_seconds <= 0:
            return "已到期"
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60
        if days > 0:
            return f"{days} day{'s' if days > 1 else ''} {hours}h {minutes}m"
        elif hours > 0:
            return f"{hours}h {minutes}m"
        else:
            return f"{minutes}m"
    except Exception:
        return due_date_str

def send_tg(result: str, due_date: str = None):
    remaining = calc_remaining(due_date) if due_date else None

    lines = [
        "🎮 Velrix 服务器续期通知",
        f"🕐 运行时间: {now_str()}",
        f"🖥 服务器: {VELRIX_USERNAME}",
        f"📊 续期结果: {result}",
    ]
    if due_date:
        lines.append(f"📅 下次到期: {due_date}")
    if remaining:
        lines.append(f"⏱️ 剩余时间: {remaining}")

    msg = "\n".join(lines)

    if not TG_TOKEN or not TG_CHAT_ID:
        print("⚠️  Telegram 未配置，跳过推送")
        return
    url  = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": TG_CHAT_ID, "text": msg}).encode()
    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=15):
            print("📨 TG推送成功")
    except Exception as e:
        print(f"⚠️  TG推送失败: {e}")

def save_debug(sb, tag: str):
    try:
        sb.save_screenshot(f"velrix_{tag}.png")
        with open(f"velrix_{tag}.html", "w", encoding="utf-8") as f:
            f.write(sb.get_page_source())
        print(f"📸 快照已保存: velrix_{tag}.png / .html")
    except Exception:
        pass

def human_delay(lo=0.6, hi=1.4):
    time.sleep(random.uniform(lo, hi))

def js_mouse_click(sb, selector):
    if selector.startswith("//") or selector.startswith("(//"):
        js = """
(function(){
    var result = document.evaluate(XPATH, document, null,
        XPathResult.FIRST_ORDERED_NODE_TYPE, null);
    var el = result.singleNodeValue;
    if (!el) return 'not-found';
    el.dispatchEvent(new MouseEvent('mouseover', {bubbles:true}));
    el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
    el.dispatchEvent(new MouseEvent('mouseup',   {bubbles:true}));
    el.dispatchEvent(new MouseEvent('click',     {bubbles:true, cancelable:true}));
    return 'clicked';
})()
        """.replace("XPATH", repr(selector))
    else:
        js = """
(function(){
    var el = document.querySelector(CSS);
    if (!el) return 'not-found';
    el.dispatchEvent(new MouseEvent('mouseover', {bubbles:true}));
    el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
    el.dispatchEvent(new MouseEvent('mouseup',   {bubbles:true}));
    el.dispatchEvent(new MouseEvent('click',     {bubbles:true, cancelable:true}));
    return 'clicked';
})()
        """.replace("CSS", repr(selector))
    return sb.execute_script(js)

# ============================================================
# IMAP 逻辑
# ============================================================

def init_mail_client():
    """建立连接并返回已选定文件夹的 mail 对象和 seen_uids 基线"""
    try:
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
                    break

        folders = ["INBOX"] + ([spam_folder] if spam_folder else [])
        baselines = {}

        for f in folders:
            status, _ = mail.select(f)
            if status == "OK":
                _, data = mail.uid("search", None, "ALL")
                baselines[f] = set(data[0].split())

        print("📬 连接Gmail，等待验证码邮件...")
        if spam_folder:
            print("🗑️  检查Gmail垃圾邮箱")
        return mail, baselines, folders
    except Exception as e:
        print(f"❌ 邮箱连接失败: {e}")
        return None, None, None

def poll_for_otp(mail, baselines, folders, wait_seconds=120):
    """
    轮询新邮件，返回:
      ("otp",  "G78JZT")  — 获取到验证码
      ("skip", None)      — 未到续期时间
      ("fail", None)      — 超时未收到邮件
    """
    if not mail:
        return "fail", None

    print(f"📨 等待OTP邮件，超时 {wait_seconds}s ...")
    deadline = time.time() + wait_seconds

    while time.time() < deadline:
        time.sleep(4)
        for f in folders:
            try:
                mail.select(f)
                _, data = mail.uid("search", None, "ALL")
                current_uids = set(data[0].split())
                new_uids = current_uids - baselines[f]

                if not new_uids:
                    continue

                for uid in new_uids:
                    _, msg_data = mail.uid("fetch", uid, "(RFC822)")
                    msg = email.message_from_bytes(msg_data[0][1])

                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                                break
                    else:
                        body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")

                    # ① 检测未到续期时间
                    if "You recently renewed your server" in body or \
                       "Renewals are limited to once every 24 hours" in body:
                        print("⏰ 未到续期时间，跳过本次续期")
                        return "skip", None

                    # ② 精确提取验证码：Your verification code is: G78JZT
                    otp_match = re.search(
                        r'Your verification code is:\s*([A-Z0-9]{6})',
                        body, re.IGNORECASE
                    )
                    if not otp_match:
                        # 兜底：匹配独立的 6 位字母数字
                        otp_match = re.search(r'\b([A-Z0-9]{6})\b', body, re.IGNORECASE)

                    if otp_match:
                        otp = otp_match.group(1).upper()
                        print(f"✅ Gmail OTP: {otp}")
                        return "otp", otp

            except Exception as e:
                print(f"⚠️  邮件轮询异常: {e}")

    return "fail", None

# ============================================================
# UI 交互辅助
# ============================================================

def dismiss_privacy_modal(sb):
    for _ in range(5):
        if sb.execute_script('return !!document.querySelector(\'[role="dialog"]\');'):
            sb.execute_script("""
                var b = document.querySelectorAll('button');
                for(var i=0; i<b.length; i++) {
                    if(b[i].innerText.includes('Accept all')) b[i].click();
                }
            """)
            time.sleep(1)
            break
        time.sleep(1)

def click_button_human(sb, selectors):
    """依次尝试多个选择器，优先 ActionChains，降级 JS 模拟点击"""
    for sel in selectors:
        try:
            if sb.is_element_visible(sel):
                el = sb.find_element(sel)
                ActionChains(sb.driver).move_to_element(el).click().perform()
                return True
        except Exception:
            pass
        result = js_mouse_click(sb, sel)
        if result == "clicked":
            return True
    return False

# ============================================================
# 主流程（以文档1顺序为基准，精准修复4处）
# ============================================================

def do_renew(sb):
    # 1. 打开续期页面
    print("🌐 打开续期页面...")
    sb.open(RENEW_URL)
    dismiss_privacy_modal(sb)

    # 2. 初始化邮箱监控（打开页面后、输入用户名前建立基线）
    mail_conn, baselines, folders = init_mail_client()

    # 3. 输入用户名
    print(f"🆔 输入用户名: {VELRIX_USERNAME}")
    sb.wait_for_element_visible('input#username', timeout=15)
    sb.type('input#username', VELRIX_USERNAME)
    human_delay()

    # 4. 点击 Continue —— 多策略确保按钮一定被点中
    print("🖱️  点击 Continue...")
    continue_selectors = [
        '//button[.//span[normalize-space()="Continue"]]',    # 精确匹配 span 文字
        '//button[contains(normalize-space(.), "Continue")]', # 宽松匹配按钮文字
        'button[type="submit"]',                              # CSS 兜底
    ]
    clicked = click_button_human(sb, continue_selectors)
    if not clicked:
        # 最终兜底：JS 遍历所有 button 找含 Continue 文字的
        sb.execute_script("""
            var btns = document.querySelectorAll('button');
            for (var i = 0; i < btns.length; i++) {
                if (btns[i].innerText.trim().includes('Continue')) {
                    btns[i].click(); break;
                }
            }
        """)
        print("⚠️  使用 JS 兜底点击 Continue")

    # 5. 轮询邮件获取 OTP
    mail_status, otp_code = poll_for_otp(mail_conn, baselines, folders, wait_seconds=120)
    if mail_conn:
        mail_conn.logout()

    # 未到续期时间
    if mail_status == "skip":
        send_tg("⏰ 未到续期时间，无需操作")
        return

    # OTP 获取失败
    if mail_status == "fail" or not otp_code:
        print("❌ 验证码获取超时，流程终止")
        save_debug(sb, "otp_fail")
        send_tg("❌ OTP 获取失败")
        return

    # 6. 等待 pin input 出现，逐格填写验证码
    print(f"⌨️  填入OTP: {otp_code}")
    try:
        sb.wait_for_element_visible('input[aria-label="pin input 1 of 6"]', timeout=20)
        pin_inputs = sb.find_elements('input[aria-label*="pin input"]')
        if not pin_inputs:
            pin_inputs = sb.find_elements('input[autocomplete="one-time-code"]')
        # 过滤掉 aria-hidden 的聚合隐藏 input
        pin_inputs = [el for el in pin_inputs if el.get_attribute("aria-hidden") != "true"]

        if len(pin_inputs) >= 6:
            for i, char in enumerate(otp_code):
                pin_inputs[i].send_keys(char)
                time.sleep(0.12)
        else:
            sb.type('input[autocomplete="one-time-code"]', otp_code)

        print("✅ OTP已填入")
    except Exception as e:
        print(f"❌ 验证码填写失败: {e}")
        save_debug(sb, "input_error")
        send_tg("❌ 验证码填写失败")
        return

    # 7. 点击 Verify Code
    print("🚀 点击 Verify Code...")
    verify_selectors = [
        '//button[.//span[normalize-space()="Verify Code"]]',
        '//button[contains(normalize-space(.), "Verify Code")]',
        'button[type="submit"]',
    ]
    clicked = click_button_human(sb, verify_selectors)
    if not clicked:
        sb.execute_script("""
            var btns = document.querySelectorAll('button');
            for (var i = 0; i < btns.length; i++) {
                if (btns[i].innerText.trim().includes('Verify Code')) {
                    btns[i].click(); break;
                }
            }
        """)
        print("⚠️  使用 JS 兜底点击 Verify Code")

    # 8. 精确等待 div[data-slot="description"] 出现并读取结果
    #    成功内容示例: "Server renewed successfully Next renewal due: 2026/3/25"
    print("⏳ 等待续期结果...")
    due_date = None
    succeeded = False

    for _ in range(30):
        try:
            desc_el = sb.find_element('div[data-slot="description"]')
            desc_text = desc_el.text.strip()
            if not desc_text:
                time.sleep(1)
                continue

            print(f"📋 页面返回: {desc_text}")

            if "successfully" in desc_text.lower():
                m = re.search(r'(\d{4}/\d{1,2}/\d{1,2})', desc_text)
                if m:
                    due_date = m.group(1)
                succeeded = True
                break

            # 冷却期 / 错误提示
            if any(kw in desc_text.lower() for kw in
                   ["renew again", "limited", "error", "failed", "invalid", "recently renewed"]):
                print("⚠️  页面返回失败提示，终止")
                save_debug(sb, "renew_fail")
                send_tg(f"❌ 续期失败：{desc_text[:80]}")
                return

        except Exception:
            pass
        time.sleep(1)

    if succeeded:
        remaining = calc_remaining(due_date) if due_date else "未知"
        print(f"✅ 续期成功！下次到期: {due_date}，剩余: {remaining}")
        send_tg("✅ 续期成功！", due_date)
    else:
        print("❌ 未检测到续期成功状态，请检查快照")
        save_debug(sb, "result_unknown")
        send_tg("❌ 续期结果未知，请人工核查")

def run_script():
    print("🔧 启动浏览器...")
    with SB(uc=True, test=True, proxy=LOCAL_PROXY) as sb:
        print("🚀 浏览器就绪！")
        do_renew(sb)

if __name__ == "__main__":
    run_script()
