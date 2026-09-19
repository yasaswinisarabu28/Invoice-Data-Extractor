const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("file-input");
const batchStatus = document.getElementById("batch-status");
const resultsEl = document.getElementById("results");
const sparklesEl = document.getElementById("sparkles");

// --- Animated sparkle background ---
function createSparkles(count = 45) {
  for (let i = 0; i < count; i++) {
    const s = document.createElement("div");
    s.className = "sparkle";
    s.style.left = `${Math.random() * 100}%`;
    s.style.top = `${Math.random() * 100}%`;
    s.style.animationDelay = `${Math.random() * 4}s`;
    s.style.animationDuration = `${3 + Math.random() * 3}s`;
    sparklesEl.appendChild(s);
  }
}
createSparkles();

// --- Dropzone interactions ---
dropzone.addEventListener("click", () => fileInput.click());

dropzone.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") fileInput.click();
});

["dragenter", "dragover"].forEach((evt) => {
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.add("is-dragover");
  });
});

["dragleave", "drop"].forEach((evt) => {
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.remove("is-dragover");
  });
});

dropzone.addEventListener("drop", (e) => {
  const files = e.dataTransfer.files;
  if (files.length) uploadFiles(files);
});

fileInput.addEventListener("change", () => {
  if (fileInput.files.length) uploadFiles(fileInput.files);
  fileInput.value = ""; // allow re-selecting the same file later
});

// --- Upload + process ---
async function uploadFiles(fileList) {
  const formData = new FormData();
  for (const file of fileList) {
    formData.append("files", file);
  }

  batchStatus.textContent = `Uploading ${fileList.length} file(s)…`;
  resultsEl.innerHTML = "";

  try {
    const res = await fetch("/process", { method: "POST", body: formData });

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      batchStatus.textContent = data.error || "Something went wrong.";
      return;
    }

    // Read the streamed response as it arrives, line by line — each line
    // is one complete JSON result, so we can render it immediately instead
    // of waiting for the whole batch to finish.
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let successCount = 0;
    let errorCount = 0;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop(); // keep the last, possibly-incomplete line for next chunk

      for (const line of lines) {
        if (!line.trim()) continue;
        const result = JSON.parse(line);

        if (result.status === "success") successCount++;
        else errorCount++;

        batchStatus.textContent =
          `Processed ${result.index} of ${result.total}` +
          (errorCount ? ` (${successCount} ok, ${errorCount} failed)` : "…");

        renderResult(result);
      }
    }

    batchStatus.textContent = `Done — ${successCount} processed successfully` +
      (errorCount ? `, ${errorCount} failed.` : ".");
  } catch (err) {
    batchStatus.textContent = "Couldn't reach the server. Is app.py running?";
  }
}

// --- Download invoice as PDF (generated entirely in the browser) ---
function downloadInvoicePDF(invoice, filename) {
  const { jsPDF } = window.jspdf;
  const doc = new jsPDF();
  let y = 20;

  doc.setFont("helvetica", "bold");
  doc.setFontSize(16);
  doc.text(invoice.vendor_name, 15, y);
  y += 10;

  doc.setFont("helvetica", "normal");
  doc.setFontSize(10);
  doc.text(`Invoice Number: ${invoice.invoice_number}`, 15, y); y += 6;
  doc.text(`Date: ${invoice.invoice_date}`, 15, y); y += 6;
  doc.text(`Billed To: ${invoice.billed_to}`, 15, y); y += 12;

  doc.setFont("helvetica", "bold");
  doc.text("Description", 15, y);
  doc.text("Qty", 110, y);
  doc.text("Unit Price", 135, y);
  doc.text("Total", 175, y);
  y += 2;
  doc.setLineWidth(0.2);
  doc.line(15, y, 195, y);
  y += 6;

  doc.setFont("helvetica", "normal");
  invoice.line_items.forEach((li) => {
    doc.text(String(li.description).slice(0, 45), 15, y);
    doc.text(String(li.quantity), 110, y);
    doc.text(formatMoney(li.unit_price), 135, y);
    doc.text(formatMoney(li.total), 175, y);
    y += 7;
  });

  y += 4;
  doc.line(120, y, 195, y);
  y += 7;

  doc.text("Subtotal:", 135, y);
  doc.text(formatMoney(invoice.subtotal), 175, y);
  y += 7;

  (invoice.adjustments || []).forEach((a) => {
    doc.text(`${a.label}:`, 135, y);
    doc.text(`${a.amount < 0 ? "-" : "+"}${formatMoney(Math.abs(a.amount))}`, 175, y);
    y += 7;
  });

  doc.text("Tax:", 135, y);
  doc.text(formatMoney(invoice.tax), 175, y);
  y += 7;

  doc.setFont("helvetica", "bold");
  doc.text("Total:", 135, y);
  doc.text(formatMoney(invoice.total_amount), 175, y);

  const safeName = filename.replace(/[^a-z0-9]/gi, "_").toLowerCase();
  doc.save(`${safeName}_extracted.pdf`);
}

// --- Rendering ---
function formatMoney(n) {
  return Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function renderResult(result) {
  const card = document.createElement("div");
  card.className = "invoice-card" + (result.status === "error" ? " is-error" : "");

  if (result.status === "success") {
    const inv = result.invoice;

    const lineItemsRows = inv.line_items.map(li => `
      <tr>
        <td>${li.description}</td>
        <td class="num">${li.quantity}</td>
        <td class="num">${formatMoney(li.unit_price)}</td>
        <td class="num">${formatMoney(li.total)}</td>
      </tr>
    `).join("");

    const adjustmentRows = (inv.adjustments || []).map(a => `
      <div class="totals__row ${a.amount < 0 ? 'is-discount' : 'is-surcharge'}">
        <span>${a.label}</span>
        <span class="num">${a.amount < 0 ? '-' : '+'}${formatMoney(Math.abs(a.amount))}</span>
      </div>
    `).join("");

    card.innerHTML = `
      <div class="invoice-card__header">
        <span class="invoice-card__vendor">${inv.vendor_name}</span>
        <span class="invoice-card__filename">${result.filename}</span>
      </div>
      <div class="invoice-card__meta">
        <span>Invoice <strong>${inv.invoice_number}</strong></span>
        <span>Date <strong>${inv.invoice_date}</strong></span>
        <span>Billed to <strong>${inv.billed_to}</strong></span>
      </div>
      <table class="line-items">
        <thead>
          <tr>
            <th>Description</th>
            <th class="num">Qty</th>
            <th class="num">Unit price</th>
            <th class="num">Total</th>
          </tr>
        </thead>
        <tbody>${lineItemsRows}</tbody>
      </table>
      <div class="totals">
        <div class="totals__row">
          <span>Subtotal</span>
          <span class="num">${formatMoney(inv.subtotal)}</span>
        </div>
        ${adjustmentRows}
        <div class="totals__row">
          <span>Tax</span>
          <span class="num">${formatMoney(inv.tax)}</span>
        </div>
        <div class="totals__row is-final">
          <span>Total</span>
          <span class="num">${formatMoney(inv.total_amount)}</span>
        </div>
      </div>
      <div class="invoice-card__footer">
        <button class="download-btn">Download PDF</button>
      </div>
    `;
  } else {
    card.innerHTML = `
      <div class="invoice-card__header">
        <span class="invoice-card__vendor">${result.filename}</span>
      </div>
      <div class="invoice-card__error">
        <span class="error-type">${result.error_type}</span>
        <pre>${result.message}</pre>
      </div>
    `;
  }

  resultsEl.appendChild(card);

  if (result.status === "success") {
    const btn = card.querySelector(".download-btn");
    if (btn) {
      btn.addEventListener("click", () => downloadInvoicePDF(result.invoice, result.filename));
    }
  }
}
