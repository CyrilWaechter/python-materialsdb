// Headless check of PickerCore.collectItems (the picker's selection union).
// Usage: node picker_core_harness.mjs <path/to/picker-core.js>

import fs from "node:fs";

const code = fs.readFileSync(process.argv[2], "utf8");
const PickerCore = new Function(`${code}\nreturn PickerCore;`)();

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

let failed = 0;
for (const { name, ok, actual, expected } of cases) {
  if (!ok) {
    failed += 1;
    console.log(`FAIL ${name}: got ${JSON.stringify(actual)} want ${JSON.stringify(expected)}`);
  }
}
if (failed) {
  console.log(`PICKER-CORE FAILED (${failed})`);
  process.exit(1);
}
console.log("PICKER-CORE OK");
