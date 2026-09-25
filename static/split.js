"use strict";

// 双语 SRT 拆轨：粘贴每块两行的双语 SRT，并排预览中/英两轨归属与总错配分，
// 可分别下载保留原时间码与正文行、从 1 重编号的中文轨 / 英文轨 SRT
(() => {
  const $ = (id) => document.getElementById(id);

  const els = {
    srt: $("spSrt"),
    runBtn: $("spRunBtn"),
    clearBtn: $("spClearBtn"),
    status: $("spStatus"),
    summary: $("spSummary"),
    resultWrap: $("spResultWrap"),
    body: $("spBody"),
    downloadZhBtn: $("spDownloadZhBtn"),
    downloadEnBtn: $("spDownloadEnBtn"),
  };

  const SAMPLE_SRT = [
    "1",
    "00:00:01,000 --> 00:00:03,000",
    "你好，世界！",
    "Hello, world!",
    "",
    "2",
    "00:00:04,000 --> 00:00:06,000",
    "今天我们来聊聊双语字幕拆轨。",
    "Today we talk about bilingual track splitting.",
    "",
    "3",
    "00:00:07,000 --> 00:00:09,000",
    "This block was flipped by mistake.",
    "这一块的中英行被颠倒了。",
    "",
    "4",
    "00:00:10,000 --> 00:00:12,000",
    "So was this one.",
    "这一块也是。",
    "",
    "5",
    "00:00:13,000 --> 00:00:14,500",
    "3、2、1——",
    "3... 2... 1...",
    "",
    "6",
    "00:00:15,000 --> 00:00:17,000",
    "拆轨完成。",
    "Splitting done.",
    "",
  ].join("\n");

  $("spSample").addEventListener("click", () => {
    els.srt.value = SAMPLE_SRT;
  });

  els.clearBtn.addEventListener("click", () => {
    els.srt.value = "";
    els.summary.classList.add("hidden");
    els.resultWrap.classList.add("hidden");
    setStatus("", false);
  });

  function setStatus(msg, isError) {
    els.status.textContent = msg;
    els.status.classList.toggle("error", !!isError);
  }

  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[c]);
  }

  function orderBadge(r) {
    const flip = r.order_change
      ? '<span class="flip-marker" title="与上一块上下顺序不同">↕</span>'
      : "";
    return r.zh_first
      ? `<span class="badge badge-zh-first">中文在上</span>${flip}`
      : `<span class="badge badge-en-first">英文在上</span>${flip}`;
  }

  function costCell(cost) {
    return cost > 0
      ? `<span class="cost-high">${cost}</span>`
      : '<span class="dim">0</span>';
  }

  function render(data) {
    const s = data.summary;
    $("spCnt").textContent = s.total;
    $("spMismatch").textContent = s.mismatch;
    $("spFlips").textContent = s.order_changes;
    $("spCost").textContent = s.total_cost;

    const frag = document.createDocumentFragment();
    for (const r of data.rows) {
      const tr = document.createElement("tr");
      tr.className = r.zh_first ? "row-zh-first" : "row-en-first";
      tr.innerHTML = `
        <td class="cell-idx">${r.new_index}</td>
        <td class="cell-idx">${r.index}</td>
        <td class="cell-tc">${escapeHtml(r.start_tc)} → ${escapeHtml(r.end_tc)}</td>
        <td>${orderBadge(r)}</td>
        <td class="cell-text">${escapeHtml(r.zh_line)}</td>
        <td class="cell-text">${escapeHtml(r.en_line)}</td>
        <td class="cell-num">${costCell(r.cost)}</td>`;
      frag.appendChild(tr);
    }
    els.body.replaceChildren(frag);
    els.summary.classList.remove("hidden");
    els.resultWrap.classList.remove("hidden");
  }

  let lastRequest = null;

  async function run() {
    lastRequest = { srt: els.srt.value };

    els.runBtn.disabled = true;
    setStatus("拆轨中……", false);
    try {
      const resp = await fetch("/api/split", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(lastRequest),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: `请求失败（${resp.status}）` }));
        throw new Error(err.detail || `请求失败（${resp.status}）`);
      }
      const data = await resp.json();
      render(data);
      const s = data.summary;
      setStatus(
        `完成：${s.total} 个字幕块，错配 ${s.mismatch} 字符，` +
        `顺序变化 ${s.order_changes} 次，总代价 ${s.total_cost}`,
        false
      );
    } catch (e) {
      lastRequest = null;
      els.summary.classList.add("hidden");
      els.resultWrap.classList.add("hidden");
      setStatus(e.message, true);
    } finally {
      els.runBtn.disabled = false;
    }
  }

  async function downloadSrt(track, filename) {
    if (!lastRequest) {
      setStatus("请先执行拆轨", true);
      return;
    }
    const btn = track === "zh" ? els.downloadZhBtn : els.downloadEnBtn;
    btn.disabled = true;
    try {
      const resp = await fetch(`/api/split.${track}.srt`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(lastRequest),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: "下载失败" }));
        throw new Error(err.detail || "下载失败");
      }
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setStatus(e.message, true);
    } finally {
      btn.disabled = false;
    }
  }

  els.runBtn.addEventListener("click", run);
  els.downloadZhBtn.addEventListener("click", () => downloadSrt("zh", "split-zh.srt"));
  els.downloadEnBtn.addEventListener("click", () => downloadSrt("en", "split-en.srt"));
})();
