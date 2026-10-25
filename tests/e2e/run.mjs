// Browser-level test: loads the REAL page in jsdom against a live server and clicks through the flows.
//   python tests/e2e/serve.py 8765 &   then   node tests/e2e/run.mjs http://127.0.0.1:8765
import { JSDOM, VirtualConsole } from "jsdom";

const BASE = process.argv[2] || "http://127.0.0.1:8765";
const errors = [];
const vc = new VirtualConsole();
vc.on("jsdomError", e => errors.push("jsdomError: " + (e.detail || e.message)));
vc.on("error", e => errors.push("console.error: " + e));

const dom = await JSDOM.fromURL(BASE + "/", {
  runScripts: "dangerously", resources: "usable", pretendToBeVisual: true, virtualConsole: vc,
  beforeParse(window) {
    window.fetch = (u, o) => fetch(new URL(u, BASE), o);
    window.TextDecoder = TextDecoder; window.FormData = FormData;
  },
});
const { window } = dom, { document } = window;
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
const text = el => (el ? el.textContent : "");
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function waitFor(fn, what, ms = 8000) {
  const t = Date.now();
  while (Date.now() - t < ms) { try { const v = fn(); if (v) return v; } catch (e) {} await sleep(25); }
  throw new Error("timeout waiting for: " + what + "\n--- page text ---\n" + text(document.body).replace(/\s+/g, " ").slice(0, 900));
}
let passed = 0;
function ok(cond, msg) { if (!cond) throw new Error("ASSERT FAILED: " + msg); passed++; console.log("  ok  " + msg); }
const click = el => el.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
async function runAndWait() {
  click($("#runbtn"));
  await waitFor(() => $("#runbtn").textContent === "Run process" && $(".decision, .banner.err"), "run to finish");
}
async function pickCase(vendorStart) {
  click($$(".case").find(b => text(b).startsWith(vendorStart)));
  await waitFor(() => !$("#runbtn").disabled, "run button enabled");
}

try {
  console.log("Boot");
  await waitFor(() => $$(".case").length === 6, "6 test cases rendered");
  ok(text($("#modeltext")).includes("Gemini"), "model pill shows the provider: " + text($("#modeltext")));
  ok($("#modeldot").className.includes("on"), "model dot is green");
  ok($("#runbtn").disabled, "run button disabled until a case is chosen");
  $("#speed").value = "instant";

  console.log("Pending case (state mismatch)");
  await pickCase("Deccan");
  await runAndWait();
  ok(text($(".decision .big")) === "PENDING", "decision is PENDING");
  ok(text($(".decision")).includes("Maharashtra"), "the ask names the state to fix");
  const stages = $$(".stage");
  ok(stages.length === 8, "8 stage cards shown (resubmission + records are hidden when not relevant): " + stages.length);
  ok(stages.every(s => ["done", "warn"].includes(s.getAttribute("data-s"))), "every stage finished (done/warn): " + stages.map(s => s.getAttribute("data-s")).join(","));
  ok($(".stage[data-stage=crossref] .r-FAIL") !== null, "cross-check stage shows the failing rule");
  ok($$(".slotbox").length === 4, "four documents shown in the document stage");
  ok($$(".st-time").some(t => /\d\.\d s/.test(t.textContent)), "stages show real timings");
  ok(text($(".mail")).includes("1. "), "vendor email lists the ask");

  console.log("Rejected case (reused bank account)");
  await pickCase("Nexora");
  await runAndWait();
  ok(text($(".decision .big")) === "REJECTED", "decision is REJECTED");
  ok(text($(".decision")).includes("internal only"), "reasons are labelled internal only");
  const mail = text($(".mail"));
  ok(!/orbit|V-0001|fraud|bank|account/i.test(mail), "vendor email reveals nothing: " + mail.replace(/\s+/g, " ").slice(0, 120));
  click($$(".tabs button").find(b => text(b) === "Internal note"));
  ok(text($(".mail")).includes("V-0001"), "internal note names the matched vendor");

  console.log("Resubmission loop (05a then 05b)");
  await pickCase("Greenleaf Facility Services Pvt Ltd".slice(0, 9));
  const cases = $$(".case").filter(b => text(b).includes("SUB-1005"));
  click(cases.find(b => !text(b).includes("v2"))); await runAndWait();
  ok(text($(".decision .big")) === "PENDING", "05a is PENDING");
  ok(text($(".decision")).includes("PAN card"), "ask says the file looks like a PAN card");
  click($$(".case").find(b => text(b).includes("v2"))); await runAndWait();
  ok(text($(".decision .big")) === "APPROVED", "05b is APPROVED");
  ok(text(document.body).includes("What changed since version 1"), "changes section is shown");
  ok(text(document.body).includes("Files the vendor changed: GST registration certificate"), "only the GST certificate is reported changed");
  ok(text($(".stage[data-stage=resubmission]")).length > 0, "a Resubmission stage appeared in the live view");
  ok(text($(".stage[data-stage=commit]")).length > 0, "a Vendor records stage appeared for the approved vendor");
  ok($$(".badge").some(b => text(b) === "reused, no model call"), "unchanged documents are labelled as reused");

  console.log("Dashboard");
  window.location.hash = "#/dashboard";
  await waitFor(() => $$("#runs tr").length >= 4, "run history rows");
  ok($$("#runs tr").length === 4, "4 runs listed: " + $$("#runs tr").length);
  const stats = $$(".stat").map(s => text(s.querySelector(".n")));
  ok(stats.join(",") === "4,1,2,1,1", "stat cards total/approved/pending/rejected/resubmissions = " + stats.join(","));
  click($$(".fchip").find(b => b.dataset.f === "REJECTED"));
  ok($$("#runs tr").length === 1 && text($("#runs")).includes("Nexora"), "filter chip shows only rejected");
  click($$(".fchip").find(b => b.dataset.f === "ALL"));
  const q = $("#q"); q.value = "deccan"; q.dispatchEvent(new window.Event("input", { bubbles: true }));
  ok($$("#runs tr").length === 1, "search narrows the table");
  q.value = ""; q.dispatchEvent(new window.Event("input", { bubbles: true }));
  const v2row = $$("#runs tr").find(r => text(r).includes("SUB-1005") && text(r).includes("v2"));
  click(v2row);
  await waitFor(() => $("#detail .decision"), "run detail loaded");
  ok(text($("#detail")).includes("All versions of this submission"), "version switcher shown for a resubmitted submission");
  ok(text($("#detail")).includes("What changed since version 1"), "detail shows what changed");
  ok(window.location.hash.startsWith("#/dashboard/"), "URL records the open run");
  ok($$("#detail img").length === 4, "document thumbnails render in the detail: " + $$("#detail img").length);

  console.log("Resubmit from the dashboard");
  click($$("#detail .btn").find(b => text(b) === "Resubmit with changes"));
  await waitFor(() => text($("#custom-pane")).includes("Resubmitting SUB-1005"), "resubmit form opens");
  ok(!$("#custom-pane").classList.contains("hidden"), "custom submission pane visible");
  ok($("input[name='company.vendor_name']").value.startsWith("Greenleaf"), "form is prefilled with the previous values");
  ok(text($("#custom-pane")).includes("using the file from the starting submission"), "existing files are carried over");
  await runAndWait();
  ok(text($(".decision .big")) !== "", "resubmitted run produced a decision: " + text($(".decision .big")));
  ok(text($("#runmeta")).includes("v3"), "it is recorded as the next version: " + text($("#runmeta")));
  ok(text(document.body).includes("What changed since version 2"), "and compared with the previous one");

  console.log("Custom submission from a blank form");
  click($("#mode-custom"));
  const sel = $("#startfrom"); sel.value = ""; sel.dispatchEvent(new window.Event("change", { bubbles: true }));
  await waitFor(() => !text($("#custom-pane")).includes("Resubmitting"), "blank form");
  await runAndWait();
  ok(text($(".decision .big")) === "PENDING", "an empty form is PENDING, not an error");
  ok(text($(".decision")).includes("missing"), "and lists what is missing");

  console.log("Server errors are shown, not swallowed");
  const realFetch = window.fetch;
  window.fetch = (u, o) => String(u).includes("/api/run")
    ? Promise.resolve(new Response(JSON.stringify({ error: "The server is busy with other runs. Try again in a few seconds." }), { status: 429 }))
    : realFetch(u, o);
  click($("#mode-cases")); await pickCase("Kavya"); click($("#runbtn"));
  await waitFor(() => $(".banner.err"), "error banner");
  ok(text($(".banner.err")).includes("busy"), "429 message is displayed: " + text($(".banner.err")));
  ok($("#runbtn").textContent === "Run process" && !$("#runbtn").disabled, "run button recovers after an error");
  window.fetch = realFetch;

  ok(errors.length === 0, "no JavaScript errors were logged" + (errors.length ? ": " + errors.join(" | ") : ""));
  console.log(`\nALL ${passed} BROWSER CHECKS PASSED`);
  window.close(); process.exit(0);
} catch (e) {
  console.error("\nFAILED:", e.message);
  if (errors.length) console.error("JS errors:", errors);
  process.exit(1);
}
