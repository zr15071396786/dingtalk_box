/* main.js — 钉钉工具箱 前端逻辑
 *
 * 通信层：
 *   通过 window.dtbox.call(method, params) 与 sidecar 通信
 *   由 launcher (Tauri 替代层) 注入该全局
 *   进度事件通过 window.dtbox.onProgress(cb) 订阅
 */

(() => {
  "use strict";

  // ── 工具 ───────────────────────────────────────────────────────────
  const $ = (id) => document.getElementById(id);
  const fmtSize = (n) => {
    if (!n) return "0 B";
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / 1024 / 1024).toFixed(2)} MB`;
  };

  const today = () => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  };

  // ── 自定义玻璃日历弹窗 ──────────────────────────────────────
  const dateModal = $("date-modal");
  const dateDisplay = $("date-display");
  const dateDisplayText = $("date-display-text");
  const dateTitle = $("date-title");
  const dateGrid = $("date-grid");
  // 弹窗打开时显示的月份（初始 = 当前选中日期所在月）
  let dpViewYear, dpViewMonth;  // month: 0-11

  const WEEKDAYS_CN = ["一", "二", "三", "四", "五", "六", "日"];

  function fmtDateInput(y, m, d) {
    return `${y}-${String(m + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
  }
  function fmtDateDisplay(y, m, d) {
    return `${y}/${String(m + 1).padStart(2, "0")}/${String(d).padStart(2, "0")}`;
  }
  function fmtDateTitle(y, m) {
    return `${y}年${String(m + 1).padStart(2, "0")}月`;
  }
  function parseDateInput(s) {
    if (!s) return null;
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
    if (!m) return null;
    return { y: +m[1], m: +m[2] - 1, d: +m[3] };
  }

  function renderDateGrid() {
    const selected = parseDateInput($("date-input").value);
    const todayParts = (() => {
      const d = new Date();
      return { y: d.getFullYear(), m: d.getMonth(), d: d.getDate() };
    })();
    dateTitle.textContent = fmtDateTitle(dpViewYear, dpViewMonth);

    // 当月第一天
    const first = new Date(dpViewYear, dpViewMonth, 1);
    // getDay: 0=Sun, 1=Mon ... → 我们的「周一开头」= (getDay + 6) % 7
    const firstWeekday = (first.getDay() + 6) % 7;
    const lastDay = new Date(dpViewYear, dpViewMonth + 1, 0).getDate();

    const cells = [];
    // leading empty
    for (let i = 0; i < firstWeekday; i++) {
      cells.push(`<button type="button" class="date-day empty" disabled></button>`);
    }
    for (let d = 1; d <= lastDay; d++) {
      const weekday = (firstWeekday + d - 1) % 7;  // 0-6, 5=Sat 6=Sun
      const isWeekend = weekday >= 5;
      const isSelected = selected && selected.y === dpViewYear && selected.m === dpViewMonth && selected.d === d;
      const isToday = todayParts.y === dpViewYear && todayParts.m === dpViewMonth && todayParts.d === d;
      // v0.3.9：未来日期不可选（今天之后的日期，生成日报没数据）
      const isFuture = (dpViewYear > todayParts.y)
        || (dpViewYear === todayParts.y && dpViewMonth > todayParts.m)
        || (dpViewYear === todayParts.y && dpViewMonth === todayParts.m && d > todayParts.d);
      const classes = [
        "date-day",
        isWeekend ? "weekend" : "",
        isSelected ? "selected" : "",
        isToday ? "today" : "",
        isFuture ? "future" : "",
      ].filter(Boolean).join(" ");
      const disabled = isFuture ? "disabled" : "";
      cells.push(`<button type="button" class="${classes}" data-day="${d}" ${disabled}>${d}</button>`);
    }
    // trailing empty 把每行补齐到 7 的倍数
    while (cells.length % 7 !== 0) {
      cells.push(`<button type="button" class="date-day empty" disabled></button>`);
    }
    dateGrid.innerHTML = cells.join("");

    // 绑定日期点击（未来日期 disabled，不可点）
    dateGrid.querySelectorAll(".date-day:not(.empty):not(.future)").forEach((b) => {
      b.addEventListener("click", () => {
        const d = +b.dataset.day;
        const iso = fmtDateInput(dpViewYear, dpViewMonth, d);
        $("date-input").value = iso;
        dateDisplayText.textContent = fmtDateDisplay(dpViewYear, dpViewMonth, d);
        closeDateModal();
      });
    });
  }

  function openDateModal() {
    const sel = parseDateInput($("date-input").value);
    if (sel) {
      dpViewYear = sel.y;
      dpViewMonth = sel.m;
    } else {
      const d = new Date();
      dpViewYear = d.getFullYear();
      dpViewMonth = d.getMonth();
    }
    renderDateGrid();
    dateModal.classList.remove("hidden");
  }
  function closeDateModal() {
    dateModal.classList.add("hidden");
  }

  function syncDateDisplay() {
    const sel = parseDateInput($("date-input").value);
    if (sel) {
      dateDisplayText.textContent = fmtDateDisplay(sel.y, sel.m, sel.d);
    } else {
      dateDisplayText.textContent = today().replace(/-/g, "/");
    }
  }

  function toast(msg, kind = "ok", duration = 3000) {
    const el = $("toast");
    el.textContent = msg;
    el.className = "toast " + (kind === "ok" ? "" : kind);
    el.classList.remove("hidden");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.add("hidden"), duration);
  }

  /**
   * v0.3.7：自定义确认弹窗（替代浏览器原生 confirm()，UI 与项目一致）
   * @param {{title?:string, message:string, okText?:string, cancelText?:string, danger?:boolean}} opts
   * @returns {Promise<boolean>}
   */
  function confirmModal(opts) {
    const { title = "确认操作", message, okText = "确定", cancelText = "取消", danger = false } = opts || {};
    return new Promise((resolve) => {
      const modal = $("confirm-modal");
      const titleEl = $("confirm-title");
      const msgEl = $("confirm-message");
      const okBtn = $("btn-confirm-ok");
      const cancelBtn = $("btn-confirm-cancel");
      titleEl.textContent = title;
      msgEl.textContent = message;
      okBtn.textContent = okText;
      cancelBtn.textContent = cancelText;
      okBtn.className = danger ? "btn-danger" : "btn-primary";
      const close = (v) => {
        modal.classList.add("hidden");
        okBtn.removeEventListener("click", onOk);
        cancelBtn.removeEventListener("click", onCancel);
        modal.removeEventListener("click", onBackdrop);
        document.removeEventListener("keydown", onKey);
        resolve(v);
      };
      const onOk = () => close(true);
      const onCancel = () => close(false);
      const onBackdrop = (e) => { if (e.target === modal) onCancel(); };
      const onKey = (e) => {
        if (e.key === "Escape") onCancel();
        else if (e.key === "Enter") onOk();
      };
      okBtn.addEventListener("click", onOk);
      cancelBtn.addEventListener("click", onCancel);
      modal.addEventListener("click", onBackdrop);
      document.addEventListener("keydown", onKey);
      modal.classList.remove("hidden");
      setTimeout(() => okBtn.focus(), 30);
    });
  }

  /**
   * v0.3.4：未登录拦截器
   * 未登录时：toast 提示 + 1.2s 后自动弹登录 modal，返回 false
   * 已登录时：返回 true，调用方继续执行
   * 用法：if (!requireLogin()) return;
   */
  function requireLogin() {
    if (state.status?.logged_in) return true;
    toast("🔒 请先登录钉钉账号", "warn", 4000);
    // 1.2s 延迟引导 — 让用户先看到提示，再自动弹登录 modal
    setTimeout(() => {
      // 防止 1.2s 内用户已经手动登录或关掉了 modal
      if (state.status?.logged_in) return;
      const m = $("login-modal");
      if (m && m.classList.contains("hidden")) onLogin();
    }, 1200);
    return false;
  }

  // ── 状态 ───────────────────────────────────────────────────────────
  const state = {
    status: null,            // { logged_in, user, corp, ... }
    corpOk: null,            // bool
    corpExpected: null,
    corpActual: null,
    currentReport: null,     // 最近一次 generate_daily 的 result
    history: [],
    pendingFile: null,
  };

  // ── LLM 配置 modal 状态 ────────────────────────────────────────
  const llmState = {
    providers: [],   // 10 个厂商，每个含 id/name/base_url/models[]
    current: null,   // 当前已保存配置（来自 get_llm_config）
    dirty: false,
    originalKey: null,  // 弹窗打开瞬间 API Key 输入框的值（cfg.key_masked 或 ""），保存时比对决定 skip_key
  };

  // ── 进度回调（launcher 注入） ────────────────────────────────────
  function onProgress(cb) {
    if (window.dtbox && typeof window.dtbox.onProgress === "function") {
      window.dtbox.onProgress(cb);
    }
  }

  // ── dws 一键安装（新用户首次启动） ──────────────────────────────
  // ── 登录 / 登出（v0.2 状态机重写） ───────────────────────────────
  // login 状态机：idle → launching → waiting_scan → polling → success | failed
  let _loginPollTimer = null;

  function _setLoginState(state, text) {
    const cancelBtn = $("btn-cancel-login");

    // 默认状态（v0.3.10：去掉启动按钮，只剩取消）
    cancelBtn.classList.remove("hidden");
    cancelBtn.disabled = false;
    cancelBtn.textContent = "取消登录";

    switch (state) {
      case "idle":
      case "launching":
      case "waiting_scan":
      case "polling":
        // 这些状态都已启动 dws，按钮保持可点（取消）
        break;
      case "success":
        cancelBtn.classList.add("hidden");
        break;
      case "failed":
        // 失败态保留取消按钮（用户可关闭弹窗）
        break;
    }
    void text;
  }

  function onLogin() {
    // v0.3.10：直接显示"浏览器授权等待"modal（弹窗即启动，无前置按钮）
    $("login-modal").classList.remove("hidden");
    renderLoginBrowserPrompt();
    _setLoginState("waiting_scan", "🌐 已在系统浏览器打开授权页，请在浏览器中扫码完成登录");
    setStatus("等待登录…");
    // 立即调 trigger_login → dws 自动开系统浏览器
    onTriggerLogin();
  }

  function _closeLoginModal() {
    // v0.3.9：关闭 modal 时**不**停轮询 —— 用户可能已经切到浏览器扫码，
    // 关 modal 只是不再看 UI；登录成功时 poll 会自动刷主页面。
    // 用户想放弃就点 modal 里的 [取消]（cancelLogin 才会真停）。
    clearLoginBrowserPrompt();
    $("login-modal").classList.add("hidden");
  }

  // 显式取消登录（用户点 modal 里 [取消] 或 [✕]）：停所有轮询
  function cancelLogin() {
    stopLoginPolling();
    stopBackgroundLoginWatch();
    clearLoginBrowserPrompt();
    // v0.3.10：通知侧车 kill 活跃的 dws 进程 + 清缓存，
    // 否则下次点登录会走 dedup 分支不再 spawn 新进程 → 浏览器不开
    call("cancel_login", {}).catch(() => {});
    $("login-modal").classList.add("hidden");
  }

  function startLoginPolling() {
    stopLoginPolling();
    // v0.3.9：去掉 60 秒硬超时——扫码耗时不可预测，
    // 强加超时会让「重试」按钮生成新 user_code 破坏当前输入，体验割裂。
    // 改为持续轮询直到登录成功；用户想放弃就点 [取消]。
    _setLoginState("polling", "⏳ 等待授权…");
    _loginPollTimer = setInterval(async () => {
      try {
        const status = await call("get_login_status", {});
        if (status?.logged_in) {
          stopLoginPolling();
          stopBackgroundLoginWatch();
          state.status = status;
          // 立即更新主 UI（modal 关没关都刷）—— 解决「关 modal 后浏览器登录成功页面不刷新」
          renderUserCard();
          clearLoginBrowserPrompt();
          $("login-modal").classList.add("hidden");
          if (!$("login-modal")?.classList.contains("hidden")) {
            _setLoginState("success", `✅ 登录成功：${status.user?.name || "未知用户"}`);
          }
          toast(`✓ 登录成功，欢迎 ${status.user?.name || ""}`, "ok");
          // 走 post-login 流程（corp 校验 + 历史加载）
          try {
            await _postLoginBoot();
          } catch (e) {
            console.warn("[login poll] _postLoginBoot failed", e);
          }
          return;  // 跳出本次 tick，poll 已停
        }
      } catch (e) {
        // 轮询中出错不中断，继续
        console.warn("[login poll] get_login_status failed", e);
      }
    }, 2000);
  }

  function stopLoginPolling() {
    if (_loginPollTimer) {
      clearInterval(_loginPollTimer);
      _loginPollTimer = null;
    }
  }

  /**
   * v0.3.9 后台登录监视器（备用）：
   * 当前主流程不再使用 —— 前台 startLoginPolling 在关 modal 时**不**停止，
   * 自然继续轮询直到登录成功。如果未来要把 polling 限到 modal 内可见时再用此 watcher。
   * 保留 API 以兼容 onTriggerLogin 调用，no-op 实现。
   */
  let _bgLoginWatchTimer = null;
  function startBackgroundLoginWatch() {
    // no-op: 前台轮询已经覆盖此场景（modal 关掉后继续轮询直到登录成功）
    if (_bgLoginWatchTimer) clearInterval(_bgLoginWatchTimer);
    _bgLoginWatchTimer = null;
  }
  function stopBackgroundLoginWatch() {
    if (_bgLoginWatchTimer) {
      clearInterval(_bgLoginWatchTimer);
      _bgLoginWatchTimer = null;
    }
  }

  async function onTriggerLogin() {
    // v0.3.10：弹窗即启动，无前置按钮；onLogin 已经调过 _setLoginState("launching")
    // 这里直接 spawn dws；防重入靠 trigger_login 内部的 _ACTIVE_LOGIN_PROC dedup
    try {
      await call("trigger_login", {});
      // 改成引导用户在 dws 自动打开的系统浏览器里完成扫码。
      renderLoginBrowserPrompt();
      _setLoginState("waiting_scan", "🌐 已在系统浏览器打开授权页，请在浏览器中扫码完成登录");
      // 1.5s 后开始轮询 + 后台监视（modal 关掉后仍继续，登录成功自动刷主页面）
      setTimeout(() => { startLoginPolling(); startBackgroundLoginWatch(); }, 1500);
    } catch (e) {
      _setLoginState("failed", `❌ 启动失败：${e?.message || e}`);
    }
  }

  /**
   * v0.3.10：钉钉 App 扫屏幕 QR 走不通（识别不到 OAuth URL），
   * 改成引导用户在 dws 自动打开的系统浏览器里完成扫码授权。
   */
  let _loginDotsTimer = null;
  function renderLoginBrowserPrompt() {
    const wrap = $("login-browser-prompt");
    if (!wrap) return;
    wrap.classList.remove("hidden");
    // 启动"..."循环（0/1/2/3 个点）
    if (_loginDotsTimer) clearInterval(_loginDotsTimer);
    const dotsEl = $("login-hint-dots");
    if (dotsEl) {
      dotsEl.textContent = "";
      let n = 0;
      _loginDotsTimer = setInterval(() => {
        n = (n + 1) % 4;
        dotsEl.textContent = ".".repeat(n);
      }, 500);
    }
  }

  function clearLoginBrowserPrompt() {
    const wrap = $("login-browser-prompt");
    if (wrap) wrap.classList.add("hidden");
    if (_loginDotsTimer) {
      clearInterval(_loginDotsTimer);
      _loginDotsTimer = null;
    }
    const dotsEl = $("login-hint-dots");
    if (dotsEl) dotsEl.textContent = "";
  }

  async function onManualOpenUrl() {
    // v0.3.10：保留此函数作为 onManualOpenUrl 兼容 stub（旧逻辑已废弃，dws 自动开浏览器）
    return;
  }

  async function onCopyUrl() {
    // v0.3.10：保留此函数作为 onCopyUrl 兼容 stub（不再显示 QR URL，无可复制内容）
    toast("已在系统浏览器打开授权页，无需复制", "info");
  }

  // v0.3.3：登出按钮已彻底去掉（用户换钉钉号直接重新登录更直观）。
  // hideLogoutConfirm 留个空 stub 防止旧代码引用报错。
  function hideLogoutConfirm() { /* no-op after v0.3.3 */ }

  async function onLogout() {
    // v0.3.3 兼容：此函数已不再被按钮调用；保留仅为不破坏其它潜在引用。
    hideLogoutConfirm();
    setStatus("登出中…");
    try {
      const r = await call("trigger_logout", {});
      if (!r.ok) {
        showError("登出失败", { message: r.err_msg || r.err_code || "未知错误" });
        try {
          state.status = await call("get_login_status", {});
          renderUserCard();
        } catch (_) { /* 静默 */ }
        return;
      }
      state.status = await call("get_login_status", {});
      renderUserCard();
      toast("✓ 已登出，可随时点 [登录钉钉] 切换账号", "ok");
      setStatus("未登录");
    } catch (e) {
      showError("登出异常", e);
      setStatus("出错");
    }
  }

  async function onDwsInstall() {
    const btn = $("btn-dws-install");
    const skip = $("btn-dws-install-skip");
    btn.disabled = true;
    skip.disabled = true;
    btn.textContent = "安装中…";
    $("dws-install-hint").textContent = "正在复制 dws 到 %APPDATA%/DingTalkBox/bin/…";
    try {
      const r = await call("install_dws", {});
      if (r.ok) {
        $("dws-install-hint").innerHTML =
          `✅ dws ${r.version} 已安装到 <code>${r.path}</code><br>正在继续检测登录态…`;
        btn.textContent = "✓ 已安装";
        btn.disabled = true;
        skip.disabled = true;
        // 1 秒后自动关 modal + 继续 boot（用户不需要重启工具）
        setTimeout(async () => {
          $("dws-install-modal").classList.add("hidden");
          await _continueBootAfterDws();
        }, 1000);
      } else {
        $("dws-install-hint").innerHTML =
          `<span style="color:#c0392b">❌ 安装失败：${escapeHtml(r.err_msg || r.err_code || "未知错误")}</span><br>可手动把 <code>bin/dws.exe</code> 复制到目标路径。`;
        btn.disabled = false;
        skip.disabled = false;
        btn.textContent = "重试";
      }
    } catch (e) {
      $("dws-install-hint").innerHTML =
        `<span style="color:#c0392b">❌ 安装异常：${escapeHtml(e.message || String(e))}</span>`;
      btn.disabled = false;
      skip.disabled = false;
      btn.textContent = "重试";
    }
  }

  function onDwsInstallSkip() {
    $("dws-install-modal").classList.add("hidden");
    // 「关闭工具」按钮 (dws 装完后也会用这个) → 真的退出
    if (window.dtbox?.exit) {
      window.dtbox.exit();
      return;
    }
    // 跳过模式：灰掉需要 dws 的按钮
    $("btn-generate").disabled = true;
    $("btn-generate").title = "需要先安装 dws";
    setStatus("已跳过 dws 安装（生成日报不可用）");
    toast("已跳过 dws 安装。LLM 配置 / 历史日报查看仍可用。", "warn");
  }

  // ── 启动流程 ───────────────────────────────────────────────────────
  async function boot() {
    setStatus("正在启动…");
    // v0.3.1：立即渲染日期问候（不依赖登录态）
    renderHeroDate();

    // 立即检查 launcher 是否新版本（launcher 注入 window.__launcher_build）
    if (window.__launcher_build) {
      console.info("[boot] launcher build:", window.__launcher_build);
    } else {
      console.warn("[boot] window.__launcher_build missing — launcher may be outdated");
    }

    // v0.2 修复：等桥就绪（pywebview 注入 dtbox.call 通常在 DOMContentLoaded 之前，
    // 但偶尔有 race；用轮询兜底，最多 3s，避免一上来就报「sidecar 未就绪」）
    const bridgeOk = await _waitForBridge(3000);
    if (!bridgeOk) {
      // 桥都没就绪 → 用户未登录态（不是 error 也不是 loading），
      // 等 5s 自动重试 boot，不要弹错误卡吓用户
      setStatus("正在启动 sidecar…");
      toast("启动较慢，将在 5 秒后自动重试", "warn", 5000);
      setTimeout(boot, 5000);
      return;
    }

    // Step 1: 必须先确认 dws 存在；否则后面的 get_login_status 会抛
    // FileNotFoundError，让用户只能看到错误卡，看不到任何登录入口
    // （这个 bug 之前坑过第一次打开工具的同事）
    let dwsCheck;
    try {
      dwsCheck = await call("check_dws_install", {});
    } catch (e) {
      // 拿不到 dws 状态 — 用户未登录态处理（首次启动 dws 还没装）
      // 不弹错误卡（避免给新用户看一堆技术栈报错）
      console.warn("[boot] check_dws_install failed", e);
      setStatus("未检测到 dws（请登录或安装）");
      // 让用户能点 [登录钉钉] 触发 dws 安装引导（boot 完成后 onLogin 会处理）
      onLogin();
      return;
    }
    if (!dwsCheck.installed) {
      if (dwsCheck.installable) {
        // 自动弹一键安装引导 — 这是新用户唯一能看到 dws 安装入口的时机
        $("dws-install-target").textContent = dwsCheck.target_path || "—";
        $("dws-install-hint").textContent =
          "检测中…".replace("检测中…", "未检测到 dws。点下方按钮一键安装（约 5 秒）");
        $("dws-install-modal").classList.remove("hidden");
        setStatus("需要先安装 dws");
        return;
      }
      // 工具根本没内嵌 dws（打包漏了？）
      showError("工具未携带 dws", {
        message: "可执行文件缺少 bin/dws.exe，请联系工具负责人。\n" +
                 `期望路径：${dwsCheck.bundled_path || "(未内嵌)"}`,
      });
      return;
    }

    // Step 2: dws 已就位 → 检查登录态
    try {
      state.status = await call("get_login_status");
      renderUserCard();
    } catch (e) {
      // 连接 sidecar 失败 — 切到未登录态，5s 后重试
      console.warn("[boot] get_login_status failed", e);
      setStatus("连接 sidecar 失败，将重试…");
      renderUserCard();  // 确保按钮态正确（显示 [登录钉钉]）
      onLogin();
      setTimeout(() => {
        if (!state.status?.logged_in) boot();
      }, 5000);
      return;
    }

    if (!state.status.logged_in) {
      // v0.2：走 onLogin() 走状态机重置（idle），不是直接显 modal
      onLogin();
      return;
    }

    // 已登录 → 走 post-login 流程（corp 校验 + 历史加载 + 就绪态）
    await _postLoginBoot();

    // ── 启动自检结果（launcher 注入 window.__self_test_result）──
    // 当前 launcher 跑 ping：返回 {ok, dt_ms} 或 {err, message}
    // 5s 内若没结果就再等一次（防止时序问题）
    const checkSelfTest = () => {
      const r = window.__self_test_result;
      if (r && r !== "pending" && typeof r === "object") {
        if (r.ok) {
          setStatus(`✓ 启动自检通过（ping ${r.dt_ms ?? r.dt ?? 0}ms）`);
          toast("✓ 启动自检通过，可以生成日报", "ok");
        } else {
          setStatus(`⚠ 启动自检失败：${r.err || r.message || "未知错误"}`);
          showError("启动自检失败", {
            message: r.err || r.message || "sidecar ping 失败",
          });
        }
        return true;
      } else if (!r) {
        // launcher 没跑 self-test → 旧版 launcher？
        setStatus("⚠ launcher 旧版：请重新启动 dingtalk_box.exe");
        showError("launcher 旧版（self-test 缺失）", {
          message: "请 kill 旧 dingtalk_box.exe 后重新启动新版本",
        });
        return true;
      }
      return false;
    };
    setTimeout(checkSelfTest, 5000);
    setTimeout(checkSelfTest, 12000);

    // v0.3.1：两栏布局，nav 仅装饰性切换 active（两栏并排，无 tab 内容切换）
    initNavDecoration();
  }

  // ── Nav active 切换（仅高亮，无 tab 切换——两栏并排布局）────
  function initNavDecoration() {
    document.querySelectorAll(".nav-item[data-tab]").forEach((el) => {
      el.addEventListener("click", (e) => {
        e.preventDefault();
        document.querySelectorAll(".nav-item").forEach((n) => n.classList.remove("active"));
        el.classList.add("active");
        // 小窗口：关抽屉
        const sidebar = document.querySelector(".sidebar");
        if (sidebar) sidebar.classList.remove("open");
      });
    });
  }

  /**
   * v0.2：等 pywebview 桥就绪（window.dtbox.call 或 window.pywebview.api.call）。
   * 轮询间隔 100ms，最多 3s。桥在 DOMContentLoaded 后才注入，有 200-500ms race。
   * @returns {Promise<boolean>} true = 就绪；false = 3s 内都没就绪
   */
  function _waitForBridge(maxMs) {
    const deadline = Date.now() + maxMs;
    return new Promise((resolve) => {
      const tick = () => {
        const ready = (typeof window.dtbox?.call === "function") ||
                      (typeof window.pywebview?.api?.call === "function");
        if (ready) return resolve(true);
        if (Date.now() >= deadline) return resolve(false);
        setTimeout(tick, 100);
      };
      tick();
    });
  }

  // ── dws 一键安装完成后，继续执行 boot 后半段 ───────────────────────
  async function _continueBootAfterDws() {
    // dws 装好了 → 重跑 get_login_status + 后续
    try {
      state.status = await call("get_login_status");
      renderUserCard();
    } catch (e) {
      showError("连接 sidecar 失败", e);
      return;
    }
    if (!state.status.logged_in) {
      // v0.2：走 onLogin() 走状态机重置（idle）
      onLogin();
      return;
    }
    // 已登录 — 走和 boot 一样的 corp 校验 + 历史加载（简化：直接重跑整个后半段）
    await _postLoginBoot();
  }

  async function _postLoginBoot() {
    setStatus("校验 corp…");
    try {
      // Opt 8：复用 state.status（get_login_status 已调过），
      // 避免再调一次 dws auth status（启动快 0.5-1s）
      const v = await call("validate_corp_with_status", {
        status: state.status,
      });
      state.corpOk = v.ok;
      state.corpExpected = v.expected_corp_name || v.expected_corp_id;
      state.corpActual = v.current_corp_name || v.current_corp_id;
      if (!v.ok) {
        // 完整显示 expected/actual（即便某个字段为空也不会 NPE）
        $("corp-expected-name").textContent = v.expected_corp_name || "(未设置)";
        $("corp-expected-id").textContent = v.expected_corp_id || "—";
        $("corp-actual-name").textContent = v.current_corp_name || "(空)";
        $("corp-actual").textContent = v.current_corp_id || "未知";
        // 特殊 code 的提示文案
        const hints = {
          NOT_LOGGED_IN: "请先 dws auth login",
          CONTACT_FAILED: "无法读取 corp 信息（dws 异常）",
          MISMATCH: "请切换到本公司钉钉账号后重试",
        };
        $("corp-hint").textContent = hints[v.code] || "请重试或联系工具负责人";
        $("corp-modal").classList.remove("hidden");
        setStatus("corp 不匹配");
        return;
      }
      // v.ok === true，但 code 非空 → 警告性 code（不阻塞用户）
      // 当前策略：所有警告都仅写入控制台/日志，不弹 toast（end user 无需关心 admin 侧配置）
      if (v.code && v.code !== "") {
        console.warn(`[corp] ${v.code} — ${v.message || ""}`);
      }
    } catch (e) {
      showError("corp 校验失败", e);
    }
    try {
      const h = await call("list_history", {});
      state.history = h.items || [];
      renderHistory();
    } catch (e) {
      console.warn("list_history failed", e);
    }
    setStatus("就绪");
    await updateBootStatus();
  }

  function renderUserCard() {
    const s = state.status;  // null = 未知（首次启动 / 桥未就绪）
    const logged_in = !!(s && s.logged_in);
    const user = s?.user;
    // v0.3.9 用户反馈：未登录状态下「未登录」3 字替换为「***」（占位符，更友好）
    if (logged_in && user?.name) {
      $("hero-name").textContent = user.name;
      $("hero-name").classList.add("logged-in");
    } else {
      $("hero-name").textContent = user?.name || "***";
      $("hero-name").classList.remove("logged-in");
    }
    if (s?.corp) $("hero-corp").textContent = s.corp.corp_name || s.corp.corp_id || "—";
    else $("hero-corp").textContent = "***";
    const el = $("hero-status");
    if (logged_in) {
      el.textContent = "已登录";
      el.classList.remove("status-warning", "status-error");
      el.classList.add("status-success");
      el.style.cursor = "default";
      el.title = "";
    } else {
      // v0.3.9 用户反馈：状态指示保留「未登录」字样（明确告知登录态）
      el.textContent = "● 未登录";
      el.classList.remove("status-success");
      el.classList.add("status-warning");
      el.style.cursor = "default";
      el.title = "";
    }
    // 登录/登出按钮互斥显示
    // v0.3.3：已登录就隐藏登录按钮（避免重复登录）；登出按钮已彻底去掉
    $("btn-login").hidden = logged_in;
    if (logged_in) hideLogoutConfirm();
    // v0.2 P1 改进：登录后显示用户头像（dws 1.0.34 contact user me 返回 avatar 字段）
    renderUserAvatar();
    // v0.3.1：渲染 Welcome Hero 日期问候
    renderHeroDate();
  }

  /**
   * v0.3.1：渲染 Welcome Hero 里的"今天是 YYYY年MM月DD日，准备生成你的日报吧~"问候文字。
   * 用 zh-CN locale 拿到"2026年06月19日"格式，比 toISOString().slice(0,10) 直观。
   */
  function renderHeroDate() {
    const el = $("hero-date");
    if (!el) return;
    const d = new Date();
    const fmt = `${d.getFullYear()}年${String(d.getMonth() + 1).padStart(2, "0")}月${String(d.getDate()).padStart(2, "0")}日`;
    el.textContent = `今天是 ${fmt}，准备生成你的日报吧~`;
  }

  /**
   * v0.2 P1：渲染用户头像。
   * dws 1.0.34 contact user me 返回的 user.avatar 是 CDN URL。
   * 用 <img> 加载；加载失败/缺失时 fallback 到「👤」emoji。
   * 注意：pywebview 跨域通常允许 https img；用 onerror 兜底。
   */
  function renderUserAvatar() {
    const wrap = $("hero-avatar");
    const fallback = $("hero-avatar-fallback");
    if (!wrap) return;
    const s = state.status;
    const url = s?.logged_in ? (s.user?.avatar || "") : "";
    const name = s?.user?.name || "";
    // 清掉旧的 img / 自生成首字头像（保留 fallback span）
    wrap.querySelectorAll("img.avatar-img, .avatar-initial").forEach((el) => el.remove());
    if (url) {
      // v0.3：sidecar 已经把头像转成 data: URL（base64），浏览器无网络直渲染
      const img = document.createElement("img");
      img.className = "avatar-img";
      img.alt = name;
      img.src = url;
      img.onerror = () => { img.remove(); showInitialAvatar(wrap, fallback, name); };
      img.onload = () => { if (fallback) fallback.hidden = true; };
      if (url.startsWith("data:")) {
        if (fallback) fallback.hidden = true;
      }
      wrap.appendChild(img);
    } else if (name) {
      // v0.3.2：dws 1.0.34 contact user me 不返 avatar 字段 → 改用首字 + 彩色背景
      showInitialAvatar(wrap, fallback, name);
    } else if (fallback) {
      fallback.hidden = false;
    }
  }

  /**
   * 用用户名首字符 + 哈希颜色生成一个本地 SVG 头像（永久可用，不依赖网络）
   * 中文取最后一个字（更接近真实名），英文取第一个字母
   */
  function showInitialAvatar(wrap, fallback, name) {
    if (!name) return;
    if (fallback) fallback.hidden = true;
    // 取首字：中文/英文/数字分别处理
    let initial = name.trim();
    // 跳过常见姓名前缀（"张" "李" "王" 等），但首字本身就是常见处理，简单起见直接取
    // 对于 CJK，取最后一个字符（更接近"名"）
    if (/[一-鿿]/.test(initial)) {
      initial = [...initial].pop();
    } else {
      initial = initial[0] || "?";
    }
    // 基于 name 哈希出色相
    let hash = 0;
    for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) | 0;
    const hue = Math.abs(hash) % 360;
    const bg = `hsl(${hue}, 60%, 55%)`;
    const fg = "#fff";
    // SVG 头像（48x48，圆 + 大字）
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" width="48" height="48">
      <circle cx="24" cy="24" r="24" fill="${bg}"/>
      <text x="24" y="24" dy="0.35em" text-anchor="middle" font-family="system-ui, -apple-system, sans-serif" font-size="22" font-weight="600" fill="${fg}">${initial}</text>
    </svg>`;
    const img = document.createElement("img");
    img.className = "avatar-img avatar-initial";
    img.alt = name;
    img.src = "data:image/svg+xml;base64," + btoa(unescape(encodeURIComponent(svg)));
    wrap.appendChild(img);
  }

  /**
   * v0.3.9：登出后清空所有用户数据 → 防止历史日报 / 当前生成结果 / 进度 / 文件状态
   * 等旧账号的残留数据在重新登录后（切到新账号）误导用户。
   * 清空：state.history（历史列表）、state.currentReport（最近日报 + PNG/MD 预览）、
   *       进度面板、日报卡、日报弹窗、文件发送状态徽章、阶段进度条。
   */
  function clearSessionData() {
    state.history = [];
    state.currentReport = null;
    state.pendingFile = null;
    // 重渲历史列表（显示「暂无历史日报」）
    renderHistory();
    // 隐藏「生成日报进度」卡片 + 「今日日报」卡片 + 日报预览弹窗（如果还开着）
    $("progress-card")?.classList.add("hidden");
    $("report-card")?.classList.add("hidden");
    $("report-modal")?.classList.add("hidden");
    // v0.3.10：登出后清空报错区域（error-card + error-msg），
    // 否则旧账号的报错信息会误导新登录用户
    const errorCard = $("error-card");
    if (errorCard && !errorCard.classList.contains("hidden")) {
      errorCard.classList.add("hidden");
      $("error-msg") && ($("error-msg").innerHTML = "");
    }
    // 进度条回到初始态（防止下次登录后还显示「已耗时 XXs」/ 阶段标记）
    try { resetStages(); } catch (e) { console.warn("[clearSessionData] resetStages", e); }
    // 清空「今日日报」文件列表（PNG / MD 文件名 + 大小 + 发送状态徽章）
    const fields = [
      "report-png-name", "report-png-size", "report-png-send-status",
      "report-md-name", "report-md-size", "report-md-send-status",
    ];
    for (const id of fields) {
      const el = $(id);
      if (!el) continue;
      if (el.tagName === "INPUT" || el.tagName === "TEXTAREA") el.value = "";
      else el.innerHTML = "";
      if (id === "report-png-name" || id === "report-md-name") el.textContent = "—";
      else if (id === "report-png-size" || id === "report-md-size") el.textContent = "—";
    }
    // 取消所有未发送的 checkbox 勾选
    const checkAll = $("report-check-all");
    if (checkAll) checkAll.checked = false;
    document.querySelectorAll(".report-file-check").forEach((cb) => { cb.checked = false; });
    // 清空预览区
    const preview = $("report-preview");
    if (preview) preview.innerHTML = "";
    setStatus("已退出登录");
  }

  function renderHistory() {
    const ul = $("history-list");
    if (!ul) return;
    if (!state.history.length) {
      ul.innerHTML = `<li class="history-row empty">暂无历史日报</li>`;
      return;
    }
    // v0.3.6：list row 卡片列表 — 日期 | 大小 | 查看文件夹
    const rows = state.history.slice(0, 20);
    ul.innerHTML = rows.map((h) => `
      <li class="history-row" data-date="${h.date}">
        <span class="h-date">${h.date}</span>
        <span class="h-actions">
          ${h.png_path ? `<button class="btn-ghost" data-action="open" data-path="${h.png_path}" title="预览日报">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
              <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
              <circle cx="12" cy="12" r="3"/>
            </svg>
            <span class="btn-label">预览</span>
          </button>` : ""}
          ${h.png_path ? `<button class="btn-ghost" data-action="open-folder" data-date="${h.date}" title="打开日报文件所在文件夹">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
            </svg>
            <span class="btn-label">打开文件夹</span>
          </button>` : ""}
        </span>
      </li>
    `).join("");
    ul.querySelectorAll("button").forEach((b) => {
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        const action = b.dataset.action;
        if (action === "open") {
          const p = b.dataset.path;
          if (window.dtbox?.openPath) window.dtbox.openPath(p);
          else toast("无法打开文件", "error");
        } else if (action === "open-folder") {
          // v0.3.6：打开日报文件所在文件夹
          const date = b.dataset.date;
          const item = state.history.find((x) => x.date === date);
          if (item?.png_path && window.dtbox?.openPath) {
            const dir = item.png_path.replace(/[\\/][^\\/]+$/, "");
            window.dtbox.openPath(dir);
          } else {
            toast("无法打开文件夹", "error");
          }
        }
      });
    });
    // 「查看更多」按钮可见性
    const more = $("history-more");
    if (more) more.classList.toggle("hidden", state.history.length <= 20);
  }

  // ── 生成日报 ───────────────────────────────────────────────────────
  async function onGenerate() {
    // 防止用户重复点击 + 拦截已有任务
    if ($("btn-generate").disabled) {
      toast("⏳ 当前有日报生成任务正在执行，请稍候…", "warn", 2500);
      return;
    }
    // v0.3.7：未登录拦截
    if (!requireLogin()) return;
    // v0.3.7：未配置 LLM 拦截 —— 弹模态告诉用户先配置
    try {
      const cfg = await call("get_llm_config", {});
      if (!cfg.configured) {
        const goConfig = await confirmModal({
          title: "请先配置 AI 模型",
          message: "生成日报需要绑定 AI 模型 API Key。\n\n点 [去配置] 打开 AI 模型配置。",
          okText: "去配置",
          cancelText: "稍后",
        });
        if (goConfig) openLlmModal();
        return;
      }
    } catch (e) {
      toast("❌ 检查 LLM 配置失败：" + (e?.message || e), "error", 4000);
      return;
    }
    console.log("[onGenerate] entered");
    const date = $("date-input").value || today();
    // 立刻给用户 toast 反馈，避免以为没反应而重复点击
    toast(`🚀 开始生成 ${date} 的日报…`, "info", 2500);
    $("progress-date").textContent = date;
    $("progress-card").classList.remove("hidden");
    $("report-card").classList.add("hidden");
    $("error-card").classList.add("hidden");
    try {
      resetStages();
    } catch (e) {
      console.error("[onGenerate] resetStages failed", e);
      toast("❌ resetStages 失败：" + (e?.message || e), "error", 5000);
    }
    $("btn-generate").disabled = true;
    setStatus("生成中…");
    const start = Date.now();
    // 立刻写一次 "已耗时 0s"，避免 1 秒空白期看不到反馈
    $("progress-elapsed").textContent = "已耗时 0s";
    console.log("[onGenerate] elapsed 0s set, calling markStage");
    // 立即把第一个 stage 标 done —— 点完生成按钮用户立刻看到反馈
    // (用新 markStage 逻辑：idx=0 done 绿, idx=1 active 蓝紫)
    try {
      markStage("fetching_chats");
    } catch (e) {
      console.error("[onGenerate] markStage failed", e);
      toast("❌ 进度条初始化失败：" + (e?.message || e), "error", 5000);
    }
    const elapsedTimer = setInterval(() => {
      const sec = Math.floor((Date.now() - start) / 1000);
      const el = $("progress-elapsed");
      if (el) el.textContent = `已耗时 ${sec}s`;
    }, 1000);

    try {
      // 阶段 1：拉数据 + 让用户确认/输入 report.json
      const r1raw = await Promise.race([
        call("generate_daily", { date }),
        new Promise((_, rej) => setTimeout(() => rej(new Error("RPC 超时（180s 无响应，AI 调用可能需要更长时间）")), 180000)),
      ]);
      // bridge.call 返回 {result: ...}，需要解包
      const r1 = r1raw?.result ?? r1raw;
      if (r1.stage === "awaiting_ai") {
        toast("AI 未配置，bundle 已生成", "warn");
        const goConfig = await confirmModal({
          title: "AI 未配置",
          message: "AI 未配置，无法一键生成报告。\n\n点 [配置模型] 打开 AI 模型配置。\n点 [走老流程] 导出 bundle 后用占位报告。",
          okText: "配置模型",
          cancelText: "走老流程",
        });
        if (goConfig) {
          openLlmModal();
        } else {
          // 走老 awaiting_ai 流程：占位 stub 报告
          const stubReport = await buildStubReport(date, r1);
          // Opt 2 修复：传 bundle_path 让后端复用，**不重新 export**（省 40% 时间）
          const r2raw = await call("generate_daily", {
            date,
            report_json: stubReport,
            bundle_path: r1.bundle_path,
          });
          const r2 = r2raw?.result ?? r2raw;
          finishReport(r2);
        }
      } else {
        finishReport(r1);
      }
    } catch (e) {
      showError("生成日报失败", e);
    } finally {
      clearInterval(elapsedTimer);
      $("btn-generate").disabled = false;
      setStatus("就绪");
    }
  }

  function finishReport(r) {
    if (!r.output) {
      // 输出简化错误（不把整个 result 序列化塞进 message）
      const missing = r.stage || "未知阶段";
      showError("生成结果缺 output", { message: `当前阶段：${missing}，请看诊断详情`, data: r });
      return;
    }
    state.currentReport = r;
    $("report-png-name").textContent = pathBasename(r.output.png_path);
    $("report-png-size").textContent = fmtSize(r.png_size_bytes);
    $("report-md-name").textContent = pathBasename(r.output.md_path);
    $("report-md-size").textContent = fmtSize(r.md_size_bytes);
    // v0.3：清空上次报告的「已发送」状态（重跑同一日期时）
    $("report-png-send-status").textContent = "";
    $("report-png-send-status").className = "file-send-status";
    $("report-md-send-status").textContent = "";
    $("report-md-send-status").className = "file-send-status";
    // v0.3.7：默认不勾选（用户主动选才发，避免误发）
    const pngCheck = document.querySelector('.file-check[data-kind="png"]');
    const mdCheck = document.querySelector('.file-check[data-kind="md"]');
    if (pngCheck) pngCheck.checked = false;
    if (mdCheck) mdCheck.checked = false;
    $("report-check-all").checked = false;
    updateSelectCount();
    $("report-card").classList.remove("hidden");
    markStage("done");
    const fill = $("progress-fill");
    if (fill) fill.style.width = "100%";
    toast("✓ 日报生成完成", "ok");
    // 刷新历史（不阻断 — 失败时主流程已完成）
    call("list_history", {}).then((h) => {
      state.history = h.items || [];
      renderHistory();
    }).catch((e) => console.warn("refresh history failed:", e));
    // v0.2 P0：生成完成后自动发送 PNG 到当前登录账号（self-send only）
    // 不阻塞主流程：失败时仅更新文件行状态徽章 + toast
    autoSendDailyReport();
  }

  /**
   * v0.2 P0：生成完成后自动发送 PNG 长图到当前登录钉钉账号。
   * v0.3 改进：把结果写到 PNG 行的状态徽章（让用户一眼看到是否成功），
   *          不再依赖 5s 就消失的 toast。
   * v0.3.3：发送完成后把进度条最后一步「发送钉钉」标 done（✓），
   *          文件行不再显示「已发给 xxx（task=...）」这类冗余文字。
   * v0.3.7：自动发送改为「图+文档 都发」，顺序：PNG 先发 → MD 后发。
   *          每个文件独立 try/catch，单个失败不影响另一个。
   *          失败仅 toast（不抛、不弹错误卡 — 用户可以手动点 [发送钉钉] 重试）。
   */
  async function autoSendDailyReport() {
    const me = state.status?.user;
    if (!me?.open_dingtalk_id && !me?.user_id) {
      console.warn("auto-send skipped: state.status.user 缺失", me);
      setFileSendStatus("png", "skipped", "未拿到登录人标识（请重新登录）");
      setFileSendStatus("md", "skipped", "未拿到登录人标识（请重新登录）");
      return;
    }
    const r = state.currentReport;
    if (!r?.output?.png_path) return;
    const date = r.date || today();

    // 顺序固定：PNG → MD（图先发，文档后发）
    const queue = [];
    if (r.output.png_path) queue.push({ kind: "png", path: r.output.png_path });
    if (r.output.md_path)   queue.push({ kind: "md",  path: r.output.md_path });

    let pngOk = 0, pngFail = 0, mdOk = 0, mdFail = 0;
    for (const item of queue) {
      setFileSendStatus(item.kind, "sending", "正在自动发送…");
      try {
        const params = {
          file_path: item.path,
          title: `钉钉工作纪要日报 ${date}`.trim(),
        };
        if (me.open_dingtalk_id) params.open_dingtalk_id = me.open_dingtalk_id;
        if (me.user_id) params.user_id = me.user_id;
        await call("send_to_dingtalk", params);
        setFileSendStatus(item.kind, "sent", "");
        if (item.kind === "png") pngOk++; else mdOk++;
      } catch (e) {
        setFileSendStatus(item.kind, "failed", `❌ 自动发送失败：${e?.message || e}`);
        if (item.kind === "png") pngFail++; else mdFail++;
        console.warn(`auto-send ${item.kind} failed:`, e);
      }
    }

    // 进度条最后一步：全成功 → ✓；任一失败 → ❌
    const total = pngOk + mdOk, fail = pngFail + mdFail;
    if (total > 0 && fail === 0) {
      markAllStagesDone("ok");
      toast(`✓ 已发给「${me.name || "自己"}」（${pngOk} 图 + ${mdOk} 文档）`, "ok", 4000);
    } else if (fail > 0 && total > 0) {
      markAllStagesDone("error");
      toast(`部分失败：${pngOk} 图 + ${mdOk} 文档成功，${fail} 失败`, "warn", 6000);
    } else if (fail > 0) {
      markAllStagesDone("error");
      toast(`自动发送失败（${pngFail + mdFail} 个文件，可手动勾选 [📤 发送钉钉] 重试）`, "warn", 6000);
    }
  }

  /**
   * v0.3：在文件行末尾显示「已发送/失败/sending」徽章。
   * 解决"自动发送没看到"的反馈问题。
   */
  function setFileSendStatus(kind, state, text) {
    const el = $(`report-${kind}-send-status`);
    if (!el) return;
    el.textContent = text;
    el.dataset.state = state;
    el.className = "file-send-status";
  }

  // stub 报告：让流程跑通，提示用户实际效果需要 AI 接入
  async function buildStubReport(date, r1) {
    // 通过 sidecar 读 bundle.json（沙箱安全）
    let bundle = null;
    try {
      const r = await call("read_text_file", { path: r1.bundle_path });
      bundle = r.content;
    } catch (e) {
      console.warn("read bundle via sidecar failed", e);
    }
    // 表格式板块必须是「dict 数组」（renderer 的 row.get(key,...) 假设每行是 dict）；
    // 空数组会触发 _ensure_rows 兜底，显示"今天未识别…"的占位行。
    const emptyRows = [];
    // 列表式板块（overview）用字符串数组，渲染时按 numbered list 处理
    return {
      date,
      title: "钉钉工作纪要日报",
      disclaimer: "以下为占位报告。MVP 阶段：bundle 已生成，请在外部用 AI 分析后调用 generate_daily(report_json=...) 完成。",
      overview: {
        work: [`今日共 ${r1.chat_count} 个工作会话 / ${r1.message_count} 条消息`],
        personal: ["（待 AI 填充）"],
      },
      work: {
        handled_items: emptyRows,
        key_decisions: emptyRows,
        projects: emptyRows,
        reply_needed: emptyRows,
        risks: emptyRows,
      },
      personal: {
        highlights: emptyRows,
        reply_needed: emptyRows,
      },
      // ⚠️ 不要传 ["（待 AI 填充）"] — render_report 会把它当成 dict 列表，row.get() 崩溃
      tomorrow: { work: emptyRows, personal: emptyRows },
      context_gaps: { work: emptyRows, personal: emptyRows },
    };
  }

  function pathBasename(p) {
    if (!p) return "—";
    const m = p.match(/[^\\/]+$/);
    return m ? m[0] : p;
  }

  // ── 进度可视化 ───────────────────────────────────────────────────
  function resetStages() {
    const fill = $("progress-fill");
    if (fill) fill.style.width = "0%";
    document.querySelectorAll("#stages li").forEach((li) => {
      li.classList.remove("active", "done", "error");
      const statusEl = li.querySelector(".stage-status");
      if (statusEl) statusEl.textContent = "待开始";
    });
    $("progress-elapsed").textContent = "已耗时 0s";
    state.currentStage = null;
  }

  function markStage(name, kind = "ok") {
    // kind: "ok" 高亮当前 stage；"error" 把当前 stage 标红
    // v0.3.7：收到 stage=X 时 → X **及之前**全部标 done，**下一个**标 active
    //  → 每次 emit 都会让一个节点「变绿」+ 下一个「变蓝」，
    //    视觉上一节节推进，不再"全部完成才刷新"
    const order = ["fetching_chats", "ai_summarize", "render_png", "render_md", "done"];
    const idx = order.indexOf(name);
    if (idx === -1) return;
    document.querySelectorAll("#stages li").forEach((li) => {
      const li_idx = order.indexOf(li.dataset.stage);
      const statusEl = li.querySelector(".stage-status");
      li.classList.remove("active", "done", "error");
      if (kind === "error") {
        // 错误态：之前的 stage 标 done，**当前 stage 标红**，之后的保持"待开始"
        if (li_idx < idx) {
          li.classList.add("done");
          if (statusEl) statusEl.textContent = "已完成";
        } else if (li_idx === idx) {
          li.classList.add("error");
          if (statusEl) statusEl.textContent = "失败";
        } else {
          if (statusEl) statusEl.textContent = "待开始";
        }
      } else {
        if (li_idx <= idx) {
          // X **及之前**全部 done（X 也算"完成"，下一个开始）
          li.classList.add("done");
          if (statusEl) statusEl.textContent = "已完成";
        } else if (li_idx === idx + 1) {
          // 紧跟的下一个：active
          li.classList.add("active");
          if (statusEl) statusEl.textContent = "进行中…";
        } else {
          // 之后 idx+2..N：保持"待开始"
          if (statusEl) statusEl.textContent = "待开始";
        }
      }
    });
  }

  /**
   * v0.3.3：把**所有** stage 标 done（✓）。
   * 用在「发送钉钉」也跑完时：markStage("done", "ok") 会让 done 阶段变成 active（蓝点），
   * 但用户期望最后一个阶段也显示 ✓。这个函数直接全部 done。
   * kind: "ok" 全部 ✓；"error" 全部 ✓ 但最后一个标红（罕见：所有步骤都过了只有最后一步失败）。
   */
  function markAllStagesDone(kind = "ok") {
    const order = ["fetching_chats", "ai_summarize", "render_png", "render_md", "done"];
    document.querySelectorAll("#stages li").forEach((li) => {
      li.classList.remove("active", "done", "error");
      li.classList.add("done");
      const statusEl = li.querySelector(".stage-status");
      if (statusEl) statusEl.textContent = "已完成";
    });
    if (kind === "error") {
      // 最后一个阶段标红（罕见：仅在「全部跑完但最后一步失败」用）
      const last = document.querySelector(`#stages li[data-stage="${order[order.length - 1]}"]`);
      if (last) {
        last.classList.remove("done");
        last.classList.add("error");
        const statusEl = last.querySelector(".stage-status");
        if (statusEl) statusEl.textContent = "失败";
      }
    }
  }

  // 监听 sidecar 推过来的 progress 事件
  onProgress((p) => {
    if (typeof p.percent === "number") {
      const fill = $("progress-fill");
      if (fill) fill.style.width = p.percent + "%";
    }
    if (p.stage) {
      state.currentStage = p.stage;
      markStage(p.stage);
    }
  });

  // ── 发送 ──────────────────────────────────────────────────────────
  /**
   * v0.3：收集「当前报告」+「历史日报」里所有勾选的文件。
   * 返回 [{kind, path, name, size, source}, ...] 数组。
   *   - kind: "png"（发给钉钉） / "md"（复制文本）
   *   - source: "current" / "history"
   */
  function collectSelectedFiles() {
    const out = [];
    // 当前报告
    const cur = state.currentReport;
    if (cur?.output) {
      const pngCheck = document.querySelector('.file-check[data-kind="png"]');
      const mdCheck = document.querySelector('.file-check[data-kind="md"]');
      if (pngCheck?.checked && cur.output.png_path) {
        out.push({
          kind: "png", source: "current",
          path: cur.output.png_path,
          name: pathBasename(cur.output.png_path),
          size: cur.png_size_bytes || 0,
        });
      }
      if (mdCheck?.checked && cur.output.md_path) {
        out.push({
          kind: "md", source: "current",
          path: cur.output.md_path,
          name: pathBasename(cur.output.md_path),
          size: cur.md_size_bytes || 0,
        });
      }
    }
    // v0.3 后续：历史日报也走同一勾选模型；本轮先支持「当前报告」+ 历史单 PNG 发送（保留旧入口）
    return out;
  }

  function showSendModal(selected) {
    const list = $("send-files-list");
    if (!list) return;
    if (!selected.length) {
      toast("请先勾选要发送的文件", "warn");
      return;
    }
    list.innerHTML = selected.map((f) => `
      <div class="send-file-item" data-kind="${f.kind}">
        <span class="send-file-kind">${f.kind === "png" ? "📤 发钉钉" : "📄 发钉钉"}</span>
        <span class="send-file-name" title="${escapeHtml(f.path)}">${escapeHtml(f.name)}</span>
        <span class="send-file-size">${fmtSize(f.size)}</span>
      </div>
    `).join("");
    const me = state.status?.user;
    $("send-recipient").textContent = me?.name
      ? `📤 发给「${me.name}」（自己）`
      : "自己（自动识别当前登录）";
    $("send-modal").classList.remove("hidden");
  }

  // self 模式（保存到本地）：直接调系统资源管理器打开文件所在目录
  function openContainingFolder(filePath) {
    if (window.dtbox?.openPath) {
      const dir = filePath.replace(/[\\/][^\\/]+$/, "");
      window.dtbox.openPath(dir);
      toast("已打开文件所在文件夹（self 模式仅保存本地，不发送钉钉）", "ok");
    } else {
      toast("self 模式仅保存本地，已生成在 output/ 目录", "warn");
    }
  }

  /**
   * v0.3：手动发送勾选文件。每个文件单独 send_to_dingtalk，按 kind 分发：
   *   - png / md 都走 dws 文件消息（--msg-type file），与图片相同
   * 失败的文件徽章会显示 ❌，成功的显示 ✓。已成功的不会被重复发。
   */
  async function onSendConfirm() {
    // v0.3.4：未登录兜底拦截（正常路径已拦截，但 modal 可能被外部脚本触发）
    if (!requireLogin()) return;
    $("send-modal").classList.add("hidden");
    const selected = collectSelectedFiles();
    if (!selected.length) return;
    setStatus("发送中…");
    let pngOk = 0, pngFail = 0, mdOk = 0, mdFail = 0;
    try {
      const me = state.status?.user;
      if (!me?.open_dingtalk_id && !me?.user_id) {
        toast("未拿到当前登录人标识，请重新登录", "error", 5000);
        return;
      }
      for (const f of selected) {
        // v0.3.2：MD 也走 dws 文件消息（--msg-type file，与 PNG 同样行为）
        setFileSendStatus(f.kind, "sending", "正在发送…");
        try {
          await sendOneFile(f, me);
          // v0.3.3：文件行不再显示「已发给 xxx（task=...）」文字（与发送步骤的 ✓ 重复）
          setFileSendStatus(f.kind, "sent", "");
          if (f.kind === "png") pngOk++; else mdOk++;
        } catch (e) {
          setFileSendStatus(f.kind, "failed", `❌ 发送失败：${e?.message || e}`);
          if (f.kind === "png") pngFail++; else mdFail++;
        }
      }
      // v0.3.3：所有勾选文件都跑完后，标「发送钉钉」步骤 done（✓）
      // 失败的文件以 ❌ 徽章体现在文件行，进度条最后一步依然给 ✓（避免进度条停在 active 状态）
      const allFail = (pngFail + mdFail) > 0;
      markAllStagesDone(allFail ? "error" : "ok");
      // 汇总 toast
      const total = pngOk + mdOk;
      const fail = pngFail + mdFail;
      if (total > 0 && fail === 0) {
        toast(`✓ 全部发送完成（${pngOk} 图 + ${mdOk} 文档）`, "ok", 4000);
      } else if (fail > 0) {
        toast(`部分失败：${total} 成功 / ${fail} 失败`, "warn", 6000);
      }
    } finally {
      setStatus("就绪");
    }
  }

  /** 内部：发一个文件（PNG 或 MD），返回 send_to_dingtalk 的 result。失败抛异常。 */
  async function sendOneFile(f, me) {
    let date = state.currentReport?.date;
    if (!date) {
      const it = state.history.find((x) => x.png_path === f.path || x.md_path === f.path);
      date = it?.date;
    }
    if (!date) date = pathBasename(f.path).match(/\d{4}-\d{2}-\d{2}/)?.[0] || "";
    const params = {
      file_path: f.path,
      title: `钉钉工作纪要日报 ${date}`.trim(),
    };
    if (me.open_dingtalk_id) params.open_dingtalk_id = me.open_dingtalk_id;
    if (me.user_id) params.user_id = me.user_id;
    return await call("send_to_dingtalk", params);
  }

  // ── 勾选 / 全选 / 计数 ────────────────────────────────────
  function updateSelectCount() {
    const checks = document.querySelectorAll(".file-check");
    const total = checks.length;
    let n = 0;
    checks.forEach((c) => { if (c.checked) n++; });
    $("report-select-count").textContent = total > 0 ? `已选 ${n}/${total} 个` : "";
    // 全选 checkbox 状态同步
    const all = $("report-check-all");
    if (all) {
      all.checked = total > 0 && n === total;
      all.indeterminate = n > 0 && n < total;
    }
  }

  // ── 错误显示 ─────────────────────────────────────────────────────
  // v0.3.10：error-card 简化为「系统报错」+ 错误信息（无详情 details、无重试/诊断按钮）。
  // 错误信息只显示 title + err.message（code / data 详情一律不展示）。
  function showError(title, err) {
    const msg = err?.message || String(err);
    $("error-msg").innerHTML = `<b>${escapeHtml(title)}</b>：${escapeHtml(msg)}`;
    $("error-card").classList.remove("hidden");
    // Opt 3 修复：失败时把当前进度 stage 标红，让用户看到「哪步炸了」
    if (state.currentStage) markStage(state.currentStage, "error");
    setStatus("出错");
  }

  function escapeHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }
  // v0.3.10：诊断功能入口已去掉（error-card 简化为「系统报错」+ 错误信息，无重试/诊断按钮）。
  // sidecar 仍保留 `diagnose` RPC（dispatcher.py METHODS），后续要排查问题可通过其他途径
  // 直接调 RPC 或让用户贴 %APPDATA%/DingTalkBox/logs/sidecar-*.log 末尾 200 行。

  // ── call helper ──────────────────────────────────────────────────
  async function call(method, params) {
    // 优先用 launcher 注入的 window.dtbox.call（兼容 dev / pywebview 双重调用栈）
    let raw;
    if (window.dtbox && typeof window.dtbox.call === "function") {
      raw = await window.dtbox.call(method, params);
    } else if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.call === "function") {
      // fallback：直接走 pywebview JS API（webview2 一加载就可用，比 loaded 事件早）
      raw = await window.pywebview.api.call(method, params || {});
    } else {
      throw new Error("sidecar 未就绪（window.dtbox.call / window.pywebview.api.call 均不可用）");
    }
    // bridge.call 返回 {result: ...} 或 {error: ...}；解包出真正的 payload
    if (raw && typeof raw === "object") {
      if ("error" in raw) {
        const e = raw.error || {};
        const err = new Error(e.message || "sidecar error");
        err.code = e.code;
        throw err;
      }
      if ("result" in raw) return raw.result;
    }
    return raw;
  }

  function setStatus(msg) {
    $("status-bar").textContent = msg;
  }

  // ── 事件绑定 ─────────────────────────────────────────────────────
  function bindEvents() {
    $("btn-login").addEventListener("click", onLogin);
    // v0.3.3：登出按钮已彻底删除（HTML 里的 #btn-logout 已不存在），不绑事件
    $("btn-generate").addEventListener("click", onGenerate);
    // v0.3：📤 发送钉钉 放列表底部，按勾选分发文件（不再 row 级单按钮）
    $("btn-send").addEventListener("click", () => {
      // v0.3.4：未登录拦截
      if (!requireLogin()) return;
      if (!state.currentReport?.output?.png_path && !state.currentReport?.output?.md_path) {
        toast("请先生成报告", "warn");
        return;
      }
      const selected = collectSelectedFiles();
      if (!selected.length) {
        toast("请先勾选要发送的文件", "warn");
        return;
      }
      showSendModal(selected);
    });
    // 报告行按钮（v0.3：行内按钮只剩 [查看][打开文件夹]；发送/复制走列表底部）
    // v0.3.8：PNG 预览调系统默认图片查看器（与历史日报打开按钮一致）；
    //          MD 预览走 app 内嵌 modal 渲染（避免 .md 没关联应用）
    $("btn-view-png")?.addEventListener("click", () => {
      const p = state.currentReport?.output?.png_path;
      if (!p) { toast("无 PNG 路径", "warn"); return; }
      if (window.dtbox?.openPath) window.dtbox.openPath(p);
      else toast("无法打开文件", "error");
    });
    $("btn-view-md")?.addEventListener("click", () => {
      const p = state.currentReport?.output?.md_path;
      if (!p) { toast("无 Markdown 路径", "warn"); return; }
      openPreview("md", p);
    });
    $("btn-open-md-dir")?.addEventListener("click", () => {
      const p = state.currentReport?.output?.md_path;
      if (p && window.dtbox?.openPath) {
        const dir = p.replace(/[\\/][^\\/]+$/, "");
        window.dtbox.openPath(dir);
      } else {
        toast("无 Markdown 路径", "warn");
      }
    });
    // v0.3.2：MD 行的「复制文本」按钮已删除 — MD 现在走底部 📤 发送钉钉（dws 文件消息）
    $("btn-open-output").addEventListener("click", () => {
      const r = state.currentReport;
      if (r?.output?.png_path && window.dtbox?.openPath) {
        const dir = r.output.png_path.replace(/[\\/][^\\/]+$/, "");
        window.dtbox.openPath(dir);
      } else {
        call("open_output", {}).then((res) => {
          if (res.path && window.dtbox?.openPath) window.dtbox.openPath(res.path);
        });
      }
    });
    // v0.3：勾选 / 全选 / 计数
    document.querySelectorAll(".file-check").forEach((cb) => {
      cb.addEventListener("change", updateSelectCount);
    });
    $("report-check-all")?.addEventListener("change", (e) => {
      const checked = e.target.checked;
      document.querySelectorAll(".file-check").forEach((cb) => { cb.checked = checked; });
      updateSelectCount();
    });
    $("btn-send-confirm").addEventListener("click", onSendConfirm);
    $("btn-send-cancel").addEventListener("click", () => $("send-modal").classList.add("hidden"));
    // 登录 modal 状态机按钮
    // v0.3.10：去掉 btn-trigger-login 按钮（弹窗即启动）
    $("btn-cancel-login").addEventListener("click", cancelLogin);
    $("btn-close-login")?.addEventListener("click", cancelLogin);

    // v0.3.9：点头像弹 popover（账号信息 + 退出登录），与项目玻璃风格一致
    const avatarBtn = $("btn-avatar");
    const avatarPopover = $("avatar-popover");
    if (avatarBtn && avatarPopover) {
      avatarBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        toggleAvatarPopover();
      });
      $("avatar-action-btn").addEventListener("click", onAvatarAction);
      // 点击 popover 外部自动关闭
      document.addEventListener("click", (e) => {
        if (avatarPopover.classList.contains("hidden")) return;
        if (!avatarPopover.contains(e.target) && e.target !== avatarBtn) {
          avatarPopover.classList.add("hidden");
        }
      });
      // ESC 关闭
      document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && !avatarPopover.classList.contains("hidden")) {
          avatarPopover.classList.add("hidden");
        }
      });
    }
    $("btn-dws-install").addEventListener("click", onDwsInstall);
    $("btn-dws-install-skip").addEventListener("click", onDwsInstallSkip);
    $("btn-exit").addEventListener("click", () => {
      if (window.dtbox?.exit) window.dtbox.exit();
      else window.close();
    });
    $("btn-corp-relogin")?.addEventListener("click", async () => {
      // corp 不匹配 → 关 modal + 登出 + 清空旧账号页面数据 + 重弹登录 modal 让用户切账号
      $("corp-modal").classList.add("hidden");
      try {
        await call("trigger_logout", {});
        state.status = await call("get_login_status", {});
        clearSessionData();  // v0.3.9：登出后清空历史/当前日报/预览
        renderUserCard();
        // v0.2：走 onLogin() 走状态机重置（idle），不是直接显 modal
        onLogin();
        toast("已登出，请用本公司账号登录", "ok");
      } catch (e) {
        showError("登出失败", e);
      }
    });
    $("date-input").value = today();
    syncDateDisplay();

    // ── 日期弹窗事件 ────────────────────────────────────────────
    dateDisplay.addEventListener("click", openDateModal);
    $("date-prev-year").addEventListener("click", () => { dpViewYear--; renderDateGrid(); });
    // v0.3.9：下一年按钮在「当前年」时不可点（未来年份没数据可生成）
    $("date-next-year").addEventListener("click", () => {
      const td = new Date();
      if (dpViewYear >= td.getFullYear()) return;
      dpViewYear++;
      renderDateGrid();
    });
    $("date-prev-month").addEventListener("click", () => {
      dpViewMonth--;
      if (dpViewMonth < 0) { dpViewMonth = 11; dpViewYear--; }
      renderDateGrid();
    });
    // v0.3.9：下一月按钮在「当前月」时不可点（未来月份没数据可生成）。
    // 跨年时本应允许跳到明年 1 月，但今天之前的月份永远允许往前翻。
    $("date-next-month").addEventListener("click", () => {
      const td = new Date();
      const isCurrentView = (dpViewYear === td.getFullYear() && dpViewMonth === td.getMonth());
      if (isCurrentView) return;  // 未来月份禁止翻入
      dpViewMonth++;
      if (dpViewMonth > 11) { dpViewMonth = 0; dpViewYear++; }
      renderDateGrid();
    });
    $("date-clear").addEventListener("click", () => {
      $("date-input").value = "";
      syncDateDisplay();
      closeDateModal();
    });
    $("date-today").addEventListener("click", () => {
      const d = new Date();
      const iso = fmtDateInput(d.getFullYear(), d.getMonth(), d.getDate());
      $("date-input").value = iso;
      dateDisplayText.textContent = fmtDateDisplay(d.getFullYear(), d.getMonth(), d.getDate());
      closeDateModal();
    });
    dateModal.addEventListener("click", (e) => {
      if (e.target === dateModal) closeDateModal();
    });

    // ── LLM modal 事件 ──────────────────────────────────────────
    // v0.3.7：合并图标 + 徽章 → 顶栏模型按钮也绑同一 handler
    const topbarModelBtn = $("topbar-model-btn");
    if (topbarModelBtn) topbarModelBtn.addEventListener("click", openLlmModal);
    $("btn-close-llm").addEventListener("click", closeLlmModal);
    // 点 modal 背景（不是 modal-box）也关闭 → ESC 不可用的 pywebview 也能关
    $("llm-modal").addEventListener("click", (e) => {
      if (e.target === $("llm-modal")) closeLlmModal();
    });
    // ESC 键关闭
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if (!$("preview-modal").classList.contains("hidden")) {
        closePreview();
      } else if (!$("llm-modal").classList.contains("hidden")) {
        closeLlmModal();
      }
    });
    $("btn-save-llm").addEventListener("click", saveLlmConfig);
    $("btn-clear-llm").addEventListener("click", clearLlmConfig);
    $("btn-toggle-key").addEventListener("click", () => {
      const inp = $("llm-key");
      inp.type = inp.type === "password" ? "text" : "password";
    });
    $("llm-provider").addEventListener("change", onLlmProviderChange);
    ["llm-model", "llm-key"].forEach((id) => {
      $(id).addEventListener("input", () => { llmState.dirty = true; });
    });
  }

  // ── LLM modal 逻辑 ──────────────────────────────────────────
  async function openLlmModal() {
    llmState.dirty = false;
    $("llm-modal").classList.remove("hidden");
    // 不再 toast「加载中…」（用户已看到 modal 弹出，loading 状态隐含即可）
    try {
      // 1. 拉厂商 + 模型清单（侧 bundled providers.yaml）
      const lp = await call("list_providers", {});
      llmState.providers = lp.providers || [];
      const provSel = $("llm-provider");
      provSel.innerHTML = llmState.providers.map((p) => `<option value="${p.id}">${escapeHtml(p.name)}</option>`).join("");
      // 2. 拉当前配置
      const cfg = await call("get_llm_config", {});
      llmState.current = cfg;
      if (cfg.configured) {
        provSel.value = cfg.provider;
        // 填充模型下拉并选中已配模型
        renderModelsForProvider(cfg.provider, cfg.model);
        // 脱敏 key 填入输入框（中间 ... 形式，不暴露完整密钥）
        $("llm-key").value = cfg.key_masked || "";
      } else {
        provSel.value = "openai";
        renderModelsForProvider("openai");
        $("llm-key").value = "";
      }
      // v0.3.10：弹窗打开瞬间记下 key 输入框的"原值"，保存时比对决定是否跳过 secret 写入
      // （用户没改 key 就保存时，cfg.key_masked 脱敏串跟原值相等 → skip_key=true → 不覆盖 llm_secret.bin）
      llmState.originalKey = $("llm-key").value;
    } catch (e) {
      setLlmStatus("加载失败：" + (e?.message || e), "bad");
    }
  }

  function renderModelsForProvider(providerId, selectedModelId) {
    const msel = $("llm-model");
    const p = llmState.providers.find((x) => x.id === providerId);
    if (!p) {
      msel.innerHTML = "";
      // 区分：providers 列表空（厂商加载失败） vs 选中的 provider 不存在
      if (llmState.providers.length === 0) {
        setLlmStatus("⚠ 厂商列表加载失败，请检查 dws / 网络", "bad");
        toast("厂商列表加载失败", "error", 5000);
      }
      return;
    }
    const models = p.models || [];
    msel.innerHTML = models.map((m) =>
      `<option value="${escapeHtml(m.id)}">${escapeHtml(m.name)}</option>`
    ).join("");
    if (selectedModelId && models.some((m) => m.id === selectedModelId)) {
      msel.value = selectedModelId;
    }
  }

  async function closeLlmModal() {
    if (llmState.dirty) {
      const ok = await confirmModal({
        title: "放弃修改？",
        message: "有未保存的修改，确定关闭吗？",
        okText: "放弃关闭",
        cancelText: "继续编辑",
      });
      if (!ok) return;
    }
    $("llm-modal").classList.add("hidden");
    llmState.dirty = false;
  }

  function onLlmProviderChange() {
    const sel = $("llm-provider");
    renderModelsForProvider(sel.value);
    $("llm-key").value = "";
    // v0.3.9: 去掉"已切换厂商，请输入 API Key"提示语 — key 字段已清空，用户能直接看出要填
    llmState.dirty = true;
  }

  async function saveLlmConfig() {
    const provider = $("llm-provider").value;
    const model = $("llm-model").value;
    const api_key = $("llm-key").value;
    // v0.3.10：diff 检测 —— 用户没改 key 时跳过覆盖 llm_secret.bin
    // 否则会把 cfg.key_masked（脱敏占位串）当成新 key 存进去，导致 qwen 401
    const skip_key = api_key === llmState.originalKey;
    setLlmStatus("保存中…", "");
    try {
      await call("set_llm_config", { provider, model, api_key, skip_key });
      toast(`✓ 已保存 · ${provider} · ${model}`, "ok", 2500);
      llmState.dirty = false;
      // v0.3.7：保存成功后自动关闭弹窗
      $("llm-modal").classList.add("hidden");
      updateBootStatus();
    } catch (e) {
      setLlmStatus("保存失败：" + (e?.message || String(e)), "bad");
    }
  }

  async function clearLlmConfig() {
    const ok = await confirmModal({
      title: "清除配置？",
      message: "确定清除当前 LLM 配置？\n旧的 API Key 将被永久删除。",
      okText: "清除",
      danger: true,
    });
    if (!ok) return;
    try {
      await call("set_llm_config", { clear: true });
      toast("✓ 已清除", "ok");
      llmState.dirty = false;
      $("llm-key").value = "";
      updateBootStatus();
    } catch (e) {
      setLlmStatus("清除失败：" + (e?.message || e), "bad");
    }
  }

  // ── 预览 modal（仅 MD 渲染；PNG 走系统默认图片查看器）─────────
  // markdown-it 实例：开启 linkify + html 走 DOMPurify 净化
  const _md = (typeof markdownit === "function")
    ? markdownit({ html: true, linkify: true, breaks: false, typographer: true })
    : null;

  async function openPreview(kind, path) {
    const modal = $("preview-modal");
    const title = $("preview-title");
    const body = $("preview-body");
    if (!modal || !body) return;
    const fname = (path || "").split(/[\\/]/).pop() || "";
    title.textContent = `预览 Markdown · ${fname}`;
    body.innerHTML = '<div class="preview-loading">加载中…</div>';
    modal.classList.remove("hidden");
    try {
      // MD → 走 sidecar 读文本，markdown-it 渲染 + DOMPurify XSS 净化
      const r = await call("read_text_file", { path });
      const text = r.content || "";
      let html = "";
      if (_md) {
        html = _md.render(text);
        // DOMPurify 兜底 XSS（即使 markdown-it 默认不解析 raw HTML，也开 html:true 了）
        if (typeof DOMPurify !== "undefined" && DOMPurify.sanitize) {
          html = DOMPurify.sanitize(html, { ADD_ATTR: ["target"] });
        }
      } else {
        // vendor JS 没加载成功 → 降级到 <pre>
        html = `<pre>${escapeHtml(text)}</pre>`;
      }
      body.innerHTML = "";
      const wrap = document.createElement("div");
      wrap.className = "markdown-body";
      wrap.innerHTML = html;
      body.appendChild(wrap);
    } catch (e) {
      body.innerHTML = `<div class="preview-loading">❌ 加载失败：${escapeHtml(String(e?.message || e))}</div>`;
    }
  }

  function closePreview() {
    const modal = $("preview-modal");
    if (modal) modal.classList.add("hidden");
    const body = $("preview-body");
    if (body) body.innerHTML = "";
  }

  // 点 modal 背景（不是 modal-box）也关闭 → ESC 不可用的 pywebview 也能关
  $("preview-modal")?.addEventListener("click", (e) => {
    if (e.target === $("preview-modal")) closePreview();
  });
  // ESC 关闭预览 modal（避免和已有 LLM modal ESC 冲突——共用 document keydown）

  function setLlmStatus(msg, kind) {
    // status row 已删除 → 改用 toast 反馈
    if (!msg) return;
    const lvl = kind === "bad" || kind === "error" ? "error"
              : kind === "warn" ? "warn"
              : kind === "ok"   ? "ok"
              : "info";
    toast(msg, lvl, kind === "bad" ? 5000 : 2500);
  }

  async function updateBootStatus() {
    try {
      const cfg = await call("get_llm_config", {});
      const btn = $("topbar-model-btn");
      if (!btn) return;
      if (cfg.configured) {
        // 已配置：显示模型 id（如 "MiniMax-M2"）
        btn.textContent = cfg.model || cfg.provider || "已配置";
        btn.title = `当前模型：${cfg.provider} · ${cfg.model || ""}（点此切换）`;
        btn.classList.remove("unbound");
        btn.classList.add("bound");
      } else {
        // 未配置：显示"未绑定模型"，提示用户点此配置
        btn.textContent = "未绑定模型";
        btn.title = "点击配置 AI 模型";
        btn.classList.remove("bound");
        btn.classList.add("unbound");
      }
    } catch (e) {
      console.warn("get_llm_config failed", e);
    }
  }

  // ── v0.3.9：点头像弹 popover（账号信息 + 动态按钮） ─────────────
  // 根据登录态切换 popover 内按钮的图标/文案/handler：
  //   - 已登录 → 红色「退出登录」（logout 图标 + onAvatarLogout）
  //   - 未登录 → 蓝紫「登录账号」（login 图标 + onLogin → 弹登录 modal）
  const _ICON_LOGOUT = '<svg id="avatar-action-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>';
  const _ICON_LOGIN   = '<svg id="avatar-action-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><polyline points="10 17 15 12 10 7"/><line x1="15" y1="12" x2="3" y2="12"/></svg>';

  function toggleAvatarPopover() {
    const popover = $("avatar-popover");
    if (!popover) return;
    if (popover.classList.contains("hidden")) {
      const logged_in = !!(state.status && state.status.logged_in);
      const user = state.status?.user || null;
      const corp = state.status?.corp || null;
      // 顶部信息：未登录时统一显示「***」（占位符，与主区 hero 一致）
      $("avatar-popover-name").textContent = logged_in
        ? (user?.nick || user?.name || "已登录")
        : "***";
      const corpEl = $("avatar-popover-corp");
      if (logged_in && corp) {
        corpEl.textContent = corp.corp_name || corp.corp_id || "—";
        corpEl.style.display = "";
      } else {
        corpEl.textContent = "***";
        corpEl.style.display = "";
      }
      // 底部按钮：根据状态切换文案/图标/handler
      const btn = $("avatar-action-btn");
      const lbl = $("avatar-action-label");
      const icon = $("avatar-action-icon");
      if (logged_in) {
        btn.dataset.mode = "logout";
        btn.classList.remove("primary");
        btn.classList.add("danger");
        icon.outerHTML = _ICON_LOGOUT;
        lbl.textContent = "退出登录";
      } else {
        btn.dataset.mode = "login";
        btn.classList.remove("danger");
        btn.classList.add("primary");
        icon.outerHTML = _ICON_LOGIN;
        lbl.textContent = "登录账号";
      }
      // 重新获取更新后的 icon 节点（outerHTML 替换后原引用失效）
      const newIcon = $("avatar-action-icon");
      popover.classList.remove("hidden");
    } else {
      popover.classList.add("hidden");
    }
  }

  async function onAvatarAction() {
    // 关闭 popover
    $("avatar-popover").classList.add("hidden");
    const mode = $("avatar-action-btn")?.dataset.mode || "login";
    if (mode === "logout") {
      // 已登录 → 二次确认登出
      const ok = await confirmModal({
        title: "退出登录？",
        message: "退出后将清除当前钉钉账号登录态，需要重新扫码登录。",
        okText: "退出",
        cancelText: "取消",
        danger: true,
      });
      if (!ok) return;
      try {
        await call("trigger_logout", {});
        toast("✓ 已登出", "ok", 2000);
        state.status = await call("get_login_status", {});
        // v0.3.9：登出后清空所有页面数据（历史日报 / 当前生成结果 / 预览）
        // 否则旧账号残留数据会在下次登录后误导用户
        clearSessionData();
        renderUserCard();
        if (!state.status?.logged_in) {
          // v0.3.10：登出后**不**自动弹登录 modal —— 用户刚登出，
          // 本意就是不想登录。modal 留到用户主动点"登录账号"时再弹。
          setStatus("已退出登录");
          // 同步刷新右上角头像卡片（显示 ***）
          renderUserCard();
        }
      } catch (e) {
        showError("登出失败", e);
      }
    } else {
      // 未登录 → 直接弹登录 modal
      onLogin();
    }
  }

  // ── 启动 ──────────────────────────────────────────────────────────
  document.addEventListener("DOMContentLoaded", () => {
    bindEvents();
    boot();
  });
})();
