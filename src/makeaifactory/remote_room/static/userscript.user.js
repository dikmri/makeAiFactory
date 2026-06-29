// ==UserScript==
// @name         makeAiFactory 転送
// @namespace    https://github.com/dikmri/makeAiFactory
// @version      1.0.1
// @description  ブラウザ上の画像にホバーして出る「動画化」ボタンから makeAiFactory へ転送し、そのまま動画生成する
// @match        *://*/*
// @connect      *
// @grant        GM_xmlhttpRequest
// @grant        GM_registerMenuCommand
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_addStyle
// @grant        GM_notification
// @run-at       document-idle
// @noframes
// ==/UserScript==

// このファイルはアプリが配信時に __MAF_PORT__ / __MAF_TOKEN__ を実際の値へ置換する。
(function () {
  "use strict";

  const PORT = "__MAF_PORT__";
  const TOKEN = "__MAF_TOKEN__";
  const BASE = "http://127.0.0.1:" + PORT;
  const MIN_SIZE = 120; // この px 未満の画像にはボタンを出さない

  let workflows = [];     // [{key,label,desc}]
  let currentImg = null;
  let btn = null;

  GM_addStyle(
    ".maf-send-btn{position:absolute;z-index:2147483647;display:none;gap:6px;" +
    "align-items:center;background:#253858;color:#fff;border:1px solid #5a8;" +
    "border-radius:6px;font:12px/1.4 sans-serif;padding:4px 8px;cursor:pointer;" +
    "opacity:.96;box-shadow:0 2px 8px rgba(0,0,0,.4)}" +
    ".maf-send-btn:hover{background:#2f4a73}" +
    ".maf-send-btn>span{cursor:pointer}" +
    ".maf-send-btn select{background:#1a1a2e;color:#fff;border:1px solid #5a8;" +
    "border-radius:4px;font:12px sans-serif;padding:1px 2px}"
  );

  function gmRequest(opts) {
    return new Promise(function (resolve, reject) {
      GM_xmlhttpRequest(Object.assign({
        onload: resolve,
        onerror: function (e) { reject(e); },
        ontimeout: function () { reject(new Error("timeout")); },
      }, opts));
    });
  }

  function notify(text) {
    try { GM_notification({ title: "makeAiFactory", text: text, timeout: 4000 }); }
    catch (_) { console.log("[makeAiFactory]", text); }
  }

  function defaultWorkflow() { return GM_getValue("maf_workflow", ""); }

  async function loadWorkflows() {
    try {
      const r = await gmRequest({ method: "GET", url: BASE + "/api/workflows" });
      workflows = (JSON.parse(r.responseText).workflows) || [];
    } catch (e) {
      workflows = [];
    }
  }

  // 表示中の <img> を canvas 経由で PNG Blob 化する (同一オリジン/CORS許可なら確実)。
  // cross-origin で汚染されている場合は SecurityError になるので reject する。
  function imgToBlobViaCanvas(img) {
    return new Promise(function (resolve, reject) {
      try {
        const w = img.naturalWidth, h = img.naturalHeight;
        if (!w || !h) { reject(new Error("no natural size")); return; }
        const c = document.createElement("canvas");
        c.width = w; c.height = h;
        c.getContext("2d").drawImage(img, 0, 0);
        c.toBlob(function (b) {
          if (b && b.size > 256) resolve(b);
          else reject(new Error("toBlob failed"));
        }, "image/png");
      } catch (e) { reject(e); }
    });
  }

  // src を GM_xmlhttpRequest で取得して Blob 化 (canvas が汚染で使えない場合のフォールバック)。
  async function imgToBlobViaFetch(img) {
    const r = await gmRequest({ method: "GET", url: img.currentSrc || img.src, responseType: "arraybuffer" });
    const buf = r.response;
    const ctype = ((r.responseHeaders || "").match(/content-type:\s*([^\r\n;]+)/i) || [])[1] || "";
    if (!buf || buf.byteLength < 256) throw new Error("empty (" + (buf ? buf.byteLength : 0) + "B)");
    if (ctype && ctype.toLowerCase().indexOf("image") === -1) throw new Error("not image (" + ctype + ")");
    return new Blob([buf], { type: ctype || "image/png" });
  }

  async function sendImage(img, workflowKey) {
    let blob = null;
    try { blob = await imgToBlobViaCanvas(img); } catch (e) { blob = null; }
    if (!blob) {
      try { blob = await imgToBlobViaFetch(img); }
      catch (e) {
        notify("画像を取得できませんでした（" + (e && e.message ? e.message : e) +
               "）。保護された画像かもしれません。");
        return;
      }
    }
    try {
      const fd = new FormData();
      fd.append("image", blob, "image.png");
      if (workflowKey) fd.append("workflow", workflowKey);
      const r = await gmRequest({
        method: "POST",
        url: BASE + "/api/jobs",
        headers: { "X-MAF-Local-Token": TOKEN },
        data: fd,
      });
      if (r.status >= 200 && r.status < 300) {
        notify("転送しました（アプリで生成中）");
      } else if (r.status === 401 || r.status === 403) {
        notify("認証エラー: スクリプトを入れ直してください");
      } else {
        notify("転送失敗: HTTP " + r.status);
      }
    } catch (e) {
      notify("転送エラー: アプリ側で「ブラウザ連携」が有効か確認してください");
    }
  }

  function ensureButton() {
    if (btn) return btn;
    btn = document.createElement("div");
    btn.className = "maf-send-btn";
    const label = document.createElement("span");
    label.textContent = "▶ 動画化";
    const sel = document.createElement("select");
    btn.appendChild(label);
    btn.appendChild(sel);
    btn._select = sel;
    label.addEventListener("click", function (ev) {
      ev.stopPropagation(); ev.preventDefault();
      if (currentImg) sendImage(currentImg, sel.value || defaultWorkflow());
    });
    sel.addEventListener("click", function (ev) { ev.stopPropagation(); });
    sel.addEventListener("change", function (ev) {
      ev.stopPropagation();
      GM_setValue("maf_workflow", sel.value);
    });
    document.body.appendChild(btn);
    return btn;
  }

  function fillSelect(sel) {
    sel.innerHTML = "";
    if (!workflows.length) {
      const o = document.createElement("option");
      o.value = ""; o.textContent = "（既定）";
      sel.appendChild(o);
      return;
    }
    const def = defaultWorkflow();
    for (const wf of workflows) {
      const o = document.createElement("option");
      o.value = wf.key; o.textContent = wf.label || wf.key;
      if (wf.key === def) o.selected = true;
      sel.appendChild(o);
    }
  }

  function showButtonFor(img) {
    currentImg = img;
    const b = ensureButton();
    fillSelect(b._select);
    const rect = img.getBoundingClientRect();
    b.style.display = "flex";
    b.style.top = (window.scrollY + rect.top + 6) + "px";
    b.style.left = (window.scrollX + rect.right - b.offsetWidth - 6) + "px";
  }

  function hideButton() {
    if (btn) btn.style.display = "none";
    currentImg = null;
  }

  document.addEventListener("mouseover", function (ev) {
    const t = ev.target;
    if (t && t.tagName === "IMG" &&
        t.naturalWidth >= MIN_SIZE && t.naturalHeight >= MIN_SIZE) {
      showButtonFor(t);
    }
  }, true);

  document.addEventListener("mouseout", function (ev) {
    const to = ev.relatedTarget;
    if (btn && to && (to === btn || btn.contains(to))) return;
    if (ev.target && ev.target.tagName === "IMG") {
      setTimeout(function () {
        if (btn && !btn.matches(":hover")) hideButton();
      }, 200);
    }
  }, true);

  GM_registerMenuCommand("makeAiFactory: ワークフロー一覧を再取得", async function () {
    await loadWorkflows();
    notify("ワークフロー一覧を更新しました (" + workflows.length + "件)");
  });

  loadWorkflows();
})();
