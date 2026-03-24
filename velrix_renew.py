import os
import time
import imaplib
import email
import re
import urllib.request
import urllib.parse
from seleniumbase import SB

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
                all_uids  = set(data[0].split())
                new_uids  = all_uids - seen_uids[folder]

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
# JS 辅助：所有脚本必须是表达式或 IIFE，不能有裸 return
# ============================================================

# 点击 Accept all —— 纯表达式，无 return
_JS_CLICK_ACCEPT = """
(function() {
    var btns = document.querySelectorAll('button[data-slot="base"]');
    for (var i = 0; i < btns.length; i++) {
        var txt = btns[i].textContent.replace(/\\s+/g, ' ').trim();
        if (txt.indexOf('Accept all') !== -1) {
            btns[i].click();
            return 'clicked:' + txt;
        }
    }
    var all = document.querySelectorAll('button');
    for (var j = 0; j < all.length; j++) {
        var t = all[j].textContent.replace(/\\s+/g, ' ').trim();
        if (t.indexOf('Accept all') !== -1) {
            all[j].click();
            return 'fallback:' + t;
        }
    }
    return null;
})()
"""

# 强制移除弹窗 DOM —— 语句块，无需返回值
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

# 检测弹窗是否存在
_JS_HAS_MODAL = '(function(){ return !!document.querySelector(\'[role="dialog"]\'); })()'


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

    # 兜底：强制移除弹窗 DOM
    print("⚠️  JS click 无效，强制移除弹窗 DOM ...")
    try:
        sb.execute_script(_JS_REMOVE_MODAL)
        print("✅ 弹窗 DOM 已强制移除")
        time.sleep(0.5)
    except Exception as e:
        print(f"⚠️  DOM 移除也失败: {e}")


# ============================================================
# 主续期流程
# ============================================================

def do_renew(sb) -> None:
    print("🔄 打开续期页面...")
    sb.open(RENEW_URL)
    time.sleep(3)
    sb.save_screenshot("velrix_renew_open.png")

    dismiss_privacy_modal(sb)
    sb.save_screenshot("velrix_after_privacy.png")

    # ── Step 1：输入用户名 ────────────────────────────────────
    print("📝 等待用户名输入框...")
    try:
        sb.wait_for_element_visible('input#username', timeout=20)
    except Exception:
        print("❌ 用户名输入框加载失败")
        sb.save_screenshot("velrix_no_username.png")
        send_tg("❌ 用户名输入框加载失败")
        return

    sb.type('input#username', VELRIX_USERNAME)
    print(f"✅ 已输入用户名: {VELRIX_USERNAME}")
    time.sleep(0.5)

    # 点击 Continue
    print("🖱️  点击 Continue ...")
    continue_clicked = False
    for sel in [
        '//button[.//span[contains(text(),"Continue")]]',
        '//button[contains(normalize-space(),"Continue")]',
        '//span[@data-slot="label" and contains(text(),"Continue")]/..',
        'button[type="submit"]',
    ]:
        try:
            if sb.is_element_visible(sel):
                sb.click(sel)
                continue_clicked = True
                print("✅ 已点击 Continue")
                break
        except Exception:
            continue

    if not continue_clicked:
        print("❌ Continue 按钮未找到")
        sb.save_screenshot("velrix_no_continue.png")
        send_tg("❌ Continue 按钮未找到")
        return

    # ── Step 2：等待 PIN 输入框 ───────────────────────────────
    print("⏳ 等待「Verify Your Email」页面渲染（最长 30s）...")
    verify_page_ready = False
    for _ in range(30):
        time.sleep(1)
        try:
            h2_text = sb.execute_script(
                "(function(){ var h2 = document.querySelector('h2'); return h2 ? h2.innerText : ''; })()"
            )
            if h2_text and "Verify" in h2_text:
                print(f"✅ 页面已切换到验证步骤: {h2_text}")
                verify_page_ready = True
                break
        except Exception:
            pass

    if not verify_page_ready:
        print("⚠️  未检测到 Verify 标题，继续尝试查找 PIN 框...")

    sb.save_screenshot("velrix_after_continue.png")

    try:
        sb.wait_for_element_visible('input[autocomplete="one-time-code"]', timeout=15)
        print("✅ PIN 输入框已找到")
    except Exception:
        print("❌ PIN 输入框加载失败，已保存截图和页面源码")
        sb.save_screenshot("velrix_no_pin.png")
        try:
            with open("velrix_no_pin_source.html", "w", encoding="utf-8") as f:
                f.write(sb.get_page_source())
        except Exception:
            pass
        send_tg("❌ PIN 输入框加载失败")
        return

    print("✅ 验证码输入框已出现，开始获取 OTP ...")
    sb.save_screenshot("velrix_pin_ready.png")

    try:
        code = fetch_otp_from_gmail(wait_seconds=90)
    except TimeoutError as e:
        print(e)
        sb.save_screenshot("velrix_otp_timeout.png")
        send_tg("❌ Gmail OTP 获取超时")
        return

    # 逐位填入验证码
    print(f"⌨️  填入验证码: {code}")
    for i, char in enumerate(code):
        js = """
        (function() {
            var inputs = document.querySelectorAll('input[aria-label^="pin input"]');
            var inp = inputs[INDEX];
            if (!inp) return;
            var nativeSetter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value'
            ).set;
            nativeSetter.call(inp, 'CHAR');
            inp.dispatchEvent(new Event('input',  { bubbles: true }));
            inp.dispatchEvent(new Event('change', { bubbles: true }));
        })()
        """.replace("INDEX", str(i)).replace("CHAR", char)
        sb.execute_script(js)
        time.sleep(0.15)

    print("✅ 验证码已填入")
    time.sleep(0.5)
    sb.save_screenshot("velrix_code_filled.png")

    # 点击 Verify Code
    print("🚀 点击 Verify Code ...")
    verify_clicked = False
    for sel in [
        '//button[.//span[contains(text(),"Verify Code")]]',
        '//button[contains(normalize-space(),"Verify Code")]',
        '//span[@data-slot="label" and contains(text(),"Verify Code")]/..',
        'button[type="submit"]',
    ]:
        try:
            if sb.is_element_visible(sel):
                sb.click(sel)
                verify_clicked = True
                print("✅ 已点击 Verify Code")
                break
        except Exception:
            continue

    if not verify_clicked:
        print("❌ Verify Code 按钮未找到")
        sb.save_screenshot("velrix_no_verify.png")
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
    var els = document.querySelectorAll('div[data-slot="description"]');
    for (var i = 0; i < els.length; i++) {
        if (els[i].innerText.indexOf('renewed successfully') !== -1) {
            return els[i].innerText;
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

    sb.save_screenshot("velrix_result.png")

    if succeeded:
        print(f"✅ 续期完成，下次续期时间: {due_date}")
        send_tg("✅ 续期成功", due_date)
    else:
        error_msg = sb.execute_script("""
(function() {
    var el = document.querySelector('[role="alert"], .error, [data-slot="description"]');
    return el ? el.innerText : '（无法读取页面状态）';
})()
        """) or "（无法读取页面状态）"
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
