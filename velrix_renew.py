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
    try:
        sb.save_screenshot(f"velrix_{tag}.png")
        with open(f"velrix_{tag}.html", "w", encoding="utf-8") as f:
            f.write(sb.get_page_source())
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
# IMAP 逻辑：分为初始化和获取两步
# ============================================================

def init_mail_client():
    """建立连接并返回已选定文件夹的 mail 对象和 seen_uids 基线"""
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(GMAIL_ADDRESS, GMAIL_PASSWORD)
        
        # 识别垃圾箱
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
                print(f"📂 {f} 监控初始化，已有邮件: {len(baselines[f])} 封")
        
        return mail, baselines, folders
    except Exception as e:
        print(f"❌ IMAP 初始化失败: {e}")
        return None, None, None

def poll_for_otp(mail, baselines, folders, wait_seconds=90):
    """在现有连接基础上轮询新邮件"""
    if not mail: return None
    print(f"📬 正在监控新邮件 (限时 {wait_seconds}s)...")
    deadline = time.time() + wait_seconds
    
    while time.time() < deadline:
        time.sleep(4)
        for f in folders:
            try:
                mail.select(f)
                _, data = mail.uid("search", None, "ALL")
                current_uids = set(data[0].split())
                new_uids = current_uids - baselines[f]
                
                if new_uids:
                    print(f"📩 {f} 发现新邮件，解析中...")
                    for uid in new_uids:
                        _, msg_data = mail.uid("fetch", uid, "(RFC822)")
                        msg = email.message_from_bytes(msg_data[0][1])
                        
                        # 提取正文
                        body = ""
                        if msg.is_multipart():
                            for part in msg.walk():
                                if part.get_content_type() == "text/plain":
                                    body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                                    break
                        else:
                            body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
                        
                        # 正则匹配 6 位字母数字验证码
                        otp_match = re.search(r'code is:\s*([A-Z0-9]{6})', body, re.IGNORECASE)
                        if not otp_match:
                            otp_match = re.search(r'\b([A-Z0-9]{6})\b', body, re.IGNORECASE)
                            
                        if otp_match:
                            otp = otp_match.group(1).upper()
                            print(f"✅ 成功抓取 OTP: {otp}")
                            return otp
            except Exception as e:
                print(f"⚠️ 轮询出错: {e}")
    return None

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

def click_button_human(sb, xpaths):
    for sel in xpaths:
        try:
            if sb.is_element_visible(sel):
                el = sb.find_element(sel)
                ActionChains(sb.driver).move_to_element(el).click().perform()
                return True
        except Exception:
            if js_mouse_click(sb, sel) == "clicked": return True
    return False

# ============================================================
# 主流程
# ============================================================

def do_renew(sb):
    # 1. 预先建立邮箱连接，标记基线
    mail_conn, baselines, folders = init_mail_client()

    print("🔄 打开续期页面...")
    sb.open(RENEW_URL)
    dismiss_privacy_modal(sb)

    print("📝 输入用户名...")
    sb.wait_for_element_visible('input#username', timeout=15)
    sb.type('input#username', VELRIX_USERNAME)
    
    # 2. 点击 Continue (此操作后邮件会发出)
    print("🖱️  点击 Continue ...")
    continue_btn = ['//button[contains(., "Continue")]', 'button[type="submit"]']
    click_button_human(sb, continue_btn)

    # 3. 立即开始轮询邮件 (与页面加载同步进行)
    otp_code = poll_for_otp(mail_conn, baselines, folders, wait_seconds=120)
    if mail_conn: mail_conn.logout()

    if not otp_code:
        print("❌ 未能获取到 OTP")
        save_debug(sb, "otp_fail")
        send_tg("❌ OTP 获取失败")
        return

    # 4. 填写验证码
    print(f"⌨️  填入验证码: {otp_code}")
    try:
        sb.wait_for_element_visible('input[autocomplete="one-time-code"]', timeout=20)
        pin_inputs = sb.find_elements('input[aria-label*="pin input"]')
        if not pin_inputs:
            pin_inputs = sb.find_elements('input[autocomplete="one-time-code"]')
        
        if len(pin_inputs) >= 6:
            for i, char in enumerate(otp_code):
                pin_inputs[i].send_keys(char)
                time.sleep(0.1)
        else:
            # 兜底单框输入
            sb.type('input[autocomplete="one-time-code"]', otp_code)
        
        print("🚀 点击 Verify Code ...")
        verify_btn = ['//button[contains(., "Verify Code")]', 'button[type="submit"]']
        click_button_human(sb, verify_btn)
    except Exception as e:
        print(f"❌ 填写验证码环节出错: {e}")
        save_debug(sb, "input_error")
        return

    # 5. 等待成功
    print("⏳ 等待续期结果...")
    succeeded = False
    for _ in range(30):
        if sb.is_text_visible("successfully", "div[data-slot='description'], p"):
            msg = sb.get_text("div[data-slot='description'], p")
            print(f"🎉 {msg}")
            send_tg("✅ 续期成功", msg)
            succeeded = True
            break
        time.sleep(1)
    
    if not succeeded:
        print("❌ 未检测到成功状态")
        send_tg("❌ 续期结果未知")

def run_script():
    print("🔧 启动浏览器 (UC Mode)...")
    with SB(uc=True, test=True, proxy=LOCAL_PROXY) as sb:
        do_renew(sb)

if __name__ == "__main__":
    run_script()
