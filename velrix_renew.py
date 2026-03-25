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

def _imap_select(mail, folder):
    folder_quoted = f'"{folder}"' if "/" in folder else folder
    return mail.select(folder_quoted)

def init_mail_client():
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(GMAIL_ADDRESS, GMAIL_PASSWORD)

        spam_folder = None
        _, folder_list = mail.list()
        for f in folder_list:
            decoded = f.decode("utf-8", errors="ignore")
            if any(k in decoded for k in ["Spam", "Junk", "垃圾", "spam", "junk"]):
                match = re.search(r'"([^"]+)"\s*$', decoded)
                if not match:
                    match = re.search(r'(\S+)\s*$', decoded)
                if match:
                    spam_folder = match.group(1).strip('"')
                    break

        folders = ["INBOX"] + ([spam_folder] if spam_folder else [])
        baselines = {}

        for f in folders:
            status, _ = _imap_select(mail, f)
            if status == "OK":
                _, data = mail.uid("search", None, "ALL")
                baselines[f] = set(data[0].split())
            else:
                print(f"⚠️  无法选择文件夹: {f}")

        print(f"📬 连接Gmail成功，监控文件夹: {folders}")
        return mail, baselines, folders
    except Exception as e:
        print(f"❌ 邮箱连接失败: {e}")
        return None, None, None

def poll_for_otp(sb, mail, baselines, folders, wait_seconds=120):
    """
    轮询新邮件，含 Resend 逻辑
    """
    if not mail:
        return "fail", None

    max_resends = 2
    resend_count = 0

    while resend_count <= max_resends:
        print(f"📨 [{resend_count}/{max_resends}] 等待OTP邮件，超时 {wait_seconds}s ...")
        deadline = time.time() + wait_seconds
        found_otp = False

        while time.time() < deadline:
            time.sleep(5)
            for f in folders:
                try:
                    status, _ = _imap_select(mail, f)
                    if status != "OK": continue
                    _, data = mail.uid("search", None, "ALL")
                    current_uids = set(data[0].split())
                    new_uids = current_uids - baselines.get(f, set())

                    if not new_uids: continue

                    for uid in new_uids:
                        _, msg_data = mail.uid("fetch", uid, "(RFC822)")
                        msg = email.message_from_bytes(msg_data[0][1])
                        body = ""
                        if msg.is_multipart():
                            for part in msg.walk():
                                if part.get_content_type() in ["text/plain", "text/html"]:
                                    body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                                    break
                        else:
                            body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")

                        body_plain = re.sub(r'<[^>]+>', ' ', body)

                        if "You recently renewed your server" in body_plain or \
                           "Renewals are limited to once every 24 hours" in body_plain:
                            return "skip", None

                        otp_match = re.search(r'Your verification code is:\s*([A-Z0-9]{6})', body_plain, re.IGNORECASE)
                        if otp_match:
                            return "otp", otp_match.group(1).upper()
                        
                        # 宽松匹配
                        loose = re.search(r'\b([A-Z0-9]{6})\b', body_plain)
                        if loose and any(kw in body_plain.lower() for kw in ["code", "verify", "otp"]):
                            return "otp", loose.group(1).upper()

                except Exception as e:
                    print(f"⚠️  邮件读取异常: {e}")

        # 如果走到这里说明当前轮次超时了
        if resend_count < max_resends:
            resend_count += 1
            print(f"⏳ 超时未收到邮件，尝试点击 Resend (第 {resend_count} 次)...")
            resend_selectors = [
                'button:contains("Resend")',
                '//button[contains(text(), "Resend")]',
                'span:contains("Resend")'
            ]
            if click_button_human(sb, resend_selectors):
                time.sleep(5) # 等待网页请求发出
                # 更新基线，防止把上一轮可能延迟到达的旧邮件当成新邮件
                for f in folders:
                    status, _ = _imap_select(mail, f)
                    if status == "OK":
                        _, data = mail.uid("search", None, "ALL")
                        baselines[f] = set(data[0].split())
            else:
                print("⚠️  无法找到 Resend 按钮")
                break 
        else:
            break

    return "fail", None

# ============================================================
# UI 交互辅助
# ============================================================

def dismiss_privacy_modal(sb):
    for _ in range(3):
        try:
            sb.execute_script("""
                var b = document.querySelectorAll('button');
                for(var i=0; i<b.length; i++) {
                    if(b[i].innerText.includes('Accept all')) b[i].click();
                }
            """)
        except: pass
        time.sleep(1)

def click_button_human(sb, selectors):
    for sel in selectors:
        try:
            if sb.is_element_visible(sel):
                sb.click(sel, timeout=3)
                return True
        except: pass
        result = js_mouse_click(sb, sel)
        if result == "clicked": return True
    return False

def wait_for_otp_input(sb, timeout=15):
    try:
        sb.wait_for_element_visible('input[autocomplete="one-time-code"]', timeout=timeout)
        return True
    except:
        return False

# ============================================================
# 主流程
# ============================================================

def do_renew(sb):
    print("📬 预先建立邮件基线...")
    mail_conn, baselines, folders = init_mail_client()

    print("🌐 打开续期页面...")
    sb.open(RENEW_URL)
    dismiss_privacy_modal(sb)
    time.sleep(2)

    print(f"🆔 输入用户名: {VELRIX_USERNAME}")
    sb.wait_for_element_visible('input#username', timeout=15)
    sb.type('input#username', VELRIX_USERNAME)
    sb.send_keys('input#username', '\n')
    time.sleep(3)

    if not wait_for_otp_input(sb, timeout=10):
        print("🖱️  尝试点击 Continue 按钮...")
        click_button_human(sb, ['button:contains("Continue")', 'button[type="submit"]'])
        
    if not wait_for_otp_input(sb, timeout=15):
        print("❌ 无法进入 OTP 输入界面")
        save_debug(sb, "no_otp_box")
        if mail_conn: mail_conn.logout()
        return

    # 轮询邮件（含自动点击 Resend 逻辑）
    mail_status, otp_code = poll_for_otp(sb, mail_conn, baselines, folders, wait_seconds=120)
    
    if mail_conn:
        try: mail_conn.logout()
        except: pass

    if mail_status == "skip":
        send_tg("⏰ 未到续期时间，无需操作")
        return
    if mail_status == "fail" or not otp_code:
        save_debug(sb, "otp_timeout")
        send_tg("❌ 验证码获取超时（已重试Resend）")
        return

    print(f"⌨️  填入OTP: {otp_code}")
    try:
        pin_inputs = sb.find_elements('input[aria-label*="pin input"]')
        pin_inputs = [el for el in pin_inputs if el.get_attribute("aria-hidden") != "true"]
        if len(pin_inputs) >= 6:
            for i, char in enumerate(otp_code):
                pin_inputs[i].send_keys(char)
                time.sleep(0.1)
        else:
            sb.type('input[autocomplete="one-time-code"]', otp_code)
        
        human_delay()
        click_button_human(sb, ['button:contains("Verify Code")', 'button[type="submit"]'])
    except Exception as e:
        print(f"❌ 填入验证码失败: {e}")
        return

    # 检查结果
    print("⏳ 等待续期结果...")
    for _ in range(20):
        try:
            content = sb.get_page_source().lower()
            if "successfully" in content:
                print("✅ 续期成功！")
                send_tg("✅ 续期成功！")
                return
            if "limit" in content or "error" in content:
                send_tg("⚠️ 续期失败或已达到频率限制")
                return
        except: pass
        time.sleep(2)
    
    save_debug(sb, "final_check")
    send_tg("❓ 续期状态未知，请检查快照")

def run_script():
    with SB(uc=True, test=True, proxy=LOCAL_PROXY) as sb:
        do_renew(sb)

if __name__ == "__main__":
    run_script()
