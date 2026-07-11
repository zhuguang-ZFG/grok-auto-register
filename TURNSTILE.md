# Turnstile / 弹窗挑战说明

## 本机结论（Chrome 150 + DrissionPage）

1. **社区 `turnstilePatch` 扩展不可靠**  
   Chrome 137+ 起 `--load-extension` 逐步失效，Chrome 150 实测扩展加载后 `MouseEvent.screenX/Y` 仍为原生 getter（常为 0）。

2. **真正有效的是 `add_init_js`**  
   在每个标签页导航前注入 `MouseEvent.prototype.screenX/Y` 补丁（见 `BrowserSession.STEALTH_INIT_JS`）。

3. **代理必须可用**  
   浏览器与 HTTP 请求走同一代理探测逻辑（`resolve_browser_proxy`）。  
   配置端口挂掉时会自动探测 `7897/7890/6152/...`。

## 推荐配置

```json
{
  "proxy": "http://127.0.0.1:7897",
  "browser_proxy": "http://127.0.0.1:7897",
  "stealth_patch": true,
  "hide_window": false,
  "block_media_fonts": false
}
```

弹窗挑战失败时，优先：

1. 确认本地代理端口通
2. `hide_window=false`（有头窗口）
3. `block_media_fonts=false`
4. `stealth_patch=true`

## 相关代码

- `create_browser_options` / `resolve_browser_proxy`
- `BrowserSession._apply_stealth_patch`
- `getTurnstileToken` / `_click_turnstile_checkbox`
- `dismiss_cookie_banner`
- `oidc_mint/browser_confirm.py`（铸造路径同样注入 init_js）
