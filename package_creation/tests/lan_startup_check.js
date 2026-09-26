"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {webcrypto} = require("node:crypto");

const source = fs.readFileSync(path.join(__dirname, "../service/static/app.js"), "utf8");

function start(hash = "") {
  const elements = new Map();
  const events = new Map();
  const element = selector => {
    if (!elements.has(selector)) elements.set(selector, {
      hidden: true, disabled: false, textContent: "", innerHTML: "", value: "",
      addEventListener() {},
    });
    return elements.get(selector);
  };
  const state = {reloads: 0, elements, events};
  const location = {hash, pathname: "/", search: "", reload() {state.reloads++;}};
  const context = vm.createContext({
    // LAN HTTP has getRandomValues, but no secure-context randomUUID API.
    crypto: {getRandomValues: webcrypto.getRandomValues.bind(webcrypto)},
    Uint8Array, URL, URLSearchParams,
    location,
    history: {replaceState() {location.hash = "";}},
    window: {addEventListener(name, callback) {events.set(name, callback);}},
    document: {
      querySelector: element,
      querySelectorAll() {return [];},
      createElement() {
        return {textContent: "", get innerHTML() {return this.textContent.replaceAll("&", "&amp;").replaceAll("<", "&lt;");}};
      },
    },
  });
  vm.runInContext(source, context);
  return {...state, context, location, state};
}

const bare = start();
assert.equal(bare.elements.has("#access-panel"), false);
assert.match(bare.elements.get("#tattoo-list").innerHTML, /class="tattoo-card"/);
vm.runInContext("addTattoo()", bare.context);
const fields = [...bare.elements.get("#tattoo-list").innerHTML.matchAll(/id="file-([a-f0-9-]+)-(am|af)"/g)];
assert.equal(fields.length, 4);
const ids = new Set(fields.map(match => match[1]));
assert.equal(ids.size, 2);
for (const id of ids) assert.match(id, /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/);

const linked = start("#token=initial-access");
assert.equal(linked.elements.has("#access-panel"), false);
assert.equal(linked.state.reloads, 0);
assert.equal(linked.events.has("creator-tools-access-required"), false);
assert.match(linked.elements.get("#tattoo-list").innerHTML, /class="tattoo-card"/);

console.log("Plain LAN startup and unique upload fields passed without browser credentials");
