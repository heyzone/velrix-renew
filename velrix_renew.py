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
# IMAP 逻辑（修复版）
# ============================================================

def _imap_select(mail, folder):
    """安全 select：含斜杠的文件夹名加引号"""
    folder_quoted = f'"{folder}"' if "/" in folder else folder
    return mail.select(folder_quoted)

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
                # 提取最后一段（文件夹名），保留原始大小写和斜杠
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
        print(f"📊 各文件夹基线UID数: { {k: len(v) for k, v in baselines.items()} }")
        if spam_folder:
            print(f"🗑️  同时检查垃圾邮箱: {spam_folder}")
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
                status, _ = _imap_select(mail, f)
                if status != "OK":
                    continue

                _, data = mail.uid("search", None, "ALL")
                current_uids = set(data[0].split())
                new_uids = current_uids - baselines.get(f, set())

                if not new_uids:
                    continue

                print(f"📩 [{f}] 发现 {len(new_uids)} 封新邮件，开始解析...")

                for uid in new_uids:
                    _, msg_data = mail.uid("fetch", uid, "(RFC822)")
                    msg = email.message_from_bytes(msg_data[0][1])

                    subject = msg.get("Subject", "")
                    sender  = msg.get("From", "")
                    print(f"  📧 发件人: {sender} | 主题: {subject}")

                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                                break
                        # 纯文本为空时尝试 HTML
                        if not body:
                            for part in msg.walk():
                                if part.get_content_type() == "text/html":
                                    body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                                    break
                    else:
                        body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")

                    # 去除 HTML 标签（简单处理）
                    body_plain = re.sub(r'<[^>]+>', ' ', body)

                    # ① 检测未到续期时间
                    if "You recently renewed your server" in body_plain or \
                       "Renewals are limited to once every 24 hours" in body_plain:
                        print("⏰ 未到续期时间，跳过本次续期")
                        return "skip", None

                    # ② 精确提取验证码：Your verification code is: G78JZT
                    otp_match = re.search(
                        r'Your verification code is:\s*([A-Z0-9]{6})',
                        body_plain, re.IGNORECASE
                    )
                    if otp_match:
                        otp = otp_match.group(1).upper()
                        print(f"✅ Gmail OTP (精确匹配): {otp}")
                        return "otp", otp

                    # ③ 宽松兜底：在含关键词的行附近匹配 6 位码
                    for line in body_plain.splitlines():
                        if any(kw in line.lower() for kw in ["code", "verify", "otp", "token", "verification"]):
                            loose = re.search(r'\b([A-Z0-9]{6})\b', line, re.IGNORECASE)
                            if loose:
                                otp = loose.group(1).upper()
                                print(f"✅ Gmail OTP (宽松匹配): {otp} | 来源行: {line.strip()[:80]}")
                                return "otp", otp

            except Exception as e:
                print(f"⚠️  邮件轮询异常 [{f}]: {e}")

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
    for sel in selectors:
        try:
            sb.click(sel, timeout=2)
            print(f"✅ 点击成功: {sel}")
            return True
        except Exception:
            try:
                if sb.is_element_visible(sel):
                    el = sb.find_element(sel)
                    ActionChains(sb.driver).move_to_element(el).click().perform()
                    print(f"✅ ActionChains 点击成功: {sel}")
                    return True
            except Exception:
                pass

        result = js_mouse_click(sb, sel)
        if result == "clicked":
            print(f"✅ JS 点击成功: {sel}")
            return True
    return False

def wait_for_otp_input(sb, timeout=15):
    """等待 OTP 输入框出现，返回 True/False"""
    try:
        sb.wait_for_element_visible('input[autocomplete="one-time-code"]', timeout=timeout)
        return True
    except Exception:
        return False

# ============================================================
# 主流程
# ============================================================

def do_renew(sb):
    # 【修复】先建立邮件基线，再打开页面，避免竞态
    print("📬 预先建立邮件基线...")
    mail_conn, baselines, folders = init_mail_client()

    # 1. 打开续期页面
    print("🌐 打开续期页面...")
    sb.open(RENEW_URL)
    dismiss_privacy_modal(sb)
    time.sleep(2)
    save_debug(sb, "page_loaded")

    # 2. 输入用户名
    print(f"🆔 输入用户名: {VELRIX_USERNAME}")
    sb.wait_for_element_visible('input#username', timeout=15)
    sb.type('input#username', VELRIX_USERNAME)
    human_delay()

    # 3. 点击 Continue —— 多策略
    print("🖱️  点击 Continue...")

    # 策略一：回车键
    try:
        sb.send_keys('input#username', '\n')
        print("  ↳ 已发送回车键")
    except Exception as e:
        print(f"  ↳ 回车键失败: {e}")

    # 等待页面响应
    time.sleep(3)
    save_debug(sb, "after_continue")

    # 检查 OTP 输入框是否已出现
    if not wait_for_otp_input(sb, timeout=5):
        print("  ↳ 回车键未生效，尝试点击按钮...")
        continue_selectors = [
            'button:contains("Continue")',
            '//button[contains(normalize-space(.), "Continue")]',
            '//button[.//span[contains(text(), "Continue")]]',
            'button[type="submit"]',
        ]
        clicked = click_button_human(sb, continue_selectors)

        if not clicked:
            sb.execute_script("""
                var btns = document.querySelectorAll('button');
                for (var i = 0; i < btns.length; i++) {
                    if (btns[i].innerText.trim().includes('Continue')) {
                        btns[i].click(); break;
                    }
                }
            """)
            print("  ↳ JS 兜底点击 Continue")

        time.sleep(3)
        save_debug(sb, "after_continue_btn")

        if not wait_for_otp_input(sb, timeout=10):
            print("❌ Continue 后未出现 OTP 输入框，请检查快照")
            save_debug(sb, "continue_failed")
            if mail_conn:
                mail_conn.logout()
            send_tg("❌ Continue 步骤失败，OTP 输入框未出现")
            return
    else:
        print("✅ OTP 输入框已出现")

    # 4. 轮询邮件获取 OTP
    mail_status, otp_code = poll_for_otp(mail_conn, baselines, folders, wait_seconds=120)
    if mail_conn:
        try:
            mail_conn.logout()
        except Exception:
            pass

    if mail_status == "skip":
        send_tg("⏰ 未到续期时间，无需操作")
        return

    if mail_status == "fail" or not otp_code:
        print("❌ 验证码获取超时，流程终止")
        save_debug(sb, "otp_fail")
        send_tg("❌ OTP 获取失败")
        return

    # 5. 填写验证码
    print(f"⌨️  填入OTP: {otp_code}")
    try:
        sb.wait_for_element_visible('input[autocomplete="one-time-code"]', timeout=20)
        pin_inputs = sb.find_elements('input[aria-label*="pin input"]')
        if not pin_inputs:
            pin_inputs = sb.find_elements('input[autocomplete="one-time-code"]')

        # 过滤掉 aria-hidden 的聚合输入框
        pin_inputs = [el for el in pin_inputs if el.get_attribute("aria-hidden") != "true"]
        print(f"🔢 有效 pin inputs 数量: {len(pin_inputs)}")

        if len(pin_inputs) >= 6:
            for i, char in enumerate(otp_code):
                pin_inputs[i].send_keys(char)
                time.sleep(0.15)
        else:
            sb.type('input[autocomplete="one-time-code"]', otp_code)

        print("✅ OTP已填入")
        save_debug(sb, "otp_filled")
    except Exception as e:
        print(f"❌ 验证码填写失败: {e}")
        save_debug(sb, "input_error")
        send_tg("❌ 验证码填写失败")
        return

    # 6. 点击 Verify Code
    print("🚀 点击 Verify Code...")
    verify_selectors = [
        'button:contains("Verify Code")',
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
        print("  ↳ JS 兜底点击 Verify Code")

    # 7. 等待续期结果
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
