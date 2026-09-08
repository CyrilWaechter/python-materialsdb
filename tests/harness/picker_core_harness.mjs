// Headless check of PickerCore.collectItems (the picker's selection union)
// and PickerCore.startTargetPoll (Bonsai target polling).
// Usage: node picker_core_harness.mjs <path/to/picker-core.js>

import fs from "node:fs";

const code = fs.readFileSync(process.argv[2], "utf8");
const PickerCore = new Function(`${code}\nreturn PickerCore;`)();

// Node has no timers; startTargetPoll uses setInterval on start and
// clearInterval in its stop() handle.
globalThis.setInterval = () => 0;
globalThis.clearInterval = () => {};

const { collectItems } = PickerCore;
const setOf = (...ids) => new Set(ids);

const cases = [];
const check = (name, actual, expected) => {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  cases.push({ name, ok, actual, expected });
};

check("whole material only", collectItems(setOf("A"), new Map()), [{ id: "A" }]);

check(
  "layer-only pick (material never in selected)",
  collectItems(new Set(), new Map([["A", setOf("l1", "l2")]])),
  [{ id: "A", layer_ids: ["l1", "l2"] }],
);

check(
  "mixed whole + layers of another material",
  collectItems(setOf("A"), new Map([["B", setOf("l1")]])),
  [{ id: "A" }, { id: "B", layer_ids: ["l1"] }],
);

check("layer wins over parent for the same id", collectItems(setOf("A"), new Map([["A", setOf("l1")]])), [
  { id: "A", layer_ids: ["l1"] },
]);

check(
  "multiple layers of one material stay in a single entry",
  collectItems(setOf("A"), new Map([["A", setOf("l1", "l2", "l3")]])),
  [{ id: "A", layer_ids: ["l1", "l2", "l3"] }],
);

// startTargetPoll: fake DOM, two api responses (one applied client, then none).
let stpFailed = 0;
const stpCheck = (name, ok, detail) => {
  if (!ok) {
    stpFailed += 1;
    console.log(`FAIL ${name}: ${detail}`);
  }
};

const runStartTargetPoll = async () => {
  const responses = [
    {
      clients: [
        { client_id: "bonsai-test-client", model_path: "/home/u/wall.ifc", last_status: { status: "applied" } },
      ],
    },
    { clients: [] },
  ];
  let calls = 0;
  const apiFn = async () => responses[calls++] ?? { clients: [] };
  const select = { style: {}, value: "", innerHTML: "", disabled: undefined };
  const button = { style: {}, value: "", innerHTML: "", disabled: undefined };

  const handle = PickerCore.startTargetPoll(apiFn, select, button);
  await new Promise((resolve) => setImmediate(resolve)); // first (un-awaited) refresh completes

  stpCheck(
    "first refresh shows the target select",
    select.style.display === "inline",
    `display=${select.style.display}`,
  );
  stpCheck("first refresh enables the send button", button.disabled === false, `disabled=${button.disabled}`);
  stpCheck(
    "first refresh lists the client",
    select.innerHTML.includes("bonsai-test-client"),
    `html=${select.innerHTML}`,
  );
  stpCheck(
    "first refresh marks an applied last_status",
    select.innerHTML.includes("\u2713"),
    `html=${select.innerHTML}`,
  );

  await handle.refresh();

  stpCheck("empty refresh hides the target select", select.style.display === "none", `display=${select.style.display}`);
  stpCheck("empty refresh disables the send button", button.disabled === true, `disabled=${button.disabled}`);
};

try {
  await runStartTargetPoll();
} catch (err) {
  stpFailed = 1;
  console.log(`FAIL startTargetPoll harness: ${err && err.stack ? err.stack : err}`);
}

let failed = 0;
for (const { name, ok, actual, expected } of cases) {
  if (!ok) {
    failed += 1;
    console.log(`FAIL ${name}: got ${JSON.stringify(actual)} want ${JSON.stringify(expected)}`);
  }
}
if (failed || stpFailed) {
  console.log(`PICKER-CORE FAILED (${failed + stpFailed})`);
  process.exit(1);
}
console.log("PICKER-CORE OK");
console.log("START-TARGET-POLL OK");
