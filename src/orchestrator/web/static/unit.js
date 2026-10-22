// The robots: engineered units rather than toys. Gunmetal housings, a visor with an LED
// strip for eyes, articulated arms, and the role's signal colour on the visor, chest stripe,
// and readout. Shared by the app (app.html) and the office (relay.html).
//
//   RelayUnit.draw(parentSvg, role, x, y, { scale, wave }) -> parts
//   RelayUnit.state(parts, "idle" | "active" | "done")
(() => {
  const NS = "http://www.w3.org/2000/svg";
  const ROLE = {
    concierge: { c: "#a08ce0", d: "#5f4fa0", id: "CON-00" },
    planner: { c: "#6b9be6", d: "#3d5d96", id: "PLN-01" },
    coder: { c: "#e6b04a", d: "#a87a22", id: "BLD-02" },
    tester: { c: "#5fc49a", d: "#3f7c63", id: "CHK-03" },
    reviewer: { c: "#e07a70", d: "#94463f", id: "REV-04" },
  };
  const METAL = "#4a5763", METAL_HI = "#5f6d7a", METAL_LO = "#2c353e", GLASS = "#081117";

  function el(tag, attrs, parent) {
    const node = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    if (parent) parent.append(node);
    return node;
  }

  function arm(g, side, cls) {
    const x = side < 0 ? -55 : 43;
    const a = el("g", { class: `arm ${cls}` }, g);
    el("rect", { x, y: 20, width: 12, height: 28, rx: 4, fill: METAL, stroke: METAL_LO, "stroke-width": 1.5 }, a);
    el("line", { x1: x + 3, y1: 26, x2: x + 3, y2: 44, stroke: METAL_HI, "stroke-width": 1.2 }, a);
    el("circle", { cx: x + 6, cy: 50, r: 5.5, fill: METAL_LO, stroke: METAL_HI, "stroke-width": 1.2 }, a);
    el("rect", { x: x + 1, y: 53, width: 10, height: 18, rx: 3, fill: METAL, stroke: METAL_LO, "stroke-width": 1.5 }, a);
    el("path", { d: `M${x} 70 h12 l-2 6 h-3 l-1 -3 l-1 3 h-3 Z`, fill: METAL_LO }, a);  // clamp
    return a;
  }

  function draw(parent, role, x, y, { scale = 1, wave = false } = {}) {
    const r = ROLE[role] || ROLE.planner;
    const g = el("g", { class: "robot", transform: `translate(${x} ${y}) scale(${scale})` }, parent);
    // sensor mast and status light
    el("rect", { x: -2, y: -68, width: 4, height: 16, fill: METAL_LO }, g);
    el("rect", { x: -5, y: -60, width: 10, height: 3, rx: 1, fill: METAL_HI }, g);
    const bulb = el("circle", { class: "bulb", cx: 0, cy: -71, r: 4.5, fill: "#3a464f", stroke: METAL_LO, "stroke-width": 1.5 }, g);
    // ear sensors
    for (const sx of [-1, 1]) {
      el("rect", { x: sx < 0 ? -47 : 39, y: -36, width: 8, height: 20, rx: 2.5, fill: METAL_LO }, g);
      el("circle", { cx: sx < 0 ? -43 : 43, cy: -26, r: 2, fill: r.c }, g);
    }
    // head housing
    el("path", { d: "M-30 -54 H30 L40 -42 V-8 L32 2 H-32 L-40 -8 V-42 Z", fill: METAL, stroke: METAL_LO, "stroke-width": 2 }, g);
    el("path", { d: "M-28 -50 H28 L35 -42", fill: "none", stroke: METAL_HI, "stroke-width": 1.5 }, g);
    for (let i = 0; i < 3; i++) el("rect", { x: 18 + i * 4, y: -49, width: 2, height: 5, rx: 1, fill: METAL_LO }, g);
    const label = el("text", { x: -26, y: -44.5, fill: "#c3ccd3", "font-size": 6, "font-family": "IBM Plex Mono, monospace", "letter-spacing": 0.5 }, g);
    label.textContent = r.id;
    // visor and eyes
    el("rect", { x: -32, y: -39, width: 64, height: 26, rx: 7, fill: GLASS, stroke: "#1a2731", "stroke-width": 1.5 }, g);
    el("path", { d: "M-27 -35 H-6", stroke: "#ffffff", "stroke-opacity": 0.08, "stroke-width": 3, "stroke-linecap": "round" }, g);
    const open = el("g", { class: "eyes-open" }, g);
    el("rect", { x: -23, y: -29, width: 15, height: 6, rx: 3, fill: r.c }, open);
    el("rect", { x: 8, y: -29, width: 15, height: 6, rx: 3, fill: r.c }, open);
    el("rect", { x: -25, y: -31, width: 50, height: 10, rx: 5, fill: r.c, opacity: 0.12 }, open);
    const shut = el("g", { class: "eyes-shut" }, g);
    el("rect", { x: -23, y: -27, width: 46, height: 2, rx: 1, fill: "#33424d" }, shut);
    const happy = el("g", { class: "eyes-happy" }, g);
    el("rect", { x: -23, y: -27, width: 30, height: 2, rx: 1, fill: "#2f5a45" }, happy);
    el("path", { d: "M11 -27 l4 4 l8 -9", fill: "none", stroke: "#6fd39b", "stroke-width": 3, "stroke-linecap": "round", "stroke-linejoin": "round" }, happy);
    // neck
    el("rect", { x: -9, y: 2, width: 18, height: 9, fill: METAL_LO }, g);
    for (let i = 0; i < 3; i++) el("line", { x1: -9, y1: 4 + i * 3, x2: 9, y2: 4 + i * 3, stroke: METAL, "stroke-width": 1 }, g);
    // torso
    el("path", { d: "M-36 10 H36 L44 20 L40 72 H-40 L-44 20 Z", fill: METAL, stroke: METAL_LO, "stroke-width": 2 }, g);
    el("rect", { x: -40, y: 15, width: 80, height: 5, fill: r.c }, g);
    el("rect", { x: -24, y: 27, width: 48, height: 24, rx: 3, fill: GLASS }, g);
    const bars = el("g", { class: "readout" }, g);
    [[30, 34], [20, 24], [26, 14]].forEach(([w, op], i) => el("rect", { x: -19, y: 32 + i * 6, width: w, height: 2.5, rx: 1, fill: r.c, opacity: 0.4 + op / 60 }, bars));
    el("circle", { cx: 16, cy: 33, r: 2.5, fill: r.d }, g);
    el("path", { d: "M-34 60 H34 M-30 66 H30", stroke: METAL_LO, "stroke-width": 1.5 }, g);
    for (const sx of [-44, 44]) el("circle", { cx: sx, cy: 21, r: 7, fill: METAL_LO, stroke: METAL_HI, "stroke-width": 1.5 }, g);
    const left = arm(g, -1, "left");
    const right = arm(g, 1, wave ? "right wave" : "right");
    return { g, bulb, open, shut, happy, left, right, role };
  }

  function state(parts, s) {
    const r = ROLE[parts.role] || ROLE.planner;
    parts.g.classList.toggle("active", s === "active");
    parts.bulb.setAttribute("fill", s === "active" ? r.c : s === "done" ? "#6fd39b" : "#3a464f");
    parts.open.style.opacity = s === "active" ? 1 : 0;
    parts.shut.style.opacity = s === "idle" ? 1 : 0;
    parts.happy.style.opacity = s === "done" ? 1 : 0;
  }

  window.RelayUnit = { draw, state, ROLE };
})();
