# Velrix 自动续期
自动登录 velrix.net，每24小时续期服务器。
流程：填写用户名 → 获取邮件 OTP → 验证 → TG 通知
---
## Secrets 配置
| Secret | 格式 | 说明 | 必填 |
|--------|------|------|------|
| `VELRIX_ACCOUNT` | `username,gmail,app_password` | 账号 + Gmail 应用专用密码 | ✅ |
| `TG_BOT` | `chat_id,bot_token` | Telegram 通知 | ⬜ |
| `GOST_PROXY` | `socks5://user:pass@host:port` | 代理 | ⬜ |
